"""
Runs one formal Run A or Run B chaos trial per docs/measurement-protocol.md:
applies a scenario, captures T0, polls telemetry every 60s (the frozen
sample interval) until the frontend SLO recovers or the 10-minute timeout,
computing Td/Tr/MTTD/MTTR — the first script in this repo that measures
recovery, not just detection.

Run A (--condition A): Td is native-observability detection (frontend
success rate < 0.99, OR a boutique pod not Running/Ready, OR a container
restart count increase vs the pre-fault snapshot) — no ML, no operator.

Run B (--condition B): Td is the trained Isolation Forest's anomaly score
crossing tau. On first detection, this creates a REAL RemediationAction CR
(dryRun: false) so the already-running operator (operator/handlers.py) can
actually act — this is what makes Run B's MTTR meaningfully different from
Run A's.

Recovery (Tr, same rule both conditions): the first point where frontend
success rate >= 0.99 holds for two consecutive 60s samples, per protocol —
"do not start MTTR at Td; starting at T0 keeps Run A and Run B comparable."

Prometheus/Loki/Jaeger query logic mirrors fusion-engine/build_state_vector.py
and infra/scripts/collect-baseline.sh — duplicated here (not imported; those
live in hyphenated directories) rather than deduplicated, to keep this
script self-contained. Keep the three in sync if you change one.

Usage:
    python3 evaluation/analysis/run_trial.py \
      --scenario evaluation/scenarios/scenario-01-cpu-starvation.yaml \
      --condition A

    python3 evaluation/analysis/run_trial.py \
      --scenario evaluation/scenarios/scenario-01-cpu-starvation.yaml \
      --condition B \
      --model-dir evaluation/runs/baseline/<run-id>/model-artifacts
"""
import argparse
import json
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import requests
import yaml

ROOT_DIR = Path(__file__).resolve().parents[2]
SAMPLE_INTERVAL_SECONDS = 60  # frozen per docs/measurement-protocol.md
TRIAL_TIMEOUT_SECONDS = 600   # T0 + 10 min, per protocol's Te definition
SLO_THRESHOLD = 0.99
ERROR_LOG_PATTERN = re.compile(r"(error|fail|exception|5\d\d)", re.IGNORECASE)

METRIC_QUERIES = {
    "cpu_usage": 'sum(rate(container_cpu_usage_seconds_total{namespace="boutique"}[1m]))',
    "memory_working_set": 'sum(container_memory_working_set_bytes{namespace="boutique"})',
    "network_receive_bytes": 'sum(rate(container_network_receive_bytes_total{namespace="boutique"}[1m]))',
    "frontend_success_rate": (
        '1 - ((sum(rate(boutique_traces_span_metrics_calls_total{'
        'service_name="frontend",span_kind="SPAN_KIND_SERVER",status_code="STATUS_CODE_ERROR"}[1m])) '
        'or vector(0)) / clamp_min(sum(rate(boutique_traces_span_metrics_calls_total{'
        'service_name="frontend",span_kind="SPAN_KIND_SERVER"}[1m])), 1e-9))'
    ),
    # v2 per-service metrics (decision-engine/model-config-v2.yaml only) —
    # mirrors the same keys added to infra/scripts/collect-baseline.sh.
    # NOTE: filtered by pod=~"<service>-.*", not container="<service>" — every
    # Online Boutique container is literally named "server" regardless of
    # Deployment name, so a container= filter matches nothing. Same bug as
    # collect-baseline.sh's v2 queries (fixed there 2026-09-16); this copy
    # was missed at the time and caused every v2 Run B trial's anomaly_score
    # to be None (missing features -> score_sample() can't score -> MTTD
    # always censors) until found via a live campaign run. See docs/experiment-log.md.
    "memory_working_set_cartservice": 'sum(container_memory_working_set_bytes{namespace="boutique",pod=~"cartservice-.*"})',
    "network_receive_bytes_productcatalogservice": 'sum(rate(container_network_receive_bytes_total{namespace="boutique",pod=~"productcatalogservice-.*"}[1m]))',
    # Added 2026-09-17 for R7v2-disk-io-stress-evict — mirrors the same key
    # added to infra/scripts/collect-baseline.sh; see that file's own note on
    # why R7 (v1, trigger cpu_util) never matched across 5/5 real scenario-03
    # Run B trials.
    "cpu_usage_paymentservice": 'sum(rate(container_cpu_usage_seconds_total{namespace="boutique",pod=~"paymentservice-.*"}[1m]))',
}
FEATURES = ["cpu_util", "mem_util", "network_rx", "log_error_rate", "trace_latency_ms", "trace_error_pct"]


def log(msg):
    print(f"[trial] {msg}", flush=True)


def die(msg):
    sys.exit(f"[trial] ERROR: {msg}")


def sh(cmd, check=True):
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if check and result.returncode != 0:
        die(f"command failed: {cmd}\n{result.stderr}")
    return result.stdout.strip()


def now_iso():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def jaeger_restart_count():
    out = sh(
        "kubectl get pod -n observability -l app.kubernetes.io/name=jaeger "
        "-o jsonpath='{.items[0].status.containerStatuses[0].restartCount}'",
        check=False,
    )
    return out or "?"


# scenario-12 has no Chaos Mesh CRD — see scenario-12-config-drift.yaml's own
# header comment for why (config drift is injected via a direct kubectl
# patch, not a chaos-mesh.org resource) and for the exact commands below.
MANUAL_SCENARIOS = {
    "scenario-12-config-drift": {
        "patch_cmd": (
            "kubectl patch deployment checkoutservice -n boutique --type=json "
            "-p='[{\"op\":\"replace\",\"path\":\"/spec/template/spec/containers/0/env/0/value\","
            "\"value\":\"paymentservice.boutique.svc.cluster.local:9\"}]'"
        ),
        "revert_cmd": "kubectl apply -k infra/boutique/",
    },
}


def is_manual_scenario(scenario_file):
    return Path(scenario_file).stem in MANUAL_SCENARIOS


def apply_manual_scenario(scenario_file):
    """Returns (t0_str, t0_source) — there is no Chaos Mesh status field to
    read, so T0 is the patch command's own timestamp (see
    docs/measurement-protocol.md's apply_timestamp_fallback precedent)."""
    stem = Path(scenario_file).stem
    t0_str = now_iso()
    sh(MANUAL_SCENARIOS[stem]["patch_cmd"])
    return t0_str, "kubectl_patch_timestamp"


def revert_manual_scenario(scenario_file):
    stem = Path(scenario_file).stem
    sh(MANUAL_SCENARIOS[stem]["revert_cmd"], check=False)


def clear_and_apply(scenario_file):
    if "kind:" not in Path(scenario_file).read_text():
        die(f"{scenario_file} has no Kubernetes resource (e.g. scenario-12 is manual-only)")
    sh(f"kubectl delete -f {scenario_file} --ignore-not-found", check=False)
    ref = sh(f"kubectl apply -f {scenario_file} -o name")
    resource_type, resource_name = ref.split("/", 1)
    namespace = sh(
        f"kubectl get {resource_type} {resource_name} -A -o jsonpath='{{.items[0].metadata.namespace}}'",
        check=False,
    ) or "chaos-mesh"
    return ref, resource_type, resource_name, namespace


def wait_for_active(resource_type, resource_name, namespace):
    for _ in range(10):
        phase = sh(
            f"kubectl get {resource_type} {resource_name} -n {namespace} "
            "-o jsonpath='{.status.experiment.desiredPhase}'",
            check=False,
        )
        if phase == "Run":
            return True
        time.sleep(2)
    return False


def capture_t0(resource_type, resource_name, namespace, apply_ts, retries=5, retry_delay=1.5):
    # status.instances is often not populated the instant desiredPhase flips to
    # "Run" (observed 2026-09-07, Run A/Iteration 1) — poll briefly before
    # falling back to the less precise sources below.
    for attempt in range(retries):
        instance_starts = sh(
            f"kubectl get {resource_type} {resource_name} -n {namespace} "
            "-o jsonpath='{.status.instances.*.startTime}'",
            check=False,
        )
        candidates = sorted(t for t in instance_starts.split() if t and t != "1970-01-01T00:00:00Z")
        if candidates:
            return candidates[0], "status.instances.*.startTime"
        if attempt < retries - 1:
            time.sleep(retry_delay)
    record_start = sh(
        f"kubectl get {resource_type} {resource_name} -n {namespace} "
        "-o jsonpath='{.status.experiment.containerRecords[0].events[0].timestamp}'",
        check=False,
    )
    if record_start:
        return record_start, "status.experiment.containerRecords[0].events[0].timestamp"
    return apply_ts, "apply_timestamp_fallback"


def cleanup_scenario(scenario_file):
    sh(f"kubectl delete -f {scenario_file} --ignore-not-found", check=False)


def deployment_replica_snapshot(namespace="boutique"):
    out = sh(f"kubectl get deployment -n {namespace} -o json", check=False)
    if not out:
        return {}
    items = json.loads(out).get("items", [])
    return {d["metadata"]["name"]: d["spec"]["replicas"] for d in items}


def restore_replicas(namespace, pre_fault_replicas):
    """Scale any deployment the trial (e.g. a Run B scale action) left at a
    different replica count back to its pre-fault value, so the next trial
    starts from a clean baseline. Previously done manually (see
    docs/experiment-log.md, Run B/Iteration 1 notes)."""
    current = deployment_replica_snapshot(namespace)
    for name, replicas in pre_fault_replicas.items():
        if current.get(name) != replicas:
            sh(f"kubectl scale deployment {name} -n {namespace} --replicas={replicas}", check=False)
            log(f"Restored deployment/{name} to {replicas} replicas (was {current.get(name)})")


# --- Telemetry sampling (mirrors fusion-engine/build_state_vector.py) ---

def _port_forward(namespace, svc, local_port, remote_port):
    proc = subprocess.Popen(
        ["kubectl", "port-forward", "-n", namespace, f"svc/{svc}", f"{local_port}:{remote_port}"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return proc


def _resolve_svc(known_name, label_selector):
    """Mirrors the discovery fallback in infra/scripts/collect-baseline.sh."""
    exists = sh(f"kubectl get svc -n observability {known_name} -o name", check=False)
    if exists:
        return known_name
    return sh(
        f"kubectl get svc -n observability -l {label_selector} "
        "-o jsonpath='{.items[0].metadata.name}'",
        check=False,
    )


def setup_port_forwards():
    prom_svc = _resolve_svc("kube-prometheus-prometheus", "app.kubernetes.io/name=prometheus")
    loki_svc = _resolve_svc("loki-gateway", "app.kubernetes.io/name=loki")
    jaeger_svc = "jaeger-query-external" if sh(
        "kubectl get svc -n observability jaeger-query-external -o name", check=False
    ) else "jaeger-query"
    if not prom_svc or not loki_svc:
        die(f"could not resolve observability services (prom={prom_svc!r}, loki={loki_svc!r})")
    procs = [
        _port_forward("observability", prom_svc, 19090, 9090),
        _port_forward("observability", loki_svc, 13100, 80),
        _port_forward("observability", jaeger_svc, 16686, 16686),
    ]
    time.sleep(5)
    return procs


def teardown_port_forwards(procs):
    for p in procs:
        p.terminate()


def prom_query(query, eval_time=None):
    try:
        params = {"query": query}
        if eval_time is not None:
            params["time"] = eval_time
        r = requests.get("http://127.0.0.1:19090/api/v1/query", params=params, timeout=5)
        result = r.json()["data"]["result"]
        return float(result[0]["value"][1]) if result else None
    except Exception:
        return None


def compute_availability(t0, end_time):
    """Availability over [t0, end_time] per docs/measurement-protocol.md:
    successful frontend server spans / all frontend server spans, computed
    directly from the spanmetrics counters (not approximated from sampled
    frontend_success_rate, which was the prior gap noted in the experiment
    log)."""
    duration_s = int((end_time - t0).total_seconds())
    if duration_s <= 0:
        return None
    end_ts = end_time.timestamp()
    total = prom_query(
        'sum(increase(boutique_traces_span_metrics_calls_total{'
        f'service_name="frontend",span_kind="SPAN_KIND_SERVER"}}[{duration_s}s]))',
        eval_time=end_ts,
    )
    if not total:
        return None
    errors = prom_query(
        'sum(increase(boutique_traces_span_metrics_calls_total{'
        f'service_name="frontend",span_kind="SPAN_KIND_SERVER",status_code="STATUS_CODE_ERROR"}}[{duration_s}s]))',
        eval_time=end_ts,
    ) or 0.0
    return 1.0 - (errors / total)


def log_error_rate():
    try:
        end_ns = int(time.time() * 1e9)
        start_ns = int((time.time() - 65) * 1e9)
        r = requests.get(
            "http://127.0.0.1:13100/loki/api/v1/query_range",
            params={"query": '{namespace="boutique"}', "start": start_ns, "end": end_ns, "limit": 500},
            timeout=5,
        )
        streams = r.json().get("data", {}).get("result", [])
        errors = sum(1 for s in streams for _, line in s.get("values", []) if ERROR_LOG_PATTERN.search(line))
        return errors / 65.0
    except Exception:
        return None


def trace_features(service="frontend"):
    try:
        r = requests.get(
            "http://127.0.0.1:16686/api/traces", params={"service": service, "limit": 20}, timeout=5
        )
        traces = r.json().get("data")
        if not traces:
            return 0.0, 0.0
        latencies, errors, spans = [], 0, 0
        for t in traces:
            for span in t.get("spans", []):
                spans += 1
                latencies.append(span["duration"] / 1000.0)
                for tag in span.get("tags", []):
                    if tag.get("key") == "error" and tag.get("value") in (True, "true"):
                        errors += 1
        if spans == 0:
            return 0.0, 0.0
        return float(np.mean(latencies)), errors / spans
    except Exception:
        return None, None


def take_sample(v2=False):
    cpu = prom_query(METRIC_QUERIES["cpu_usage"])
    mem = prom_query(METRIC_QUERIES["memory_working_set"])
    net = prom_query(METRIC_QUERIES["network_receive_bytes"])
    slo = prom_query(METRIC_QUERIES["frontend_success_rate"])
    logs = log_error_rate()
    latency, err_pct = trace_features()
    sample = {
        "timestamp": now_iso(),
        "cpu_util": cpu,
        "mem_util": mem / (1024 * 1024) if mem is not None else None,
        "network_rx": net / 1024 if net is not None else None,
        "log_error_rate": logs,
        "trace_latency_ms": latency,
        "trace_error_pct": err_pct,
        "frontend_success_rate": slo,
    }
    if v2:
        # decision-engine/model-config-v2.yaml features only — see
        # docs/experiment-log.md's "v2 model" entry.
        mem_cart = prom_query(METRIC_QUERIES["memory_working_set_cartservice"])
        net_pcs = prom_query(METRIC_QUERIES["network_receive_bytes_productcatalogservice"])
        _, cart_err_pct = trace_features(service="cartservice")
        cpu_pay = prom_query(METRIC_QUERIES["cpu_usage_paymentservice"])
        sample["mem_util_cartservice"] = mem_cart / (1024 * 1024) if mem_cart is not None else None
        sample["network_rx_productcatalogservice"] = net_pcs / 1024 if net_pcs is not None else None
        sample["trace_error_pct_cartservice"] = cart_err_pct
        sample["cpu_util_paymentservice"] = cpu_pay
    return sample


# --- Run A native detection ---

def boutique_pod_snapshot():
    out = sh("kubectl get pods -n boutique -o json", check=False)
    if not out:
        return {}
    pods = json.loads(out).get("items", [])
    snapshot = {}
    for pod in pods:
        name = pod["metadata"]["name"]
        phase = pod["status"].get("phase")
        ready = all(c.get("ready") for c in pod["status"].get("containerStatuses", [])) if pod["status"].get("containerStatuses") else False
        restarts = sum(c.get("restartCount", 0) for c in pod["status"].get("containerStatuses", []))
        snapshot[name] = {"phase": phase, "ready": ready, "restarts": restarts}
    return snapshot


def run_a_detected(sample, pre_fault_snapshot):
    if sample["frontend_success_rate"] is not None and sample["frontend_success_rate"] < SLO_THRESHOLD:
        return True, "success-rate"
    current = boutique_pod_snapshot()
    for name, info in current.items():
        if info["phase"] != "Running" or not info["ready"]:
            return True, "pod-not-ready"
        pre = pre_fault_snapshot.get(name)
        if pre and info["restarts"] > pre["restarts"]:
            return True, "restart-count"
    return False, None


# --- Run B ML detection + actuation ---

def load_model(model_dir):
    scaler = joblib.load(Path(model_dir) / "scaler.pkl")
    iforest = joblib.load(Path(model_dir) / "isolation_forest.pkl")
    threshold = json.loads((Path(model_dir) / "threshold.json").read_text())
    return scaler, iforest, threshold


def load_playbook(playbook_path=None):
    playbook_path = playbook_path or (ROOT_DIR / "decision-engine" / "playbook.yaml")
    return yaml.safe_load(Path(playbook_path).read_text())["rules"]


def score_sample(sample, scaler, iforest, features):
    if any(sample.get(f) is None for f in features):
        return None
    X = scaler.transform([[sample[f] for f in features]])
    return float(-iforest.score_samples(X)[0])


def match_rule(sample, scaler, rules, z_threshold, features):
    """Mirrors decision-engine/orchestrator.py's match_rule — keep in sync.

    A rule matches if ITS OWN trigger_feature's |z-score| exceeds
    z_threshold, not only if that feature is the single most-deviated one
    overall. Fixed 2026-09-07: the old "top feature only" version missed a
    real CPU fault because log_error_rate briefly out-ranked cpu_util
    before the CPU stress had fully ramped up.

    `features` is the model's own feature list (threshold["features"]), not
    a hardcoded constant — this is what lets the same function serve both
    the v1 6-feature model and decision-engine/model-config-v2.yaml's
    9-feature model.
    """
    z_scores = (np.array([sample[f] for f in features]) - scaler.mean_) / scaler.scale_
    candidates = []
    for rule in rules:
        idx = features.index(rule["trigger_feature"])
        z = float(z_scores[idx])
        if abs(z) > z_threshold:
            candidates.append((abs(z), rule, rule["trigger_feature"], z))

    top_idx = int(np.argmax(np.abs(z_scores)))
    top_feature, top_z = features[top_idx], float(z_scores[top_idx])

    if not candidates:
        return None, top_feature, top_z
    candidates.sort(key=lambda c: -c[0])
    _, rule, matched_feature, matched_z = candidates[0]
    return rule, matched_feature, matched_z


def resolve_dynamic_target(target):
    """Rules whose target is chosen at trial time rather than fixed in
    decision-engine/playbook.yaml (target.kind == "Node", target.name ==
    "AUTO" — e.g. R10-node-starvation-evict, scenario-08) get resolved here:
    the node currently hosting a not-Running/not-Ready boutique pod is
    treated as the affected node. Returns the resolved target dict, or None
    if no unhealthy pod can be found yet (caller should retry on a later
    sample rather than act on a guess)."""
    if target.get("kind") != "Node" or target.get("name") != "AUTO":
        return target
    out = sh("kubectl get pods -n boutique -o json", check=False)
    if not out:
        return None
    for pod in json.loads(out).get("items", []):
        phase = pod["status"].get("phase")
        ready = all(c.get("ready") for c in pod["status"].get("containerStatuses", [])) if pod["status"].get("containerStatuses") else False
        if phase != "Running" or not ready:
            node_name = pod["spec"].get("nodeName")
            if node_name:
                return {**target, "name": node_name}
    return None


def match_and_emit(sample, scaler, rules, score, tau, z_threshold, features):
    """Returns (acted, record). acted=True only once a RemediationAction was
    actually created — caller should keep retrying on later samples while
    False. `record` is always populated with the match attempt's outcome
    (even when nothing matched/nothing was created) so main() can persist it
    into the trial JSON — previously this was only ever printed to stdout
    and lost the moment the terminal scrollback did (see docs/experiment-log.md,
    2026-09-17: scenario-03's actual rule matches had to be reconstructed
    from still-live RemediationAction CRs on the cluster after the fact)."""
    import kubernetes
    rule, matched_feature, matched_z = match_rule(sample, scaler, rules, z_threshold, features)
    if rule is None:
        log(f"anomaly_score={score:.4f} > tau={tau:.4f}, top feature={matched_feature} "
            f"(z={matched_z:.2f}) — NO MATCHING RULE")
        return False, {
            "rule_id": None, "outcome": "no_matching_rule",
            "top_feature": matched_feature, "top_z": matched_z,
            "anomaly_score": score, "tau": tau,
        }
    target = resolve_dynamic_target(rule["target"])
    if target is None:
        log(f"rule {rule['rule_id']} matched but its dynamic target (Node/AUTO) "
            "could not be resolved yet — no unhealthy pod found, will retry on next sample")
        return False, {
            "rule_id": rule["rule_id"], "outcome": "dynamic_target_unresolved",
            "matched_feature": matched_feature, "matched_z": matched_z,
            "anomaly_score": score, "tau": tau,
        }
    name = f"{rule['rule_id'].lower()}-{int(time.time())}"
    explanation = rule["explanation_template"].format(
        name=target["name"], replicas=rule.get("scale_replicas"),
        zscore=matched_z, score=score, tau=tau, rule_id=rule["rule_id"],
    )
    body = {
        "apiVersion": "selfhealing.research.io/v1alpha1",
        "kind": "RemediationAction",
        "metadata": {"name": name, "namespace": target["namespace"]},
        "spec": {
            "targetRef": target, "action": rule["action"], "scaleReplicas": rule.get("scale_replicas"),
            "ruleId": rule["rule_id"], "anomalyScore": score, "tau": tau,
            "explanation": explanation, "dryRun": False,
        },
    }
    try:
        kubernetes.config.load_kube_config()
    except Exception:
        kubernetes.config.load_incluster_config()
    api = kubernetes.client.CustomObjectsApi()
    # _request_timeout bounds a stalled apiserver connection to a few seconds
    # instead of hanging indefinitely — observed 2026-09-13 consuming an
    # entire trial's 600s budget in a single loop iteration (1 sample
    # recorded, Td/Tr forced to censor even though nothing had actually
    # failed). See docs/experiment-log.md.
    try:
        created = api.create_namespaced_custom_object(
            group="selfhealing.research.io", version="v1alpha1",
            namespace=target["namespace"], plural="remediationactions", body=body,
            _request_timeout=15,
        )
    except Exception as e:
        log(f"RemediationAction create failed/timed out ({e}) — will retry on next sample")
        return False, {
            "rule_id": rule["rule_id"], "outcome": "create_failed_or_timed_out",
            "matched_feature": matched_feature, "matched_z": matched_z,
            "anomaly_score": score, "tau": tau, "error": str(e),
        }
    log(f"Created RemediationAction/{created['metadata']['name']} — rule={rule['rule_id']}, "
        f"matched_feature={matched_feature} (z={matched_z:.2f})")
    return True, {
        "rule_id": rule["rule_id"], "outcome": "created",
        "action": rule["action"], "target": target,
        "matched_feature": matched_feature, "matched_z": matched_z,
        "anomaly_score": score, "tau": tau,
        "remediation_action_name": created["metadata"]["name"],
    }


def recovery_check(recent_samples):
    """Two consecutive samples with frontend_success_rate >= SLO_THRESHOLD."""
    if len(recent_samples) < 2:
        return False
    a, b = recent_samples[-2]["frontend_success_rate"], recent_samples[-1]["frontend_success_rate"]
    return a is not None and b is not None and a >= SLO_THRESHOLD and b >= SLO_THRESHOLD


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--condition", required=True, choices=["A", "B"])
    parser.add_argument("--model-dir", default=None, help="Required for --condition B")
    parser.add_argument(
        "--playbook", default=None,
        help="Defaults to decision-engine/playbook.yaml. Pass "
             "decision-engine/playbook-v2.yaml when --model-dir points at a "
             "v2 (per-service-feature) model — see docs/experiment-log.md.",
    )
    parser.add_argument(
        "--config", default=None,
        help="Model config to read rule_match_z_threshold from. Defaults to "
             "decision-engine/model-config.yaml; pass model-config-v2.yaml "
             "to match a v2 --model-dir/--playbook.",
    )
    parser.add_argument("--timeout-seconds", type=int, default=TRIAL_TIMEOUT_SECONDS)
    args = parser.parse_args()

    if args.condition == "B" and not args.model_dir:
        die("--model-dir is required for --condition B")

    scaler = iforest = threshold = rules = None
    features = FEATURES
    is_v2 = False
    z_threshold = 3.0
    if args.condition == "B":
        scaler, iforest, threshold = load_model(args.model_dir)
        rules = load_playbook(args.playbook)
        features = threshold.get("features", FEATURES)
        is_v2 = any(f not in FEATURES for f in features)
        config_path = Path(args.config) if args.config else (ROOT_DIR / "decision-engine" / "model-config.yaml")
        z_threshold = yaml.safe_load(config_path.read_text()).get("rule_match_z_threshold", 3.0)

    restarts_before = jaeger_restart_count()
    log(f"Jaeger restart count before trial: {restarts_before}")

    pre_fault_snapshot = boutique_pod_snapshot()
    pre_fault_replicas = deployment_replica_snapshot("boutique")

    manual = is_manual_scenario(args.scenario)
    resource_type = resource_name = namespace = None
    if manual:
        log("Manual scenario (no Chaos Mesh CRD) — applying kubectl patch directly...")
        t0_str, t0_source = apply_manual_scenario(args.scenario)
        log(f"Patched at {t0_str}")
    else:
        log("Clearing stale experiment and applying fresh...")
        apply_ts = now_iso()
        ref, resource_type, resource_name, namespace = clear_and_apply(args.scenario)
        log(f"Created {ref} at {apply_ts}")

        active = wait_for_active(resource_type, resource_name, namespace)
        log(f"Experiment active: {active}")

        t0_str, t0_source = capture_t0(resource_type, resource_name, namespace, apply_ts)
    t0 = datetime.fromisoformat(t0_str.replace("Z", "+00:00"))
    log(f"T0 = {t0_str} (source: {t0_source})")

    port_forwards = setup_port_forwards()
    samples = []
    td = None
    td_signal = None
    tr = None
    acted = False
    availability = None
    remediation = None
    last_match_attempt = None

    try:
        start = time.time()
        while time.time() - start < args.timeout_seconds:
            iter_start = time.time()
            sample = take_sample(v2=is_v2)
            samples.append(sample)
            elapsed = (datetime.now(timezone.utc) - t0).total_seconds()
            log(f"t+{elapsed:.0f}s: success_rate={sample['frontend_success_rate']}, "
                f"cpu={sample['cpu_util']}, mem={sample['mem_util']}")

            if args.condition == "A":
                if td is None:
                    detected, signal = run_a_detected(sample, pre_fault_snapshot)
                    if detected:
                        td = datetime.now(timezone.utc)
                        td_signal = signal
                        log(f"Td reached (Run A signal: {signal})")
            else:
                score = score_sample(sample, scaler, iforest, features)
                sample["anomaly_score"] = score
                if score is not None and score > threshold["tau"]:
                    if td is None:
                        td = datetime.now(timezone.utc)
                        td_signal = f"anomaly_score={score:.4f}>tau={threshold['tau']:.4f}"
                        log(f"Td reached (Run B: {td_signal})")
                    # Keep retrying rule-matching/actuation on later samples even
                    # after Td latches — a rule may not qualify yet on the exact
                    # sample that first crossed tau (e.g. cpu_util hadn't ramped
                    # up while log_error_rate had, on 2026-09-07's first attempt
                    # at this fix). Td itself must not move once set.
                    if not acted:
                        acted, last_match_attempt = match_and_emit(
                            sample, scaler, rules, score, threshold["tau"], z_threshold, features
                        )
                        if acted:
                            remediation = last_match_attempt

            if recovery_check(samples):
                tr = datetime.now(timezone.utc)
                log(f"Tr reached — SLO held for 2 consecutive samples")
                break

            iter_elapsed = time.time() - iter_start
            time.sleep(max(0, SAMPLE_INTERVAL_SECONDS - iter_elapsed))

        te_dt = datetime.fromtimestamp(t0.timestamp() + args.timeout_seconds, tz=timezone.utc)
        availability_end = tr or te_dt
        availability = compute_availability(t0, availability_end)
    finally:
        teardown_port_forwards(port_forwards)
        if manual:
            revert_manual_scenario(args.scenario)
        else:
            cleanup_scenario(args.scenario)
        restore_replicas("boutique", pre_fault_replicas)

    te = t0.timestamp() + args.timeout_seconds
    tr_censored = tr is None
    td_censored = td is None
    mttd = (td - t0).total_seconds() if td else (te - t0.timestamp())
    mttr = (tr - t0).total_seconds() if tr else (te - t0.timestamp())

    restarts_after = jaeger_restart_count()
    valid = restarts_before == restarts_after or restarts_before == "?" or restarts_after == "?"
    log(f"Jaeger restart count after trial: {restarts_after} (valid={valid})")

    result = {
        "scenario_file": args.scenario,
        "condition": args.condition,
        "t0": t0_str,
        "t0_source": t0_source,
        "td": td.isoformat().replace("+00:00", "Z") if td else None,
        "td_signal": td_signal,
        "td_censored": td_censored,
        "tr": tr.isoformat().replace("+00:00", "Z") if tr else None,
        "tr_censored": tr_censored,
        "mttd_seconds": mttd,
        "mttr_seconds": mttr,
        "availability": availability,
        "jaeger_restarts_before": restarts_before,
        "jaeger_restarts_after": restarts_after,
        "trial_valid": valid,
        # Condition B only: the actual RemediationAction created (rule_id,
        # action, target, matched_feature/z, remediation_action_name), or
        # None if Run A / no rule ever matched. last_match_attempt records
        # the most recent match_and_emit() outcome even when it never led to
        # an action (e.g. "no_matching_rule" or "dynamic_target_unresolved")
        # — previously this was only ever printed to stdout, not saved (see
        # match_and_emit()'s docstring / docs/experiment-log.md, 2026-09-17).
        "remediation": remediation,
        "last_match_attempt": last_match_attempt,
        "samples": samples,
    }

    out_dir = ROOT_DIR / "evaluation" / "runs" / "trials"
    out_dir.mkdir(parents=True, exist_ok=True)
    scenario_name = Path(args.scenario).stem
    out_path = out_dir / f"{scenario_name}-Run{args.condition}-{now_iso().replace(':', '').replace('-', '')}.json"
    out_path.write_text(json.dumps(result, indent=2))

    print("\n" + "=" * 60)
    print(f"TRIAL RESULT — {scenario_name}, Run {args.condition}")
    print("=" * 60)
    print(f"T0: {t0_str}")
    print(f"Td: {result['td']} (censored={td_censored}, signal={td_signal})")
    print(f"Tr: {result['tr']} (censored={tr_censored})")
    print(f"MTTD: {mttd:.1f}s")
    print(f"MTTR: {mttr:.1f}s")
    print(f"Availability: {availability:.4f}" if availability is not None else "Availability: n/a")
    print(f"Trial valid (no Jaeger restart during capture): {valid}")
    if remediation:
        print(f"Remediation: {remediation['rule_id']} -> {remediation['action']} "
              f"{remediation['target']['kind']}/{remediation['target']['name']} "
              f"(RemediationAction/{remediation['remediation_action_name']})")
    elif args.condition == "B" and last_match_attempt:
        print(f"Remediation: none — {last_match_attempt['outcome']} "
              f"(rule_id={last_match_attempt['rule_id']})")
    print(f"Saved: {out_path}")
    if not valid:
        print("\nWARNING: Jaeger restarted during this trial — DISCARD, do not log as evidence.")


if __name__ == "__main__":
    main()
