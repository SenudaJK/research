"""
Train the Isolation Forest anomaly detector on a quality-gated baseline,
freeze its detection threshold (tau), and optionally produce an algorithm
comparison table (Isolation Forest vs One-Class SVM vs LOF) for RQ1/RQ2
justification.

Ground rules enforced here, per docs/measurement-protocol.md:
  - Training only proceeds on a baseline run whose meta/quality.json says
    "trainable": true. Two known runs are explicitly NOT trainable
    (20260817T015611Z, 20260817T141615Z) and this script refuses them.
  - tau is derived only from the training (fault-free) scores, never from
    chaos-run data, and is written once to threshold.json.
  - The comparison table is never computed from synthetic data unless
    --synthetic-smoke-test is passed explicitly, and the output is labeled
    as a smoke test, not a research result, when that happens.

Usage: see decision-engine/README.md.
"""
import argparse
import json
import sys
import time
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import yaml
from sklearn.ensemble import IsolationForest
from sklearn.metrics import f1_score, precision_score, recall_score
from sklearn.neighbors import LocalOutlierFactor
from sklearn.preprocessing import StandardScaler
from sklearn.svm import OneClassSVM


def load_config(config_path):
    with open(config_path) as f:
        return yaml.safe_load(f)


def assert_trainable(run_dir):
    quality_path = run_dir / "meta" / "quality.json"
    if not quality_path.exists():
        sys.exit(
            f"ERROR: {quality_path} not found.\n"
            "Run infra/scripts/check-baseline-quality.sh against this run "
            "before training (see docs/measurement-protocol.md)."
        )
    quality = json.loads(quality_path.read_text())
    if not quality.get("trainable"):
        sys.exit(
            f"ERROR: {quality_path} says trainable={quality.get('trainable')!r}.\n"
            "Isolation Forest must only be trained on a quality-gated, "
            "fault-free baseline. Collect a fresh 30-minute run with "
            "infra/scripts/collect-baseline.sh and re-gate it."
        )


class ModelTrainerAndComparer:
    def __init__(self, config, features):
        self.config = config
        self.features = features
        self.scaler = StandardScaler()

    def load_and_scale(self, data_path):
        df = pd.read_csv(data_path)
        missing = [c for c in self.features if c not in df.columns]
        if missing:
            sys.exit(f"ERROR: state vector CSV missing columns: {missing}")
        min_samples = self.config.get("min_training_samples", 0)
        if len(df) < min_samples:
            sys.exit(
                f"ERROR: {data_path} has {len(df)} rows, below "
                f"min_training_samples={min_samples} in model-config.yaml.\n"
                "Isolation Forest on too few samples produces shallow trees "
                "and an unstable percentile threshold that cannot separate "
                "real anomalies from baseline noise (confirmed empirically "
                "on 2026-08-27 with a 30-sample run). Re-collect a longer "
                "baseline: BASELINE_DURATION_SECONDS=$(( "
                f"{min_samples} * BASELINE_SAMPLE_INTERVAL_SECONDS )) "
                "bash infra/scripts/collect-baseline.sh"
            )
        self.X_train = df[self.features]
        self.X_scaled = self.scaler.fit_transform(self.X_train)

    def train_models(self):
        results = {}

        start = time.time()
        iforest = IsolationForest(**self.config["isolation_forest"])
        iforest.fit(self.X_scaled)
        results["Isolation Forest"] = {"model": iforest, "train_time": time.time() - start}

        start = time.time()
        ocsvm = OneClassSVM(**self.config["one_class_svm"])
        ocsvm.fit(self.X_scaled)
        results["One-Class SVM"] = {"model": ocsvm, "train_time": time.time() - start}

        start = time.time()
        lof = LocalOutlierFactor(**self.config["local_outlier_factor"])
        lof.fit(self.X_scaled)
        results["LOF (Baseline)"] = {"model": lof, "train_time": time.time() - start}

        return results

    def compute_isolation_forest_threshold(self, iforest):
        """tau = Nth percentile of anomaly scores on the TRAINING data only."""
        anomaly_scores = -iforest.score_samples(self.X_scaled)  # higher = more anomalous
        percentile = self.config["threshold_percentile"]
        tau = float(np.percentile(anomaly_scores, percentile))
        return tau, percentile

    def compare_on_labeled_validation(self, results, validation_csv):
        """Evaluates models against REAL labeled validation data.

        validation_csv must contain the feature columns plus a `label` column
        (0 = normal, 1 = anomaly) derived from actual chaos-run ground truth
        per the TP/FP/FN/TN definitions in docs/measurement-protocol.md.
        """
        df = pd.read_csv(validation_csv)
        if "label" not in df.columns:
            sys.exit(f"ERROR: {validation_csv} has no 'label' column (0=normal, 1=anomaly).")
        missing = [c for c in self.features if c not in df.columns]
        if missing:
            sys.exit(f"ERROR: {validation_csv} missing feature columns: {missing}")

        y_true = df["label"].tolist()
        X_val_scaled = self.scaler.transform(df[self.features])
        return self._score_models(results, X_val_scaled, y_true)

    def compare_on_synthetic_smoke_test(self, results):
        """Pipeline smoke test ONLY — not research evidence. Synthesized
        normal/anomaly clusters, not measured from the cluster."""
        rng = np.random.RandomState(42)
        normal_val = pd.DataFrame({
            "cpu_util": rng.normal(35.0, 4.0, 200),
            "mem_util": rng.normal(45.0, 2.0, 200),
            "network_rx": rng.normal(1200.0, 80.0, 200),
            "log_error_rate": rng.poisson(0.1, 200),
            "trace_latency_ms": rng.normal(15.0, 1.5, 200),
            "trace_error_pct": rng.binomial(100, 0.01, 200) / 100.0,
        })
        chaos_val = pd.DataFrame({
            "cpu_util": rng.normal(12.0, 2.0, 50),
            "mem_util": rng.normal(46.0, 1.0, 50),
            "network_rx": rng.normal(450.0, 50.0, 50),
            "log_error_rate": rng.poisson(15.0, 50),
            "trace_latency_ms": rng.normal(215.0, 10.0, 50),
            "trace_error_pct": rng.binomial(100, 0.15, 50) / 100.0,
        })
        y_true = [0] * 200 + [1] * 50
        X_val_raw = pd.concat([normal_val, chaos_val])
        X_val_scaled = self.scaler.transform(X_val_raw[self.features])
        return self._score_models(results, X_val_scaled, y_true)

    def _score_models(self, results, X_val_scaled, y_true):
        comparison_table = []
        for name, data in results.items():
            model = data["model"]
            start_infer = time.time()
            raw_pred = model.predict(X_val_scaled)
            y_pred = [1 if val == -1 else 0 for val in raw_pred]
            infer_time_us = (time.time() - start_infer) / len(X_val_scaled) * 1e6

            comparison_table.append({
                "Algorithm": name,
                "Precision": precision_score(y_true, y_pred, zero_division=0),
                "Recall": recall_score(y_true, y_pred, zero_division=0),
                "F1-Score": f1_score(y_true, y_pred, zero_division=0),
                "Train Time (s)": data["train_time"],
                "Inference Latency (us)": infer_time_us,
            })
        return pd.DataFrame(comparison_table)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run-dir", required=True, type=Path,
        help="Baseline run directory, e.g. evaluation/runs/baseline/<run-id>/",
    )
    parser.add_argument(
        "--state-vector-csv", default="state_vector.csv",
        help="Filename of the fused State Vector CSV inside --run-dir (default: state_vector.csv)",
    )
    parser.add_argument(
        "--config", default=Path(__file__).parent / "model-config.yaml", type=Path,
    )
    parser.add_argument(
        "--validation-csv", default=None, type=Path,
        help="Real labeled validation CSV (features + label column) from Phase 4 chaos runs.",
    )
    parser.add_argument(
        "--synthetic-smoke-test", action="store_true",
        help="Run the comparison table against synthetic data as a pipeline smoke test ONLY. "
             "Output is explicitly labeled as non-evidence.",
    )
    parser.add_argument(
        "--output-dir", default=None, type=Path,
        help="Where to write model artifacts (default: <run-dir>/model-artifacts/)",
    )
    args = parser.parse_args()

    assert_trainable(args.run_dir)

    config = load_config(args.config)
    output_dir = args.output_dir or (args.run_dir / "model-artifacts")
    output_dir.mkdir(parents=True, exist_ok=True)

    comparer = ModelTrainerAndComparer(config, config["features"])
    comparer.load_and_scale(args.run_dir / args.state_vector_csv)
    results = comparer.train_models()

    joblib.dump(comparer.scaler, output_dir / "scaler.pkl")
    joblib.dump(results["Isolation Forest"]["model"], output_dir / "isolation_forest.pkl")
    joblib.dump(results["One-Class SVM"]["model"], output_dir / "oc_svm.pkl")
    joblib.dump(results["LOF (Baseline)"]["model"], output_dir / "lof.pkl")

    tau, percentile = comparer.compute_isolation_forest_threshold(results["Isolation Forest"]["model"])
    threshold_path = output_dir / "threshold.json"
    threshold_path.write_text(json.dumps({
        "tau": tau,
        "percentile": percentile,
        "source_run_dir": str(args.run_dir),
        "features": config["features"],
    }, indent=2))
    print(f"tau (Isolation Forest, p{percentile}) = {tau:.6f} -> {threshold_path}")

    if args.validation_csv:
        comp_df = comparer.compare_on_labeled_validation(results, args.validation_csv)
        comp_df.to_csv(output_dir / "model-comparison.csv", index=False)
        print("\n" + "=" * 60)
        print("MODEL COMPARISON — real labeled validation data")
        print("=" * 60)
        print(comp_df.to_string(index=False))
    elif args.synthetic_smoke_test:
        comp_df = comparer.compare_on_synthetic_smoke_test(results)
        comp_df.to_csv(output_dir / "model-comparison-SYNTHETIC-SMOKE-TEST.csv", index=False)
        print("\n" + "=" * 60)
        print("SYNTHETIC SMOKE TEST — NOT RESEARCH EVIDENCE")
        print("Validation data is randomly generated, not measured from the cluster.")
        print("Do not cite these numbers in the dissertation.")
        print("=" * 60)
        print(comp_df.to_string(index=False))
    else:
        print(
            "\nNo --validation-csv provided and --synthetic-smoke-test not set — "
            "skipping comparison table. Provide real labeled validation data from "
            "Phase 4 chaos runs once available (see docs/measurement-protocol.md)."
        )


if __name__ == "__main__":
    main()
