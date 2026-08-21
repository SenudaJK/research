import joblib
import time
import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.svm import OneClassSVM
from sklearn.neighbors import LocalOutlierFactor
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import f1_score, precision_score, recall_score

class ModelTrainerAndComparer:
    def __init__(self, data_path):
        self.data_path = data_path
        self.features = ["cpu_util", "mem_util", "network_rx", "log_error_rate", "trace_latency_ms", "trace_error_pct"]
        self.scaler = StandardScaler()
        
    def load_and_scale(self):
        df = pd.read_csv(self.data_path)
        self.X_train = df[self.features]
        self.X_scaled = self.scaler.fit_transform(self.X_train)
        joblib.dump(self.scaler, "scaler.pkl")
        
    def train_models(self):
        self.load_and_scale()
        results = {}
        
        # 1. ISOLATION FOREST
        start = time.time()
        iforest = IsolationForest(n_estimators=100, contamination=0.01, random_state=42)
        iforest.fit(self.X_scaled)
        iforest_time = time.time() - start
        joblib.dump(iforest, "isolation_forest.pkl")
        results["Isolation Forest"] = {"model": iforest, "train_time": iforest_time}
        
        # 2. ONE-CLASS SVM
        start = time.time()
        ocsvm = OneClassSVM(nu=0.01, kernel="rbf", gamma="scale")
        ocsvm.fit(self.X_scaled)
        ocsvm_time = time.time() - start
        joblib.dump(ocsvm, "oc_svm.pkl")
        results["One-Class SVM"] = {"model": ocsvm, "train_time": ocsvm_time}
        
        # 3. LOCAL OUTLIER FACTOR (LOF)
        start = time.time()
        lof = LocalOutlierFactor(n_neighbors=20, contamination=0.01, novelty=True)
        lof.fit(self.X_scaled)
        lof_time = time.time() - start
        joblib.dump(lof, "lof.pkl")
        results["LOF (Baseline)"] = {"model": lof, "train_time": lof_time}
        
        return results

    def compare_performance_on_mock_validation(self, results):
        """Evaluates models against a synthesized validation set (normal + chaos)."""
        # Synthesize a labeled validation dataset: 200 normal samples, 50 chaos anomalies
        np.random.seed(42)
        normal_val = pd.DataFrame({
            'cpu_util': np.random.normal(35.0, 4.0, 200),
            'mem_util': np.random.normal(45.0, 2.0, 200),
            'network_rx': np.random.normal(1200.0, 80.0, 200),
            'log_error_rate': np.random.poisson(0.1, 200),
            'trace_latency_ms': np.random.normal(15.0, 1.5, 200),
            'trace_error_pct': np.random.binomial(100, 0.01, 200) / 100.0
        })
        
        chaos_val = pd.DataFrame({
            'cpu_util': np.random.normal(12.0, 2.0, 50),          # S4: blocked CPU drop
            'mem_util': np.random.normal(46.0, 1.0, 50),
            'network_rx': np.random.normal(450.0, 50.0, 50),      # Throttle drop
            'log_error_rate': np.random.poisson(15.0, 50),        # 504 logs surge
            'trace_latency_ms': np.random.normal(215.0, 10.0, 50), # 200ms latency spike
            'trace_error_pct': np.random.binomial(100, 0.15, 50) / 100.0
        })
        
        # Ground Truth Labels: 0 = Normal, 1 = Anomaly
        y_true = [0] * 200 + [1] * 50
        X_val_raw = pd.concat([normal_val, chaos_val])
        X_val_scaled = self.scaler.transform(X_val_raw[self.features])
        
        comparison_table = []
        
        for name, data in results.items():
            model = data["model"]
            start_infer = time.time()
            # Predict: 1 = Normal, -1 = Outlier. Map to: 0 = Normal, 1 = Anomaly
            raw_pred = model.predict(X_val_scaled)
            y_pred = [1 if val == -1 else 0 for val in raw_pred]
            infer_time = (time.time() - start_infer) / len(X_val_raw) * 1000 # Microseconds per vector
            
            p = precision_score(y_true, y_pred)
            r = recall_score(y_true, y_pred)
            f1 = f1_score(y_true, y_pred)
            
            comparison_table.append({
                "Algorithm": name,
                "Precision": f"{p:.2%}",
                "Recall": f"{r:.2%}",
                "F1-Score": f"{f1:.2%}",
                "Train Time (s)": f"{data['train_time']:.4f}s",
                "Inference Latency (μs)": f"{infer_time:.2f}μs"
            })
            
        return pd.DataFrame(comparison_table)

if __name__ == "__main__":
    comparer = ModelTrainerAndComparer("healthy_baseline.csv")
    model_results = comparer.train_models()
    comp_df = comparer.compare_performance_on_mock_validation(model_results)
    
    print("\n" + "="*50)
    print("      ACADEMIC MODEL COMPARISON TABLE")
    print("="*50)
    print(comp_df.to_string(index=False))
    print("="*50)