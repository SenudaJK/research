"""
Fuse a collect-baseline.sh run directory's raw per-sample telemetry
(metrics/, logs/, traces/) into the unified six-feature State Vector CSV
that decision-engine/train_and_compare.py trains on.

This reads ONLY already-collected, already-quality-gated data — it never
queries Prometheus/Loki/Jaeger live. That is deliberate: collect-baseline.sh
is the single source of truth for what was measured and when (T0-relative
timestamps, sample cadence, run manifest); this script's only job is to
reshape that data, not to re-observe the cluster on a different schedule.

Ground rules enforced here:
  - A query that failed or returned empty in the source JSON produces NaN
    for that feature on that row, NEVER a silent 0.0 — a failed measurement
    and a genuinely healthy zero must stay distinguishable (this was the
    root problem in the old fusion-engine/harverster.py it replaces).
  - Every row records which features (if any) are missing, so a downstream
    consumer can decide to drop, impute, or fail loudly — that decision is
    not made here.

Usage: see fusion-engine/README.md.
"""
import argparse
import json
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ERROR_LOG_PATTERN = re.compile(r"(error|fail|exception|5\d\d)", re.IGNORECASE)
LOG_WINDOW_SECONDS = 120.0  # matches the --start/--end window collect-baseline.sh queries


def sample_index(path):
    return int(re.search(r"sample_(\d+)\.json$", path.name).group(1))


def prom_scalar(metrics_json, key):
    """Returns (value, ok). ok=False means the query failed/returned empty."""
    entry = metrics_json.get("queries", {}).get(key)
    if not entry or entry.get("status") != "success":
        return np.nan, False
    result = entry.get("data", {}).get("result", [])
    if not result:
        return np.nan, False
    try:
        return float(result[0]["value"][1]), True
    except (KeyError, IndexError, ValueError, TypeError):
        return np.nan, False


def log_error_rate(logs_path):
    """Error-ish lines per second across the queried window. NaN if the
    query itself failed (as opposed to legitimately finding zero errors)."""
    if not logs_path.exists():
        return np.nan, False
    try:
        payload = json.loads(logs_path.read_text())
    except json.JSONDecodeError:
        return np.nan, False
    if payload.get("status") != "success":
        return np.nan, False
    streams = payload.get("data", {}).get("result", [])
    error_lines = 0
    for stream in streams:
        for _, line in stream.get("values", []):
            if ERROR_LOG_PATTERN.search(line):
                error_lines += 1
    return error_lines / LOG_WINDOW_SECONDS, True


def trace_features(traces_path):
    """(avg_latency_ms, error_pct, ok). ok=False if the query itself failed."""
    if not traces_path.exists():
        return np.nan, np.nan, False
    try:
        payload = json.loads(traces_path.read_text())
    except json.JSONDecodeError:
        return np.nan, np.nan, False
    traces = payload.get("data")
    if traces is None:
        return np.nan, np.nan, False
    if not traces:
        # Query succeeded but returned zero traces in this window — that is
        # a legitimate (if concerning) reading, not a query failure.
        return 0.0, 0.0, True

    latencies = []
    error_count = 0
    span_count = 0
    for trace in traces:
        for span in trace.get("spans", []):
            span_count += 1
            latencies.append(span["duration"] / 1000.0)  # us -> ms
            for tag in span.get("tags", []):
                if tag.get("key") == "error" and tag.get("value") in (True, "true"):
                    error_count += 1
    if span_count == 0:
        return 0.0, 0.0, True
    return float(np.mean(latencies)), error_count / span_count, True


def build(run_dir):
    metrics_dir = run_dir / "metrics"
    logs_dir = run_dir / "logs"
    traces_dir = run_dir / "traces"

    metrics_files = sorted(metrics_dir.glob("sample_*.json"), key=sample_index)
    if not metrics_files:
        sys.exit(f"ERROR: no metrics/sample_*.json found under {run_dir}")

    rows = []
    for metrics_path in metrics_files:
        n = sample_index(metrics_path)
        metrics_json = json.loads(metrics_path.read_text())

        cpu, cpu_ok = prom_scalar(metrics_json, "cpu_usage")
        mem_bytes, mem_ok = prom_scalar(metrics_json, "memory_working_set")
        net_bytes_per_sec, net_ok = prom_scalar(metrics_json, "network_receive_bytes")
        logs_rate, logs_ok = log_error_rate(logs_dir / f"sample_{n}.json")
        latency_ms, error_pct, trace_ok = trace_features(traces_dir / f"sample_{n}.json")

        missing = [
            name for name, ok in [
                ("cpu_util", cpu_ok), ("mem_util", mem_ok), ("network_rx", net_ok),
                ("log_error_rate", logs_ok), ("trace_latency_ms", trace_ok),
                ("trace_error_pct", trace_ok),
            ] if not ok
        ]

        rows.append({
            "sample": n,
            "timestamp": metrics_json.get("timestamp"),
            "cpu_util": cpu,
            "mem_util": mem_bytes / (1024 * 1024) if mem_ok else np.nan,  # bytes -> MB
            "network_rx": net_bytes_per_sec / 1024 if net_ok else np.nan,  # B/s -> KB/s
            "log_error_rate": logs_rate,
            "trace_latency_ms": latency_ms,
            "trace_error_pct": error_pct,
            "missing_features": ",".join(missing),
        })

    return pd.DataFrame(rows).sort_values("sample")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run-dir", required=True, type=Path,
        help="Baseline run directory produced by infra/scripts/collect-baseline.sh, "
             "e.g. evaluation/runs/baseline/<run-id>/",
    )
    parser.add_argument(
        "--output", default=None, type=Path,
        help="Output CSV path (default: <run-dir>/state_vector.csv)",
    )
    parser.add_argument(
        "--max-missing-fraction", type=float, default=0.2,
        help="Fail if more than this fraction of rows have any missing feature (default 0.2).",
    )
    args = parser.parse_args()

    if not (args.run_dir / "meta" / "quality.json").exists():
        print(
            f"WARNING: {args.run_dir}/meta/quality.json not found — run "
            "infra/scripts/check-baseline-quality.sh before treating this "
            "State Vector as training input.",
            file=sys.stderr,
        )

    df = build(args.run_dir)
    output = args.output or (args.run_dir / "state_vector.csv")
    df.to_csv(output, index=False)

    rows_with_gaps = (df["missing_features"] != "").sum()
    frac = rows_with_gaps / len(df)
    print(f"Wrote {len(df)} rows to {output}")
    print(f"Rows with at least one missing feature: {rows_with_gaps}/{len(df)} ({frac:.1%})")
    if frac > args.max_missing_fraction:
        sys.exit(
            f"ERROR: {frac:.1%} of rows have missing features, exceeding "
            f"--max-missing-fraction={args.max_missing_fraction:.0%}. "
            "Do not train on this State Vector without addressing the gaps "
            "(see the 'missing_features' column)."
        )


if __name__ == "__main__":
    main()
