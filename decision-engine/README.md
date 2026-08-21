# Decision Engine

Hybrid decision-making: Isolation Forest + deterministic rule-based playbook.

## Responsibilities

- Train/evaluate Isolation Forest on baseline State Vectors
- Score incoming vectors for anomaly detection
- Map anomaly patterns to recovery actions via heuristic logic tree
- Emit decisions with explainability metadata (matched rule, signals)

## Planned Contents

- Model training and inference code
- Rule playbook (YAML/JSON)
- Hybrid decision orchestrator
- Dry-run and safety guard configuration
