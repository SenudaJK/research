# Research Findings Summary — Autonomous Remediation for Cloud-Native Systems

**Purpose of this document:** A self-contained summary of quantitative results, methodological findings, and limitations from a chaos-engineering research project evaluating an ML-based anomaly detection + rule-based autonomous remediation pipeline against a manual/legacy baseline, on a Kubernetes deployment of the Online Boutique microservices demo. Intended as source material for generating charts and a narrative summary.

---

## 1. System Under Test

- **Target application:** Online Boutique (12-microservice demo app) on Kubernetes.
- **Fault injection:** Chaos Mesh (StressChaos, PodChaos, NetworkChaos, IOChaos, manual `kubectl patch` for config drift).
- **Detection model:** Isolation Forest anomaly detector trained on a 6-feature cluster-wide state vector (`cpu_util`, `mem_util`, `network_rx`, `log_error_rate`, `trace_latency_ms`, `trace_error_pct`), threshold τ set at the 95th percentile of training scores.
- **Remediation:** A playbook of rules (`decision-engine/playbook.yaml`) mapping a triggering feature + z-score threshold to a Kubernetes-level action (`scale`, `restart`, `evict`, `reconcile`) executed by a custom Kubernetes operator via a `RemediationAction` CRD.
- **Two experimental conditions per scenario:**
  - **Run A (Legacy):** native Kubernetes self-healing only (readiness probes, restart policies, no ML/operator involvement).
  - **Run B (Proposed):** the full detect → match rule → act closed loop.
- **Metrics:** MTTD (Mean Time to Detect), MTTR (Mean Time to Recovery), Availability (span-count-based success ratio over the fault window), Detection outcome (TP/FP/FN/TN).

---

## 2. Headline Quantitative Results

Computed by `evaluation/analysis/academic_analyzer.py` over all valid, non-censored trials collected to date.

| Scenario | Condition | N (trials) | Mean MTTD (s) | Mean MTTR (s) | Std MTTR | Success Rate |
|---|---|---|---|---|---|---|
| S1 — CPU Starvation | Run A (Legacy) | 32 | 564.79 | 66.61 | 0.62 | 100% |
| S1 — CPU Starvation | Run B (Proposed) | 27 | **8.73** | 71.09 | 23.18 | 100% |
| S2 — Memory Leak | Run A (Legacy) | 12 | 21.26 | 314.93 | 135.97 | 91.7% |
| S2 — Memory Leak | Run B (Proposed) | 10 | **12.10** | **240.10** | 73.82 | 100% |
| S4 — Network Latency | Run A (Legacy) | 10 | 600.00 (censored) | 72.18 | 0.39 | 100% |
| S4 — Network Latency | Run B (Proposed) | 10 | **32.41** | 98.41 | 64.44 | 100% |
| S7 — Random Pod Kill | Run A (Legacy) | 10 | 482.48 | 84.29 | 38.03 | 100% |
| S7 — Random Pod Kill | Run B (Proposed) | 10 | **43.07** | 109.09 | 82.39 | 100% |

**Global aggregates (across all scenarios above):**
- Average Legacy MTTD: **455.52s** → Average Proposed MTTD: **19.50s** → **95.72% reduction**
- Average Legacy MTTR: **116.80s** → Average Proposed MTTR: **112.20s** → **3.94% reduction**

**Key takeaway for charts:** the system's core, well-supported result is a **detection-speed** win (MTTD), not a recovery-speed win (MTTR) — MTTR is roughly flat between conditions across scenarios, and in some scenarios (S4, S7) Run B's *mean* MTTR is actually higher than Run A's, driven by high variance (see §4 limitations) rather than the operator's action being slower on average.

**Suggested charts:**
1. Grouped bar chart: MTTD, Legacy vs Proposed, per scenario (log scale recommended — S1/S4 legacy values are near the 600s censor ceiling).
2. Grouped bar chart: MTTR, Legacy vs Proposed, per scenario, with error bars (std dev) — this is the chart that visually makes the "MTTR is not a clean win" point honest.
3. Single summary chart: global MTTD reduction (95.72%) vs global MTTR reduction (3.94%) — the contrast is itself a finding.
4. Sample-size (N) bar per scenario/condition — worth showing since S1 has far more trials (32/27) than S4/S7 (10/10), which affects how much to trust each comparison.

---

## 3. Qualitative / Architectural Findings

1. **Detection generalizes well; recovery-action correctness does not, by default.** The Isolation Forest + fixed τ reliably detects all four validated fault types (S1, S2, S4, S7) with consistent ~6-45s MTTD. But *which* rule fires, and whether it targets the right service, is far less reliable — see the rule-collision findings below.

2. **Single-feature, largest-magnitude rule matching is a structural bottleneck, not a tuning problem.** Two independent failure mechanisms were found and diagnosed in production trials:
   - **Signal dilution (S2 / R3-memory-leak-restart):** the aggregate `mem_util` feature sums memory across all 12 services. A single service's (cartservice, 256Mi limit) leak is a small fraction of the ~2.1–2.4 GiB cluster-wide baseline, and gets OOMKilled faster than the 60s sample cadence can catch a sustained elevated reading. Result: `mem_util` never carries a usable signal for this fault, at *any* threshold. The system still "recovers" S2, but via `R2-pod-kill-restart` (triggered by `log_error_rate`) restarting **frontend**, not cartservice — a coincidental side effect, not a fix to the underlying leak.
   - **Rule collision (S9 vs. S11 / R5 vs. R6):** `network_rx` *does* carry a real, valid signal for S11 (DB pool exhaustion), but `trace_error_pct` (R5's trigger, intended for S9) reliably outmagnitudes it whenever both are elevated simultaneously — which they are, because S11's bandwidth throttle also raises request error rates via timeouts. The rule-matcher's "largest |z|-score wins" tie-break therefore always picks the wrong rule (R5, restarting redis-cart) even though R6's own signal, in isolation, is valid. This produced a fully executed, completely ineffective remediation action, and the trial's real recovery was the fault's own 5-minute expiry — not the system, and the trial was correctly discarded as invalid evidence.

3. **A per-service-feature model (v2) was designed specifically to address (2), and is architecturally implemented but not yet evidence.** `decision-engine/model-config-v2.yaml` (9 features: the original 6 cluster-wide + 3 new per-service ones) and `decision-engine/playbook-v2.yaml` (corrected `trigger_feature`s for R3/R5/R6) exist and pass synthetic-input verification, but have **not yet been validated against a real trained model + live cluster trial** — this is the single largest open item (see §5).

4. **Sampling-cadence artifacts can make MTTR look identical across conditions even when the mechanism is completely different.** For S1 and S7 in particular, several early trials showed near-identical MTTR between Run A and Run B not because the operator's action was equivalent in effect to native self-healing, but because (a) the SLO (`frontend_success_rate`) never meaningfully dropped in the first place (S1's CPU stress hurt backend latency without translating to frontend errors at this load level), or (b) a Deployment-managed pod-kill (S7) self-heals via Kubernetes' own pod replacement well inside the 60s sample cadence, in *either* condition. This is flagged explicitly in the experiment log as a measurement-protocol blind spot, not a code bug — some fault types at this scale/load are simply not good tests of the RQ3 "does autonomous remediation shorten recovery" claim.

5. **Infrastructure-level chaos-injection failures are a real, separate source of invalid data.** An S9 (volume detachment) trial failed at the Chaos Mesh IOChaos layer itself (`toda`, the fuse-based fault injector, never successfully injected — `injectedCount: 0`), and got stuck retrying its own teardown, requiring a manual Kubernetes finalizer removal to unblock. This is orthogonal to the detection/remediation pipeline being evaluated, but consumed real experiment time and blocks any valid S9 data until resolved.

6. **Two real software bugs in the measurement harness itself were found and fixed mid-campaign**, both of which would otherwise have silently corrupted trial data as false negatives:
   - An unbounded API call (`create_namespaced_custom_object` with no request timeout) could stall an entire trial's remaining time budget on a transient apiserver hiccup, producing a false-censored (`MTTD=600s`) result indistinguishable from a genuine detection failure. Fixed with a 15s request timeout + retry.
   - Rule-matching originally considered only the single most-deviated feature at the *first* anomalous sample and never retried on subsequent samples — meaning a real, strongly-triggering feature (e.g., `cpu_util` z=22) could be permanently missed because a coincidental, rule-less feature (`log_error_rate`) was marginally larger on the very first anomalous tick.

7. **Sample sizes are uneven and, for 3 of 4 validated scenarios, below the target N=10 per condition at time of last count** (S4: 10A/7B, S7: 10A/7B — 3 more of each still needed per the experiment log; S1 is well-powered at 32A/27B; S2 sits at 12A/10B). This should be disclosed as a limitation on statistical confidence, particularly for the higher-variance MTTR numbers.

8. **6 of 12 fault scenarios (S3, S5, S6, S8, S10, S12) have no validated trial data yet at all.** Playbook rules were only just added for them (2026-09-15) and are explicitly marked `UNVALIDATED`; several are *predicted*, before any real trial, to reproduce the same rule-collision pattern found in S9/S11, since they key on features already claimed by earlier rules.

---

## 4. Limitations (for explicit disclosure in any write-up)

- **MTTR improvement is not established.** The global 3.94% MTTR reduction is small relative to its variance (std dev of 23–82s per scenario/condition) and is not a clean, monotonic win across scenarios — S4 and S7's proposed-condition mean MTTR is *higher* than legacy. This should not be reported as a positive result without heavy caveats.
- **Two scenarios (S4, S7) undershoot the target sample size** (7 vs. 10 for Run B), and one scenario (S9) has zero valid trials due to an infrastructure fault-injection failure.
- **Two structural rule-matching failure modes are documented but not yet fixed-and-validated** (only designed and synthetically tested): signal dilution (S2) and rule collision (S9/S11). The v2 per-service-feature model addresses these in design but has no real-cluster evidence yet.
- **Half of the twelve fault scenarios in scope have never been run.**
- **The frozen v1 model is a single Isolation Forest trained once** (`20260906T023729Z`); no cross-validation across multiple independently trained models is reported, so some of the observed generalization could be specific to this one trained artifact.
- **Some MTTR parity between conditions is a measurement-protocol artifact** (60s sampling cadence too coarse to distinguish native self-healing from operator-driven remediation for fast-resolving faults), not necessarily evidence that the two conditions perform equivalently.

---

## 5. Current Status / What's Left

- Bring S4-B and S7-B up to N=10 (3 more trials each).
- Collect a fresh ≥200-sample baseline, train, and run real S2/S9/S11 trials under the v2 per-service-feature model to test whether it actually resolves the signal-dilution and rule-collision failures (currently only synthetically verified).
- Resolve the Chaos Mesh IOChaos/`toda` infrastructure issue blocking any valid S9 data.
- Run first-ever trials for S3, S5, S6, S8, S10, S12 (rules added but `UNVALIDATED`); expect several to reproduce the rule-collision pattern given shared feature triggers.

---

## 6. Self-Assessment / Rating (my opinion, for context — not a finding)

This is a solid, credible piece of applied systems research at what reads like an MSc/early-PhD dissertation level — but it is **not yet complete evidence for its own central RQ3 claim** (that autonomous remediation improves recovery time, not just detection time). Strengths: real cluster experiments (not simulation), honest documentation of every failure mode including ones that make the system look bad, two genuine measurement-bugs found and fixed rather than papered over, and a clear-eyed architectural diagnosis of *why* the rule-matching approach breaks (not just that it does). That level of self-critical rigor is unusually strong and is itself a defensible contribution (a taxonomy of failure modes in threshold/rule-based autonomous remediation).

Weaknesses that matter for a final grade/publication bar: the flagship MTTR result is currently weak-to-null, half the fault scenarios are unvalidated, and the proposed fix (v2 model) is designed but unproven. As it stands today this reads as **strong preliminary/mid-project results with a well-scoped remaining work plan**, not a finished evaluation — I'd rate the work-so-far quality as high (would not be surprised to see it in a workshop paper on its methodology and failure taxonomy alone), but the *evidence completeness* against the paper's own stated hypotheses as roughly 60–65% done. The next round of trials (v2 model, remaining 6 scenarios, N top-ups) is what would move this from "promising with caveats" to "defensible complete result."
