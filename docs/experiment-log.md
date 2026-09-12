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

## 2026-09-11 — `run_trial.py` gap fixes (no new trial run yet)

Addressed three known gaps flagged in the Run A/B Iteration 1 entries above, before running further scenario-01 iterations:

1. **Availability** now computed from raw `boutique_traces_span_metrics_calls_total` span counts over `[T_0, max(T_r, T_e)]` per `docs/measurement-protocol.md`'s exact formula (`compute_availability()`), instead of approximated from the sampled `frontend_success_rate`. Computed while port-forwards are still open, right after the sampling loop ends.
2. **T0 capture** now retries `status.instances.*.startTime` up to 5 times (1.5s apart) before falling back to the less precise `containerRecords` timestamp or the `kubectl apply` timestamp — Run A/Iteration 1 fell back to `apply_timestamp_fallback` because the field wasn't populated yet at the single check performed at the time.
3. **Replica restoration**: `deployment_replica_snapshot()`/`restore_replicas()` capture each boutique deployment's replica count before the trial and restore any that differ afterward (in the trial's `finally` block), so a Run B scale action no longer requires the manual `kubectl scale ... --replicas=1` done after Run B/Iteration 1.

Not yet re-validated with a live trial (requires a running cluster) — next scenario-01 iteration should confirm all three behave as expected before treating further data as campaign-final.

## Scenario 1 - CPU Starvation - Run B - Iteration 2 (`run_trial.py` fixes validated end-to-end)

- **Date:** 2026-09-11
- **Model:** `evaluation/runs/baseline/20260906T023729Z/model-artifacts`, tau (p95) = 0.516964
- **T0 fault start (UTC):** 2026-09-11T01:37:35Z (source: `status.instances.*.startTime`, first attempt, no retries needed)
- **Td detection (UTC):** 2026-09-11T01:37:41.266566Z
- **Tr recovery (UTC):** 2026-09-11T01:38:41.256237Z
- **Te trial end (UTC):** n/a (recovered before the 600s timeout)
- **MTTD (Td − T0):** 6.3s
- **MTTR (Tr − T0):** 66.3s
- **Availability:** 1.0000 (computed from real `boutique_traces_span_metrics_calls_total` span counts over `[T0, Tr]`, not approximated — see Notes)
- **Detection outcome:** TP
- **Run B score / τ:** 0.5778 at Td vs τ=0.516964; rule matched on the second sample (cpu_util z=23.78) — first sample's top feature was again log_error_rate (z=9.38), same pattern as Iteration 1
- **Rule matched:** R1-cpu-starvation-scale
- **Action taken:** scale — executed for real, `RemediationAction/r1-cpu-starvation-scale-1789090721`, checkoutservice scaled 1 -> 3, `status.phase=Succeeded`
- **Censored:** no
- **Notes:**
  - First trial to validate all three `run_trial.py` fixes above together, cleanly: (1) T0 captured from `status.instances` on the first check; (2) availability computed directly from span counts, printed and saved; (3) `run_trial.py` logged `Restored deployment/checkoutservice to 1 replicas (was 3)` in its `finally` block — no manual `kubectl scale` needed afterward, confirmed both in the script's log line and independently via `kubectl get deployment`.
  - This iteration was preceded by two discarded attempts (same day, not logged as formal iterations) that surfaced a real environment issue rather than a code bug: the custom operator (`operator/handlers.py`) had not been running at all, so an earlier trial's `RemediationAction` sat unprocessed; once the operator was started, it reconciled that backlog CR retroactively mid-way through the *next* attempt, which then hit `CooldownBlocked` on its own new CR and left checkoutservice stuck at 3 replicas. Both attempts' state (replicas, cooldown annotations) were manually cleared before this iteration. **Process takeaway:** confirm the operator is running and `kubectl get remediationaction -n boutique` shows no pending/backlogged CRs before starting a trial, not just before starting the campaign.
  - Same MTTR caveat as Iteration 1 still applies: `frontend_success_rate` stayed at 1.0 throughout (only 2 samples were taken before the SLO-hold recovery check fired), so this fault type still isn't demonstrating the operator's action meaningfully shortening recovery — Tr is really "SLO was never broken," not "operator fixed it fast." Availability being exactly 1.0000 is consistent with this. This scenario-01 fault likely needs the RQ3 comparison made on a scenario where the frontend SLO is genuinely and sustainedly impacted (see Iteration 1 notes).

## Scenario 7 - Random Pod Kill (frontend) - Run A / Run B, Iteration 1 (both inconclusive — playbook fix applied)

- **Date:** 2026-09-11
- **Run A:** T0 `2026-09-11T02:10:41Z` (source: `status.experiment.containerRecords[0].events[0].timestamp` — PodChaos has no `status.instances`). **Td: censored (600s timeout, never detected).** Tr `02:11:53Z` (MTTR 72.0s). Availability 1.0000.
  - Raw data: `evaluation/runs/trials/scenario-07-random-pod-kill-RunA-20260911T021153.229626Z.json`
  - **Root cause of the censor, not a bug**: a Deployment-managed pod-kill deletes the pod and creates a brand-new one (new name, `restartCount` reset to 0) rather than restarting a container in place. By the first sample (t+12s), the replacement pod was already `Running`/`Ready` — a lightweight frontend image typically becomes ready well inside 12s. `run_a_detected()`'s "pod not ready" / "restart count increased" checks never had a window to fire. This is a genuine blind spot of the frozen 60s sample cadence in `docs/measurement-protocol.md`, not something to patch in the detection code — a single transient pod-kill on a low-traffic, 1-replica service can resolve faster than this protocol can observe, in *either* condition.
- **Run B:** T0 `2026-09-11T02:12:31Z` (same source). Td `02:12:43.39Z` (MTTD 12.4s, signal `anomaly_score=0.5660>tau=0.5170`). Tr `02:13:43.34Z` (MTTR 72.3s). Availability 1.0000.
  - Raw data: `evaluation/runs/trials/scenario-07-random-pod-kill-RunB-20260911T021343.630824Z.json`
  - Anomaly detected, but **no rule matched and no action executed**: top feature at Td was `log_error_rate` (z=12.63), not `trace_error_pct` — `R2-pod-kill-restart`'s original `trigger_feature` choice. The spike was transient (single sample above tau; by t+72s cpu/mem/log features were back near baseline), so there was no later sample to retry the match on, unlike scenario-01's sustained CPU stress.
  - **Fix applied same day**: `R2-pod-kill-restart`'s `trigger_feature` changed from `trace_error_pct` to `log_error_rate` in `decision-engine/playbook.yaml`, based on this trial's actual z-scores. Not yet re-validated — next scenario-07 Run B iteration should confirm the rule now matches and the operator executes a real `restart`.
- **Detection outcome:** Run A FN (censored), Run B TP (detection only, no matched rule this iteration)
- **Action taken:** none in either condition
- **Censored:** Run A MTTD; Run B no
- **Notes:**
  - Neither condition demonstrates a meaningful MTTD/MTTR comparison from this pair: Run A never detected at all under the protocol's cadence, and Run B detected but never acted. MTTR (72.0s vs 72.3s) is again the sampling-cadence floor (`first-sample-delay + 60s`), not a real recovery-time effect — same artifact pattern as scenario-01.
  - Open question carried forward: even after the playbook fix, a *single* pod-kill on a 1-replica service may resolve (natively or via the operator) faster than this protocol's 60s cadence can meaningfully separate Run A from Run B — the opposite failure mode from scenario-01 (SLO never breaks) but the same practical result (no visible MTTR difference). A scenario with a longer, self-*non*-healing outage (e.g. scenario-09 volume detachment, scenario-11 DB pool exhaustion) may be necessary to get a real RQ3 signal.

## Scenario 7 - Random Pod Kill (frontend) - Run B - Iteration 2 (playbook fix validated end-to-end)

- **Date:** 2026-09-11
- **Model:** `evaluation/runs/baseline/20260906T023729Z/model-artifacts`, tau (p95) = 0.516964
- **T0 fault start (UTC):** 2026-09-11T02:33:08Z (source: `status.experiment.containerRecords[0].events[0].timestamp`)
- **Td detection (UTC):** 2026-09-11T02:33:20.300770Z
- **Tr recovery (UTC):** 2026-09-11T02:34:20.476626Z
- **MTTD (Td − T0):** 12.3s
- **MTTR (Tr − T0):** 72.5s
- **Availability:** 1.0000
- **Detection outcome:** TP
- **Run B score / τ:** 0.5768 at Td vs τ=0.516964
- **Rule matched:** R2-pod-kill-restart, matched on `log_error_rate` (z=7.41) — confirms the 2026-09-11 playbook fix (trigger_feature `trace_error_pct` -> `log_error_rate`)
- **Action taken:** restart — executed for real, `RemediationAction/r2-pod-kill-restart-1789094000`, `status.phase=Succeeded`, `"Restarted frontend"`; confirmed via `kubectl get pods`: a *new* pod (`frontend-bb67f6b7c-wh49j`, a different ReplicaSet hash than Chaos Mesh's own pod-kill replacement) was running, proving the operator's rollout-restart genuinely fired on top of the native reschedule
- **Censored:** no
- **Notes:**
  - First fully clean, uncontaminated scenario-07 Run B trial: no cooldown block, no stale CR backlog, no rule-match failure. Detection -> rule match -> real actuation, closing the loop for a second scenario (previously only scenario-01/checkoutservice-scale had been proven this cleanly).
  - This was preceded by a discarded attempt the same day where the trial's own `RemediationAction` was `CooldownBlocked` by a separate action fired 39s earlier from an overlapping prior run — cooldown annotations were cleared before this iteration. **Same process takeaway as scenario-01**: never run two trials on the same target within `COOLDOWN_SECONDS` (default 300s) of each other without clearing state in between.
  - Same MTTD/MTTR caveat as the earlier scenario-07 attempts still applies: this is still a single transient pod-kill on a 1-replica service, so MTTR (72.5s) is still bounded by the sampling-cadence floor rather than reflecting the operator's action meaningfully shortening a sustained outage. This trial validates the closed loop mechanically (detection -> rule -> real action), but not yet RQ3's MTTR-improvement claim — a Run A trial on this same, now-fixed setup, plus a scenario with a genuinely sustained (non-self-healing) outage, are still needed for that.
