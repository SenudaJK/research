# Chaos Scenarios

Twelve failure scenarios for empirical validation (see `docs/methodology-checklist.md`).

| # | File | Scenario | Target service |
|---|------|----------|-----------------|
| 01 | `scenario-01-cpu-starvation.yaml` | CPU starvation (noisy neighbor) | checkoutservice |
| 02 | `scenario-02-memory-leak.yaml` | Memory leak → OOM | cartservice |
| 03 | `scenario-03-disk-io-stress.yaml` | Disk I/O stress | paymentservice* |
| 04 | `scenario-04-network-latency.yaml` | 200ms network latency | recommendationservice |
| 05 | `scenario-05-packet-loss.yaml` | 10% packet loss | shippingservice |
| 06 | `scenario-06-dns-failure.yaml` | DNS resolution failure | frontend (catalog DNS) |
| 07 | `scenario-07-random-pod-kill.yaml` | Random pod kill | frontend |
| 08 | `scenario-08-node-unresponsive.yaml` | Node unresponsiveness | (node-scoped) |
| 09 | `scenario-09-volume-detachment.yaml` | Stateful volume detachment | redis-cart |
| 10 | `scenario-10-http-5xx.yaml` | HTTP 5xx injection | frontend |
| 11 | `scenario-11-db-pool-exhaustion.yaml` | DB connection pool exhaustion | productcatalogservice* |
| 12 | `scenario-12-config-drift.yaml` | Config drift (manual env var modification) | checkoutservice |

\* No real database exists behind this service in Online Boutique — see the
substitution note inside that scenario's YAML file.

Target services and descriptions are aligned to Table I of
`Final Disseration for ICTAC.md`, the fixed reference point for this
project's chaos suite. Each scenario is executed 10 times under Run A
(baseline) and Run B (framework enabled).
