# Custom Kubernetes Operator

Closed-loop actuator executing remediation via the Kubernetes API. Built in
Python with [kopf](https://kopf.readthedocs.io/) — chosen over Go/kubebuilder
to reuse the rest of this repo's Python stack directly (joblib models,
sklearn scoring) without a cross-language boundary.

**Status: vertical slice.** scenario-01 (CPU starvation -> checkoutservice,
action `scale`) and scenario-07 (pod kill -> frontend, action `restart`)
are implemented end-to-end, proving the Sense->Analyze->Act loop closes for
real before generalizing to the remaining 10 scenarios. `evict` is in the
CRD schema but still rejected at runtime as `unsupported_action`.

## Contents

- `crds/remediationaction-crd.yaml` — the `RemediationAction` CRD; the
  contract between `decision-engine/orchestrator.py` (creates these) and
  this operator (watches + executes them)
- `handlers.py` — the kopf operator: on create, checks dry-run + per-target
  cooldown + max-actions/hour (enforced via annotations on the target
  Deployment, so limits survive operator restarts), executes the action,
  and logs one structured JSON line with trigger signals + matched rule +
  timestamp (the RQ2 explainability evidence)
- `deploy.sh` — installs the CRD only (no in-cluster Deployment/RBAC yet —
  the operator runs out-of-cluster for this vertical slice)
- `requirements.txt` — `pip install -r operator/requirements.txt`

## Responsibilities

- Subscribe to decision engine outputs (`RemediationAction` CRs)
- Execute actions: pod restart, horizontal scaling, node eviction, etc.
- Enforce safety limits (cooldowns, max actions per hour)
- Log every action with trigger context for RQ2 explainability

## Usage

```bash
bash operator/deploy.sh                       # installs the CRD
pip install -r operator/requirements.txt
kopf run operator/handlers.py --namespace boutique
```

Leave that running, then in another terminal drive it end-to-end with
`decision-engine/orchestrator.py` (see `decision-engine/README.md`).

Safety guard tuning (env vars, both optional):
- `COOLDOWN_SECONDS` (default 300) — minimum time between actions on the same target
- `MAX_ACTIONS_PER_HOUR` (default 6) — per-target rate limit
- `OPERATOR_DRY_RUN=true` — global override; logs/reports every action without executing any of them, regardless of each CR's own `dryRun` field

## Planned Contents

- `evict` action implementation, as more of the 12 scenarios' rules are added
- In-cluster Deployment manifest + RBAC (ServiceAccount, ClusterRole) once the vertical slice is proven
- Container image / Dockerfile for in-cluster operation
