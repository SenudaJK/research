# Measurement Protocol (Phase 1)

Operational definitions for MTTD, MTTR, availability, and detection outcomes.
These definitions are frozen before Phase 2 training and Phase 4 Run A vs Run B.
Do not change them mid-experiment without a thesis justification and a new baseline.

Aligned with Chapter 3 of `IM2021014.pdf` and `docs/methodology-checklist.md`.

## Why this exists

Run A (framework OFF) and Run B (framework ON) are only comparable if
“fault started”, “detected”, and “recovered” mean the same thing in every trial.
Isolation Forest scores are **not** used to define recovery. Recovery is an SLO.

## Time origin

| Symbol | Meaning | Source |
|--------|---------|--------|
| \(T_0\) | Fault start | Chaos Mesh experiment `status.experimentStartTime` (UTC). If absent, use the apply timestamp recorded in `docs/experiment-log.md`. |
| \(T_d\) | Detection time | First sample window that satisfies the **detection rule** for that run condition (below). |
| \(T_r\) | Recovery time | First time the **frontend SLO** is met for **two consecutive 60 s windows** after \(T_0\). |
| \(T_e\) | Fault end / trial end | Chaos Mesh experiment end, or \(T_0 + 10\) min timeout, whichever is first. |

All clocks are UTC. Sample interval is 60 s (same as baseline collection).

## Service-level objective (recovery and availability)

Application health is measured from OTEL spanmetrics already scraped by Prometheus
(`boutique_traces_span_metrics_*`), not from Loki or Jaeger UI clicks.

**Frontend success rate** over a 60 s window:

```promql
1 - (
  sum(rate(boutique_traces_span_metrics_calls_total{
    service_name="frontend",
    span_kind="SPAN_KIND_SERVER",
    status_code="STATUS_CODE_ERROR"
  }[1m])) or vector(0)
)
/
clamp_min(
  sum(rate(boutique_traces_span_metrics_calls_total{
    service_name="frontend",
    span_kind="SPAN_KIND_SERVER"
  }[1m])),
  1e-9
)
```

| SLO | Threshold | Hold |
|-----|-----------|------|
| Frontend success rate | \(\ge 0.99\) | 2 consecutive 60 s windows |
| Supporting RED (logged, not the recovery gate) | request rate, error rate, avg duration | same window |

A trial is **recovered** at \(T_r\) when the SLO hold is met. If the SLO is never
met before \(T_e\), MTTR is recorded as **censored** at \(T_e - T_0\) and flagged
in the experiment log.

## MTTD — Mean Time To Detect

\[
\mathrm{MTTD} = T_d - T_0
\]

Detection is condition-specific so Run A does not pretend the fusion engine exists.

### Run A (framework OFF) — native observability

\(T_d\) is the first 60 s window after \(T_0\) where **any** of the following is true:

1. Frontend success rate \(< 0.99\), or
2. Boutique pod not `Running`/`Ready` (kube-state-metrics), or
3. Container restart count increases relative to the pre-fault snapshot.

This is “when a competent operator watching Prometheus + `kubectl` would first
see the fault”. It is **not** an Isolation Forest score.

### Run B (framework ON) — fusion engine

\(T_d\) is the first 60 s window after \(T_0\) where **all** of the following are true:

1. Isolation Forest anomaly score \(> \tau\), and
2. A playbook rule ID is matched, and
3. The decision is logged (trigger signals, rule ID, timestamp).

Threshold \(\tau\) is set in Phase 2 from the **fault-free** baseline only
(e.g. 99th percentile of training scores). It is not tuned on chaos runs.

If the engine never fires before \(T_e\), the trial is a **false negative** and
MTTD is censored at \(T_e - T_0\).

## MTTR — Mean Time To Recovery

\[
\mathrm{MTTR} = T_r - T_0
\]

Recovery uses the **same SLO** in Run A and Run B.

| Condition | What is allowed to restore the SLO |
|-----------|--------------------------------------|
| Run A | Native Kubernetes only (kubelet restart, probe failure, reschedule). No operator. |
| Run B | Native Kubernetes **plus** the custom operator after a matched rule. |

Do not start MTTR at \(T_d\). Starting at \(T_0\) keeps Run A and Run B comparable
(RQ3). Detection latency is reported separately as MTTD.

## Availability

Over the trial window \([T_0, \max(T_r, T_e)]\):

\[
\mathrm{Availability} = \frac{\text{successful frontend server spans}}{\text{all frontend server spans}}
\]

Successful = `status_code != STATUS_CODE_ERROR`.
Report per-trial availability and the mean across the 10 iterations of each scenario.

## Detection outcomes (precision / recall / F1)

Ground-truth **fault window**: \([T_0, T_e]\).
Ground-truth **healthy window**: the 30-minute fault-free baseline, plus any
pre-fault soak before \(T_0\).

| Outcome | Definition |
|---------|------------|
| **TP** | Detection fires inside the fault window |
| **FN** | Fault window ends with no detection |
| **FP** | Detection fires inside a healthy window |
| **TN** | Healthy window with no detection |

\[
\mathrm{Precision} = \frac{\mathrm{TP}}{\mathrm{TP}+\mathrm{FP}}, \quad
\mathrm{Recall} = \frac{\mathrm{TP}}{\mathrm{TP}+\mathrm{FN}}, \quad
F_1 = \frac{2 \cdot \mathrm{Precision} \cdot \mathrm{Recall}}{\mathrm{Precision}+\mathrm{Recall}}
\]

For RQ1 (Phase 2), compute the same matrix four ways on the **same** windows:
metrics-only, logs-only, traces-only, and fused State Vector. That is the
unimodal vs multi-modal comparison.

## What is recorded per trial

Every chaos iteration writes one block to `docs/experiment-log.md` and raw
files under `evaluation/runs/`:

- `T_0`, `T_d`, `T_r`, `T_e` (UTC)
- MTTD, MTTR, availability
- TP / FP / FN / TN
- Run A: which native signal fired
- Run B: anomaly score, matched rule ID, action taken (or `dry-run`)
- Notes (censored times, operator blocked by cooldown, etc.)

## Baseline used for training

Isolation Forest is trained only on a **quality-gated** fault-free run.

| Run ID | Status | Use |
|--------|--------|-----|
| `20260817T015611Z` | Incomplete. Application RED query was `http_server_duration_milliseconds_count` (empty in all 29 samples). CPU, memory, logs, traces present. | Instrumentation proof only. **Do not train.** |
| `20260817T141615Z` | 180 s smoke. All pillars non-empty, including RED. `trainable: false`. | Pipeline proof only. **Do not train.** |
| Later 1800 s run with `meta/quality.json` `"trainable": true` | Full soak, quality gate passed. | **Training set** for Phase 2. |

Collect with:

```bash
bash infra/scripts/collect-baseline.sh          # 30 minutes
bash infra/scripts/check-baseline-quality.sh    # fail if RED is empty
```

Smoke (not for training):

```bash
BASELINE_DURATION_SECONDS=180 bash infra/scripts/collect-baseline.sh
```

## Log-feature hygiene

Promtail must not ship the `observability` namespace into Loki. Loki 3.5.0
`push.go` error spam would otherwise dominate log features and bias RQ1.
Fusion queries are `{namespace="boutique"}` only.
