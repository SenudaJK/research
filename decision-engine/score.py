"""
Score a State Vector CSV against a previously trained, frozen Isolation
Forest model — the actual detection step, as distinct from training.

train_and_compare.py trains a model and freezes tau from a baseline. This
script is what evaluates NEW telemetry (e.g. a post-fault window) against
that frozen model, printing the per-row anomaly score, tau, and whether
detection would have fired. It never re-trains or re-computes tau — doing
so on fault-window data would violate the frozen-threshold rule in
docs/measurement-protocol.md.

Usage: see decision-engine/README.md.
"""
import argparse
import json
import sys
from pathlib import Path

import joblib
import pandas as pd


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--model-dir", required=True, type=Path,
        help="Directory containing scaler.pkl, isolation_forest.pkl, threshold.json "
             "(e.g. evaluation/runs/baseline/<run-id>/model-artifacts/)",
    )
    parser.add_argument(
        "--state-vector-csv", required=True, type=Path,
        help="State Vector CSV to score, e.g. from a post-fault fusion-engine run.",
    )
    args = parser.parse_args()

    scaler = joblib.load(args.model_dir / "scaler.pkl")
    iforest = joblib.load(args.model_dir / "isolation_forest.pkl")
    threshold = json.loads((args.model_dir / "threshold.json").read_text())
    tau = threshold["tau"]
    features = threshold["features"]

    df = pd.read_csv(args.state_vector_csv)
    missing = [c for c in features if c not in df.columns]
    if missing:
        sys.exit(f"ERROR: {args.state_vector_csv} missing feature columns: {missing}")

    X_scaled = scaler.transform(df[features])
    anomaly_scores = -iforest.score_samples(X_scaled)  # higher = more anomalous
    df["anomaly_score"] = anomaly_scores
    df["tau"] = tau
    df["detected"] = anomaly_scores > tau

    cols = ["sample", "timestamp"] if "sample" in df.columns else []
    cols += ["anomaly_score", "tau", "detected"]
    print(df[cols].to_string(index=False))

    n_detected = int(df["detected"].sum())
    print(f"\n{n_detected}/{len(df)} rows scored above tau ({tau:.6f})")
    if n_detected:
        first_hit = df[df["detected"]].iloc[0]
        ts = first_hit.get("timestamp", "?")
        print(f"First detection at row timestamp: {ts}  <- this is T_d for a manual dry run")


if __name__ == "__main__":
    main()
