#!/usr/bin/env bash
# Orchestrates: collect-baseline.sh -> fusion-engine -> decision-engine
# training, auto-resolving the run id in between so it never has to be
# copy-pasted between commands.
#
# Guards against the failure mode found on 2026-08-27/09-06: Jaeger's
# storage backend restarting mid-collection and silently corrupting samples.
# If Jaeger's restart count changes during collection, this refuses to train
# and tells you to re-collect instead of proceeding on suspect data.
#
# Usage:
#   bash infra/scripts/run-training-pipeline.sh                # full 12000s run
#   BASELINE_DURATION_SECONDS=600 bash infra/scripts/run-training-pipeline.sh   # smoke
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
source "${SCRIPT_DIR}/../versions.env"

log() { echo "[pipeline] $*"; }
die() { echo "[pipeline] ERROR: $*" >&2; exit 1; }

jaeger_restart_count() {
  kubectl get pod -n "${OBSERVABILITY_NAMESPACE}" -l app.kubernetes.io/name=jaeger \
    -o jsonpath='{.items[0].status.containerStatuses[0].restartCount}' 2>/dev/null || echo "?"
}

RESTARTS_BEFORE="$(jaeger_restart_count)"
log "Jaeger restart count before collection: ${RESTARTS_BEFORE}"

log "Starting baseline collection (BASELINE_DURATION_SECONDS=${BASELINE_DURATION_SECONDS})..."
set +e
bash "${SCRIPT_DIR}/collect-baseline.sh"
COLLECT_EXIT=$?
set -e

RUN_ID_FILE="${ROOT_DIR}/${BASELINE_OUTPUT_DIR}/.last_run_id"
[[ -f "${RUN_ID_FILE}" ]] || die "no run id recorded — collect-baseline.sh did not reach its own end"
RUN_ID="$(cat "${RUN_ID_FILE}")"
RUN_DIR="${ROOT_DIR}/${BASELINE_OUTPUT_DIR}/${RUN_ID}"
log "Run: ${RUN_ID} (${RUN_DIR})"

RESTARTS_AFTER="$(jaeger_restart_count)"
log "Jaeger restart count after collection: ${RESTARTS_AFTER}"
if [[ "${RESTARTS_BEFORE}" != "?" && "${RESTARTS_AFTER}" != "?" && "${RESTARTS_BEFORE}" != "${RESTARTS_AFTER}" ]]; then
  die "Jaeger restarted during collection (${RESTARTS_BEFORE} -> ${RESTARTS_AFTER}) — this run's traces/metrics may be corrupted around the restart. Do not train on ${RUN_DIR}. Investigate (kubectl describe pod -n ${OBSERVABILITY_NAMESPACE} -l app.kubernetes.io/name=jaeger) and re-collect."
fi

if [[ ${COLLECT_EXIT} -ne 0 ]]; then
  log "collect-baseline.sh's quality gate failed (exit ${COLLECT_EXIT}) — fusing anyway for inspection, but NOT training."
  python3 "${ROOT_DIR}/fusion-engine/build_state_vector.py" --run-dir "${RUN_DIR}"
  die "Quality gate failed — see ${RUN_DIR}/meta/quality.json. Fix and re-collect before training."
fi

log "Fusing telemetry into State Vector..."
python3 "${ROOT_DIR}/fusion-engine/build_state_vector.py" --run-dir "${RUN_DIR}"

log "Training Isolation Forest and freezing tau..."
python3 "${ROOT_DIR}/decision-engine/train_and_compare.py" --run-dir "${RUN_DIR}"

log "Done. Model artifacts: ${RUN_DIR}/model-artifacts/"
