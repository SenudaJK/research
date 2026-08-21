#!/usr/bin/env bash
# Verify Phase 1 stack health: cluster, observability, boutique, telemetry flow.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../versions.env"

log() { echo "[verify] $*"; }
warn() { echo "[verify] WARN: $*" >&2; }
fail() { echo "[verify] FAIL: $*" >&2; FAILED=1; }

FAILED=0

log "Checking namespaces..."
for ns in "${BOUTIQUE_NAMESPACE}" "${OBSERVABILITY_NAMESPACE}"; do
  kubectl get namespace "${ns}" >/dev/null 2>&1 || fail "namespace ${ns} missing"
done

log "Checking boutique pods..."
NOT_READY="$(kubectl get pods -n "${BOUTIQUE_NAMESPACE}" --field-selector=status.phase!=Running,status.phase!=Succeeded -o name 2>/dev/null | wc -l | tr -d ' ')"
if [[ "${NOT_READY}" -gt 0 ]]; then
  fail "${NOT_READY} boutique pods not Running/Succeeded"
  kubectl get pods -n "${BOUTIQUE_NAMESPACE}"
else
  log "All boutique pods healthy"
fi

log "Checking observability pods..."
kubectl wait --for=condition=ready pod \
  -l app.kubernetes.io/name=prometheus \
  -n "${OBSERVABILITY_NAMESPACE}" \
  --timeout=60s 2>/dev/null || warn "Prometheus pods not ready"

kubectl get pods -n "${OBSERVABILITY_NAMESPACE}" | grep -E 'loki|promtail|jaeger|opentelemetry' || \
  warn "Some observability components may be missing"

log "Checking OTEL collector endpoint..."
kubectl get svc opentelemetry-collector -n "${OBSERVABILITY_NAMESPACE}" >/dev/null 2>&1 || \
  fail "opentelemetry-collector service missing"

log "Checking load generator..."
kubectl get deployment loadgenerator -n "${BOUTIQUE_NAMESPACE}" >/dev/null 2>&1 && \
  log "Load generator present" || warn "Load generator not found"

log "Checking Prometheus has boutique RED spanmetrics..."
PROM_SVC="kube-prometheus-prometheus"
kubectl get svc -n "${OBSERVABILITY_NAMESPACE}" "${PROM_SVC}" >/dev/null 2>&1 || \
  PROM_SVC="$(kubectl get svc -n "${OBSERVABILITY_NAMESPACE}" -l app.kubernetes.io/name=prometheus -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
if [[ -n "${PROM_SVC}" ]]; then
  kubectl port-forward -n "${OBSERVABILITY_NAMESPACE}" "svc/${PROM_SVC}" 19091:9090 >/dev/null 2>&1 &
  PF_PROM=$!
  sleep 3
  RED="$(curl -sfG "http://127.0.0.1:19091/api/v1/query" \
    --data-urlencode 'query=sum(rate(boutique_traces_span_metrics_calls_total[1m]))' 2>/dev/null || true)"
  kill "${PF_PROM}" 2>/dev/null || true
  wait "${PF_PROM}" 2>/dev/null || true
  if echo "${RED}" | jq -e '.status=="success" and (.data.result|length)>0 and ((.data.result[0].value[1]|tonumber)>0)' >/dev/null 2>&1; then
    log "RED spanmetrics present in Prometheus"
  else
    fail "Prometheus missing boutique_traces_span_metrics_calls_total (cannot collect a trainable baseline)"
  fi
else
  fail "Prometheus service not found"
fi

if [[ ${FAILED} -eq 0 ]]; then
  log "Stack verification passed"
  exit 0
else
  log "Stack verification failed"
  exit 1
fi
