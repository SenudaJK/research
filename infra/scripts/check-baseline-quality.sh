#!/usr/bin/env bash
# Fail if a baseline run is not usable for Isolation Forest training (RQ1).
# Usage: bash infra/scripts/check-baseline-quality.sh [run-dir]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
source "${SCRIPT_DIR}/../versions.env"

log() { echo "[quality] $*"; }
fail() { echo "[quality] FAIL: $*" >&2; FAILED=1; }

FAILED=0
RUN_DIR="${1:-}"

if [[ -z "${RUN_DIR}" ]]; then
  RUN_DIR="$(ls -1d "${ROOT_DIR}/${BASELINE_OUTPUT_DIR}"/*/ 2>/dev/null | tail -1 || true)"
fi
[[ -n "${RUN_DIR}" && -d "${RUN_DIR}" ]] || { echo "[quality] ERROR: no baseline run directory" >&2; exit 1; }
RUN_DIR="$(cd "${RUN_DIR}" && pwd)"

log "Checking ${RUN_DIR}"

MANIFEST="${RUN_DIR}/meta/manifest.json"
[[ -f "${MANIFEST}" ]] || fail "missing meta/manifest.json"

SAMPLES=( "${RUN_DIR}"/metrics/sample_*.json )
[[ -f "${SAMPLES[0]}" ]] || fail "no metrics samples"
SAMPLE_COUNT="${#SAMPLES[@]}"
log "Metrics samples: ${SAMPLE_COUNT}"

# Training runs must be the full 30-minute soak unless this is an explicit smoke.
DURATION="$(jq -r '.duration_seconds // 0' "${MANIFEST}" 2>/dev/null || echo 0)"
MIN_SAMPLES=3
if [[ "${DURATION}" -ge 1800 ]]; then
  MIN_SAMPLES=25
fi
if [[ "${SAMPLE_COUNT}" -lt "${MIN_SAMPLES}" ]]; then
  fail "expected at least ${MIN_SAMPLES} samples for duration=${DURATION}s, got ${SAMPLE_COUNT}"
fi

nonempty() {
  local file="$1" key="$2"
  jq -e --arg k "${key}" '
    .queries[$k].status == "success"
    and (.queries[$k].data.result | length) > 0
    and (
      (.queries[$k].data.result[0].value[1] | tonumber? // -1) > 0
      or ($k == "red_error_rate")
      or ($k == "pod_phase")
    )
  ' "${file}" >/dev/null 2>&1
}

RED_OK=0
CPU_OK=0
MEM_OK=0
ERR_OK=0
DUR_OK=0
SLO_OK=0

for f in "${SAMPLES[@]}"; do
  nonempty "${f}" red_request_rate && RED_OK=$((RED_OK + 1))
  nonempty "${f}" cpu_usage && CPU_OK=$((CPU_OK + 1))
  nonempty "${f}" memory_working_set && MEM_OK=$((MEM_OK + 1))
  nonempty "${f}" red_error_rate && ERR_OK=$((ERR_OK + 1))
  nonempty "${f}" red_avg_duration_ms && DUR_OK=$((DUR_OK + 1))
  nonempty "${f}" frontend_success_rate && SLO_OK=$((SLO_OK + 1))
done

need_frac() {
  local ok="$1" name="$2"
  local need=$(( (SAMPLE_COUNT * 80 + 99) / 100 ))
  if [[ "${ok}" -lt "${need}" ]]; then
    fail "${name} non-empty in ${ok}/${SAMPLE_COUNT} samples (need >= ${need}, 80%)"
  else
    log "${name}: ${ok}/${SAMPLE_COUNT} ok"
  fi
}

need_frac "${RED_OK}" "red_request_rate"
need_frac "${CPU_OK}" "cpu_usage"
need_frac "${MEM_OK}" "memory_working_set"
need_frac "${DUR_OK}" "red_avg_duration_ms"
need_frac "${SLO_OK}" "frontend_success_rate"
# error rate may be legitimately zero; require the query to succeed, not to be > 0
if [[ "${ERR_OK}" -lt 1 ]]; then
  fail "red_error_rate missing from all samples (query should return 0, not empty)"
else
  log "red_error_rate: ${ERR_OK}/${SAMPLE_COUNT} ok"
fi

LOG_OK=0
for f in "${RUN_DIR}"/logs/sample_*.json; do
  [[ -f "${f}" ]] || continue
  jq -e '.status == "success" and (.data.result | length) > 0' "${f}" >/dev/null 2>&1 && LOG_OK=$((LOG_OK + 1))
done
need_frac "${LOG_OK}" "boutique logs"

TRACE_OK=0
for f in "${RUN_DIR}"/traces/services_*.json; do
  [[ -f "${f}" ]] || continue
  jq -e '(.data | length) > 0 or (. | length) > 0' "${f}" >/dev/null 2>&1 && TRACE_OK=$((TRACE_OK + 1))
done
if [[ "${TRACE_OK}" -lt 1 ]]; then
  fail "Jaeger services list empty in all samples"
else
  log "traces services: ${TRACE_OK} non-empty"
fi

PASSED=true
[[ "${FAILED}" -eq 0 ]] || PASSED=false

# "passed" only means the data is complete/non-empty. "trainable" additionally
# requires enough samples for Isolation Forest to produce a statistically
# meaningful model and threshold — see MIN_TRAINABLE_SAMPLES in
# infra/versions.env for why 1800s/30 samples was found insufficient.
TRAINABLE=false
if [[ "${PASSED}" == "true" && "${SAMPLE_COUNT}" -ge "${MIN_TRAINABLE_SAMPLES}" ]]; then
  TRAINABLE=true
elif [[ "${PASSED}" == "true" && "${DURATION}" -ge 1800 ]]; then
  log "Data complete, but only ${SAMPLE_COUNT} samples (< MIN_TRAINABLE_SAMPLES=${MIN_TRAINABLE_SAMPLES}) — not marking trainable."
  log "Re-collect with: BASELINE_DURATION_SECONDS=$(( MIN_TRAINABLE_SAMPLES * BASELINE_SAMPLE_INTERVAL_SECONDS )) bash infra/scripts/collect-baseline.sh"
fi

mkdir -p "${RUN_DIR}/meta"
jq -n \
  --argjson passed "${PASSED}" \
  --argjson trainable "${TRAINABLE}" \
  --argjson duration "${DURATION}" \
  --argjson samples "${SAMPLE_COUNT}" \
  --argjson red "${RED_OK}" \
  --argjson cpu "${CPU_OK}" \
  --argjson mem "${MEM_OK}" \
  --argjson dur "${DUR_OK}" \
  --argjson slo "${SLO_OK}" \
  --argjson logs "${LOG_OK}" \
  --argjson traces "${TRACE_OK}" \
  --argjson min_trainable_samples "${MIN_TRAINABLE_SAMPLES}" \
  --arg checked_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  '{
    passed: $passed,
    trainable: $trainable,
    duration_seconds: $duration,
    checked_at: $checked_at,
    samples: $samples,
    min_trainable_samples: $min_trainable_samples,
    non_empty: {
      red_request_rate: $red,
      cpu_usage: $cpu,
      memory_working_set: $mem,
      red_avg_duration_ms: $dur,
      frontend_success_rate: $slo,
      boutique_logs: $logs,
      jaeger_services: $traces
    }
  }' > "${RUN_DIR}/meta/quality.json"

if [[ "${PASSED}" != "true" ]]; then
  log "FAILED — do not train Isolation Forest on this run"
  exit 1
fi
if [[ "${TRAINABLE}" == "true" ]]; then
  log "PASSED — trainable 30-minute baseline: ${RUN_DIR}"
else
  log "PASSED — smoke/pipeline proof only (duration ${DURATION}s < 1800s). Not for training."
fi
exit 0
