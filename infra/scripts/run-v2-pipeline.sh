#!/usr/bin/env bash
# End-to-end v2 pipeline: collect a fresh per-service baseline -> fuse -> train
# an Isolation Forest under decision-engine/model-config-v2.yaml -> run a real
# Run A/B campaign for R3v2/R5v2/R6v2/R7v2's scenarios under playbook-v2.yaml.
#
# Exists because nothing in this repo currently chains these steps together:
# run-training-pipeline.sh stops after training and is hardcoded to the v1
# config; run-campaign.sh needs a --model-dir that only exists after training.
# This script exists specifically to run the "still needed" list at the
# bottom of docs/experiment-log.md's 2026-09-15 entry in one shot.
#
# Reuses the Jaeger-restart-during-collection guard from
# run-training-pipeline.sh (2026-08-27/09-06 corruption finding) and
# run-campaign.sh's cooldown/pending-CR guards (2026-09-07/09-11 findings) —
# does not reimplement either, just calls both scripts in sequence.
#
# Safe to re-run: every step is idempotent against a fresh RUN_ID, and this
# script stops (does not train/campaign on bad data) if collection produces
# a run that fails infra/scripts/check-baseline-quality.sh, or if Jaeger
# restarted mid-collection.
#
# Usage:
#   bash infra/scripts/run-v2-pipeline.sh
#   bash infra/scripts/run-v2-pipeline.sh --iterations 10
#   bash infra/scripts/run-v2-pipeline.sh --scenarios scenario-02-memory-leak --conditions B --iterations 5
#   BASELINE_DURATION_SECONDS=180 bash infra/scripts/run-v2-pipeline.sh --skip-campaign   # smoke test
#
# Options:
#   --config <path>       decision-engine config (default: decision-engine/model-config-v2.yaml)
#   --playbook <path>     playbook (default: decision-engine/playbook-v2.yaml)
#   --scenarios <list>    forwarded to run-campaign.sh
#                         (default: scenario-02-memory-leak,scenario-03-disk-io-stress,scenario-09-volume-detachment,scenario-11-db-pool-exhaustion
#                          — the four scenarios R3v2/R5v2/R6v2/R7v2 exist for)
#   --conditions <list>   forwarded to run-campaign.sh (default: A,B)
#   --iterations <N>      forwarded to run-campaign.sh (default: 1)
#   --skip-baseline       reuse the most recent baseline run instead of collecting a new one
#   --skip-campaign       stop after training (baseline + train only, e.g. for a smoke test)
#   --force-untrainable   forwarded to train_and_compare.py — local pipeline testing only,
#                         never for dissertation evidence (see that script's own flag doc)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
source "${SCRIPT_DIR}/../versions.env"
cd "${ROOT_DIR}"

log() { echo "[v2-pipeline] $*"; }
die() { echo "[v2-pipeline] ERROR: $*" >&2; exit 1; }

CONFIG="decision-engine/model-config-v2.yaml"
PLAYBOOK="decision-engine/playbook-v2.yaml"
SCENARIOS="scenario-02-memory-leak,scenario-03-disk-io-stress,scenario-09-volume-detachment,scenario-11-db-pool-exhaustion"
CONDITIONS="A,B"
ITERATIONS=1
SKIP_BASELINE=0
SKIP_CAMPAIGN=0
FORCE_UNTRAINABLE=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --config) CONFIG="$2"; shift 2 ;;
    --playbook) PLAYBOOK="$2"; shift 2 ;;
    --scenarios) SCENARIOS="$2"; shift 2 ;;
    --conditions) CONDITIONS="$2"; shift 2 ;;
    --iterations) ITERATIONS="$2"; shift 2 ;;
    --skip-baseline) SKIP_BASELINE=1; shift ;;
    --skip-campaign) SKIP_CAMPAIGN=1; shift ;;
    --force-untrainable) FORCE_UNTRAINABLE=1; shift ;;
    *) die "unknown argument: $1" ;;
  esac
done

[[ -f "${CONFIG}" ]] || die "config not found: ${CONFIG}"
[[ -f "${PLAYBOOK}" ]] || die "playbook not found: ${PLAYBOOK}"

jaeger_restart_count() {
  kubectl get pod -n "${OBSERVABILITY_NAMESPACE}" -l app.kubernetes.io/name=jaeger \
    -o jsonpath='{.items[0].status.containerStatuses[0].restartCount}' 2>/dev/null || echo "?"
}

RUN_ID_FILE="${ROOT_DIR}/${BASELINE_OUTPUT_DIR}/.last_run_id"

if [[ "${SKIP_BASELINE}" -eq 1 ]]; then
  [[ -f "${RUN_ID_FILE}" ]] || die "--skip-baseline given but no ${RUN_ID_FILE} to read"
  RUN_ID="$(cat "${RUN_ID_FILE}")"
  RUN_DIR="${ROOT_DIR}/${BASELINE_OUTPUT_DIR}/${RUN_ID}"
  [[ -d "${RUN_DIR}" ]] || die "recorded run ${RUN_ID} has no directory at ${RUN_DIR}"
  log "Reusing existing baseline run: ${RUN_ID} (${RUN_DIR})"
else
  log "=== Step 1/3: baseline collection (BASELINE_DURATION_SECONDS=${BASELINE_DURATION_SECONDS}) ==="
  RESTARTS_BEFORE="$(jaeger_restart_count)"
  log "Jaeger restart count before collection: ${RESTARTS_BEFORE}"

  set +e
  bash "${SCRIPT_DIR}/collect-baseline.sh"
  COLLECT_EXIT=$?
  set -e

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
fi

log "=== Step 2/3: fuse + train (${CONFIG}) ==="
python3 "${ROOT_DIR}/fusion-engine/build_state_vector.py" --run-dir "${RUN_DIR}"

TRAIN_CMD=(python3 "${ROOT_DIR}/decision-engine/train_and_compare.py" --run-dir "${RUN_DIR}" --config "${ROOT_DIR}/${CONFIG}")
[[ "${FORCE_UNTRAINABLE}" -eq 1 ]] && TRAIN_CMD+=(--force-untrainable)
log "Running: ${TRAIN_CMD[*]}"
"${TRAIN_CMD[@]}"

MODEL_DIR="${RUN_DIR}/model-artifacts"
log "Model artifacts: ${MODEL_DIR}"

if [[ "${SKIP_CAMPAIGN}" -eq 1 ]]; then
  log "Done (--skip-campaign given). Model artifacts ready at ${MODEL_DIR}."
  exit 0
fi

log "=== Step 3/3: campaign (${SCENARIOS} / ${CONDITIONS} / x${ITERATIONS}) ==="
CAMPAIGN_CMD=(
  bash "${SCRIPT_DIR}/run-campaign.sh"
  --scenarios "${SCENARIOS}"
  --conditions "${CONDITIONS}"
  --iterations "${ITERATIONS}"
  --model-dir "${MODEL_DIR}"
  --config "${ROOT_DIR}/${CONFIG}"
  --playbook "${ROOT_DIR}/${PLAYBOOK}"
)
log "Running: ${CAMPAIGN_CMD[*]}"
"${CAMPAIGN_CMD[@]}"

log "Pipeline complete."
log "Model: ${MODEL_DIR}"
log "Trials: evaluation/runs/trials/ (review trial_valid/censored flags before treating as campaign-final data)"
log "Next: record this run's R3v2/R5v2/R6v2/R7v2 match outcomes in docs/experiment-log.md, per the 2026-09-15 entry's 'still needed' list."
