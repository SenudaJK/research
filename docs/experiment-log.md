# Experiment Log

Record each chaos engineering run for reproducibility and thesis evidence.

## Template

```markdown
## Scenario {N} - {Name} - Run {A|B} - Iteration {1-10}

- **Date:**
- **T0 fault start (UTC):**
- **Td detection (UTC):**
- **Tr recovery (UTC):**
- **Te trial end (UTC):**
- **MTTD (Td − T0):**
- **MTTR (Tr − T0):**
- **Availability (frontend success spans / all frontend server spans):**
- **Detection outcome:** TP / FP / FN / TN
- **Run A signal (if A):** success-rate | pod-not-ready | restart-count
- **Run B score / τ (if B):**
- **Rule matched (if B):**
- **Action taken (if B):** dry-run | restart | scale | evict | none
- **Censored:** no / MTTD / MTTR
- **Notes:**
```

Definitions: `docs/measurement-protocol.md`.

---

## Runs

<!-- Add entries below as experiments are executed -->

## Scenario 1 - CPU Starvation (checkoutservice) - Phase 2 dry run (not a formal Run A/B campaign iteration)

- **Date:** 2026-09-07
- **Purpose:** First validation that the trained Isolation Forest detects a real, uncorrupted injected fault — the "at least one injected fault detected in dry run" item in `docs/methodology-checklist.md` Phase 2.
- **Model:** `evaluation/runs/baseline/20260906T023729Z/model-artifacts` (trained on 197/200 clean rows, `tau` at p95 = 0.516964)
- **T0 fault start (UTC):** not captured precisely — `run-fault-dry-run.sh` does not yet log the exact `kubectl apply` timestamp; approx. 2026-09-07T02:21:25Z (inferred from the phase-confirmation poll completing quickly before the first capture sample at 02:21:45Z). **Action item:** add explicit T0 logging to the script before using it for real Run A/B trials.
- **Td detection (UTC):** 2026-09-07T02:22:15Z (first row scoring above tau)
- **MTTD (approx, given T0 uncertainty):** ~50s
- **Fault-window run:** `evaluation/runs/baseline/20260907T022139Z` (0/4 rows missing features; Jaeger restart count unchanged 4->4 during capture — trial confirmed uncorrupted)
- **Detection outcome:** TP (3/4 sampled rows detected; row 1 at ~20s post-fault was below tau, rows 2-4 above)
- **Run B score / τ:** scores 0.489, 0.521, 0.560, 0.558 vs τ=0.516964
- **Rule matched:** not run through `orchestrator.py` for this trial — detection-only (`score.py`), to isolate testing the threshold change from testing the Phase 3 actuation loop (which was separately verified live the same day — see notes)
- **Action taken:** none (detection-only dry run)
- **Censored:** no
- **Notes:**
  - `threshold_percentile` was changed from 99 to 95 the same day (see `decision-engine/model-config.yaml`) after p99 failed to detect an earlier, equally clean trial (`20260907T021502Z`) despite a real ~21-sigma `cpu_util` deviation — diagnosed as p99 sitting too close to the training score ceiling for a 6-feature/single-anomalous-dimension case. p95 was chosen from the training score distribution alone, then validated on *this* fresh trial (not the one used for diagnosis) to avoid tuning tau on chaos data.
  - Phase 3 (operator executing a real `scale` action end-to-end, with cooldown/rate-limit guards both verified) was proven working live earlier the same day via a manual test `RemediationAction`, independent of this detection trial.

## Scenario 1 - CPU Starvation (checkoutservice) - Phase 2 dry run #2 (T0-fix validation)

- **Date:** 2026-09-07
- **Purpose:** Second independent confirmation of p95 detection (different trial from the one above), and first validation of the corrected T0 capture in `infra/scripts/run-fault-dry-run.sh` (see Notes — the field named in the original protocol doesn't exist on this chart's status schema).
- **Model:** same as above, `evaluation/runs/baseline/20260906T023729Z/model-artifacts`, tau (p95) = 0.516964
- **T0 fault start (UTC):** 2026-09-07T04:20:28Z (source: `status.instances.*.startTime`, recorded in `evaluation/runs/baseline/20260907T042028Z/meta/t0.json` — first trial with a precisely captured T0)
- **Td detection (UTC):** 2026-09-07T04:21:03Z
- **MTTD (Td − T0):** 35s (first trial with an exact, protocol-correct MTTD)
- **Fault-window run:** `evaluation/runs/baseline/20260907T042028Z` (0/4 rows missing features; Jaeger restart count unchanged 4->4 — trial confirmed uncorrupted)
- **Detection outcome:** TP (3/4 sampled rows detected; scores 0.457, 0.604, 0.602, 0.596 vs τ=0.516964)
- **Run B score / τ:** see above
- **Rule matched:** not run (detection-only, `score.py`)
- **Action taken:** none
- **Censored:** no
- **Notes:**
  - Confirmed empirically that `status.experimentStartTime` (the field named in the original `docs/measurement-protocol.md`) does not exist on StressChaos's status in chaos-mesh 2.8.4. The real field is `status.instances.<key>.startTime`; `docs/measurement-protocol.md` and `run-fault-dry-run.sh` were both corrected same-day.
  - This is the second scenario-01 trial to independently confirm detection at p95 (see the prior entry above), on different fault-window data each time.
