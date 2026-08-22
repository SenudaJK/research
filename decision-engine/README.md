# Decision Engine

Hybrid decision-making: Isolation Forest + deterministic rule-based playbook.

## Responsibilities

- Train/evaluate Isolation Forest on baseline State Vectors
- Score incoming vectors for anomaly detection
- Map anomaly patterns to recovery actions via heuristic logic tree
- Emit decisions with explainability metadata (matched rule, signals)

## Contents

- `model-config.yaml` — hyperparameters for Isolation Forest, One-Class SVM, LOF, plus `threshold_percentile` for tau
- `train_and_compare.py` — trains all three models on a quality-gated baseline, freezes tau, optionally produces the algorithm comparison table
- `requirements.txt` — `pip install -r decision-engine/requirements.txt`

## Usage

```bash
python3 decision-engine/train_and_compare.py \
  --run-dir evaluation/runs/baseline/<run-id> \
  --state-vector-csv state_vector.csv
```

Refuses to run unless `<run-dir>/meta/quality.json` says `"trainable": true`.
Writes `scaler.pkl`, `isolation_forest.pkl`, `oc_svm.pkl`, `lof.pkl`, and
`threshold.json` (tau) into `<run-dir>/model-artifacts/`.

The comparison table (Isolation Forest vs OC-SVM vs LOF) is skipped by
default — it requires real labeled data:

```bash
python3 decision-engine/train_and_compare.py \
  --run-dir evaluation/runs/baseline/<run-id> \
  --validation-csv evaluation/runs/<chaos-run-id>/labeled_state_vectors.csv
```

`--synthetic-smoke-test` runs the comparison against randomly generated data
purely to sanity-check the pipeline before Phase 4 exists. Its output is
labeled "NOT RESEARCH EVIDENCE" and must never be cited in the dissertation.

## Planned Contents

- Rule playbook (YAML/JSON)
- Hybrid decision orchestrator
- Dry-run and safety guard configuration
