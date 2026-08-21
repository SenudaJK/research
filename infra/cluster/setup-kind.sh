#!/usr/bin/env bash
# Provision a Kind cluster for Phase 1 (Sense).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
source "${SCRIPT_DIR}/../versions.env"

CLUSTER_CONFIG="${SCRIPT_DIR}/kind-config.yaml"

log() { echo "[cluster/kind] $*"; }
die() { echo "[cluster/kind] ERROR: $*" >&2; exit 1; }

command -v kind >/dev/null 2>&1 || die "kind not found. Install: https://kind.sigs.k8s.io/docs/user/quick-start/"
command -v kubectl >/dev/null 2>&1 || die "kubectl not found"
command -v helm >/dev/null 2>&1 || die "helm not found"

if kind get clusters 2>/dev/null | grep -qx "${CLUSTER_NAME}"; then
  log "Cluster '${CLUSTER_NAME}' already exists — skipping create"
else
  log "Creating Kind cluster '${CLUSTER_NAME}' (K8s ${KUBERNETES_VERSION}, kind ${KIND_VERSION})"
  kind create cluster \
    --name "${CLUSTER_NAME}" \
    --config "${CLUSTER_CONFIG}" \
    --image "kindest/node:v${KUBERNETES_VERSION}" \
    --wait 300s
fi

kubectl cluster-info --context "kind-${CLUSTER_NAME}"
kubectl apply -f "${SCRIPT_DIR}/namespaces.yaml"

log "Kind cluster ready. Context: kind-${CLUSTER_NAME}"
