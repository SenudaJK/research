#!/usr/bin/env bash
# Provision a Minikube cluster for Phase 1 (Sense).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../versions.env"

log() { echo "[cluster/minikube] $*"; }
die() { echo "[cluster/minikube] ERROR: $*" >&2; exit 1; }

command -v minikube >/dev/null 2>&1 || die "minikube not found. Install: https://minikube.sigs.k8s.io/docs/start/"
command -v kubectl >/dev/null 2>&1 || die "kubectl not found"
command -v helm >/dev/null 2>&1 || die "helm not found"

if minikube status -p "${CLUSTER_NAME}" >/dev/null 2>&1; then
  log "Profile '${CLUSTER_NAME}' already exists — starting if stopped"
  minikube start -p "${CLUSTER_NAME}" || true
else
  log "Creating Minikube profile '${CLUSTER_NAME}' (K8s ${KUBERNETES_VERSION})"
  minikube start \
    -p "${CLUSTER_NAME}" \
    --kubernetes-version="v${KUBERNETES_VERSION}" \
    --cpus=4 \
    --memory=8192 \
    --disk-size=40g \
    --driver=docker \
    --addons=metrics-server
fi

kubectl config use-context "${CLUSTER_NAME}"
kubectl apply -f "${SCRIPT_DIR}/namespaces.yaml"

log "Minikube cluster ready. Context: ${CLUSTER_NAME}"
