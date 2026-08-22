# Chaos Mesh

Chaos engineering platform installation and base configuration for Phase 4 (Validate).

## Contents

- `values.yaml` — Helm values (containerd runtime for Kind/Minikube, DNS server enabled for `DNSChaos`, dashboard in security mode)
- `deploy.sh` — installs Chaos Mesh into the `chaos-mesh` namespace via Helm, pinned to `HELM_CHAOS_MESH_CHART_VERSION` in `infra/versions.env`

## Deploy

```bash
bash infra/chaos-mesh/deploy.sh
kubectl get pods -n chaos-mesh
```

Dashboard: `kubectl port-forward -n chaos-mesh svc/chaos-dashboard 2333:2333` → http://localhost:2333

## Notes

- Blast radius is scoped per-experiment via each manifest's `selector.namespaces: [boutique]` in `evaluation/scenarios/`, not via chart-level namespace restriction — `clusterScoped` is left at the chart default (`true`).
- `chaos-mesh` and `observability` namespaces must never be chaos targets — this would poison the RQ1 telemetry features, the same reason Promtail excludes the `observability` namespace (see `docs/measurement-protocol.md`).
- Scenario 6 (`scenario-06-dns-failure.yaml`) requires `dnsServer.create: true`, already set in `values.yaml`.
- Scenario 8 (`scenario-08-node-unresponsive.yaml`) and scenario 9 (`scenario-09-volume-detachment.yaml`) use documented substitutions — Chaos Mesh has no native NodeChaos or volume-detach action; see comments in those files.
- Scenario 12 (`scenario-12-config-drift.yaml`) is a plain Kubernetes `NetworkPolicy`, not a Chaos Mesh CRD.
