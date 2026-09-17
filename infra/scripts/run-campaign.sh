#!/usr/bin/env bash
# Runs a batch of evaluation/analysis/run_trial.py trials across multiple
# scenarios/conditions/iterations, one at a time, instead of invoking
# run_trial.py by hand for every trial.
#
# Guards against the two real failure modes documented in
# docs/experiment-log.md for exactly this kind of batch:
#   1. Cooldown collision (2026-09-07, 2026-09-11): running two trials on the
#      same target within COOLDOWN_SECONDS (operator/handlers.py, default
#      300s) of each other makes the second trial's RemediationAction
#      CooldownBlocked. This script always sleeps CAMPAIGN_COOLDOWN_BUFFER
#      seconds *on top of* COOLDOWN_SECONDS between trials.
#   2. Backlogged/pending RemediationAction CRs (2026-09-11): if the operator
#      wasn't running or fell behind, a stale CR can get reconciled mid-way
#      through the *next* trial. Before every trial this checks
#      `kubectl get remediationaction -n boutique` for anything not yet
#      terminal and refuses to proceed until it clears (or times out).
#      Terminal phases are Succeeded/Failed/CooldownBlocked/DryRun — all four
#      are set by operator/handlers.py's one-shot on.create handler, which
#      never revisits a CR after any of them; only treating Succeeded/Failed
#      as terminal (the bug found 2026-09-17) makes this wait forever on any
#      old rate-limited CR that's actually done, not in flight.
#   3. Operator not running at all (2026-09-17): unlike #2 above (a stale CR
#      from a *previous* run), if the operator process isn't running right
#      now, every RemediationAction this campaign creates for a Run B trial
#      will sit with no phase forever — nothing will ever process it. This
#      script now refuses to start unless a local `kopf run
#      operator/handlers.py` process is found (pass --skip-operator-check if
#      the operator runs somewhere this can't see with `ps`, e.g. in-cluster).
#
# Does NOT retry failed/censored trials automatically and does NOT invent
# scenario/condition combinations beyond what you pass in — every trial's
# raw result lands in evaluation/runs/trials/ exactly as run_trial.py always
# writes it; read docs/experiment-log.md and decide what's valid yourself.
#
# Usage:
#   bash infra/scripts/run-campaign.sh \
#     --scenarios scenario-04-network-latency,scenario-07-random-pod-kill \
#     --conditions A,B \
#     --iterations 3 \
#     --model-dir evaluation/runs/baseline/<run-id>/model-artifacts
#
#   # v2 model/playbook:
#   bash infra/scripts/run-campaign.sh \
#     --scenarios scenario-02-memory-leak,scenario-09-volume-detachment,scenario-11-db-pool-exhaustion \
#     --conditions A,B \
#     --iterations 5 \
#     --model-dir evaluation/runs/baseline/<v2-run-id>/model-artifacts \
#     --config decision-engine/model-config-v2.yaml \
#     --playbook decision-engine/playbook-v2.yaml
#
# Options:
#   --scenarios <comma-separated stems, no .yaml, e.g. scenario-01-cpu-starvation>
#               default: every scenario-*.yaml under evaluation/scenarios/
#   --conditions <comma-separated A and/or B>   default: A,B
#   --iterations <N>                            default: 1 (per scenario x condition)
#   --model-dir / --config / --playbook         forwarded to run_trial.py for condition B
#   --cooldown-buffer <seconds>                 default: 30 (added on top of COOLDOWN_SECONDS)
#   --pending-cr-timeout <seconds>               default: 120 (give up waiting for stale CRs)
#   --skip-operator-check                       skip the local kopf-process liveness check (see guard #3 above)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${ROOT_DIR}"

log() { echo "[campaign] $*"; }
die() { echo "[campaign] ERROR: $*" >&2; exit 1; }

SCENARIOS=""
CONDITIONS="A,B"
ITERATIONS=1
MODEL_DIR=""
CONFIG=""
PLAYBOOK=""
COOLDOWN_SECONDS="${COOLDOWN_SECONDS:-300}"
COOLDOWN_BUFFER=30
PENDING_CR_TIMEOUT=120
SKIP_OPERATOR_CHECK=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --scenarios) SCENARIOS="$2"; shift 2 ;;
    --conditions) CONDITIONS="$2"; shift 2 ;;
    --iterations) ITERATIONS="$2"; shift 2 ;;
    --model-dir) MODEL_DIR="$2"; shift 2 ;;
    --config) CONFIG="$2"; shift 2 ;;
    --playbook) PLAYBOOK="$2"; shift 2 ;;
    --cooldown-buffer) COOLDOWN_BUFFER="$2"; shift 2 ;;
    --pending-cr-timeout) PENDING_CR_TIMEOUT="$2"; shift 2 ;;
    --skip-operator-check) SKIP_OPERATOR_CHECK=1; shift ;;
    *) die "unknown argument: $1" ;;
  esac
done

if [[ -z "${SCENARIOS}" ]]; then
  SCENARIOS="$(ls "${ROOT_DIR}/evaluation/scenarios"/scenario-*.yaml | xargs -n1 basename | sed 's/\.yaml$//' | paste -sd,)"
fi

IFS=',' read -ra SCENARIO_LIST <<< "${SCENARIOS}"
IFS=',' read -ra CONDITION_LIST <<< "${CONDITIONS}"

NEEDS_OPERATOR=0
for c in "${CONDITION_LIST[@]}"; do
  if [[ "$c" == "B" && -z "${MODEL_DIR}" ]]; then
    die "--model-dir is required when --conditions includes B"
  fi
  [[ "$c" == "B" ]] && NEEDS_OPERATOR=1
done

if [[ "${NEEDS_OPERATOR}" -eq 1 && "${SKIP_OPERATOR_CHECK}" -eq 0 ]]; then
  if ! pgrep -f "kopf run operator/handlers.py" >/dev/null 2>&1; then
    die "no local 'kopf run operator/handlers.py' process found, but --conditions includes B. Every RemediationAction this campaign creates would sit unprocessed forever (see guard #3 in this script's header comment). Start the operator first (kopf run operator/handlers.py --namespace boutique) or pass --skip-operator-check if it's running somewhere this can't see with ps."
  fi
  log "Operator liveness check: found a running 'kopf run operator/handlers.py' process."
fi

wait_for_no_pending_crs() {
  local waited=0
  while true; do
    local pending
    pending="$(kubectl get remediationaction -n boutique -o json 2>/dev/null \
      | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit(0)
items = d.get('items', [])
pending = [i['metadata']['name'] for i in items if i.get('status', {}).get('phase') not in ('Succeeded', 'Failed', 'CooldownBlocked', 'DryRun')]
print(','.join(pending))
" 2>/dev/null || true)"
    if [[ -z "${pending}" ]]; then
      return 0
    fi
    if [[ "${waited}" -ge "${PENDING_CR_TIMEOUT}" ]]; then
      die "RemediationAction(s) still pending after ${PENDING_CR_TIMEOUT}s: ${pending} — clear these (kubectl get remediationaction -n boutique) before continuing; see docs/experiment-log.md's 2026-09-11 cooldown-collision entries."
    fi
    log "Waiting for pending RemediationAction(s) to settle: ${pending} (${waited}s/${PENDING_CR_TIMEOUT}s)"
    sleep 10
    waited=$((waited + 10))
  done
}

TOTAL=$(( ${#SCENARIO_LIST[@]} * ${#CONDITION_LIST[@]} * ITERATIONS ))
COUNT=0
FAILURES=()

for scenario_stem in "${SCENARIO_LIST[@]}"; do
  scenario_file="${ROOT_DIR}/evaluation/scenarios/${scenario_stem}.yaml"
  [[ -f "${scenario_file}" ]] || die "scenario file not found: ${scenario_file}"

  for condition in "${CONDITION_LIST[@]}"; do
    for ((i=1; i<=ITERATIONS; i++)); do
      COUNT=$((COUNT + 1))
      log "=== [${COUNT}/${TOTAL}] ${scenario_stem} — Run ${condition} — iteration ${i}/${ITERATIONS} ==="

      wait_for_no_pending_crs

      CMD=(python3 "${ROOT_DIR}/evaluation/analysis/run_trial.py" --scenario "${scenario_file}" --condition "${condition}")
      if [[ "${condition}" == "B" ]]; then
        CMD+=(--model-dir "${MODEL_DIR}")
        [[ -n "${CONFIG}" ]] && CMD+=(--config "${CONFIG}")
        [[ -n "${PLAYBOOK}" ]] && CMD+=(--playbook "${PLAYBOOK}")
      fi

      log "Running: ${CMD[*]}"
      if ! "${CMD[@]}"; then
        log "TRIAL FAILED (non-zero exit) — logging and continuing with the rest of the campaign."
        FAILURES+=("${scenario_stem} Run${condition} iter${i}")
      fi

      IS_LAST=$(( COUNT == TOTAL ? 1 : 0 ))
      if [[ "${IS_LAST}" -eq 0 ]]; then
        SLEEP_SECONDS=$(( COOLDOWN_SECONDS + COOLDOWN_BUFFER ))
        log "Sleeping ${SLEEP_SECONDS}s (COOLDOWN_SECONDS=${COOLDOWN_SECONDS} + buffer=${COOLDOWN_BUFFER}) before next trial..."
        sleep "${SLEEP_SECONDS}"
      fi
    done
  done
done

log "Campaign complete: ${COUNT} trials attempted."
if [[ ${#FAILURES[@]} -gt 0 ]]; then
  log "Trials that exited non-zero (inspect before treating as valid data):"
  for f in "${FAILURES[@]}"; do log "  - ${f}"; done
  exit 1
fi
log "All trials completed. Raw results in evaluation/runs/trials/ — review trial_valid/censored flags before treating as campaign-final data."
