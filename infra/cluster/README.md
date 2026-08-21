# Cluster Setup

Minikube or Kind cluster provisioning for Phase 1 (Sense).

## Quick Start

```bash
# Kind (recommended for reproducibility)
bash infra/cluster/setup-kind.sh

# Minikube
bash infra/cluster/setup-minikube.sh

# Tear down
bash infra/cluster/destroy.sh kind    # or minikube
```

## Requirements

- `kubectl`, `helm`
- Kind: `kind` CLI
- Minikube: `minikube` CLI, Docker driver
- Minimum host resources: 4 CPU, 8 GB RAM

## Files

| File | Purpose |
|------|---------|
| `kind-config.yaml` | 3-node Kind cluster with UI port mappings |
| `namespaces.yaml` | `boutique`, `observability`, `chaos-mesh` |
| `setup-kind.sh` | Create Kind cluster |
| `setup-minikube.sh` | Create Minikube profile |
| `destroy.sh` | Delete cluster |

Versions are pinned in `infra/versions.env`.
