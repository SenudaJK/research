#!/usr/bin/env bash
# Deploy Chaos Mesh for Phase 4 (Validate).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../versions.env"

VALUES_DIR="${SCRIPT_DIR}"
log() { echo "[chaos-mesh] $*"; }
die() { echo "[chaos-mesh] ERROR: $*" >&2; exit 1; }

command -v helm >/dev/null 2>&1 || die "helm not found"
command -v kubectl >/dev/null 2>&1 || die "kubectl not found"

kubectl create namespace "${CHAOS_NAMESPACE}" --dry-run=client -o yaml | kubectl apply -f -

log "Adding Helm repository..."
helm repo add chaos-mesh https://charts.chaos-mesh.org 2>/dev/null || true
helm repo update

log "Installing Chaos Mesh (chart ${HELM_CHAOS_MESH_CHART_VERSION})..."
helm upgrade --install chaos-mesh chaos-mesh/chaos-mesh \
  --namespace "${CHAOS_NAMESPACE}" \
  --version "${HELM_CHAOS_MESH_CHART_VERSION}" \
  --values "${VALUES_DIR}/values.yaml" \
  --wait --timeout 5m

log "Chaos Mesh deployed."
kubectl get pods -n "${CHAOS_NAMESPACE}"

log "Verify CRDs are registered:"
kubectl get crds | grep -c 'chaos-mesh.org' | xargs -I{} echo "  {} chaos-mesh.org CRDs found"

log "Next: run evaluation/scenarios/*.yaml against the boutique namespace."
log "Dashboard access: kubectl port-forward -n ${CHAOS_NAMESPACE} svc/chaos-dashboard 2333:2333"
