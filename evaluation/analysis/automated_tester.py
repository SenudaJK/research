import time
import subprocess
import json
import pandas as pd

class ChaosTester:
    def __init__(self, target_service="recommendation-service", namespace="default"):
        self.target_service = target_service
        self.namespace = namespace

    def run_cmd(self, cmd):
        """Executes a local shell command (e.g. kubectl apply)."""
        result = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        return result.stdout.strip()

    def inject_chaos(self, manifest_path):
        print(f"[Chaos] Applying Chaos Mesh manifest: {manifest_path}")
        self.run_cmd(f"kubectl apply -f {manifest_path}")

    def clear_chaos(self, manifest_path):
        print(f"[Chaos] Deleting Chaos Mesh manifest: {manifest_path}")
        self.run_cmd(f"kubectl delete -f {manifest_path}")
        # Wait for system cool-down
        time.sleep(30)

    def monitor_run_a_legacy(self, scenario_id, timeout_sec=300):
        """Runs the control trial (Native K8s). Measures recovery time (infinite for gray failures)."""
        print(f"\n--- [Run A] Starting Trial for {scenario_id} ---")
        start_time = time.time()
        detected_time = None
        recovered_time = None
        
        # Poll to see if K8s natively restarts container
        while (time.time() - start_time) < timeout_sec:
            # Check pod restart counts
            status = self.run_cmd(f"kubectl get pods -n {self.namespace} -l app={self.target_service} -o json")
            if status:
                pod_info = json.loads(status)
                restarts = pod_info["items"][0]["status"]["containerStatuses"][0]["restartCount"]
                
                # In gray failures, restarts remain 0, triggering manual SRE paging (timeout representation)
                if restarts > 0:
                    recovered_time = time.time()
                    detected_time = time.time() # K8s doesn't tell us when it 'detected' it
                    break
            time.sleep(5)
            
        mttd = (detected_time - start_time) if detected_time else timeout_sec
        mttr = (recovered_time - start_time) if recovered_time else timeout_sec
        return {"Scenario_ID": scenario_id, "Run_Type": "Run A (Legacy)", "MTTD": mttd, "MTTR": mttr, "Success": (recovered_time is not None)}

    def monitor_run_b_proposed(self, scenario_id, detector_script_path, timeout_sec=120):
        """Runs the experimental trial. Evaluates ML detection and Operator actuation."""
        print(f"\n--- [Run B] Starting Trial for {scenario_id} ---")
        start_time = time.time()
        
        # Start your ML detector background daemon
        proc = subprocess.Popen(["python", detector_script_path, "--live-eval", self.target_service], stdout=subprocess.PIPE, text=True)
        
        mttd = None
        mttr = None
        success = False
        
        # Monitor stdout of our Sense-Analyze-Act pipeline
        try:
            while (time.time() - start_time) < timeout_sec:
                line = proc.stdout.readline()
                if "Anomaly Detected!" in line and mttd is None:
                    mttd = time.time() - start_time
                    print(f"[*] Proposed Framework detected anomaly in {mttd:.2f} seconds.")
                if "Self-healing complete." in line:
                    mttr = time.time() - start_time
                    print(f"[*] Custom Operator completed eviction in {mttr:.2f} seconds.")
                    success = True
                    break
                time.sleep(1)
        finally:
            proc.terminate()
            
        mttd = mttd if mttd else timeout_sec
        mttr = mttr if mttr else timeout_sec
        return {"Scenario_ID": scenario_id, "Run_Type": "Run B (Proposed)", "MTTD": mttd, "MTTR": mttr, "Success": success}

    def execute_evaluation_campaign(self, scenario_manifests, num_runs=10):
        """Executes all 120 campaign test runs automatically."""
        all_results = []
        
        for scenario_id, manifest in scenario_manifests.items():
            for run_num in range(1, num_runs + 1):
                print(f"\n==========================================")
                print(f" EXECUTE EXPERIMENT RUN {run_num}/{num_runs} FOR {scenario_id}")
                print(f"==========================================")
                
                # --- RUN A (LEGACY) ---
                self.inject_chaos(manifest)
                res_a = self.monitor_run_a_legacy(scenario_id)
                all_results.append(res_a)
                self.clear_chaos(manifest)
                
                # --- RUN B (PROPOSED) ---
                self.inject_chaos(manifest)
                # Pass path to your Component 3 Anomaly Detector script
                res_b = self.monitor_run_b_proposed(scenario_id, "decision-engine/detector.py")
                all_results.append(res_b)
                self.clear_chaos(manifest)
                
                # Auto-save results table
                pd.DataFrame(all_results).to_csv("experiment_results.csv", index=False)
                
        print("\nAll 120 testing campaign loops completed. Master results written to 'experiment_results.csv'.")

if __name__ == "__main__":
    tester = ChaosTester()
    
    # Map of your 12 Chaos Mesh manifests matching SLR definitions
    scenarios = {
        "S1_CPU_Starvation": "infra/chaos/cpu-starvation.yaml",
        "S2_Memory_Leak": "infra/chaos/memory-leak.yaml",
        "S4_Network_Latency": "infra/chaos/network-latency.yaml",
        # ... Add references for all 12 scenarios
    }
    
    # Run the automated testing loops
    tester.execute_evaluation_campaign(scenarios, num_runs=10)