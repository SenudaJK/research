"""
Build a labeled validation CSV (features + label) for
decision-engine/train_and_compare.py's --validation-csv, from REAL measured
data only:

  - label=0 (normal): rows from the frozen model's own training baseline
    (evaluation/runs/baseline/20260906T023729Z/state_vector.csv) — the same
    fault-free run tau was derived from, per docs/measurement-protocol.md.
  - label=1 (anomaly): every sample recorded during a fault window in
    evaluation/runs/trials/*.json (excluding trials/invalid/), i.e. all
    samples with timestamp >= t0, from trials with trial_valid=true.

This does NOT touch chaos-run data when deriving tau or fitting any model —
it only assembles ground truth for the separately-computed comparison table.

Usage: python evaluation/analysis/build_labeled_validation_csv.py
Output: evaluation/runs/validation_labeled.csv
"""
import glob
import json
from pathlib import Path

import pandas as pd

ROOT_DIR = Path(__file__).resolve().parents[2]
FEATURES = [
    "cpu_util", "mem_util", "network_rx",
    "log_error_rate", "trace_latency_ms", "trace_error_pct",
]
BASELINE_CSV = ROOT_DIR / "evaluation/runs/baseline/20260906T023729Z/state_vector.csv"
TRIALS_GLOB = str(ROOT_DIR / "evaluation/runs/trials/*.json")
OUTPUT_CSV = ROOT_DIR / "evaluation/runs/validation_labeled.csv"


def load_normal_rows():
    df = pd.read_csv(BASELINE_CSV)
    df = df.dropna(subset=FEATURES)
    rows = df[FEATURES].copy()
    rows["label"] = 0
    rows["source"] = "baseline_20260906T023729Z"
    rows["scenario"] = "none"
    rows["condition"] = "none"
    return rows


def load_anomaly_rows():
    all_rows = []
    skipped_trials = 0
    for path in sorted(glob.glob(TRIALS_GLOB)):
        trial = json.loads(Path(path).read_text())
        if trial.get("trial_valid") is not True:
            skipped_trials += 1
            continue
        t0 = trial.get("t0")
        scenario = Path(trial.get("scenario_file", path)).stem
        condition = trial.get("condition")
        for sample in trial.get("samples", []):
            if any(sample.get(f) is None for f in FEATURES):
                continue
            if t0 is not None and sample.get("timestamp", "") < t0:
                continue
            row = {f: sample[f] for f in FEATURES}
            row["label"] = 1
            row["source"] = Path(path).name
            row["scenario"] = scenario
            row["condition"] = condition
            all_rows.append(row)
    print(f"Anomaly rows from {len(all_rows)} samples; skipped {skipped_trials} invalid/censored trials.")
    return pd.DataFrame(all_rows)


def main():
    normal = load_normal_rows()
    anomaly = load_anomaly_rows()
    combined = pd.concat([normal, anomaly], ignore_index=True)
    combined.to_csv(OUTPUT_CSV, index=False)
    print(f"Wrote {len(combined)} rows ({len(normal)} normal / {len(anomaly)} anomaly) -> {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
