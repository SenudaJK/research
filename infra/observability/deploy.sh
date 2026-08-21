#!/usr/bin/env bash
# Deploy the multi-modal observability stack: Prometheus, Loki, Jaeger, OpenTelemetry.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../versions.env"

VALUES_DIR="${SCRIPT_DIR}/values"
log() { echo "[observability] $*"; }
die() { echo "[observability] ERROR: $*" >&2; exit 1; }

command -v helm >/dev/null 2>&1 || die "helm not found"
command -v kubectl >/dev/null 2>&1 || die "kubectl not found"

kubectl create namespace "${OBSERVABILITY_NAMESPACE}" --dry-run=client -o yaml | kubectl apply -f -

log "Adding Helm repositories..."
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts 2>/dev/null || true
helm repo add grafana https://grafana.github.io/helm-charts 2>/dev/null || true
helm repo add jaegertracing https://jaegertracing.github.io/helm-charts 2>/dev/null || true
helm repo add open-telemetry https://open-telemetry.github.io/opentelemetry-helm-charts 2>/dev/null || true
helm repo update

log "Installing kube-prometheus-stack (chart ${HELM_KUBE_PROMETHEUS_STACK_CHART_VERSION})..."
helm upgrade --install kube-prometheus-stack prometheus-community/kube-prometheus-stack \
  --namespace "${OBSERVABILITY_NAMESPACE}" \
  --version "${HELM_KUBE_PROMETHEUS_STACK_CHART_VERSION}" \
  --values "${VALUES_DIR}/kube-prometheus-stack.yaml" \
  --wait --timeout 10m

log "Installing Loki (chart ${HELM_LOKI_CHART_VERSION})..."
helm upgrade --install loki grafana/loki \
  --namespace "${OBSERVABILITY_NAMESPACE}" \
  --version "${HELM_LOKI_CHART_VERSION}" \
  --values "${VALUES_DIR}/loki.yaml" \
  --wait --timeout 10m

log "Installing Promtail (chart ${HELM_PROMTAIL_CHART_VERSION})..."
helm upgrade --install promtail grafana/promtail \
  --namespace "${OBSERVABILITY_NAMESPACE}" \
  --version "${HELM_PROMTAIL_CHART_VERSION}" \
  --values "${VALUES_DIR}/promtail.yaml" \
  --wait --timeout 5m

log "Installing Jaeger (chart ${HELM_JAEGER_CHART_VERSION})..."
helm upgrade --install jaeger jaegertracing/jaeger \
  --namespace "${OBSERVABILITY_NAMESPACE}" \
  --version "${HELM_JAEGER_CHART_VERSION}" \
  --values "${VALUES_DIR}/jaeger.yaml" \
  --wait --timeout 5m

kubectl apply -f "${SCRIPT_DIR}/manifests/jaeger-query-nodeport.yaml"

log "Installing OpenTelemetry Collector (chart ${HELM_OTEL_COLLECTOR_CHART_VERSION})..."
helm upgrade --install opentelemetry-collector open-telemetry/opentelemetry-collector \
  --namespace "${OBSERVABILITY_NAMESPACE}" \
  --version "${HELM_OTEL_COLLECTOR_CHART_VERSION}" \
  --values "${VALUES_DIR}/otel-collector.yaml" \
  --wait --timeout 5m

log "Observability stack deployed."
kubectl get pods -n "${OBSERVABILITY_NAMESPACE}"
