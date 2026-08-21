# Chaos Scenarios

Twelve failure scenarios for empirical validation (see `docs/methodology-checklist.md`).

| # | File (planned) | Scenario |
|---|----------------|----------|
| 01 | `scenario-01-cpu-starvation.yaml` | CPU starvation (noisy neighbor) |
| 02 | `scenario-02-memory-leak.yaml` | Memory leak → OOM |
| 03 | `scenario-03-disk-io-stress.yaml` | Disk I/O stress |
| 04 | `scenario-04-network-latency.yaml` | 200ms network latency |
| 05 | `scenario-05-packet-loss.yaml` | 10–20% packet loss |
| 06 | `scenario-06-dns-failure.yaml` | DNS resolution failure |
| 07 | `scenario-07-random-pod-kill.yaml` | Random pod kill |
| 08 | `scenario-08-node-unresponsive.yaml` | Node unresponsiveness |
| 09 | `scenario-09-volume-detachment.yaml` | Stateful volume detachment |
| 10 | `scenario-10-http-5xx.yaml` | HTTP 5xx injection |
| 11 | `scenario-11-db-pool-exhaustion.yaml` | DB connection pool exhaustion |
| 12 | `scenario-12-config-drift.yaml` | Port misconfig / auth revocation |

Each scenario is executed 10 times under Run A (baseline) and Run B (framework enabled).
