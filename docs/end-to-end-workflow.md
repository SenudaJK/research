# End-to-End Workflow — Autonomous Remediation for Cloud-Native Systems

This document walks through the full pipeline built for this research project: what each
stage does, how metrics/logs/traces flow through it, and where the results currently stand.
It complements two other source documents that go deeper on specific angles:

- `docs/findings-summary-for-chart-generation.md` — quantitative results, limitations, self-assessment.
- `docs/model-comparison-summary-for-chart-generation.md` — why Isolation Forest was chosen over One-Class SVM/LOF.
- `docs/experiment-log.md` — the raw, chronological trial-by-trial record (the primary source of truth).

---

## 1. What the system does

The project evaluates whether an ML-based anomaly detector wired to a rule-based Kubernetes
operator can **detect and recover from infrastructure faults faster than Kubernetes' own
native self-healing**, on a real cluster running the Online Boutique microservices demo (12
services). It follows a MAPE-K structure: **Monitor → Analyze → Plan → Execute**, over a
**Knowledge** base (the trained model + playbook).

Two experimental conditions are run per fault scenario:

- **Run A (Legacy):** native Kubernetes self-healing only — readiness probes, restart
  policies. No ML, no operator.
- **Run B (Proposed):** the full closed loop — detect → match rule → act.

Twelve fault scenarios are in scope (S1–S12: CPU starvation, memory leak, disk I/O stress,
network latency, packet loss, DNS failure, random pod kill, node starvation, volume
detachment, HTTP 5xx errors, DB pool exhaustion, config drift), injected via Chaos Mesh
(`StressChaos`, `PodChaos`, `NetworkChaos`, `IOChaos`) or, for config drift, a manual
`kubectl patch`.

---

## 2. Pipeline stage by stage

### Stage 1 — Sense: collecting metrics, logs, and traces

`infra/scripts/collect-baseline.sh` is the single source of truth for what gets measured and
when. It port-forwards to Prometheus, Loki, and Jaeger and polls all three every 60 seconds
for the duration of a run, writing one file per sample into `metrics/`, `logs/`, and
`traces/` under `evaluation/runs/baseline/<run-id>/`:

- **Metrics (Prometheus, PromQL)** — 8 cluster-wide queries: request rate, error rate,
  average duration, frontend success rate, CPU usage, memory working set, network receive
  bytes, and pod phase, all scoped to the `boutique` namespace. A v2 extension adds two
  **per-service** queries (`memory_working_set_cartservice`,
  `network_receive_bytes_productcatalogservice`) to localize faults that the cluster-wide
  aggregates blur together (see §5).
- **Logs (Loki)** — a `query_range` pull of every log line in the `boutique` namespace over
  a trailing 120-second window per sample.
- **Traces (Jaeger)** — the `frontend` service's spans (plus, in v2, `cartservice`'s), used
  to derive average latency and error percentage.

A run's `meta/manifest.json` records the exact PromQL used, the sample cadence, and
start/end timestamps, so every downstream number is traceable back to how it was measured.
`infra/scripts/check-baseline-quality.sh` gates the run immediately afterward (sample count,
missing-feature fraction) before it's trusted as training input.

For chaos trials (as opposed to the fault-free baseline), the same three telemetry sources
are sampled during and after fault injection by `evaluation/analysis/run_trial.py`, which
additionally captures **T0** (fault start, read from the Chaos Mesh CR's own status field —
`status.instances.*.startTime` for StressChaos, `status.experiment.containerRecords[*].events[*].timestamp`
for PodChaos) so every timing metric is anchored to a real, cluster-reported fault-start
time rather than a script-side guess.

### Stage 2 — Fuse: building the State Vector

`fusion-engine/build_state_vector.py` reshapes one run's raw `metrics/`, `logs/`, `traces/`
JSON into a single tidy CSV — the **State Vector** — with one row per sample and one column
per feature:

- `cpu_util`, `mem_util` (MB), `network_rx` (KB/s) — from Prometheus.
- `log_error_rate` — error/fail/exception/5xx line matches per second, from Loki.
- `trace_latency_ms`, `trace_error_pct` — from Jaeger spans.
- v2 only: `mem_util_cartservice`, `network_rx_productcatalogservice`, `trace_error_pct_cartservice`.

It deliberately never re-queries the cluster — it only reshapes what `collect-baseline.sh`
already captured, so the State Vector is reproducible from the raw run directory alone. A
core invariant: a **failed** query produces `NaN`, never a silent `0.0` — a real "nothing bad
happened" zero must stay distinguishable from "we couldn't measure this sample," and each
row's `missing_features` column records which (if any) columns are unreliable. Rows are
rejected at training time (not silently imputed) if too large a fraction of the run has gaps
(`--max-missing-fraction`, default 20%).

### Stage 3 — Analyze (train): the anomaly detector

`decision-engine/train_and_compare.py` trains on a fault-free baseline's State Vector.

- **Model:** Isolation Forest, trained on the 6 v1 features (v2 model adds the 3 per-service
  features — see `decision-engine/model-config-v2.yaml`).
- **Threshold (τ):** the 95th percentile of the model's own anomaly scores on the training
  data (`decision-engine/model-config.yaml`, `threshold_percentile: 95`). This was lowered
  from the initial 99th percentile after p99 failed to catch a real, strongly-deviated
  fault in early testing — p99 sat too close to the training score ceiling for a
  single-anomalous-dimension case (`docs/experiment-log.md`, 2026-09-07).
- **Minimum trainable size:** 200 samples — set after a 30-sample baseline produced
  unusably shallow trees.
- **Model selection:** Isolation Forest was compared against One-Class SVM and LOF on a
  513-row labeled set (197 real clean rows + 316 real fault-window rows from 121 valid
  trials across S1/S2/S4/S7 — no synthetic data). All three were scored the same way (each
  model's own 95th-percentile training score as its cutoff) after an earlier, unfair
  comparison (each model's own internal `contamination`/`nu` cutoff) made Isolation Forest
  look far worse (F1 0.243) than it really is (F1 0.915, once fixed). On the corrected
  numbers, One-Class SVM and LOF actually edge out Isolation Forest on F1 (0.984 vs 0.915,
  driven by recall: 1.000 vs 0.870) — Isolation Forest was still selected for
  **architectural fit** (O(depth) inference independent of training-set size, workable at
  the modest baseline sizes this cluster can realistically produce), with its recall gap
  treated as a known, disclosed tradeoff compensated for by the retry logic in Stage 4. Full
  writeup: `docs/model-comparison-summary-for-chart-generation.md`.

Output: `scaler.pkl`, `isolation_forest.pkl`, `threshold.json` (includes `tau` and the
feature list) under `evaluation/runs/baseline/<run-id>/model-artifacts/`.

### Stage 4 — Analyze + Plan: scoring and rule matching

`decision-engine/orchestrator.py` (Run B only) scores each new State Vector row against the
frozen model:

1. Compute `anomaly_score = -iforest.score_samples(...)`. If `score <= tau`, nothing happens.
2. If anomalous, compute each feature's z-score against the training baseline's mean/std,
   and check every rule in `decision-engine/playbook.yaml` (`R1`–`R12`, one per scenario) —
   a rule matches if **its own** `trigger_feature`'s `|z| > rule_match_z_threshold` (3.0),
   not just whichever feature happens to be most deviated overall. Among multiple qualifying
   rules, the largest `|z|` wins.
3. On a match, build a `RemediationAction` custom resource (target, action, rule ID, the
   anomaly score, tau, and a human-readable `explanation` string for RQ2 explainability) and
   create it via the Kubernetes API.

This "retry on later samples, and match on each rule's own feature rather than the single
top feature" design exists because of two bugs found and fixed mid-project (see §5) — an
earlier version could permanently miss a real, strongly-triggering fault because a
coincidental, rule-less feature spiked marginally higher on the very first anomalous tick.

### Stage 5 — Execute: the operator

`operator/handlers.py` is a `kopf`-based Kubernetes operator watching for
`RemediationAction` creation. For each CR it:

1. Checks **cooldown** (default 300s) and **rate limit** (default 6/hour), tracked as
   annotations on the target object itself (Deployment, or Node for R10) — this survives
   operator restarts without an external database.
2. If not blocked, and not `dryRun`, executes the action against the Kubernetes API:
   - `scale` — patch replica count (R1, S1).
   - `restart` — rollout-restart via a `restartedAt` pod-template annotation (R2, S7).
   - `evict` — delete Deployment pods (R7, S3) or cordon+evict a Node's pods (R10, S8).
   - `reconcile` — `kubectl apply -k infra/boutique/` to revert config drift (R12, S12).
3. Logs one structured JSON line per decision (trigger signals, matched rule, outcome) to
   stdout, and writes the same fields to the CR's own `.status`, so
   `kubectl get remediationaction -o yaml` is a self-contained explainability record.

### Stage 6 — Measure: trials and metrics

`evaluation/analysis/run_trial.py` runs one full Run A or Run B trial end-to-end: applies
the fault, captures T0, polls telemetry every 60s, detects `Td` (Run A: native signals —
pod-not-ready / restart-count-increase; Run B: `anomaly_score > tau`), detects recovery `Tr`
(two consecutive samples of SLO-holding `frontend_success_rate`), and restores replica
counts afterward. Per-trial outputs land in `evaluation/runs/trials/*.json`.

Four metrics are computed per trial:

- **MTTD** (`Td − T0`) — mean time to detect.
- **MTTR** (`Tr − T0`) — mean time to recovery.
- **Availability** — computed from real `boutique_traces_span_metrics_calls_total` span
  counts over `[T0, max(Tr, Te)]` (not approximated from sampled success-rate).
- **Detection outcome** — TP/FP/FN/TN, with a trial marked **censored** if the 600s (or
  300s, depending on scenario) timeout is hit before detection/recovery.

`evaluation/analysis/academic_analyzer.py` aggregates all valid, non-censored trials into
the headline comparison table (§4), including MTTD/MTTR reduction percentages.

---

## 3. Data flow at a glance

```
Chaos Mesh (fault injection)
        │
        ▼
Prometheus / Loki / Jaeger  ──(collect-baseline.sh, 60s cadence)──▶  metrics/ logs/ traces/ (raw JSON)
        │                                                                    │
        │                                                        build_state_vector.py (fusion-engine)
        │                                                                    ▼
        │                                                          state_vector.csv
        │                                                     ┌──────────────┴──────────────┐
        │                                                     ▼                              ▼
        │                                         train_and_compare.py              orchestrator.py (Run B only)
        │                                         (Isolation Forest,                  scores row vs τ, matches
        │                                          scaler, τ)                         playbook.yaml rule by z-score
        │                                                                                     │
        │                                                                                     ▼
        │                                                                       RemediationAction CR created
        │                                                                                     │
        │                                                                                     ▼
        │                                                                       operator/handlers.py executes
        │                                                                       (scale/restart/evict/reconcile)
        ▼                                                                                     │
run_trial.py samples frontend_success_rate for Tr  ◀────────────────────────────────────────┘
        │
        ▼
academic_analyzer.py aggregates MTTD/MTTR/Availability across trials
```

---

## 4. Headline results (as of the last aggregation)

Computed by `evaluation/analysis/academic_analyzer.py` over all valid, non-censored trials
for the four scenarios with validated playbook rules:

| Scenario | Condition | N | Mean MTTD (s) | Mean MTTR (s) | Std MTTR | Success Rate |
|---|---|---|---|---|---|---|
| S1 — CPU Starvation | Legacy | 32 | 564.79 | 66.61 | 0.62 | 100% |
| S1 — CPU Starvation | Proposed | 27 | **8.73** | 71.09 | 23.18 | 100% |
| S2 — Memory Leak | Legacy | 12 | 21.26 | 314.93 | 135.97 | 91.7% |
| S2 — Memory Leak | Proposed | 10 | **12.10** | **240.10** | 73.82 | 100% |
| S4 — Network Latency | Legacy | 10 | 600.00 (censored) | 72.18 | 0.39 | 100% |
| S4 — Network Latency | Proposed | 10 | **32.41** | 98.41 | 64.44 | 100% |
| S7 — Random Pod Kill | Legacy | 10 | 482.48 | 84.29 | 38.03 | 100% |
| S7 — Random Pod Kill | Proposed | 10 | **43.07** | 109.09 | 82.39 | 100% |

**Global aggregates:** MTTD 455.52s → 19.50s (**95.72% reduction**). MTTR 116.80s →
112.20s (**3.94% reduction** — small relative to variance, and *not* a clean win: S4 and S7
have a higher mean MTTR under the proposed condition).

**The honest headline:** this is a **detection-speed** result, not (yet) a proven
**recovery-speed** result. MTTR is roughly flat, and in two of four scenarios slightly worse
on average, driven by variance rather than the operator being slower.

---

## 5. What broke, and what it taught us

Two categories of finding came out of running this on a real cluster rather than a
simulation — both are treated as first-class results, not just bugs to fix quietly.

**Architectural limitations of single-feature, magnitude-only rule matching:**

- **Signal dilution (S2, `mem_util`):** the aggregate memory feature sums usage across all
  12 services; a single service's small-limit leak (cartservice, 256Mi) is swamped by the
  ~2.1–2.4 GiB cluster-wide baseline and gets OOMKilled faster than the 60s cadence can
  catch a sustained reading. The fault still "recovers," but via an unrelated rule
  (`log_error_rate` → restart frontend) — a coincidental side effect, not a real fix.
- **Rule collision (S9 vs S11):** `network_rx` is a valid signal for S11, but
  `trace_error_pct` (intended for S9) reliably outmagnitudes it whenever both are elevated
  simultaneously (which they are, since S11's fault also raises request errors via
  timeouts). The largest-|z| tie-break always picks the wrong rule, producing a fully
  executed but completely ineffective remediation.

Both are diagnosed, not yet fixed-with-evidence: a **v2 per-service-feature model**
(`decision-engine/model-config-v2.yaml`, `decision-engine/playbook-v2.yaml`) was built
specifically to disambiguate these cases, and passes synthetic-input verification, but has
not yet been trained and run against a real trial.

**Two genuine measurement-harness bugs found and fixed mid-campaign** (both would otherwise
silently look like detection failures):

1. An unbounded Kubernetes API call in `orchestrator.py`'s CR-creation step could stall an
   entire trial's remaining time budget on a transient hiccup, producing a false-censored
   `MTTD=600s`. Fixed with a 15s request timeout and retry.
2. Rule matching originally only considered the single most-deviated feature at the first
   anomalous sample and never retried — so a real, strongly-triggering feature (e.g.
   `cpu_util` z=22) could be missed because a coincidental, rule-less feature
   (`log_error_rate`) was marginally larger on the very first tick.

**Infrastructure-level chaos-injection failures** are a separate, orthogonal source of
invalid data: an S9 (volume detachment) trial failed at the Chaos Mesh `IOChaos`/`toda`
layer itself (never actually injected the fault), and got stuck retrying its own teardown
until a finalizer was manually cleared.

**Sampling-cadence artifacts** can make MTTR look identical between conditions for reasons
that have nothing to do with the operator: S1's CPU stress hurt backend latency without
tripping the frontend SLO at this load level, and S7's pod-kill self-heals via Kubernetes'
own pod replacement well inside the 60s sample cadence, in *either* condition.

---

## 6. Current status / what's left

- **4 of 12 scenarios validated** (S1, S2, S4, S7) with working playbook rules and real
  trial data; **6 scenarios (S3, S5, S6, S8, S10, S12)** have rules added (2026-09-15) but
  zero trials run — several are predicted to reproduce the rule-collision pattern since they
  share trigger features with earlier rules. **S9 and S11** have rules but no valid trial
  yet (infrastructure failure / rule collision respectively).
- **Sample sizes are uneven**: S1 is well-powered (32A/27B); S4 and S7 are short of the
  N=10 target for Run B (7 each, 3 more needed); S2 sits at 12A/10B.
- **The v2 per-service-feature model is designed and synthetically verified but has no
  real-cluster evidence** — next step is a fresh ≥200-sample baseline collected with the
  extended `collect-baseline.sh`, a new model trained via `train_and_compare.py --config
  decision-engine/model-config-v2.yaml`, and real S2/S9/S11 Run B trials under it.
- **The MTTR-improvement claim (RQ3) is not yet established** — this is the single biggest
  gap between where the evidence stands and the project's own central hypothesis, and is
  flagged explicitly rather than glossed over in every results document.

Overall assessment (from `docs/findings-summary-for-chart-generation.md` §6): strong,
methodologically rigorous preliminary results — including a real, useful taxonomy of why
threshold/rule-based autonomous remediation breaks — but roughly 60–65% complete against
the project's own stated hypotheses, with a clear, scoped list of what the next round of
trials needs to cover.
