#!/usr/bin/env bash
# Collect fault-free baseline telemetry (metrics, logs, traces).
# Default: 30 minutes. Override with BASELINE_DURATION_SECONDS (smoke: 180).
# Output: evaluation/runs/baseline/<timestamp>/
# Quality gate: infra/scripts/check-baseline-quality.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
source "${SCRIPT_DIR}/../versions.env"

log() { echo "[baseline] $*"; }
die() { echo "[baseline] ERROR: $*" >&2; exit 1; }

command -v kubectl >/dev/null 2>&1 || die "kubectl not found"
command -v curl >/dev/null 2>&1 || die "curl not found"
command -v jq >/dev/null 2>&1 || die "jq not found"

RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
OUTPUT_DIR="${ROOT_DIR}/${BASELINE_OUTPUT_DIR}/${RUN_ID}"
mkdir -p "${OUTPUT_DIR}"/{metrics,logs,traces,meta}

log "Baseline run ${RUN_ID} — duration ${BASELINE_DURATION_SECONDS}s, interval ${BASELINE_SAMPLE_INTERVAL_SECONDS}s"
log "Output directory: ${OUTPUT_DIR}"

# Named PromQL keys must stay stable — check-baseline-quality.sh and Phase 2 depend on them.
# See docs/measurement-protocol.md.
METRIC_KEYS=(
  red_request_rate
  red_error_rate
  red_avg_duration_ms
  frontend_success_rate
  cpu_usage
  memory_working_set
  network_receive_bytes
  pod_phase
)
METRIC_QUERY_red_request_rate='sum(rate(boutique_traces_span_metrics_calls_total[1m]))'
METRIC_QUERY_red_error_rate='sum(rate(boutique_traces_span_metrics_calls_total{status_code="STATUS_CODE_ERROR"}[1m])) or vector(0)'
METRIC_QUERY_red_avg_duration_ms='sum(rate(boutique_traces_span_metrics_duration_milliseconds_sum[1m])) / clamp_min(sum(rate(boutique_traces_span_metrics_duration_milliseconds_count[1m])), 1e-9)'
METRIC_QUERY_frontend_success_rate='1 - ((sum(rate(boutique_traces_span_metrics_calls_total{service_name="frontend",span_kind="SPAN_KIND_SERVER",status_code="STATUS_CODE_ERROR"}[1m])) or vector(0)) / clamp_min(sum(rate(boutique_traces_span_metrics_calls_total{service_name="frontend",span_kind="SPAN_KIND_SERVER"}[1m])), 1e-9))'
METRIC_QUERY_cpu_usage='sum(rate(container_cpu_usage_seconds_total{namespace="boutique"}[1m]))'
METRIC_QUERY_memory_working_set='sum(container_memory_working_set_bytes{namespace="boutique"})'
METRIC_QUERY_network_receive_bytes='sum(rate(container_network_receive_bytes_total{namespace="boutique"}[1m]))'
METRIC_QUERY_pod_phase='kube_pod_status_phase{namespace="boutique"}'

query_for() {
  local key="$1"
  eval "printf '%s' \"\${METRIC_QUERY_${key}}\""
}

cat > "${OUTPUT_DIR}/meta/manifest.json" <<EOF
{
  "run_id": "${RUN_ID}",
  "phase": "sense",
  "condition": "fault-free-baseline",
  "protocol": "docs/measurement-protocol.md",
  "duration_seconds": ${BASELINE_DURATION_SECONDS},
  "sample_interval_seconds": ${BASELINE_SAMPLE_INTERVAL_SECONDS},
  "boutique_version": "${BOUTIQUE_VERSION}",
  "boutique_namespace": "${BOUTIQUE_NAMESPACE}",
  "observability_namespace": "${OBSERVABILITY_NAMESPACE}",
  "metric_keys": $(printf '%s\n' "${METRIC_KEYS[@]}" | jq -R . | jq -s .),
  "log_selector": "{namespace=\"${BOUTIQUE_NAMESPACE}\"}",
  "started_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
EOF

PROM_SVC="kube-prometheus-prometheus"
LOKI_SVC="loki-gateway"
JAEGER_SVC="jaeger-query-external"

kubectl get svc -n "${OBSERVABILITY_NAMESPACE}" "${PROM_SVC}" >/dev/null 2>&1 || \
  PROM_SVC="$(kubectl get svc -n "${OBSERVABILITY_NAMESPACE}" -l app.kubernetes.io/name=prometheus -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
kubectl get svc -n "${OBSERVABILITY_NAMESPACE}" "${LOKI_SVC}" >/dev/null 2>&1 || \
  LOKI_SVC="$(kubectl get svc -n "${OBSERVABILITY_NAMESPACE}" -l app.kubernetes.io/name=loki -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
kubectl get svc -n "${OBSERVABILITY_NAMESPACE}" "${JAEGER_SVC}" >/dev/null 2>&1 || \
  JAEGER_SVC="jaeger-query"

[[ -n "${PROM_SVC}" ]] || die "Prometheus service not found"
[[ -n "${LOKI_SVC}" ]] || die "Loki service not found"

log "Port-forwarding Prometheus (${PROM_SVC}), Loki (${LOKI_SVC}), Jaeger (${JAEGER_SVC})..."
kubectl port-forward -n "${OBSERVABILITY_NAMESPACE}" "svc/${PROM_SVC}" 19090:9090 >/dev/null 2>&1 &
PF_PROM=$!
kubectl port-forward -n "${OBSERVABILITY_NAMESPACE}" "svc/${LOKI_SVC}" 13100:80 >/dev/null 2>&1 &
PF_LOKI=$!
kubectl port-forward -n "${OBSERVABILITY_NAMESPACE}" "svc/${JAEGER_SVC}" 16686:16686 >/dev/null 2>&1 &
PF_JAEGER=$!

cleanup() {
  kill "${PF_PROM}" "${PF_LOKI}" "${PF_JAEGER}" 2>/dev/null || true
  if [[ -f "${OUTPUT_DIR}/meta/manifest.json" ]]; then
    ENDED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    jq --arg ended "${ENDED_AT}" '. + {ended_at: $ended}' "${OUTPUT_DIR}/meta/manifest.json" > "${OUTPUT_DIR}/meta/manifest.tmp"
    mv "${OUTPUT_DIR}/meta/manifest.tmp" "${OUTPUT_DIR}/meta/manifest.json"
  fi
}
trap cleanup EXIT

sleep 5
curl -sf "http://127.0.0.1:19090/api/v1/status/config" >/dev/null || die "Prometheus port-forward failed"
log "Prometheus reachable"

END_TIME=$((SECONDS + BASELINE_DURATION_SECONDS))
SAMPLE=0

while [[ ${SECONDS} -lt ${END_TIME} ]]; do
  SAMPLE=$((SAMPLE + 1))
  TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  log "Sample ${SAMPLE} at ${TS}"

  METRICS_FILE="${OUTPUT_DIR}/metrics/sample_${SAMPLE}.json"
  echo "{\"timestamp\":\"${TS}\",\"queries\":{}}" > "${METRICS_FILE}"
  for key in "${METRIC_KEYS[@]}"; do
    q="$(query_for "${key}")"
    RESULT="$(curl -sfG "http://127.0.0.1:19090/api/v1/query" --data-urlencode "query=${q}" 2>/dev/null || echo '{"status":"error"}')"
    jq --arg k "${key}" --arg q "${q}" --argjson r "${RESULT}" \
      '.queries[$k] = ($r + {promql: $q})' "${METRICS_FILE}" > "${METRICS_FILE}.tmp"
    mv "${METRICS_FILE}.tmp" "${METRICS_FILE}"
  done

  NOW_NS=$(date +%s)000000000
  START_NS=$(( ( $(date +%s) - 120 ) * 1000000000 ))
  curl -sfG "http://127.0.0.1:13100/loki/api/v1/query_range" \
    --data-urlencode "query={namespace=\"${BOUTIQUE_NAMESPACE}\"}" \
    --data-urlencode "start=${START_NS}" \
    --data-urlencode "end=${NOW_NS}" \
    --data-urlencode 'limit=500' \
    -o "${OUTPUT_DIR}/logs/sample_${SAMPLE}.json" 2>/dev/null || \
    echo '{"status":"error"}' > "${OUTPUT_DIR}/logs/sample_${SAMPLE}.json"

  curl -sf "http://127.0.0.1:16686/api/services" \
    -o "${OUTPUT_DIR}/traces/services_${SAMPLE}.json" 2>/dev/null || \
    echo '{"data":[],"total":0}' > "${OUTPUT_DIR}/traces/services_${SAMPLE}.json"

  curl -sfG "http://127.0.0.1:16686/api/traces" \
    --data-urlencode 'service=frontend' \
    --data-urlencode 'limit=20' \
    -o "${OUTPUT_DIR}/traces/sample_${SAMPLE}.json" 2>/dev/null || \
    echo '{"data":[],"total":0}' > "${OUTPUT_DIR}/traces/sample_${SAMPLE}.json"

  REMAINING=$(( END_TIME - SECONDS ))
  if [[ ${REMAINING} -le 0 ]]; then break; fi
  SLEEP_FOR=$(( BASELINE_SAMPLE_INTERVAL_SECONDS < REMAINING ? BASELINE_SAMPLE_INTERVAL_SECONDS : REMAINING ))
  sleep "${SLEEP_FOR}"
done

kubectl get pods -n "${BOUTIQUE_NAMESPACE}" -o wide > "${OUTPUT_DIR}/meta/boutique-pods.txt"
kubectl top pods -n "${BOUTIQUE_NAMESPACE}" 2>/dev/null > "${OUTPUT_DIR}/meta/boutique-resource-usage.txt" || true

log "Baseline collection complete: ${OUTPUT_DIR}"
log "Samples collected: ${SAMPLE}"
log "Running quality gate..."
bash "${SCRIPT_DIR}/check-baseline-quality.sh" "${OUTPUT_DIR}"
