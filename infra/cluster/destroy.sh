#!/usr/bin/env bash
# Tear down the research cluster (Kind or Minikube).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../versions.env"

PROVIDER="${1:-kind}"

case "${PROVIDER}" in
  kind)
    if kind get clusters 2>/dev/null | grep -qx "${CLUSTER_NAME}"; then
      kind delete cluster --name "${CLUSTER_NAME}"
      echo "[cluster] Deleted Kind cluster '${CLUSTER_NAME}'"
    else
      echo "[cluster] Kind cluster '${CLUSTER_NAME}' not found"
    fi
    ;;
  minikube)
    if minikube profile list 2>/dev/null | grep -q "${CLUSTER_NAME}"; then
      minikube delete -p "${CLUSTER_NAME}"
      echo "[cluster] Deleted Minikube profile '${CLUSTER_NAME}'"
    else
      echo "[cluster] Minikube profile '${CLUSTER_NAME}' not found"
    fi
    ;;
  *)
    echo "Usage: $0 [kind|minikube]" >&2
    exit 1
    ;;
esac
