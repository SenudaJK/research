"""
Phase 3 actuator — watches RemediationAction custom resources and executes
the matched recovery action against the Kubernetes API.

Vertical slice scope: "scale" (scenario-01, CPU starvation ->
checkoutservice) and "restart" (scenario-07, pod kill -> frontend rollout
restart) are implemented against a Deployment.

Added for R7/R9/R10/R11/R12 (scenarios 03/06/08/10/12 — see
decision-engine/playbook.yaml):
  - "evict", targetRef.kind == "Deployment": deletes the target Deployment's
    current pods so the scheduler recreates them fresh (R7, scenario-03
    disk I/O — matches Table I's "evict affected pods").
  - "evict", targetRef.kind == "Node": cordons the node then evicts every
    pod running on it (R10, scenario-08 node starvation — matches Table I's
    "drain affected node; evict and reschedule workloads"). targetRef.name
    is resolved dynamically at match time by decision-engine/orchestrator.py
    / evaluation/analysis/run_trial.py's resolve_dynamic_target(), since the
    affected node isn't known until the trial actually picks one.
  - "reconcile", targetRef.kind == "Deployment": re-applies
    infra/boutique/'s manifests (`kubectl apply -k infra/boutique/`) to
    restore the drifted Deployment to its known-good spec (R12, scenario-12
    config drift — matches Table I's "reconcile with active Git repository",
    the same revert command scenario-12-config-drift.yaml's own header
    documents and evaluation/analysis/run_trial.py's manual-scenario path
    uses). This is the one action that shells out rather than using the
    Kubernetes Python client directly: infra/boutique/'s Deployment specs
    come from a remote kustomize base (see infra/boutique/kustomization.yaml),
    so there is no local "known-good" manifest to read and patch from — only
    `kubectl apply -k` can resolve that base the same way the original
    deployment was created.

Safety guards (both required by docs/methodology-checklist.md Phase 3):
  - dry-run: OPERATOR_DRY_RUN=true env var (global) or spec.dryRun (per-CR)
  - cooldown + max actions/hour: tracked via annotations on the TARGET
    Deployment itself (not on the CR), so limits are enforced per-target
    even across operator restarts, without needing an external database.

Every decision is logged as one structured JSON line (trigger signals,
matched rule, timestamp) to stdout — this is the RQ2 explainability
evidence trail, in addition to the same fields being written to the CR's
own .status so `kubectl get remediationaction -o yaml` is self-contained.

Run (out-of-cluster, using local kubeconfig — no in-cluster Deployment/RBAC
built yet for this vertical slice):
    kopf run operator/handlers.py --namespace boutique
"""
import json
import os
import subprocess
from datetime import datetime, timezone

import kopf
import kubernetes
from kubernetes.client.rest import ApiException

COOLDOWN_SECONDS = int(os.environ.get("COOLDOWN_SECONDS", 300))
MAX_ACTIONS_PER_HOUR = int(os.environ.get("MAX_ACTIONS_PER_HOUR", 6))
GLOBAL_DRY_RUN = os.environ.get("OPERATOR_DRY_RUN", "false").lower() == "true"

ANNOTATION_LAST_ACTION = "selfhealing.research.io/last-action-timestamp"
ANNOTATION_ACTION_HISTORY = "selfhealing.research.io/action-history"  # JSON list of ISO timestamps, pruned to 1h


def _apps_v1():
    try:
        kubernetes.config.load_kube_config()
    except Exception:
        kubernetes.config.load_incluster_config()
    return kubernetes.client.AppsV1Api()


def _core_v1():
    try:
        kubernetes.config.load_kube_config()
    except Exception:
        kubernetes.config.load_incluster_config()
    return kubernetes.client.CoreV1Api()


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _get_target_deployment(apps_v1, target_ref):
    return apps_v1.read_namespaced_deployment(target_ref["name"], target_ref["namespace"])


def _get_target_annotations(apps_v1, core_v1, target_ref):
    """Cooldown/rate-limit state is tracked as annotations directly on the
    target object — a Deployment normally, but a Node for target_ref.kind ==
    "Node" (R10, scenario-08) since there's no Deployment to annotate."""
    if target_ref.get("kind") == "Node":
        node = core_v1.read_node(target_ref["name"])
        return node.metadata.annotations or {}
    dep = _get_target_deployment(apps_v1, target_ref)
    return dep.metadata.annotations or {}


def _patch_target_annotations(apps_v1, core_v1, target_ref, annotations):
    patch_body = {"metadata": {"annotations": annotations}}
    if target_ref.get("kind") == "Node":
        core_v1.patch_node(target_ref["name"], patch_body)
    else:
        apps_v1.patch_namespaced_deployment(target_ref["name"], target_ref["namespace"], patch_body)


def _check_cooldown_and_rate_limit(apps_v1, core_v1, target_ref):
    """Returns (allowed: bool, reason: str). Enforced per-target via
    annotations on the target object, so limits survive operator restarts."""
    annotations = _get_target_annotations(apps_v1, core_v1, target_ref)

    last_action = annotations.get(ANNOTATION_LAST_ACTION)
    if last_action:
        elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(last_action)).total_seconds()
        if elapsed < COOLDOWN_SECONDS:
            return False, f"cooldown active ({elapsed:.0f}s < {COOLDOWN_SECONDS}s since last action)"

    try:
        history = json.loads(annotations.get(ANNOTATION_ACTION_HISTORY, "[]"))
    except json.JSONDecodeError:
        history = []
    cutoff = datetime.now(timezone.utc).timestamp() - 3600
    recent = [t for t in history if datetime.fromisoformat(t).timestamp() > cutoff]
    if len(recent) >= MAX_ACTIONS_PER_HOUR:
        return False, f"max actions/hour reached ({len(recent)}/{MAX_ACTIONS_PER_HOUR})"

    return True, "ok"


def _record_action(apps_v1, core_v1, target_ref):
    annotations = _get_target_annotations(apps_v1, core_v1, target_ref)
    now = _now_iso()
    try:
        history = json.loads(annotations.get(ANNOTATION_ACTION_HISTORY, "[]"))
    except json.JSONDecodeError:
        history = []
    cutoff = datetime.now(timezone.utc).timestamp() - 3600
    history = [t for t in history if datetime.fromisoformat(t).timestamp() > cutoff]
    history.append(now)
    _patch_target_annotations(apps_v1, core_v1, target_ref, {
        ANNOTATION_LAST_ACTION: now,
        ANNOTATION_ACTION_HISTORY: json.dumps(history),
    })


def _execute_scale(apps_v1, target_ref, replicas):
    dep = _get_target_deployment(apps_v1, target_ref)
    previous_replicas = dep.spec.replicas
    apps_v1.patch_namespaced_deployment_scale(
        target_ref["name"], target_ref["namespace"],
        {"spec": {"replicas": replicas}},
    )
    return previous_replicas


def _execute_restart(apps_v1, target_ref):
    """Rollout-restart (scenario-07 -> R2-pod-kill-restart): patches the pod
    template with a restartedAt annotation, the same mechanism `kubectl
    rollout restart` uses, forcing a rolling recreation of every pod so
    readiness probes are re-verified on fresh pods."""
    patch_body = {
        "spec": {
            "template": {
                "metadata": {
                    "annotations": {
                        "selfhealing.research.io/restarted-at": _now_iso(),
                    }
                }
            }
        }
    }
    apps_v1.patch_namespaced_deployment(target_ref["name"], target_ref["namespace"], patch_body)


def _execute_evict_deployment(core_v1, target_ref):
    """R7-disk-io-stress-evict (scenario-03): deletes the target
    Deployment's current pods so the scheduler recreates them fresh — a
    simple delete rather than the Eviction API/PodDisruptionBudget-aware
    flow, consistent with this vertical slice's other actions, which all
    act directly rather than shelling out. Returns the number of pods
    evicted."""
    pods = core_v1.list_namespaced_pod(
        target_ref["namespace"], label_selector=f"app={target_ref['name']}"
    )
    for pod in pods.items:
        core_v1.delete_namespaced_pod(pod.metadata.name, target_ref["namespace"])
    return len(pods.items)


def _execute_evict_node(core_v1, target_ref):
    """R10-node-starvation-evict (scenario-08): cordons the node
    (spec.unschedulable=true) then deletes every pod running on it so they
    reschedule onto a healthy node — matches Table I's "drain affected node;
    evict and reschedule workloads to healthy nodes". target_ref["name"] is
    resolved dynamically before this CR is created (see
    decision-engine/orchestrator.py / evaluation/analysis/run_trial.py's
    resolve_dynamic_target()), so it is always a real node name by the time
    this runs, never the "AUTO" placeholder. Returns the number of pods
    evicted."""
    node_name = target_ref["name"]
    core_v1.patch_node(node_name, {"spec": {"unschedulable": True}})
    pods = core_v1.list_pod_for_all_namespaces(field_selector=f"spec.nodeName={node_name}")
    evicted = 0
    for pod in pods.items:
        if pod.metadata.owner_references and any(
            ref.kind == "DaemonSet" for ref in pod.metadata.owner_references
        ):
            continue  # DaemonSet pods run on every node by design; evicting them is a no-op at best
        core_v1.delete_namespaced_pod(pod.metadata.name, pod.metadata.namespace)
        evicted += 1
    return evicted


def _execute_reconcile():
    """R12-config-drift-reconcile (scenario-12): re-applies
    infra/boutique/'s manifests to restore any drifted Deployment (e.g.
    checkoutservice's PAYMENT_SERVICE_ADDR env var) to its known-good spec —
    matches Table I's "reconcile with active Git repository". Shells out
    because infra/boutique/'s Deployment specs come from a remote kustomize
    base (infra/boutique/kustomization.yaml) with no local manifest to read
    and patch from directly."""
    result = subprocess.run(
        ["kubectl", "apply", "-k", "infra/boutique/"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"kubectl apply -k infra/boutique/ failed: {result.stderr}")
    return result.stdout


@kopf.on.create("selfhealing.research.io", "v1alpha1", "remediationactions")
def on_remediation_action(spec, patch, logger, **_kwargs):
    target_ref = spec["targetRef"]
    action = spec["action"]
    dry_run = GLOBAL_DRY_RUN or spec.get("dryRun", False)

    log_event = {
        "timestamp": _now_iso(),
        "rule_id": spec["ruleId"],
        "action": action,
        "target": target_ref,
        "anomaly_score": spec.get("anomalyScore"),
        "tau": spec.get("tau"),
        "explanation": spec["explanation"],
        "dry_run": dry_run,
    }

    if action not in ("scale", "restart", "evict", "reconcile"):
        log_event["outcome"] = "unsupported_action"
        logger.warning(json.dumps(log_event))
        patch.status["phase"] = "Failed"
        patch.status["message"] = f"action '{action}' not implemented in this vertical slice"
        return

    apps_v1 = _apps_v1()
    core_v1 = _core_v1()

    allowed, reason = _check_cooldown_and_rate_limit(apps_v1, core_v1, target_ref)
    if not allowed:
        log_event["outcome"] = "blocked"
        log_event["reason"] = reason
        logger.warning(json.dumps(log_event))
        patch.status["phase"] = "CooldownBlocked"
        patch.status["message"] = reason
        return

    if dry_run:
        log_event["outcome"] = "dry_run"
        logger.info(json.dumps(log_event))
        patch.status["phase"] = "DryRun"
        if action == "scale":
            patch.status["message"] = f"Would scale {target_ref['name']} to {spec.get('scaleReplicas')} replicas"
        elif action == "restart":
            patch.status["message"] = f"Would restart {target_ref['name']}"
        elif action == "evict":
            patch.status["message"] = f"Would evict pods from {target_ref['kind']}/{target_ref['name']}"
        else:
            patch.status["message"] = "Would reconcile infra/boutique/ against the live cluster"
        patch.status["executedAt"] = log_event["timestamp"]
        return

    try:
        if action == "scale":
            previous_replicas = _execute_scale(apps_v1, target_ref, spec["scaleReplicas"])
            _record_action(apps_v1, core_v1, target_ref)
            log_event["outcome"] = "executed"
            log_event["previous_replicas"] = previous_replicas
            logger.info(json.dumps(log_event))
            patch.status["phase"] = "Succeeded"
            patch.status["message"] = f"Scaled {target_ref['name']} {previous_replicas} -> {spec['scaleReplicas']}"
            patch.status["executedAt"] = log_event["timestamp"]
            patch.status["previousReplicas"] = previous_replicas
        elif action == "restart":
            _execute_restart(apps_v1, target_ref)
            _record_action(apps_v1, core_v1, target_ref)
            log_event["outcome"] = "executed"
            logger.info(json.dumps(log_event))
            patch.status["phase"] = "Succeeded"
            patch.status["message"] = f"Restarted {target_ref['name']}"
            patch.status["executedAt"] = log_event["timestamp"]
        elif action == "evict":
            if target_ref.get("kind") == "Node":
                evicted = _execute_evict_node(core_v1, target_ref)
            else:
                evicted = _execute_evict_deployment(core_v1, target_ref)
            _record_action(apps_v1, core_v1, target_ref)
            log_event["outcome"] = "executed"
            log_event["pods_evicted"] = evicted
            logger.info(json.dumps(log_event))
            patch.status["phase"] = "Succeeded"
            patch.status["message"] = f"Evicted {evicted} pod(s) from {target_ref['kind']}/{target_ref['name']}"
            patch.status["executedAt"] = log_event["timestamp"]
        else:
            _execute_reconcile()
            _record_action(apps_v1, core_v1, target_ref)
            log_event["outcome"] = "executed"
            logger.info(json.dumps(log_event))
            patch.status["phase"] = "Succeeded"
            patch.status["message"] = "Reconciled infra/boutique/ against the live cluster"
            patch.status["executedAt"] = log_event["timestamp"]
    except (ApiException, RuntimeError) as e:
        log_event["outcome"] = "error"
        log_event["error"] = str(e)
        logger.error(json.dumps(log_event))
        patch.status["phase"] = "Failed"
        patch.status["message"] = str(e)
