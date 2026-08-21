import time
import os
import requests
import numpy as np
import pandas as pd

class TelemetryHarvester:
    def __init__(self, prom_url="http://localhost:9090", loki_url="http://localhost:3100", jaeger_url="http://localhost:16686"):
        self.prom_url = prom_url
        self.loki_url = loki_url
        self.jaeger_url = jaeger_url
        self.feature_cols = ["cpu_util", "mem_util", "network_rx", "log_error_rate", "trace_latency_ms", "trace_error_pct"]

    def get_prometheus_metric(self, query):
        """Queries Prometheus API for instant metric values."""
        try:
            response = requests.get(f"{self.prom_url}/api/v1/query", params={"query": query}, timeout=5)
            result = response.json()["data"]["result"]
            if result:
                return float(result[0]["value"][1])
        except Exception:
            pass
        return 0.0

    def get_loki_error_rate(self, query_range, window_sec=30):
        """Queries Loki API for log rate matching error terms in the sliding window."""
        try:
            # Query log counts containing "error", "fail", or "exception"
            # In production, substitute actual LogQL query based on service
            response = requests.get(f"{self.loki_url}/loki/api/v1/query", params={"query": query_range}, timeout=5)
            result = response.json()["data"]["result"]
            if result:
                return float(len(result[0]["values"])) / window_sec
        except Exception:
            pass
        return 0.0

    def get_jaeger_traces(self, service_name, limit=50):
        """Queries Jaeger traces API to calculate average trace duration and error rate."""
        try:
            response = requests.get(f"{self.jaeger_url}/api/traces", params={"service": service_name, "limit": limit}, timeout=5)
            traces = response.json()["data"]
            latencies = []
            error_count = 0
            
            for trace in traces:
                for span in trace["spans"]:
                    latencies.append(span["duration"] / 1000.0) # convert microseconds to ms
                    # Check for error tag in distributed trace span
                    for tag in span.get("tags", []):
                        if tag.get("key") == "error" and tag.get("value") is True:
                            error_count += 1
            
            avg_latency = np.mean(latencies) if latencies else 0.0
            error_pct = (error_count / len(latencies)) if latencies else 0.0
            return avg_latency, error_pct
        except Exception:
            return 0.0, 0.0

    def compile_state_vector(self, service_name):
        """Fuses multi-modal streams into a unified State Vector."""
        # Prometheus queries for target deployment
        cpu_q = f'sum(node_namespace_pod_container:container_cpu_usage_seconds_total:sum_irate{{container="{service_name}"}})'
        mem_q = f'sum(container_memory_working_set_bytes{{container="{service_name}"}})'
        net_q = f'sum(container_network_receive_bytes_total{{pod=~"{service_name}-.*"}})'
        
        # Loki query
        loki_q = f'{{app="{service_name}"}} |~ "(?i)(error|fail|exception|504)"'
        
        cpu = self.get_prometheus_metric(cpu_q)
        mem = self.get_prometheus_metric(mem_q) / (1024 * 1024) # Normalise to MB
        net = self.get_prometheus_metric(net_q) / 1024          # Normalise to KB/s
        logs = self.get_loki_error_rate(loki_q)
        latency, errors = self.get_jaeger_traces(service_name)
        
        return {
            "timestamp": int(time.time()),
            "cpu_util": cpu,
            "mem_util": mem,
            "network_rx": net,
            "log_error_rate": logs,
            "trace_latency_ms": latency,
            "trace_error_pct": errors
        }

    def harvest_loop(self, service_name, output_csv, duration_sec=1800, interval_sec=30):
        """Continuously harvests telemetry to construct your training baseline dataset."""
        print(f"Starting telemetry harvest loop for {service_name}...")
        records = []
        elapsed = 0
        
        while elapsed < duration_sec:
            start_time = time.time()
            vector = self.compile_state_vector(service_name)
            records.append(vector)
            
            # Auto-save backup at each step
            df = pd.DataFrame(records)
            df.to_csv(output_csv, index=False)
            
            print(f"[Sense] Timestamp: {vector['timestamp']} | Latency: {vector['trace_latency_ms']:.2f}ms | CPU: {vector['cpu_util']:.2f}% | Saved to {output_csv}")
            
            time.sleep(max(0, interval_sec - (time.time() - start_time)))
            elapsed += interval_sec

if __name__ == "__main__":
    harvester = TelemetryHarvester()
    # Harvest 30 minutes of baseline healthy cluster telemetry
    harvester.harvest_loop("recommendation-service", "healthy_baseline.csv", duration_sec=1800, interval_sec=30)
