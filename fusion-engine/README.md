# Fusion Engine

Multi-modal telemetry fusion: Prometheus metrics, Loki logs, Jaeger traces.

## Responsibilities

- Ingest heterogeneous telemetry streams
- Normalize and scale features uniformly
- Time-align via sliding window onto a temporal grid
- Output unified **State Vector**

## Contents

- `build_state_vector.py` — parses a `infra/scripts/collect-baseline.sh` run
  directory's raw per-sample JSON (`metrics/`, `logs/`, `traces/`) into the
  six-feature State Vector CSV that `decision-engine/` trains on
- `requirements.txt` — `pip install -r fusion-engine/requirements.txt`

This deliberately reads only already-collected, already-quality-gated data —
it never queries Prometheus/Loki/Jaeger live, so there is exactly one
telemetry collection path (`collect-baseline.sh`) and one transform
(`build_state_vector.py`), not two independent collectors that could drift
out of sync with each other.

## State Vector schema

| Column | Unit | Source |
|---|---|---|
| `cpu_util` | CPU cores (rate) | Prometheus `cpu_usage` |
| `mem_util` | MB | Prometheus `memory_working_set` |
| `network_rx` | KB/s | Prometheus `network_receive_bytes` |
| `log_error_rate` | errors/sec | Loki, lines matching `(error\|fail\|exception\|5\d\d)` |
| `trace_latency_ms` | ms, mean over window | Jaeger frontend trace spans |
| `trace_error_pct` | fraction | Jaeger frontend trace spans tagged `error=true` |

A `missing_features` column names any feature whose source query failed or
returned empty on that sample — those cells are `NaN`, never a silent `0.0`,
so a failed measurement is never indistinguishable from a genuinely healthy
reading. `--max-missing-fraction` (default 20%) fails the run if too many
rows have gaps.

## Usage

```bash
pip install -r fusion-engine/requirements.txt
python3 fusion-engine/build_state_vector.py \
  --run-dir evaluation/runs/baseline/<run-id>
```

Writes `evaluation/runs/baseline/<run-id>/state_vector.csv`. Warns (but does
not block) if that run directory has no `meta/quality.json` yet — run
`infra/scripts/check-baseline-quality.sh` first.

## Planned Contents

- API or event stream for downstream anomaly detection (Phase 3+)
