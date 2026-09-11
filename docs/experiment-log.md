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

## Scenario 1 - CPU Starvation - Run A - Iteration 1

- **Date:** 2026-09-07
- **T0 fault start (UTC):** 2026-09-07T13:27:08.658233Z (source: apply_timestamp_fallback — `status.instances` wasn't populated by the time T0 was captured for this trial; see `evaluation/analysis/run_trial.py`)
- **Td detection (UTC):** 2026-09-07T13:27:14.715901Z
- **Tr recovery (UTC):** 2026-09-07T13:28:14.350380Z
- **Te trial end (UTC):** n/a (recovered before the 300s timeout used for this trial)
- **MTTD (Td − T0):** 6.1s
- **MTTR (Tr − T0):** 65.7s
- **Availability:** not computed this trial (frontend_success_rate sampled at 1.0 then 0.992; a proper span-count-based availability calculation per `docs/measurement-protocol.md` is not yet implemented in `run_trial.py` — see Notes)
- **Detection outcome:** TP (native Run A signal fired inside the fault window)
- **Run A signal:** pod-not-ready
- **Censored:** no
- **Notes:**
  - First trial run through the new `evaluation/analysis/run_trial.py`, the first script in this repo that measures Tr/MTTR (not just detection). Trial confirmed valid — Jaeger restart count unchanged (4->4) throughout.
  - `cpu_util` at the second sample (t+66s) was 0.600 vs baseline mean 0.145 — the fault was genuinely active and strong — yet `frontend_success_rate` barely moved (1.0 -> 0.992), which is why recovery registered so quickly: this fault type stresses the backend without translating into failed frontend requests at this load level. Worth noting for RQ3 discussion: SLO-based recovery may under-measure the real impact of resource-starvation faults that manifest as latency rather than errors.
  - The `pod-not-ready` signal at t+6s is suspiciously fast for a CPU stress fault and is more likely a transient readiness-probe flap caused by Chaos Mesh's chaos-daemon injecting into the container's process namespace, not the CPU stress itself. Worth a second iteration to see if this is consistent or a one-off.
  - Raw sample data: `evaluation/runs/trials/scenario-01-cpu-starvation-RunA-20260907T132814.679824Z.json`
  - Known gaps in `run_trial.py` to fix before treating this as final campaign data: (1) availability isn't computed from raw span counts per the protocol's exact formula, just approximated via the sampled success-rate; (2) T0 fell back to the apply timestamp rather than the more precise `status.instances` field this time — worth investigating why the instance field wasn't populated yet at the check.

## Scenario 1 - CPU Starvation - Run B - Iteration 1 (rule-matching fix validation)

- **Date:** 2026-09-07
- **Model:** `evaluation/runs/baseline/20260906T023729Z/model-artifacts`, tau (p95) = 0.516964
- **T0 fault start (UTC):** 2026-09-07T13:52:54Z (source: `status.instances.*.startTime`)
- **Td detection (UTC):** 2026-09-07T13:53:00.925602Z
- **Tr recovery (UTC):** 2026-09-07T13:54:00.711889Z
- **Te trial end (UTC):** n/a (recovered before the 300s timeout)
- **MTTD (Td − T0):** 6.9s
- **MTTR (Tr − T0):** 66.7s
- **Availability:** not computed this trial (same gap as the Run A entry above)
- **Detection outcome:** TP
- **Run B score / τ:** 0.559 at Td vs τ=0.516964; 0.687 at the sample that matched a rule (t+67s)
- **Rule matched:** R1-cpu-starvation-scale, matched on the SECOND sample (cpu_util z=22.19) — the first anomalous sample (t+7s) scored above tau but only `log_error_rate` (z=12.71) exceeded the match threshold at that instant, and `log_error_rate` has no playbook rule
- **Action taken:** scale — **executed for real** (`operator/handlers.py`, not a manual test): `RemediationAction/r1-cpu-starvation-scale-1788789240`, checkoutservice scaled 1 -> 3, `status.phase=Succeeded`
- **Censored:** no
- **Notes:**
  - This is the first fully autonomous closed-loop trial in the project: detection, rule matching, CR creation, and operator execution all happened without any manual CR creation — compare to the manual `live-test-scale-001` test on 2026-09-07 earlier the same day, which proved the mechanism but wasn't a real detection-triggered action.
  - This trial is the second attempt at Run B/Iteration 1; the first attempt (same T0 window structure, different timestamps) hit two real bugs, both fixed same-day before this run: (1) rule matching only considered the single most-deviated feature overall, so a `log_error_rate` spike (present at fault injection in every scenario-01 trial so far — worth investigating as a real, consistent side effect of Chaos Mesh's injection process, not noise) blocked a `cpu_util`-triggered rule from ever matching; (2) once `Td` latched on the first anomalous sample, the code never retried rule-matching on later samples even though `cpu_util` became overwhelmingly significant (z=22) one sample later. Both fixes are in `decision-engine/orchestrator.py`, `evaluation/analysis/run_trial.py`, and `decision-engine/model-config.yaml` (`rule_match_z_threshold: 3.0`).
  - checkoutservice was manually scaled back to 1 replica after this trial to restore a clean starting state for the next trial — `run_trial.py` does not yet do this automatically (known gap).
  - Direct comparison to Run A/Iteration 1 above: MTTD is nearly identical (6.9s vs 6.1s — both conditions detect fast for this fault type), but this is not yet a meaningful MTTR comparison, since Run A's fast "recovery" was really the SLO barely dipping (1.0 -> 0.992) rather than the operator's scale-up meaningfully mattering. A fault type/scenario where the SLO is genuinely and sustainedly impacted is needed to show Run B's actuation actually shortens recovery relative to Run A — scenario-01's fault may not be a strong test case for that comparison at this load level.
