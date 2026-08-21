# Custom Kubernetes Operator

Closed-loop actuator executing remediation via the Kubernetes API.

## Responsibilities

- Subscribe to decision engine outputs
- Execute actions: pod restart, horizontal scaling, node eviction, etc.
- Enforce safety limits (cooldowns, max actions per hour)
- Log every action with trigger context for RQ2 explainability

## Planned Contents

- Operator implementation (Go or Python)
- CRDs or webhook handlers (TBD)
- Deployment manifests for in-cluster installation
