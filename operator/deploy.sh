#!/usr/bin/env bash
# Installs the RemediationAction CRD. Does NOT run the operator itself —
# for this vertical slice the operator runs out-of-cluster (see README),
# so there is no in-cluster Deployment/RBAC to install yet.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

log() { echo "[operator] $*"; }

command -v kubectl >/dev/null 2>&1 || { echo "[operator] ERROR: kubectl not found" >&2; exit 1; }

log "Installing RemediationAction CRD..."
kubectl apply -f "${SCRIPT_DIR}/crds/remediationaction-crd.yaml"

log "Done. Run the operator (out-of-cluster) with:"
log "  pip install -r operator/requirements.txt"
log "  kopf run operator/handlers.py --namespace boutique"
