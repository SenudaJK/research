# Intelligent Self-Healing Framework for Kubernetes

Research implementation for dissertation **IM/2021/014** — *An Intelligent Self-Healing Framework for Kubernetes: Integrating Multi-Modal Observability Fusion (Metrics, Logs, Traces) with Hybrid Decision-Making*.

## Architecture

The framework follows a **Design Science Research (DSR)** methodology with a continuous **Sense → Analyze → Act** control loop:

| Phase | Component | Location |
|-------|-----------|----------|
| Sense | Cluster + observability stack | `infra/` |
| Analyze | Multi-modal fusion + anomaly detection | `fusion-engine/` |
| Act | Hybrid decisions + K8s operator | `decision-engine/`, `operator/` |
| Validate | Chaos engineering experiments | `evaluation/` |

## Repository Structure

```
Research/
├── IM2021014.pdf              # Thesis (source of truth)
├── docs/                      # Methodology, architecture, experiment logs
├── infra/                     # Kubernetes cluster, observability, testbed
├── fusion-engine/             # Telemetry normalization + state vector
├── decision-engine/           # Isolation Forest + rule-based playbook
├── operator/                  # Custom Kubernetes operator (actuator)
└── evaluation/                # Chaos scenarios, raw runs, statistical analysis
```

## Tech Stack

- **Cluster:** Minikube or Kind
- **Workload:** Google Online Boutique
- **Observability:** Prometheus, Grafana Loki, Jaeger (+ OpenTelemetry)
- **Chaos:** Chaos Mesh (12 failure scenarios)
- **ML:** Isolation Forest (unsupervised anomaly detection)

## Getting Started

1. Review `docs/methodology-checklist.md` for phase gates
2. Deploy Phase 1 (Sense):
   ```bash
   bash infra/scripts/deploy-phase1.sh kind      # or minikube
   bash infra/scripts/verify-stack.sh
   bash infra/scripts/collect-baseline.sh      # 30-minute fault-free baseline
   bash infra/scripts/check-baseline-quality.sh
```
3. See `docs/measurement-protocol.md` for MTTD/MTTR/SLO definitions and `docs/architecture.md` for versions
4. Implement fusion and decision engines (Phase 2)
5. Deploy the custom operator (Phase 3)
6. Run evaluation: `evaluation/scenarios/` (Phase 4)

## Research Questions

- **RQ1:** Multi-modal fusion vs unimodal monitoring for anomaly detection
- **RQ2:** Hybrid (ML + rules) vs pure black-box AI for safety and explainability
- **RQ3:** MTTR and availability improvement vs native Kubernetes recovery
