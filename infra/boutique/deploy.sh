#!/usr/bin/env bash
# Deploy Google Online Boutique with OpenTelemetry tracing enabled.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../versions.env"

log() { echo "[boutique] $*"; }
die() { echo "[boutique] ERROR: $*" >&2; exit 1; }

command -v kubectl >/dev/null 2>&1 || die "kubectl not found"
command -v kustomize >/dev/null 2>&1 || die "kustomize not found (kubectl kustomize also works)"

kubectl create namespace "${BOUTIQUE_NAMESPACE}" --dry-run=client -o yaml | kubectl apply -f -

log "Deploying Online Boutique ${BOUTIQUE_VERSION} to namespace ${BOUTIQUE_NAMESPACE}"
kubectl apply -k "${SCRIPT_DIR}"

log "Waiting for boutique workloads to become ready..."
kubectl rollout status deployment/frontend -n "${BOUTIQUE_NAMESPACE}" --timeout=300s
kubectl rollout status deployment/checkoutservice -n "${BOUTIQUE_NAMESPACE}" --timeout=300s
kubectl rollout status deployment/loadgenerator -n "${BOUTIQUE_NAMESPACE}" --timeout=300s || true

log "Online Boutique deployed. Frontend:"
kubectl get svc frontend-external -n "${BOUTIQUE_NAMESPACE}" 2>/dev/null || kubectl get svc -n "${BOUTIQUE_NAMESPACE}"
