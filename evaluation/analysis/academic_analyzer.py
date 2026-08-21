import os
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns

class AcademicDataAnalyser:
    def __init__(self, results_csv):
        self.df = pd.read_csv(results_csv)
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
        
        improvement = ((avg_legacy_mttr - avg_proposed_mttr) / avg_legacy_mttr) * 100
        print("\n" + "-"*50)
        print(f"Global Average Legacy MTTR: {avg_legacy_mttr:.2f} seconds")
        print(f"Global Average Proposed MTTR: {avg_proposed_mttr:.2f} seconds")
        print(f"Overall MTTR Reduction: {improvement:.2f}% (Target: >84.2%)")
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

if __name__ == "__main__":
    # Create mock experimental data if the CSV doesn't exist yet for demo run
    if not os.path.exists("experiment_results.csv"):
        mock_scenarios = ["S1_CPU_Starvation", "S2_Memory_Leak", "S4_Network_Latency"]
        mock_data = []
        for s in mock_scenarios:
            for _ in range(10): # 10 runs each
                # Run A (Legacy K8s is slow or times out on gray failures)
                mock_data.append({"Scenario_ID": s, "Run_Type": "Run A (Legacy)", "MTTD": 300.0, "MTTR": 300.0, "Success": False})
                # Run B (Our system is fast)
                mock_data.append({"Scenario_ID": s, "Run_Type": "Run B (Proposed)", "MTTD": np.random.normal(4.2, 0.5), "MTTR": np.random.normal(12.5, 1.2), "Success": True})
        pd.DataFrame(mock_data).to_csv("experiment_results.csv", index=False)

    analyser = AcademicDataAnalyser("experiment_results.csv")
    analyser.print_statistical_summary()
    analyser.plot_mttr_comparison()
    analyser.plot_mttd_distribution()