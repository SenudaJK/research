# Observability Stack

Multi-modal telemetry for Phase 1 (Sense): metrics, logs, and distributed traces.

## Deploy

```bash
bash infra/observability/deploy.sh
```

## Components

| Pillar | Tool | Helm Chart | Version |
|--------|------|------------|---------|
| Metrics | Prometheus (+ Grafana) | kube-prometheus-stack | 88.3.0 |
| Logs | Loki + Promtail | loki, promtail | 6.30.1, 6.17.0 |
| Traces | Jaeger + OTEL Collector | jaeger, opentelemetry-collector | 3.4.1, 0.127.2 |

All deploy to namespace `observability`.

## Values Files

| File | Component |
|------|-----------|
| `values/kube-prometheus-stack.yaml` | Prometheus, Grafana, Alertmanager, exporters |
| `values/loki.yaml` | Log aggregation (single-binary) |
| `values/promtail.yaml` | Log shipping DaemonSet |
| `values/jaeger.yaml` | Jaeger all-in-one |
| `values/otel-collector.yaml` | OTLP receiver → Jaeger + spanmetrics → Prometheus exporter |

## Verify

```bash
bash infra/scripts/verify-stack.sh
kubectl get pods -n observability
```

## Collect Baseline

After boutique is running with load generator. Definitions: `docs/measurement-protocol.md`.

```bash
bash infra/scripts/verify-stack.sh
bash infra/scripts/collect-baseline.sh                          # 30 minutes
bash infra/scripts/check-baseline-quality.sh                    # required before training
# BASELINE_DURATION_SECONDS=180 bash infra/scripts/collect-baseline.sh   # smoke only
```

Output: `evaluation/runs/baseline/<timestamp>/` with `meta/quality.json`.
