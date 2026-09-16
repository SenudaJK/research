# Model Selection Justification — Isolation Forest vs One-Class SVM vs LOF

**Purpose of this document:** Source material for generating a chart + narrative explaining why Isolation Forest was selected as the production anomaly detector, for a viva/panel defense. Self-contained — includes the raw numbers, the methodology fix that produced them, and the honest interpretation.

---

## 1. The Comparison

Three unsupervised anomaly detectors were trained on the same fault-free baseline (`evaluation/runs/baseline/20260906T023729Z/state_vector.csv`, 197 clean rows after dropping missing-feature rows) using the same 6-feature state vector (`cpu_util`, `mem_util`, `network_rx`, `log_error_rate`, `trace_latency_ms`, `trace_error_pct`), then evaluated against a labeled validation set built entirely from real measured data:

- **513 total rows**: 197 label=0 ("normal") rows from the same clean training baseline, plus 316 label=1 ("anomaly") rows — every recorded sample from `t0` onward across 121 real, valid fault trials (`evaluation/runs/trials/*.json`) spanning scenarios S1 (CPU starvation), S2 (memory leak), S4 (network latency), and S7 (random pod kill).
- No synthetic data. No chaos-run data used to pick hyperparameters or thresholds — only to score the already-fixed models.

| Algorithm | Precision | Recall | F1-Score | Train Time (s) | Inference Latency (µs) |
|---|---|---|---|---|---|
| **Isolation Forest** | 0.965 | 0.870 | **0.915** | 0.042 | 5.82 |
| One-Class SVM | 0.969 | 1.000 | 0.984 | 0.001 | 0.92 |
| LOF (baseline) | 0.969 | 1.000 | 0.984 | 0.002 | 2.42 |

**Suggested chart:** grouped bar chart, one group per algorithm, bars for Precision / Recall / F1 (0–1 scale) — this is the headline "why did you pick X" chart. A second, secondary chart: Train Time vs Inference Latency (log scale, µs) per algorithm, since that's the actual deciding factor, not F1.

---

## 2. The Methodology Fix Behind These Numbers (important — a panel will ask about this)

The first version of this comparison (produced before this fix) showed Isolation Forest with F1 = 0.243 — apparently far worse than SVM/LOF. That number was **wrong**, not because Isolation Forest is a bad model, but because of a scoring bug:

- `sklearn`'s `IsolationForest.predict()` classifies using an internal cutoff derived from its own `contamination=0.01` hyperparameter (flags roughly the most anomalous 1% of training data).
- SVM and LOF's `.predict()` similarly use their own internal `nu`/`contamination`-derived cutoffs.
- These three internal cutoffs are **not directly comparable** — each model draws its "this counts as anomalous" line differently, at a different implicit percentile, using a different scoring convention.
- Separately, the actual **production** system does not use Isolation Forest's internal `.predict()` cutoff at all — it uses a hand-derived τ (tau), set to the 95th percentile of the model's own anomaly scores on the training data (`decision-engine/model-config.yaml`, `threshold_percentile: 95`). That's a deliberately more permissive operating point than the internal 1%-contamination cutoff, chosen specifically because the stricter default failed to catch a real fault in early testing (documented in `docs/experiment-log.md`, 2026-09-07).

**The fix:** the comparison script (`decision-engine/train_and_compare.py`) was changed so that *all three* models are scored the same way: each model's own raw anomaly score (via `decision_function()`) is thresholded at that same model's own 95th-percentile score on the training data — i.e., every model gets the exact same treatment Isolation Forest already gets in production, instead of Isolation Forest being held to a stricter, non-representative internal default while SVM/LOF were not. Re-run under this corrected, fair basis, Isolation Forest's F1 rose from 0.243 to 0.915.

**Framing for the panel:** *"Our first pass showed Isolation Forest underperforming, which didn't match its behavior in live trials. We traced this to an apples-to-oranges thresholding mismatch between sklearn's default per-model cutoffs, fixed the comparison to score every model at the same operating point our production system actually uses, and the corrected result is consistent with what we observed in the field."* This is a **stronger** story for research rigor than if the numbers had been right the first time — it shows you caught and diagnosed a subtle methodological flaw rather than reporting a convenient number.

---

## 3. Why Isolation Forest Was Still Selected, Despite Slightly Lower F1

On the corrected, fair comparison, One-Class SVM and LOF both edge out Isolation Forest on F1 (0.984 vs 0.915) — driven by their recall of 1.000 vs Isolation Forest's 0.870. Be upfront about this rather than hiding it. The selection of Isolation Forest for production is defensible on different, and arguably more important, engineering grounds for this system's actual constraints:

1. **Scalability / no pairwise-distance computation.** LOF (`k`-nearest-neighbors based) and One-Class SVM (kernel-based, whose training cost typically scales worse with sample count) both require comparing new points against the training set's structure at inference time in ways that scale less favorably as the training window grows. Isolation Forest's trees make inference O(depth) per sample, independent of training-set size once trained — relevant for a detector meant to score a live 60-second telemetry cadence indefinitely, not a fixed offline batch.
2. **Low minimum sample requirement already validated empirically.** `min_training_samples: 200` in `model-config.yaml` was set after discovering (2026-08-27) that a 30-sample baseline produced unusably shallow trees; 200 samples was sufficient once retested — this is a constraint that was actually hit and fixed during the project, giving real evidence Isolation Forest is workable at the sample sizes this cluster can realistically produce a baseline for.
3. **Inference latency is workable across all three** (5.82µs vs 0.92–2.42µs) — a real difference, but all three are negligible relative to the 60-second sampling cadence; this is not the deciding factor in practice, and should not be oversold as one.
4. **The recall gap (0.870 vs 1.000) is a real, disclosed limitation**, not one to gloss over. It means roughly 13% of true fault-window samples would, in isolation, score below Isolation Forest's own τ — consistent with real trial data already showing Isolation Forest sometimes takes 2–3 samples (not the first) to cross τ (see `docs/experiment-log.md`'s scenario-01/scenario-07 entries), which is exactly why `rule_match_z_threshold` retry logic exists in the orchestrator. Framing: Isolation Forest was chosen for architectural/operational fit for a continuously-running production detector, with a known, acceptable, and independently-compensated-for recall tradeoff — not because it scored best on a single offline metric.

**Suggested chart:** a simple annotated table or small multiples showing Precision/Recall/F1 *and* Train Time/Inference Latency side by side, with a callout on the recall gap and how the retry-based rule-matching in the live system compensates for it.

---

## 4. One-Sentence Summary for the Panel

*"We picked Isolation Forest not because it has the best raw F1 in an offline comparison — a corrected, fair comparison shows One-Class SVM and LOF slightly ahead on F1 — but because it best fits this system's actual operational constraints: workable at the modest baseline sample sizes we can realistically collect, and architecturally suited to continuous low-latency scoring in production; its lower recall relative to the alternatives is a known, disclosed tradeoff that the rule-matching layer's retry logic is explicitly designed to absorb."*
