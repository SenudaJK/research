#!/usr/bin/env bash
# Orchestrates a full fault dry run: clear any stale chaos experiment, apply
# fresh, wait for it to actually be active, capture telemetry, fuse, score
# against a trained model, and clean up — auto-resolving the run id so
# nothing has to be copy-pasted between steps.
#
# Guards against the two failure modes found this session:
#   1. A stale/already-finished chaos object making `kubectl apply` a no-op
#      (2026-08-27) -> this always deletes before applying.
#   2. Jaeger restarting mid-capture and corrupting samples (2026-08-27,
#      2026-09-06) -> this checks its restart count before/after and
#      refuses to score a run where it changed.
#
# Usage:
#   bash infra/scripts/run-fault-dry-run.sh \
#     evaluation/scenarios/scenario-01-cpu-starvation.yaml \
#     evaluation/runs/baseline/<trained-run-id>/model-artifacts
#
# Optional overrides:
#   DRY_RUN_DURATION_SECONDS=120 DRY_RUN_SAMPLE_INTERVAL_SECONDS=30 bash ...
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
source "${SCRIPT_DIR}/../versions.env"

log() { echo "[dry-run] $*"; }
die() { echo "[dry-run] ERROR: $*" >&2; exit 1; }

SCENARIO_FILE="${1:-}"
MODEL_DIR="${2:-}"
[[ -n "${SCENARIO_FILE}" && -f "${SCENARIO_FILE}" ]] || die "usage: $0 <scenario-yaml> <model-artifacts-dir>"
[[ -n "${MODEL_DIR}" && -f "${MODEL_DIR}/threshold.json" ]] || die "usage: $0 <scenario-yaml> <model-artifacts-dir> (must contain threshold.json)"

DRY_RUN_DURATION_SECONDS="${DRY_RUN_DURATION_SECONDS:-120}"
DRY_RUN_SAMPLE_INTERVAL_SECONDS="${DRY_RUN_SAMPLE_INTERVAL_SECONDS:-30}"

if ! grep -q '^kind:' "${SCENARIO_FILE}"; then
  die "${SCENARIO_FILE} has no Kubernetes resource (e.g. scenario-12 is manual-only — see the kubectl patch commands documented inside that file)."
fi

jaeger_restart_count() {
  kubectl get pod -n "${OBSERVABILITY_NAMESPACE}" -l app.kubernetes.io/name=jaeger \
    -o jsonpath='{.items[0].status.containerStatuses[0].restartCount}' 2>/dev/null || echo "?"
}

log "Clearing any stale experiment from this manifest..."
kubectl delete -f "${SCENARIO_FILE}" --ignore-not-found >/dev/null

log "Applying fresh..."
APPLY_REF="$(kubectl apply -f "${SCENARIO_FILE}" -o name)"
log "Created: ${APPLY_REF}"
RESOURCE_TYPE="${APPLY_REF%%/*}"
RESOURCE_NAME="${APPLY_REF##*/}"
NAMESPACE="$(kubectl get "${RESOURCE_TYPE}" "${RESOURCE_NAME}" -A -o jsonpath='{.items[0].metadata.namespace}' 2>/dev/null || echo "${CHAOS_NAMESPACE}")"

cleanup() {
  log "Cleaning up ${APPLY_REF}..."
  kubectl delete -f "${SCENARIO_FILE}" --ignore-not-found >/dev/null || true
}
trap cleanup EXIT

log "Waiting for the experiment to become active (up to 20s; one-shot faults like pod-kill may never report Running)..."
for _ in $(seq 1 10); do
  PHASE="$(kubectl get "${RESOURCE_TYPE}" "${RESOURCE_NAME}" -n "${NAMESPACE}" -o jsonpath='{.status.experiment.desiredPhase}' 2>/dev/null || true)"
  [[ "${PHASE}" == "Run" ]] && break
  sleep 2
done
if [[ "${PHASE}" == "Run" ]]; then
  log "Experiment is active (desiredPhase=Run)."
else
  log "WARNING: desiredPhase never reported 'Run' (last seen: '${PHASE:-none}') — proceeding anyway; this is expected for one-shot faults (e.g. pod-kill)."
fi

RESTARTS_BEFORE="$(jaeger_restart_count)"
log "Jaeger restart count before capture: ${RESTARTS_BEFORE}"

log "Capturing telemetry (duration=${DRY_RUN_DURATION_SECONDS}s, interval=${DRY_RUN_SAMPLE_INTERVAL_SECONDS}s)..."
BASELINE_DURATION_SECONDS="${DRY_RUN_DURATION_SECONDS}" \
  BASELINE_SAMPLE_INTERVAL_SECONDS="${DRY_RUN_SAMPLE_INTERVAL_SECONDS}" \
  bash "${SCRIPT_DIR}/collect-baseline.sh" || true   # quality gate "fail" is expected/ignored for a short fault-window capture

RUN_ID_FILE="${ROOT_DIR}/${BASELINE_OUTPUT_DIR}/.last_run_id"
[[ -f "${RUN_ID_FILE}" ]] || die "no run id recorded — collect-baseline.sh did not reach its own end"
RUN_ID="$(cat "${RUN_ID_FILE}")"
RUN_DIR="${ROOT_DIR}/${BASELINE_OUTPUT_DIR}/${RUN_ID}"
log "Fault-window run: ${RUN_ID} (${RUN_DIR})"

RESTARTS_AFTER="$(jaeger_restart_count)"
log "Jaeger restart count after capture: ${RESTARTS_AFTER}"
if [[ "${RESTARTS_BEFORE}" != "?" && "${RESTARTS_AFTER}" != "?" && "${RESTARTS_BEFORE}" != "${RESTARTS_AFTER}" ]]; then
  die "Jaeger restarted during capture (${RESTARTS_BEFORE} -> ${RESTARTS_AFTER}) — this trial is INVALID (see ${RUN_DIR}). Discard it and re-run this script."
fi

log "Fusing telemetry..."
python3 "${ROOT_DIR}/fusion-engine/build_state_vector.py" --run-dir "${RUN_DIR}"

log "Scoring against ${MODEL_DIR}..."
python3 "${ROOT_DIR}/decision-engine/score.py" \
  --model-dir "${MODEL_DIR}" \
  --state-vector-csv "${RUN_DIR}/state_vector.csv"
