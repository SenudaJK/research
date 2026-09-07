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
- `score.py` — scores a NEW State Vector CSV against an already-trained, already-frozen model (the actual detection step — use this for fault dry runs, never train_and_compare.py again)
- `playbook.yaml` — rule playbook (vertical slice: scenario-01 only — CPU starvation -> scale checkoutservice)
- `orchestrator.py` — the actual "Analyze -> Plan" step: scores a State Vector, matches the anomaly to a playbook rule, and creates a `RemediationAction` CR for `operator/` to execute (distinct from `score.py`, which only reports — this one closes the loop)
- `requirements.txt` — `pip install -r decision-engine/requirements.txt`

## Usage

The full collect -> fuse -> train sequence is automated in
`infra/scripts/run-training-pipeline.sh` — it resolves the run id itself
(via `evaluation/runs/baseline/.last_run_id`, written by `collect-baseline.sh`)
and refuses to train if Jaeger restarted mid-collection:

```bash
bash infra/scripts/run-training-pipeline.sh
```

Or run the steps individually:

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

## Dry-running fault detection (before Phase 3/4 exist)

Automated, one command — clears any stale leftover experiment, applies
fresh, waits for it to actually be active, captures telemetry, fuses,
scores, and cleans up, refusing to score the trial if Jaeger restarted
during capture:

```bash
bash infra/scripts/run-fault-dry-run.sh \
  evaluation/scenarios/scenario-01-cpu-starvation.yaml \
  evaluation/runs/baseline/<trained-run-id>/model-artifacts
```

Or manually, step by step:

1. Inject a fault and, WHILE it is active, collect a short telemetry window:
   ```bash
   BASELINE_DURATION_SECONDS=120 BASELINE_SAMPLE_INTERVAL_SECONDS=30 \
     bash infra/scripts/collect-baseline.sh
   ```
   This writes a new run under `evaluation/runs/baseline/<new-run-id>/` — it
   is NOT a training baseline, just a telemetry capture; ignore its
   `trainable` verdict.
2. Fuse it the same way as any run:
   ```bash
   python3 fusion-engine/build_state_vector.py --run-dir evaluation/runs/baseline/<new-run-id>
   ```
3. Score it against the model trained on your real baseline (never retrain
   on the fault window — tau must stay frozen):
   ```bash
   python3 decision-engine/score.py \
     --model-dir evaluation/runs/baseline/<original-trainable-run-id>/model-artifacts \
     --state-vector-csv evaluation/runs/baseline/<new-run-id>/state_vector.csv
   ```
   A row with `detected=True` is a successful dry run.

Timing matters: the fault must still be active (or its effect still visible
in metrics/logs/traces) during step 1's collection window — a fault injected
and fully recovered before you start collecting won't show up.

## Closing the loop for real (Phase 3 vertical slice)

Once a fault dry run (above) confirms `detected=True`, instead of stopping
at `score.py`'s report, run the orchestrator to actually emit a
`RemediationAction` and let the operator act on it:

```bash
# Terminal 1 — install the CRD and start the operator (see operator/README.md)
bash operator/deploy.sh
pip install -r operator/requirements.txt
kopf run operator/handlers.py --namespace boutique

# Terminal 2 — drive it with a fault-window State Vector
python3 decision-engine/orchestrator.py \
  --model-dir evaluation/runs/baseline/<run-id>/model-artifacts \
  --state-vector-csv evaluation/runs/baseline/<fault-run-id>/state_vector.csv
  # add --execute to have the operator actually scale (omit for dryRun:true)
```

Watch it happen: `kubectl get remediationaction -n boutique -w`

## Planned Contents

- Rule playbook coverage for the other 11 scenarios
- `restart`/`evict` action support in the orchestrator + operator
- Multi-feature/compound rule matching (current matching is single strongest-feature only)
