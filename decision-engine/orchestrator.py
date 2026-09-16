"""
Hybrid decision orchestrator (Phase 3 vertical slice) — the "Analyze -> Plan"
half of the MAPE-K loop. Scores a State Vector against the frozen Isolation
Forest model, matches an anomaly to a rule in decision-engine/playbook.yaml,
and creates a RemediationAction custom resource for operator/handlers.py to
execute.

Distinct from score.py: score.py only reports whether a row would be
detected (for a manual dry run). This actually closes the loop by emitting
a CR — run it against real telemetry only when you intend the operator
(if running) to react.

Rule matching (first pass, single-feature): the feature with the largest
|z-score| vs the training baseline's mean/std is treated as "what triggered
this," and matched against each rule's trigger_feature. This is intentionally
simple for the scenario-01 vertical slice — multi-feature/compound rule
matching is future work once more of the 12 scenarios' rules exist.

Usage: see decision-engine/README.md.
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import joblib
import kubernetes
import numpy as np
import pandas as pd
import yaml


def load_model(model_dir):
    scaler = joblib.load(model_dir / "scaler.pkl")
    iforest = joblib.load(model_dir / "isolation_forest.pkl")
    threshold = json.loads((model_dir / "threshold.json").read_text())
    return scaler, iforest, threshold


def load_playbook(playbook_path):
    return yaml.safe_load(playbook_path.read_text())["rules"]


def resolve_dynamic_target(target):
    """Mirrors evaluation/analysis/run_trial.py's resolve_dynamic_target —
    keep in sync. Rules whose target is chosen at trial time (target.kind ==
    "Node", target.name == "AUTO" — e.g. R10-node-starvation-evict) get
    resolved here to the node currently hosting a not-Running/not-Ready
    boutique pod. Returns None if no unhealthy pod can be found yet."""
    if target.get("kind") != "Node" or target.get("name") != "AUTO":
        return target
    import subprocess
    out = subprocess.run(
        "kubectl get pods -n boutique -o json", shell=True, capture_output=True, text=True,
    ).stdout
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


def match_rule(row, features, scaler, rules, z_threshold=3.0):
    """Returns (rule_or_None, matched_feature, matched_z).

    A rule matches if ITS OWN trigger_feature's |z-score| exceeds
    z_threshold — not only if that feature happens to be the single
    most-deviated feature overall. Changed 2026-09-07: the old "top
    feature only" version missed a real CPU-starvation fault because
    log_error_rate briefly out-ranked cpu_util before the CPU stress had
    fully ramped up, and log_error_rate has no playbook rule. Among
    multiple qualifying rules, the one with the largest |z| wins.
    """
    z_scores = (row[features].values - scaler.mean_) / scaler.scale_
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


def build_remediation_action(rule, anomaly_score, tau, top_z, execute):
    target = resolve_dynamic_target(rule["target"])
    if target is None:
        return None
    name = f"{rule['rule_id'].lower()}-{int(datetime.now(timezone.utc).timestamp())}"
    explanation = rule["explanation_template"].format(
        name=target["name"], replicas=rule.get("scale_replicas"),
        zscore=top_z, score=anomaly_score, tau=tau, rule_id=rule["rule_id"],
    )
    return {
        "apiVersion": "selfhealing.research.io/v1alpha1",
        "kind": "RemediationAction",
        "metadata": {"name": name, "namespace": target["namespace"]},
        "spec": {
            "targetRef": target,
            "action": rule["action"],
            "scaleReplicas": rule.get("scale_replicas"),
            "ruleId": rule["rule_id"],
            "anomalyScore": anomaly_score,
            "tau": tau,
            "explanation": explanation,
            "dryRun": not execute,
        },
    }


def emit(body):
    try:
        kubernetes.config.load_kube_config()
    except Exception:
        kubernetes.config.load_incluster_config()
    api = kubernetes.client.CustomObjectsApi()
    return api.create_namespaced_custom_object(
        group="selfhealing.research.io", version="v1alpha1",
        namespace=body["metadata"]["namespace"], plural="remediationactions", body=body,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", required=True, type=Path)
    parser.add_argument("--state-vector-csv", required=True, type=Path)
    parser.add_argument("--playbook", default=Path(__file__).parent / "playbook.yaml", type=Path)
    parser.add_argument("--config", default=Path(__file__).parent / "model-config.yaml", type=Path)
    parser.add_argument(
        "--execute", action="store_true",
        help="Without this, the emitted RemediationAction is dryRun:true and "
             "the operator (if running) will only log/report, never act.",
    )
    args = parser.parse_args()

    with open(args.config) as f:
        config = yaml.safe_load(f)
    z_threshold = config.get("rule_match_z_threshold", 3.0)

    scaler, iforest, threshold = load_model(args.model_dir)
    rules = load_playbook(args.playbook)
    features = threshold["features"]
    tau = threshold["tau"]

    df = pd.read_csv(args.state_vector_csv)
    df = df.dropna(subset=features).reset_index(drop=True)
    if df.empty:
        sys.exit("ERROR: no complete rows in state vector CSV")

    X_scaled = scaler.transform(df[features])
    anomaly_scores = -iforest.score_samples(X_scaled)

    any_emitted = False
    for i, row in df.iterrows():
        score = float(anomaly_scores[i])
        if score <= tau:
            continue
        rule, top_feature, top_z = match_rule(row, features, scaler, rules, z_threshold=z_threshold)
        if rule is None:
            print(f"Row {i}: anomaly_score={score:.4f} > tau={tau:.4f}, "
                  f"top feature={top_feature} (z={top_z:.2f}) — NO MATCHING RULE in playbook.")
            continue
        print(f"Row {i}: anomaly_score={score:.4f} > tau={tau:.4f}, matched {rule['rule_id']} "
              f"(top feature={top_feature}, z={top_z:.2f})")
        body = build_remediation_action(rule, score, tau, top_z, args.execute)
        if body is None:
            print(f"  -> rule {rule['rule_id']}'s dynamic target (Node/AUTO) could not be "
                  "resolved yet (no unhealthy pod found) — skipping this row.")
            continue
        created = emit(body)
        any_emitted = True
        print(f"  -> Created RemediationAction/{created['metadata']['name']} "
              f"in namespace {created['metadata']['namespace']} (dryRun={not args.execute})")

    if not any_emitted:
        print(f"No rows scored above tau ({tau:.4f}) — nothing to act on.")


if __name__ == "__main__":
    main()
