# Supervisor Briefing — What's Built, How It Works, What's Validated, What's Not

**Purpose:** a single document to walk your supervisor through the project: what each
script does and how it achieves its role, how the 12-scenario/240-trial evaluation plan is
tracked and validated, what MTTD/MTTR results exist today, how to read the charts, and — most
importantly — a clear, defensible statement of what is done vs. still open. Nothing here needs
to be "finished" to present; the goal is that you can explain *exactly* where the line between
done and not-done sits, and why.

This is a synthesis of three deeper source documents already in the repo — read them if a
question goes further than this briefing:
- `docs/end-to-end-workflow.md` — full pipeline walkthrough with a data-flow diagram.
- `docs/experiment-log.md` — the raw, dated, trial-by-trial lab notebook (source of truth).
- `docs/findings-summary-for-chart-generation.md` — results tables + limitations, chart-ready.
- `docs/model-comparison-summary-for-chart-generation.md` — Isolation Forest vs SVM vs LOF.

---

## 1. The one-paragraph pitch

The system watches a running Kubernetes application (Online Boutique, 12 microservices) via
metrics/logs/traces, uses an ML anomaly detector (Isolation Forest) to notice when something
is wrong, matches the anomaly to a rule in a playbook, and has a custom Kubernetes operator
carry out the fix (scale, restart, evict, or reconcile) automatically — no human in the loop.
It's compared against Kubernetes' own built-in self-healing (readiness probes, restart
policies) on the same injected faults, using Chaos Mesh to break things on purpose. This
follows the standard **MAPE-K** structure (Monitor → Analyze → Plan → Execute, over a
Knowledge base = the trained model + playbook).

---

## 2. Script-by-script: what each one does and how

Ordered by pipeline stage, matching the diagram in `docs/end-to-end-workflow.md` §3.

### Stage 1 — Sense (collect telemetry)

**`infra/scripts/collect-baseline.sh`**
- **Role:** the single place that defines *what* gets measured and *how often*, for both the
  training baseline and every trial.
- **How:** port-forwards to Prometheus/Loki/Jaeger and polls all three every 60 seconds. Runs
  8 cluster-wide PromQL queries (request rate, error rate, latency, frontend success rate,
  CPU, memory, network, pod phase) plus, in the v2 extension, 2 per-service queries. Pulls
  Loki log lines for the namespace over a trailing 120s window, and Jaeger spans for
  `frontend` (+ `cartservice` in v2) to derive latency/error stats. Writes one raw JSON file
  per sample into `metrics/`, `logs/`, `traces/`, and a `meta/manifest.json` recording the
  exact queries and timestamps used — so every later number is traceable back to how it was
  captured.
- **`infra/scripts/check-baseline-quality.sh`** gates the output immediately after: checks
  sample count and missing-feature fraction before a baseline is trusted for training.

### Stage 2 — Fuse (build the "State Vector")

**`fusion-engine/build_state_vector.py`**
- **Role:** turn the three raw telemetry streams into one tidy table — one row per sample,
  one column per feature — that everything downstream (training, scoring, labeling) reads.
- **How:** reshapes `metrics/`, `logs/`, `traces/` JSON from a single run directory into a
  CSV: `cpu_util`, `mem_util`, `network_rx` (Prometheus), `log_error_rate` (Loki
  error/exception/5xx line rate), `trace_latency_ms`, `trace_error_pct` (Jaeger). It never
  re-queries the cluster, so a State Vector is always reproducible from the raw run alone. A
  deliberate design choice: a failed query writes `NaN`, never a silent `0.0` — a real "all
  clear" zero must stay distinguishable from "we couldn't measure this." A `missing_features`
  column flags unreliable rows per-sample, and rows are *rejected* (not imputed) at training
  time if too much of a run has gaps (`--max-missing-fraction`, default 20%).

### Stage 3 — Analyze / train (the anomaly detector)

**`decision-engine/train_and_compare.py`**
- **Role:** trains the production anomaly detector on a fault-free baseline, and separately
  runs the fair 3-model comparison (Isolation Forest vs One-Class SVM vs LOF) used to justify
  the model choice.
- **How:** fits Isolation Forest on the 6 (v1) or 9 (v2) baseline features, sets the decision
  threshold **τ (tau)** to the 95th percentile of the model's own anomaly scores on the
  training data itself (`decision-engine/model-config.yaml`), and writes `scaler.pkl`,
  `isolation_forest.pkl`, `threshold.json` to `evaluation/runs/baseline/<run-id>/model-artifacts/`.
  When run with `--validation-csv`, it scores all three candidate models the same way (each at
  its own 95th-percentile training cutoff) against a labeled set, producing the Precision/
  Recall/F1/latency table in §5 below.
- **Two config files** (`decision-engine/model-config.yaml` for v1 / 6 features,
  `decision-engine/model-config-v2.yaml` for v2 / 9 features) let the same script train either
  model without code changes — v2 adds 3 per-service features aimed at fixing two known
  failure modes (§6).

**`evaluation/analysis/build_labeled_validation_csv.py`** *(new, supports the above)*
- **Role:** assembles the ground-truth labeled dataset used for the model comparison, from
  **real measured data only** — no synthetic rows.
- **How:** label=0 rows come straight from the frozen model's own training baseline CSV;
  label=1 rows are every sample timestamped at-or-after each trial's real fault-start (`t0`),
  pulled from `evaluation/runs/trials/*.json`, skipping anything not marked `trial_valid`.
  Output: `evaluation/runs/validation_labeled.csv` (513 rows: 197 normal + 316 anomaly, from
  121 real trials across S1/S2/S4/S7).

### Stage 4 — Analyze + Plan (score and match a rule)

**`decision-engine/orchestrator.py`**
- **Role:** the live decision-maker for "Run B" (the proposed/ML condition) — turns a scored
  telemetry row into a real Kubernetes action.
- **How:** for each new State Vector row, computes `anomaly_score = -iforest.score_samples(...)`.
  If the score is at or below τ, it does nothing. If anomalous, it z-scores every feature
  against the training baseline's mean/std and checks each rule in
  `decision-engine/playbook.yaml` — a rule only qualifies if **its own designated
  `trigger_feature`** exceeds `|z| > 3.0` (not just whichever feature is most deviated
  overall); among qualifying rules, the largest `|z|` wins. On a match it creates a
  `RemediationAction` custom resource (Kubernetes CRD) recording the target, action, rule ID,
  score, τ, and a human-readable explanation string — this CR *is* the explainability record
  for RQ2 ("can the system justify its own decisions").
- This retry-on-later-samples, match-on-your-own-feature design exists specifically because of
  two real bugs found mid-project (§7) — worth mentioning to your supervisor as an example of
  iterative, evidence-driven engineering rather than a first-try design.

**`decision-engine/playbook.yaml` / `decision-engine/playbook-v2.yaml`**
- **Role:** not code but the actual "knowledge base" — a human-editable table of *IF this
  feature is this deviated, THEN do this action to this service*, one rule per scenario
  (`R1`–`R12`).
- **How it's structured:** each rule names a `trigger_feature`, a `target` service/kind, and
  an `action` (`scale` / `restart` / `evict` / `reconcile`). The v2 file swaps in the 3 new
  per-service features for the 3 rules known to be broken under v1 (§6).

**`decision-engine/score.py`** — a thin CLI wrapper used for detection-only dry runs (scores a
State Vector against a frozen model and prints anomaly scores vs τ), used early on to validate
the threshold before wiring up the full actuation loop.

### Stage 5 — Execute (the operator)

**`operator/handlers.py`**
- **Role:** the only thing in the system allowed to actually touch the cluster on the
  system's own initiative — a `kopf`-based Kubernetes operator watching for new
  `RemediationAction` objects.
- **How:** for each CR, first checks **cooldown** (default 300s) and **rate limit** (default
  6/hour), both tracked as annotations directly on the target object — this means the safety
  state survives an operator restart with no external database needed. If not blocked, it
  executes: `scale` (patch replica count), `restart` (rollout-restart via a pod-template
  annotation), `evict` (delete pods, or cordon+evict a whole Node), `reconcile` (`kubectl
  apply -k` to snap config back to the committed manifest). Every decision is logged as one
  structured JSON line to stdout *and* written back into the CR's own `.status`, so `kubectl
  get remediationaction -o yaml` alone is a self-contained audit trail — no separate log
  system needed to explain a decision after the fact.
- **`operator/crds/remediationaction-crd.yaml`** — the schema definition for the
  `RemediationAction` custom resource type itself (what fields are valid, e.g. which `action`
  and `targetRef.kind` values are accepted).

### Stage 6 — Measure (run a trial, aggregate results)

**`evaluation/analysis/run_trial.py`**
- **Role:** runs one complete, timed experiment — apply a fault, watch it, tear it down,
  record everything needed to compute the metrics.
- **How:** applies the scenario's Chaos Mesh fault (or a manual `kubectl patch` for config
  drift), captures **T0** from the fault's own cluster-reported start time (not a script-side
  guess), polls telemetry every 60s, detects **Td** (Run A: native signals — pod-not-ready or
  restart-count increase; Run B: `anomaly_score > τ`), detects recovery **Tr** (two
  consecutive samples holding the frontend SLO), computes **availability** directly from real
  span-count data, and restores any replica counts it changed afterward. One JSON file per
  trial lands in `evaluation/runs/trials/`.

**`infra/scripts/run-campaign.sh`** *(new)*
- **Role:** runs a whole batch of trials (multiple scenarios × conditions × iterations)
  unattended instead of invoking `run_trial.py` by hand dozens of times.
- **How:** loops over the requested scenario/condition/iteration combinations, and — this is
  the actual reason it exists — defensively re-implements two lessons paid for in real failed
  trials (§7): it sleeps `cooldown + buffer` seconds between trials so a new
  `RemediationAction` never lands inside the previous one's cooldown window, and it refuses to
  start the next trial while any `RemediationAction` is still pending/unprocessed in the
  cluster. It does **not** auto-retry failed trials or decide validity for you — every raw
  result still lands in `evaluation/runs/trials/` for you to check against the experiment log.

**`evaluation/analysis/academic_analyzer.py`**
- **Role:** turns a pile of individual trial JSON files into the headline comparison table
  (mean MTTD/MTTR/availability by scenario and condition) and the MTTD/MTTR reduction
  percentages quoted in §5.
- **How:** aggregates only trials marked valid and non-censored, groups by scenario ×
  condition, computes mean/std, and the Legacy→Proposed percentage reduction per metric.

---

## 3. What "240 scenarios" actually means, and how to validate it

There's no single "240" number in the code — it's the **target size of the full evaluation
campaign**, defined in `docs/methodology-checklist.md` Phase 4:

```
12 fault scenarios × 2 conditions (Run A / Run B) × 10 trials each = 240 total trial runs
```

The 12 scenarios (S1–S12) are fixed in advance (CPU starvation, memory leak, disk I/O stress,
network latency, packet loss, DNS failure, random pod kill, node unresponsiveness, volume
detachment, HTTP 5xx, DB pool exhaustion, config drift) — one YAML manifest each under
`evaluation/scenarios/`, aligned to Table I of the dissertation reference document.

**How to validate/track progress against this target — three checks, in order:**

1. **Does the scenario have a working playbook rule at all?**
   Check `decision-engine/playbook.yaml` for a rule whose `trigger_feature` is a real,
   non-colliding signal for that fault (see §6 for two ways this can silently fail even when
   a rule "exists"). Cross-reference `docs/experiment-log.md`'s "Current validated-working
   rule count" note.

2. **Has a real trial actually been run and marked valid?**
   Every trial's raw JSON lives in `evaluation/runs/trials/`; anything moved to
   `evaluation/runs/trials/invalid/` was thrown out for a documented reason (infra failure,
   wrong rule fired, single-sample false-censor bug, etc. — always logged in
   `docs/experiment-log.md` with the root cause). Never treat file *presence* in
   `evaluation/runs/trials/` as validity — check `trial_valid` in the JSON and cross-reference
   the experiment log, since a mechanically "successful" trial can still be scientifically
   invalid (e.g. the S11 trial where the wrong rule fired).

3. **Is the sample count adequate (N=10 per scenario × condition)?**
   Run `evaluation/analysis/academic_analyzer.py` to get current per-scenario/condition N —
   this is the actual "how many of the 240 are done" answer.

**Current tally against the 240 target (as of the last aggregation, `docs/experiment-log.md` /
`docs/end-to-end-workflow.md` §6):**

| Scenario | Rule status | Run A N | Run B N | Verdict |
|---|---|---|---|---|
| S1 CPU starvation | Working (R1) | 32 | 27 | ✅ validated, over-powered |
| S2 Memory leak | Rule exists but structurally dead (R3) | 12 | 10 | ⚠️ "recovers" via the wrong rule (R2) — see §6 |
| S4 Network latency | Working (R4) | 10 | 7 | ✅ validated, 3 short of N=10 |
| S7 Random pod kill | Working (R2) | 10 | 7 | ✅ validated, 3 short of N=10 |
| S3, S5, S6, S8, S10, S12 | Rules added 2026-09-15, `UNVALIDATED` | 0 | 0 | ⬜ not started |
| S9 Volume detachment | Rule exists, colliding (R5) | 0 valid | 0 valid | ❌ blocked (infra fault-injection failure + rule collision) |
| S11 DB pool exhaustion | Rule exists, colliding (R6) | 0 valid | 0 valid | ❌ blocked (rule collision, real trial discarded) |

**Bottom line to tell your supervisor:** 4 of 12 scenarios (S1/S2/S4/S7) have real trial data;
of those, only S1/S4/S7 have a rule that is doing the *right* thing (S2's fix is a
coincidental side effect, not the intended repair). 8 of 12 scenarios have zero valid Run
B evidence. Against the 240-trial target, the realistic completed-and-trustworthy count is
roughly **91 trials** (32+27+10+7+10+7, excluding the invalid S2 pre-fix batch and S9/S11
discards) — **about 38%** of the planned campaign, concentrated in 3 well-understood
scenarios rather than spread thin across all 12.

---

## 4. It's fine that it isn't finished — here's how to frame that

Your supervisor already said incompleteness is acceptable; what you need is precision about
*which* parts are done, which are designed-but-unproven, and which haven't started. Use this
three-tier framing directly:

- **Done and evidenced:** detection pipeline (Sense→Fuse→Analyze) works end-to-end and
  generalizes across S1/S2/S4/S7 with consistent ~6–45s MTTD; the safe-actuation loop (Plan→
  Execute, cooldown/rate-limit/CR-audit-trail) is proven live for S1 (scale) and S7 (restart);
  the model-selection comparison (Isolation Forest vs SVM vs LOF) is complete and
  methodologically defensible.
- **Designed and synthetically verified, but no real-cluster evidence yet:** the v2
  per-service-feature model and playbook, meant to fix the two known rule-matching failure
  modes (§6) — code exists, passes hand-constructed test inputs, has never been trained on a
  real baseline or run against a live fault.
- **Not started:** 6 of 12 scenarios have never had a single trial; N-top-ups needed for S4/S7;
  S9 is blocked on an infrastructure bug independent of the ML/operator system being evaluated.

## 5. MTTD/MTTR results — and how to explain the charts

Computed by `academic_analyzer.py` over all valid, non-censored trials (S1/S2/S4/S7 only):

| Scenario | Condition | N | Mean MTTD (s) | Mean MTTR (s) | Std MTTR | Success Rate |
|---|---|---|---|---|---|---|
| S1 CPU Starvation | Legacy | 32 | 564.79 | 66.61 | 0.62 | 100% |
| S1 CPU Starvation | Proposed | 27 | **8.73** | 71.09 | 23.18 | 100% |
| S2 Memory Leak | Legacy | 12 | 21.26 | 314.93 | 135.97 | 91.7% |
| S2 Memory Leak | Proposed | 10 | **12.10** | **240.10** | 73.82 | 100% |
| S4 Network Latency | Legacy | 10 | 600.00 (censored) | 72.18 | 0.39 | 100% |
| S4 Network Latency | Proposed | 10 | **32.41** | 98.41 | 64.44 | 100% |
| S7 Random Pod Kill | Legacy | 10 | 482.48 | 84.29 | 38.03 | 100% |
| S7 Random Pod Kill | Proposed | 10 | **43.07** | 109.09 | 82.39 | 100% |

**Global aggregates:** MTTD 455.52s → 19.50s (**95.72% reduction**). MTTR 116.80s → 112.20s
(**3.94% reduction**).

**How to explain each chart type you'd build from this table:**

1. **Grouped bar chart, MTTD by scenario (Legacy vs Proposed), log scale.**
   Say: "Detection speed improves dramatically and consistently — the proposed system detects
   in single-digit-to-tens of seconds where legacy detection either takes minutes or times out
   entirely (S4's 600s value *is* the timeout being hit, not a real measured 600 seconds — flag
   this explicitly, it's censored data, not a slow-but-real detection)." Log scale is needed
   because S1/S4 legacy values sit near the 600s censor ceiling while proposed values are under
   50s — a linear axis would visually flatten the proposed bars to nothing.

2. **Grouped bar chart, MTTR by scenario, with error bars (std dev).**
   Say: "This is the honest chart — recovery time is roughly flat between conditions, and in
   S4/S7 the proposed condition's *mean* is actually a little higher. The error bars are the
   point: MTTR variance (23–82s) is large relative to the difference between conditions, so
   there isn't yet a statistically meaningful recovery-time win, even though detection is
   clearly faster." Don't let this chart get quietly dropped from a presentation — showing it
   is what makes the MTTD win credible rather than cherry-picked.

3. **Single summary chart, global MTTD reduction (95.72%) vs global MTTR reduction (3.94%).**
   Say: "The contrast between these two bars *is* the finding: this system is proven to make
   you notice problems faster; it is not yet proven to make you fix them faster. Those are
   different claims, and only the first one has strong evidence right now."

4. **Sample-size (N) bar chart per scenario/condition.**
   Say: "S1 has 3x the trials of S4/S7 — treat S1's numbers as the most statistically solid,
   and S4/S7's as indicative but not yet fully powered."

5. **Model comparison chart (Precision/Recall/F1, Isolation Forest vs SVM vs LOF)** — from
   `docs/model-comparison-summary-for-chart-generation.md`:

   | Algorithm | Precision | Recall | F1 | Train Time (s) | Inference (µs) |
   |---|---|---|---|---|---|
   | Isolation Forest | 0.965 | 0.870 | 0.915 | 0.042 | 5.82 |
   | One-Class SVM | 0.969 | 1.000 | 0.984 | 0.001 | 0.92 |
   | LOF | 0.969 | 1.000 | 0.984 | 0.002 | 2.42 |

   Say: "SVM and LOF actually score slightly better on F1 — we picked Isolation Forest anyway,
   for architectural fit (inference cost stays flat as the training window grows, which
   matters for something scoring live telemetry indefinitely) and because its lower recall is
   a known, disclosed tradeoff that the rule-matcher's retry logic is specifically designed to
   absorb." If asked why the numbers changed from an earlier version: an earlier comparison
   scored each model at its own *internal* default cutoff (each model's own
   `contamination`/`nu` parameter), which isn't a fair apples-to-apples comparison; fixing it
   to score all three models the same way (each at its own 95th-percentile training score, the
   same convention already used in production) raised Isolation Forest's F1 from 0.243 to
   0.915. This is worth mentioning proactively — catching and fixing your own methodology bug
   is a stronger story than not having found it.

---

## 6. The two real architectural limitations (present these as findings, not failures)

Both were discovered by running on a real cluster, not simulation, and are documented as
first-class results:

**Signal dilution (S2 — memory leak).** The `mem_util` feature sums memory across all 12
services. cartservice's leak (256Mi limit) is a small fraction of the ~2.1–2.4 GiB
cluster-wide baseline and gets OOMKilled faster than the 60s sample cadence can catch a
sustained reading — the feature never carries the fault's signal, at any threshold. The system
still "recovers" S2 in the data, but via an unrelated rule (`log_error_rate` → restart
frontend), not by fixing cartservice. **This is not a threshold-tuning bug — the aggregate
feature structurally cannot see a small-limit single-service fault.**

**Rule collision (S9 vs S11).** `network_rx` is a genuinely valid signal for S11 (DB pool
exhaustion), but `trace_error_pct` (meant for S9) reliably outweighs it whenever both spike
together — which happens here because S11's fault also raises request errors via timeouts.
The "largest z-score wins" tie-break always picks the wrong rule, producing a fully-executed
but completely ineffective remediation (confirmed in a real discarded trial: `network_rx`
z≈-5 to -6, `trace_error_pct` z up to 16.11 at the same samples — the wrong rule wins every
time). **This is a structural limitation of single-feature, magnitude-only matching, not a
tuning problem.**

Both are addressed **in design only** by the v2 per-service-feature model
(`decision-engine/model-config-v2.yaml`, `decision-engine/playbook-v2.yaml`) — passes
synthetic-input tests, has never been trained on a real baseline or proven against a live
fault. This is the clearest, most honest "here's exactly what's left" item to show your
supervisor.

---

## 7. Two real measurement-harness bugs found and fixed (worth mentioning — shows rigor)

1. **Unbounded API call → false-censored trials.** `orchestrator.py`'s CR-creation call had no
   timeout; a transient API hiccup could silently eat an entire trial's time budget, looking
   identical to a genuine detection failure (`MTTD=600s`). Fixed with a 15s timeout + retry.
2. **Rule-matching only looked at the first anomalous sample's single most-deviated feature,
   with no retry.** A real, strongly-triggering feature (e.g. `cpu_util` z=22, one sample
   later) could be permanently missed because an unrelated, rule-less feature happened to be
   marginally larger on the very first tick. Fixed by matching each rule against *its own*
   trigger feature and retrying on later samples.

Separately, an **infrastructure-level** failure (Chaos Mesh's `IOChaos`/`toda` fault injector
never actually injecting for S9) is a different class of problem — it's orthogonal to the
ML/operator system being evaluated, but it's the reason S9 has zero valid data.

---

## 8. What to say if asked "what's left before this is a complete evaluation?"

In priority order:
1. Collect a fresh ≥200-sample baseline, train the v2 model, and run real S2/S9/S11 trials
   under it — this is the single highest-value next step, since it tests whether the v2
   design actually fixes the two documented failure modes rather than just being untested code.
2. Top up S4-B and S7-B to N=10 (3 more trials each).
3. Run first-ever trials for the 6 untouched scenarios (S3/S5/S6/S8/S10/S12) — several are
   *predicted* to reproduce the rule-collision pattern, since their features are already
   claimed by earlier rules; that prediction is itself worth testing.
4. Fix the Chaos Mesh `IOChaos` infrastructure issue blocking S9.
5. Establish the MTTR-improvement claim (RQ3) properly — right now it's the one central
   hypothesis without a clean supporting result, and it needs either a fault type where the
   frontend SLO is genuinely and sustainedly broken (not resolved inside one 60s sample), or
   an acknowledgment that MTTR parity itself is a valid, disclosed finding for fast-resolving
   faults at this scale.
