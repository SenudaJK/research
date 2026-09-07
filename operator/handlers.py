"""
Phase 3 actuator — watches RemediationAction custom resources and executes
the matched recovery action against the Kubernetes API.

Vertical slice scope (first pass): only the "scale" action against a
Deployment is implemented, proven end-to-end for scenario-01 (CPU
starvation -> checkoutservice). Restart/evict are accepted by the CRD
schema for forward compatibility but rejected here as unsupported — extend
this alongside decision-engine/playbook.yaml as more of the 12 scenarios'
recovery actions are built out (see docs/architecture.md Phase 3).

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


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _get_target_deployment(apps_v1, target_ref):
    return apps_v1.read_namespaced_deployment(target_ref["name"], target_ref["namespace"])


def _check_cooldown_and_rate_limit(apps_v1, target_ref):
    """Returns (allowed: bool, reason: str). Enforced per-target via
    annotations on the Deployment, so limits survive operator restarts."""
    dep = _get_target_deployment(apps_v1, target_ref)
    annotations = dep.metadata.annotations or {}

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


def _record_action(apps_v1, target_ref):
    dep = _get_target_deployment(apps_v1, target_ref)
    annotations = dep.metadata.annotations or {}
    now = _now_iso()
    try:
        history = json.loads(annotations.get(ANNOTATION_ACTION_HISTORY, "[]"))
    except json.JSONDecodeError:
        history = []
    cutoff = datetime.now(timezone.utc).timestamp() - 3600
    history = [t for t in history if datetime.fromisoformat(t).timestamp() > cutoff]
    history.append(now)
    patch_body = {
        "metadata": {
            "annotations": {
                ANNOTATION_LAST_ACTION: now,
                ANNOTATION_ACTION_HISTORY: json.dumps(history),
            }
        }
    }
    apps_v1.patch_namespaced_deployment(target_ref["name"], target_ref["namespace"], patch_body)


def _execute_scale(apps_v1, target_ref, replicas):
    dep = _get_target_deployment(apps_v1, target_ref)
    previous_replicas = dep.spec.replicas
    apps_v1.patch_namespaced_deployment_scale(
        target_ref["name"], target_ref["namespace"],
        {"spec": {"replicas": replicas}},
    )
    return previous_replicas


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

    if action != "scale":
        log_event["outcome"] = "unsupported_action"
        logger.warning(json.dumps(log_event))
        patch.status["phase"] = "Failed"
        patch.status["message"] = f"action '{action}' not implemented in this vertical slice"
        return

    apps_v1 = _apps_v1()

    allowed, reason = _check_cooldown_and_rate_limit(apps_v1, target_ref)
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
        patch.status["message"] = f"Would scale {target_ref['name']} to {spec.get('scaleReplicas')} replicas"
        patch.status["executedAt"] = log_event["timestamp"]
        return

    try:
        previous_replicas = _execute_scale(apps_v1, target_ref, spec["scaleReplicas"])
        _record_action(apps_v1, target_ref)
        log_event["outcome"] = "executed"
        log_event["previous_replicas"] = previous_replicas
        logger.info(json.dumps(log_event))
        patch.status["phase"] = "Succeeded"
        patch.status["message"] = f"Scaled {target_ref['name']} {previous_replicas} -> {spec['scaleReplicas']}"
        patch.status["executedAt"] = log_event["timestamp"]
        patch.status["previousReplicas"] = previous_replicas
    except ApiException as e:
        log_event["outcome"] = "error"
        log_event["error"] = str(e)
        logger.error(json.dumps(log_event))
        patch.status["phase"] = "Failed"
        patch.status["message"] = str(e)
