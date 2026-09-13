"""
Statistical comparison of Run A (native Kubernetes) vs Run B (framework)
chaos trial results — MTTD/MTTR summary table and publication charts.

Ground rule: this must only ever run against a real experiment_results.csv
produced by evaluation/analysis/run_campaign.py (or hand-assembled from
docs/experiment-log.md entries per docs/measurement-protocol.md). It used
to auto-generate synthetic mock data and print a full statistical summary
+ charts from it whenever the CSV was missing, with no warning that the
numbers were fake — a real research-integrity risk if run absentmindedly
near a deadline. That path now requires --synthetic-smoke-test explicitly
and labels its output as non-evidence, mirroring decision-engine/train_and_compare.py.
"""
import argparse
import os
import sys

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns

REQUIRED_COLUMNS = {"Scenario_ID", "Run_Type", "MTTD", "MTTR", "Success"}


class AcademicDataAnalyser:
    def __init__(self, results_csv):
        self.df = pd.read_csv(results_csv)
        missing = REQUIRED_COLUMNS - set(self.df.columns)
        if missing:
            sys.exit(f"ERROR: {results_csv} missing columns: {missing}")
        sns.set_theme(style="whitegrid")

    def print_statistical_summary(self):
        """Computes academic metrics and percentage improvements."""
        print("\n" + "="*50)
        print("          SRE METRICS STATISTICAL SUMMARY")
        print("="*50)
        
        grouped = self.df.groupby(["Scenario_ID", "Run_Type"]).agg(
            Mean_MTTD=("MTTD", "mean"),
            Mean_MTTR=("MTTR", "mean"),
            Std_MTTR=("MTTR", "std"),
            Success_Rate=("Success", "mean")
        ).reset_index()
        
        print(grouped.to_string(index=False))
        
        # Calculate global aggregated improvements
        avg_legacy_mttr = self.df[self.df["Run_Type"] == "Run A (Legacy)"]["MTTR"].mean()
        avg_proposed_mttr = self.df[self.df["Run_Type"] == "Run B (Proposed)"]["MTTR"].mean()
        mttr_improvement = ((avg_legacy_mttr - avg_proposed_mttr) / avg_legacy_mttr) * 100

        avg_legacy_mttd = self.df[self.df["Run_Type"] == "Run A (Legacy)"]["MTTD"].mean()
        avg_proposed_mttd = self.df[self.df["Run_Type"] == "Run B (Proposed)"]["MTTD"].mean()
        mttd_improvement = ((avg_legacy_mttd - avg_proposed_mttd) / avg_legacy_mttd) * 100

        print("\n" + "-"*50)
        print(f"Global Average Legacy MTTD: {avg_legacy_mttd:.2f} seconds")
        print(f"Global Average Proposed MTTD: {avg_proposed_mttd:.2f} seconds")
        print(f"Overall MTTD Reduction: {mttd_improvement:.2f}%")
        print("-"*50)
        print(f"Global Average Legacy MTTR: {avg_legacy_mttr:.2f} seconds")
        print(f"Global Average Proposed MTTR: {avg_proposed_mttr:.2f} seconds")
        print(f"Overall MTTR Reduction: {mttr_improvement:.2f}%")
        print("-"*50 + "\n")

    def plot_mttr_comparison(self, output_path="mttr_comparison.png"):
        """Generates side-by-side bar plots comparing recovery times."""
        plt.figure(figsize=(10, 6))
        
        ax = sns.barplot(
            x="Scenario_ID", 
            y="MTTR", 
            hue="Run_Type", 
            data=self.df, 
            palette={"Run A (Legacy)": "#E74C3C", "Run B (Proposed)": "#1ABC9C"},
            errorbar=None
        )
        
        plt.title("Mean Time to Recovery (MTTR) Comparison under Chaos Injection", fontsize=14, fontweight="bold", pad=15)
        plt.xlabel("Failure Scenario (Chaos Mesh)", fontsize=12, labelpad=10)
        plt.ylabel("Mean Time to Recovery (Seconds)", fontsize=12, labelpad=10)
        plt.xticks(rotation=15)
        plt.legend(title="Framework Execution")
        
        plt.tight_layout()
        plt.savefig(output_path, dpi=300)
        plt.close()
        print(f"[Visualise] Saved publication-quality bar chart to '{output_path}'")

    def plot_mttd_distribution(self, output_path="mttd_distribution.png"):
        """Generates a kernel density distribution chart of model detection latency."""
        plt.figure(figsize=(8, 5))
        
        proposed_data = self.df[self.df["Run_Type"] == "Run B (Proposed)"]
        
        sns.kdeplot(data=proposed_data, x="MTTD", fill=True, color="#2980B9", alpha=0.6, linewidth=2)
        plt.axvline(proposed_data["MTTD"].mean(), color="#C0392B", linestyle="--", linewidth=1.5, label=f"Mean MTTD: {proposed_data['MTTD'].mean():.2f}s")
        
        plt.title("Distribution of Anomaly Detection Latency (MTTD) in Proposed Framework", fontsize=12, fontweight="bold", pad=15)
        plt.xlabel("Detection Latency (Seconds)", fontsize=10)
        plt.ylabel("Density", fontsize=10)
        plt.legend()
        
        plt.tight_layout()
        plt.savefig(output_path, dpi=300)
        plt.close()
        print(f"[Visualise] Saved distribution plot to '{output_path}'")

def _write_synthetic_smoke_test_csv(path):
    print("=" * 60)
    print("SYNTHETIC SMOKE TEST — NOT RESEARCH EVIDENCE")
    print("Data below is randomly generated, not measured from real chaos trials.")
    print("Do not cite these numbers or charts in the dissertation/checkpoint.")
    print("=" * 60)
    mock_scenarios = ["S1_CPU_Starvation", "S2_Memory_Leak", "S4_Network_Latency"]
    mock_data = []
    for s in mock_scenarios:
        for _ in range(10):
            mock_data.append({"Scenario_ID": s, "Run_Type": "Run A (Legacy)", "MTTD": 300.0, "MTTR": 300.0, "Success": False})
            mock_data.append({"Scenario_ID": s, "Run_Type": "Run B (Proposed)", "MTTD": np.random.normal(4.2, 0.5), "MTTR": np.random.normal(12.5, 1.2), "Success": True})
    pd.DataFrame(mock_data).to_csv(path, index=False)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--results-csv", default="experiment_results.csv",
        help="Real experiment_results.csv from evaluation/analysis/run_campaign.py "
             "or hand-assembled from docs/experiment-log.md.",
    )
    parser.add_argument(
        "--synthetic-smoke-test", action="store_true",
        help="Generate random placeholder data if --results-csv doesn't exist, "
             "purely to sanity-check this script's plotting/summary code before "
             "real campaign data exists. Output is labeled non-evidence.",
    )
    args = parser.parse_args()

    if not os.path.exists(args.results_csv):
        if not args.synthetic_smoke_test:
            sys.exit(
                f"ERROR: {args.results_csv} not found. Run the real chaos campaign "
                "first (evaluation/analysis/run_campaign.py), or pass "
                "--synthetic-smoke-test to sanity-check this script's plotting "
                "code with clearly-labeled placeholder data."
            )
        _write_synthetic_smoke_test_csv(args.results_csv)

    analyser = AcademicDataAnalyser(args.results_csv)
    analyser.print_statistical_summary()
    analyser.plot_mttr_comparison()
    analyser.plot_mttd_distribution()