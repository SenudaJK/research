#!/usr/bin/env bash
# Deploy complete Phase 1 (Sense): cluster namespaces, observability, boutique.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
source "${SCRIPT_DIR}/../versions.env"

PROVIDER="${1:-kind}"

log() { echo "[phase1] $*"; }

case "${PROVIDER}" in
  kind)
  log "Step 1/3: Provision Kind cluster"
  bash "${ROOT_DIR}/infra/cluster/setup-kind.sh"
  ;;
  minikube)
  log "Step 1/3: Provision Minikube cluster"
  bash "${ROOT_DIR}/infra/cluster/setup-minikube.sh"
  ;;
  skip-cluster)
  log "Step 1/3: Skipping cluster provisioning (existing cluster)"
  kubectl apply -f "${ROOT_DIR}/infra/cluster/namespaces.yaml"
  ;;
  *)
  echo "Usage: $0 [kind|minikube|skip-cluster]" >&2
  exit 1
  ;;
esac

log "Step 2/3: Deploy observability stack"
bash "${ROOT_DIR}/infra/observability/deploy.sh"

log "Step 3/3: Deploy Online Boutique"
bash "${ROOT_DIR}/infra/boutique/deploy.sh"

log "Phase 1 deployment complete."
log "Run baseline collection: bash infra/scripts/collect-baseline.sh"
log "Verify stack health:      bash infra/scripts/verify-stack.sh"
