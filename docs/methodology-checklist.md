# Methodology Checklist

Phase gates and evidence requirements aligned with Chapter 3 of `IM2021014.pdf`.

## Phase 1 — Sense (Baseline Environment)

**Goal:** Isolated cluster with multi-modal observability and baseline telemetry.

- [x] Minikube/Kind cluster provisioned (`infra/cluster/`)
- [x] Google Online Boutique deployed (`infra/boutique/`)
- [x] Prometheus scraping metrics (`infra/observability/`)
- [x] Grafana Loki aggregating logs
- [x] Jaeger + OpenTelemetry capturing traces
- [x] Baseline telemetry collected under normal conditions (run `infra/scripts/collect-baseline.sh`)
- [x] Baseline MTTD/MTTR measurement procedure documented (`docs/measurement-protocol.md`)
- [ ] Quality-gated 30-minute baseline re-collected (`meta/quality.json` `"trainable": true`) — 180 s smoke `20260817T141615Z` proved RED is non-empty

**Evidence:** Trainable baseline in `evaluation/runs/baseline/<run>/` with `meta/quality.json`. Run `20260817T015611Z` is instrumentation-only (empty RED) and must not be used to train Isolation Forest.

---



## Phase 2 — Analyze (Fusion + Anomaly Detection)

**Goal:** Unified State Vector and Isolation Forest detecting gray failures.

- [ ] State vector schema defined (`fusion-engine/`)
- [ ] Prometheus, Loki, Jaeger data ingested and normalized
- [ ] Sliding-window temporal alignment implemented
- [ ] Isolation Forest trained on fault-free baseline data
- [ ] Anomaly scores exposed via API or event stream
- [ ] At least one injected fault detected in dry run

**Evidence:** Training data, model artifacts, detection logs

---



## Phase 3 — Act (Hybrid Decisions + Operator)

**Goal:** Safe, explainable remediation via Custom Kubernetes Operator.

- [ ] Rule-based playbook defined (`decision-engine/`)
- [ ] Hybrid engine: anomaly signal → rule match → action
- [ ] Custom K8s Operator deployed (`operator/`)
- [ ] Actions: pod restart, HPA scale, node eviction (as applicable)
- [ ] Safety guards: dry-run mode, cooldowns, max actions/hour
- [ ] Every action logs: trigger signals, matched rule, timestamp

**Evidence:** Remediation logs with rule IDs for RQ2 explainability

---



## Phase 4 — Validate (Chaos Engineering)

**Goal:** 12 scenarios × 10 runs × 2 conditions (Run A vs Run B).

### Scenarios


| #   | Domain      | Scenario                         |
| --- | ----------- | -------------------------------- |
| 1   | Resource    | CPU starvation (noisy neighbor)  |
| 2   | Resource    | Memory leak → OOM                |
| 3   | Resource    | Disk I/O stress                  |
| 4   | Network     | 200ms latency injection          |
| 5   | Network     | 10–20% packet loss               |
| 6   | Network     | DNS resolution failure           |
| 7   | Pod/State   | Random pod kill                  |
| 8   | Pod/State   | Node unresponsiveness            |
| 9   | Pod/State   | Stateful volume detachment       |
| 10  | Application | HTTP 5xx error injection         |
| 11  | Application | DB connection pool exhaustion    |
| 12  | Application | Port misconfig / auth revocation |




### Evaluation Gates

- [ ] All 12 Chaos Mesh manifests created (`evaluation/scenarios/`)
- [ ] Run A (framework OFF): 120 fault injections completed
- [ ] Run B (framework ON): 120 fault injections completed
- [ ] MTTD, MTTR, precision, recall, F1, availability recorded
- [ ] Statistical comparison Run A vs Run B (`evaluation/analysis/`)

**Evidence:** Raw CSV/JSON in `evaluation/runs/`, analysis outputs, thesis tables

---



## Research Question Mapping


| RQ  | Phase   | Primary Evidence                                             |
| --- | ------- | ------------------------------------------------------------ |
| RQ1 | Phase 2 | Multi-modal correlation logs, detection accuracy vs unimodal |
| RQ2 | Phase 3 | Rule-matched remediation logs, safety incident count         |
| RQ3 | Phase 4 | MTTR and availability comparison tables                      |


