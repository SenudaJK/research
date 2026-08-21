# Fusion Engine

Multi-modal telemetry fusion: Prometheus metrics, Loki logs, Jaeger traces.

## Responsibilities

- Ingest heterogeneous telemetry streams
- Normalize and scale features uniformly
- Time-align via sliding window onto a temporal grid
- Output unified **State Vector** (JSON schema TBD)

## Planned Contents

- Ingestion clients/adapters
- Preprocessing and fusion pipeline
- State vector schema definition
- API or event stream for downstream anomaly detection
