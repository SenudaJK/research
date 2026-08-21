# Google Online Boutique

Microservices testbed workload for fault injection and recovery evaluation.

## Deploy

```bash
# Requires observability stack (OTEL collector) to be running first
bash infra/boutique/deploy.sh
```

Or deploy everything via `bash infra/scripts/deploy-phase1.sh`.

## Configuration

- **Version:** `v0.10.6` (pinned in `infra/versions.env`)
- **Namespace:** `boutique`
- **Tracing:** OTLP gRPC → `opentelemetry-collector.observability.svc.cluster.local:4317`
- **Metrics:** Cluster metrics via kube-prometheus-stack; app RED metrics via OTEL spanmetrics → Prometheus
- **Traffic:** Built-in `loadgenerator` generates continuous requests

## Files

| File | Purpose |
|------|---------|
| `kustomization.yaml` | Remote base from microservices-demo v0.10.6 |
| `patches/otel-tracing.yaml` | ENABLE_TRACING + COLLECTOR_SERVICE_ADDR on 8 services |
| `deploy.sh` | Apply kustomize overlay and wait for rollouts |

Reference: https://github.com/GoogleCloudPlatform/microservices-demo
