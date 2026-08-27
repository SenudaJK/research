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
- [ ] Quality-gated, statistically-adequate baseline collected (`meta/quality.json` `"trainable": true`, samples >= `MIN_TRAINABLE_SAMPLES`) — 180 s smoke `20260817T141615Z` proved RED is non-empty; `20260827T012747Z` (30 samples) proved the pipeline works end-to-end but was too small to freeze a usable tau

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


Target services and descriptions are aligned to Table I of "Final
Disseration for ICTAC.md" (the fixed reference point — see
docs/experiment-log.md for any deviations required by what Online Boutique
actually has available, e.g. no real DB behind payment/product-catalog).

| #   | Domain      | Scenario                                    | Target service         |
| --- | ----------- | -------------------------------------------- | ----------------------- |
| 1   | Resource    | CPU starvation (noisy neighbor)             | checkoutservice         |
| 2   | Resource    | Memory leak → OOM                            | cartservice             |
| 3   | Resource    | Disk I/O stress                              | paymentservice*         |
| 4   | Network     | 200ms latency injection                      | recommendationservice   |
| 5   | Network     | 10% packet loss                              | shippingservice         |
| 6   | Network     | DNS resolution failure                       | frontend (catalog DNS)  |
| 7   | Pod/State   | Random pod kill                              | frontend                |
| 8   | Pod/State   | Node unresponsiveness                        | (node-scoped)           |
| 9   | Pod/State   | Stateful volume detachment                   | redis-cart              |
| 10  | Application | HTTP 5xx error injection                     | frontend                |
| 11  | Application | DB connection pool exhaustion                | productcatalogservice*  |
| 12  | Application | Config drift (manual env var modification)   | checkoutservice         |

\* No real database exists behind this service in Online Boutique — see the
substitution note in the corresponding `evaluation/scenarios/*.yaml` file.




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


