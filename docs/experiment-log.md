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

## Scenario 2 - Memory Leak - Playbook Gap Found and Partially Fixed, Known Detection Limitation

- **Date:** 2026-09-13
- **Context:** All 27 prior scenario-02 trials (Run A and Run B, 2026-09-12/13) were collected while `decision-engine/playbook.yaml` had no rule at all for this scenario. Under Run B, the anomaly detector correctly fired (MTTD ~12s, consistent with other scenarios) but `orchestrator.py`/`run_trial.py`'s `match_rule()` found no matching rule and silently continued — no `RemediationAction` was ever created, and no operator action executed. Every one of those 13 Run B trials measured the fault's own 5-minute StressChaos `duration` expiring naturally (or the operator's own restart from a *different*, unrelated rule — see below), not the framework recovering anything. **Treat all scenario-02 trials dated before 2026-09-13T03:36Z as invalid/non-evidence for RQ3; re-collect from this date forward.**
- **Fix applied:** Added `R3-memory-leak-restart` (`trigger_feature: mem_util`, target `cartservice`, `action: restart`) to `decision-engine/playbook.yaml`, matching Table I's S2 recovery ("trigger graceful container restart").
- **Validation trial (Run B):** `evaluation/runs/trials/scenario-02-memory-leak-RunB-20260913T033952.578963Z.json`. MTTD 12.3s, MTTR 192.3s, availability 0.8337 — real improvement over the unmanaged baseline (~339s mean MTTR). However, the matched rule was **R2-pod-kill-restart** (`log_error_rate` z=8.93), not R3 — R3 never fired.
- **Root cause (mem_util z-score analysis across all 27 scenario-02 trials, both conditions):** `mem_util` z-scores range from -11.6 to +5.3 with no reliable positive correlation to the fault — mostly negative. `mem_util` sums `container_memory_working_set_bytes` across **all 12** `boutique` services; cartservice's own memory limit (256Mi) is a small fraction of the ~2100-2400 MiB aggregate baseline. Combined with StressChaos's `size: "90%"` stressor OOMKilling cartservice almost instantly — far faster than the 60s sample cadence can catch a sustained elevated reading — the fault's effect on the aggregate metric is swamped by the other 11 services' normal variance. This is not a threshold-tuning problem: the feature itself does not carry the signal, at any `threshold_percentile` or `rule_match_z_threshold`.
- **Consequence:** `R3-memory-leak-restart` is structurally dead code for scenario-02 under the current 6-feature model — whenever this fault is genuinely detected, it's via `log_error_rate` (consistently 8-10σ) or `trace_error_pct`, both far exceeding whatever `mem_util` manages, so `R2-pod-kill-restart` (already keyed on `log_error_rate`, targeting `frontend`) always outranks R3 by z-score and wins the match. The de facto S2 recovery path is therefore R2 restarting **frontend**, not cartservice — a coincidental side effect (clearing frontend's own connection/circuit-breaker state to a still-struggling cartservice), not a fix to the underlying leak: in the validation trial, cartservice's own `mem_util` contribution climbed monotonically throughout (2112 -> 2261 -> 2273 -> 2288 MiB) and was never restarted.
- **Decision:** Keep R3 in the playbook, documented as inactive for scenario-02 specifically (it may still be reachable by a genuine sustained memory fault that doesn't OOMKill as fast as this one does). Report this as an explicit RQ1/RQ2 limitation: aggregate, cluster-wide feature engineering cannot localize a small-resource-limit single-service fault when a much larger-magnitude, shared symptom (downstream request errors) dominates the same anomaly score. Resolving this would require either a per-service memory feature (requires retraining the frozen Isolation Forest, invalidating comparability with all prior trials) or compound/multi-feature rule matching (a change to the already-validated detection path used by S1/S2/S7).

## Scenario 4, 9, 11 - Playbook Rules R4/R5/R6 Added; R5/R6 Rule-Collision Limitation Found

- **Date:** 2026-09-13
- **Rules added:** `R4-network-latency-scale` (`trace_latency_ms` -> recommendationservice -> scale, S4), `R5-volume-detachment-restart` (`trace_error_pct` -> redis-cart -> restart, S9), `R6-db-pool-exhaustion-scale` (`network_rx` -> productcatalogservice -> scale, S11) — added to `decision-engine/playbook.yaml` to cover the 3 remaining scenarios with a free (unclaimed) feature after R1/R2/R3.
- **R4 validation (S4, Run B):** `evaluation/runs/trials/scenario-04-network-latency-RunB-20260913T045108.599410Z.json`. Matched cleanly — `trace_latency_ms` z=45.84 at Td, no competing rule close in magnitude. MTTD 12.3s, MTTR 72.3s, availability 1.0000. **R4 confirmed working as designed.**
- **S9 attempt (Run B):** Chaos Mesh's IOChaos fault injection failed at the infrastructure level — `toda` (the fuse-based I/O fault injector) never successfully injected (`injectedCount: 0`, repeated `"toda update RPC failed: context canceled"` / `"read |0: file already closed"` errors in the IOChaos status), then got stuck retrying its "Stop" transition, blocking `kubectl delete` indefinitely (required manually clearing the `chaos-mesh/records` finalizer to unblock). The resulting trial (`frontend_success_rate: 1.0` throughout) tested nothing — moved to `evaluation/runs/trials/invalid/`. **S9/IOChaos on this cluster needs separate infrastructure debugging (chaos-daemon/toda health, possibly a kernel/fuse prerequisite) before any valid S9 trial can be collected. R5 remains unvalidated against its intended fault.**
- **S11 attempt (Run B):** `evaluation/runs/trials/invalid/scenario-11-db-pool-exhaustion-RunB-20260913T052758.540195Z.json` (moved to invalid). `network_rx` (R6's trigger) showed real signal (z ≈ -5 to -6 throughout the fault — well past the ±3 threshold), but `trace_error_pct` (R5's trigger) was consistently larger in magnitude (z up to 16.11) at the same samples. `match_rule()`'s "largest |z| among qualifying candidates" tie-break therefore picked **R5 every time**, restarting redis-cart — completely unrelated to productcatalogservice's bandwidth throttle. That action had no effect (`frontend_success_rate` stayed at 0.23-0.36 for the next ~6 minutes); the eventual recovery at t+~300s lines up with the NetworkChaos fault's own `duration: 5m` expiring, not any operator action. The trial missed the 2-consecutive-sample recovery check by ~12s and timed out (`tr_censored: true`, MTTR=600s, availability 0.499).
- **Root cause (second instance of a class of bug, distinct mechanism from R3/mem_util):** `network_rx` *does* carry a real, usable signal for S11 — this is not a dead feature like `mem_util` was for S2. The problem is architectural: `match_rule()` has no way to know that R5 and R6 are describing two different faults; when both rules' trigger features cross threshold simultaneously (as they do here, since S11's bandwidth throttle also elevates `trace_error_pct` via request timeouts), the rule with the larger raw z-score wins regardless of which one is actually true to the underlying cause. `trace_error_pct` reliably outmagnitudes `network_rx` whenever both are elevated, so **R6 is structurally unable to win against R5 for this fault**, even though R6's own signal is valid in isolation.
- **Decision:** Keep both R5 and R6 in the playbook as-is. Documented as a second concrete RQ1/RQ2 limitation instance: single-feature, magnitude-only rule matching cannot disambiguate two simultaneously-qualifying faults, even when the "correct" rule's feature is itself a valid, non-noisy signal — this is a structural ceiling of the current architecture, not a threshold-tuning problem, and mirrors (with a different mechanism) the R3/mem_util finding above. Resolving this would require compound/multi-feature rule matching or per-rule priority/specificity weighting, both changes to the already-validated S1/S4 detection path.
- **Current validated-working rule count:** R1 (S1), R2 (S7 — its originally intended scenario), R4 (S4). R3 (S2) and R5/R6 (S9/S11) are confirmed non-functional for their intended scenarios under the current architecture, documented above.

## Sample-Size Campaign (S2/S4/S7) - `run_trial.py` Unbounded-Hang Bug Found and Fixed

- **Date:** 2026-09-13
- **Goal:** bring S2/S4/S7 Run B trial counts up toward N=10 per condition (S1 already had 32A/27B). Ran a batch of `run_trial.py` invocations (script not part of the committed codebase, ad-hoc for this session) for S4-A (x10), S7-A (x10), S2-B (x9), S4-B (x9), S7-B (x7).
- **Bug found:** 6 of the ~25 Run B trials in this batch (3x scenario-04, 3x scenario-07) recorded only **1 sample** despite running the full 600s timeout, forcing `Td`/`Tr` both to censor (`MTTD=600.0, MTTR=600.0`) even though nothing had actually gone wrong. Root cause: the main polling loop's `while time.time() - start < timeout_seconds` check only runs *between* iterations — there was no bound on how long a single iteration could take. `match_and_emit()`'s `api.create_namespaced_custom_object(...)` call (creating the RemediationAction CR) had no `_request_timeout` set, so a transient apiserver/network hiccup could silently stall one iteration for the entire remaining trial budget, producing a false-censored result that looks identical to a real detection/recovery failure.
- **Fix applied:** `evaluation/analysis/run_trial.py`'s `match_and_emit()` now passes `_request_timeout=15` to the CR-creation call and wraps it in a try/except that logs and returns `False` (triggering a retry on the next sample) instead of letting an exception propagate or the call hang indefinitely.
- **Data cleanup:** all 6 single-sample trial files moved to `evaluation/runs/trials/invalid/`. One additional S4-B trial (`...20260913T084830...json`, MTTD 213.9s vs the normal ~12s) showed a milder version of the same symptom (abnormally slow first iteration, only 2 samples) but did produce real Td/Tr — left in place but flagged as lower-confidence.
- **Real trial counts after cleanup:** S1 32A/27B (unchanged), S2 12A/10B, S4 10A/7B, S7 10A/7B. Need 3 more S4-B and 3 more S7-B to reach N=10 per condition — collected after this fix landed (see next entry once run).

## 2026-09-16 — Same `container=` PromQL bug found in `run_trial.py`'s own query copy (all v2 Run B trials censored)

- **Context:** overnight v2 campaign (`run-campaign.sh --scenarios scenario-02-memory-leak,scenario-09-volume-detachment,scenario-11-db-pool-exhaustion --conditions A,B --iterations 10 --config model-config-v2.yaml --playbook playbook-v2.yaml`). First Run B trial (`scenario-02-memory-leak-RunB-20260916T123155.614057Z.json`) came back fully censored: `Td: None, MTTD: 600.0s`, despite `mem_util`/`cpu_util`/`frontend_success_rate` clearly moving during the fault window.
- **Root cause:** every sample in the trial had `anomaly_score: None`, `mem_util_cartservice: None`, `network_rx_productcatalogservice: None`. `evaluation/analysis/run_trial.py` keeps its **own separate copy** of the v2 PromQL in its `METRIC_QUERIES` dict (not shared code with `infra/scripts/collect-baseline.sh`) — this copy still had the `container="cartservice"`/`container="productcatalogservice"` filter bug fixed earlier today in `collect-baseline.sh` (see the entry below), because the fix was applied to only one of the two places the same query string is duplicated. With both v2 features always `None`, `score_sample()` cannot compute an anomaly score at all, so `anomaly_score > tau` can never be true — every v2 Run B trial in this campaign (S2-B, S9-B, S11-B; 30 of the planned 60 trials) was running with detection structurally disabled, regardless of whether the underlying fault was real.
- **Fix applied:** `run_trial.py`'s `METRIC_QUERIES["memory_working_set_cartservice"]` / `["network_receive_bytes_productcatalogservice"]` changed to the same `pod=~"<service>-.*"` filter as the `collect-baseline.sh` fix.
- **Consequence:** the campaign was stopped after its first Run B trial (S2-B iteration 1, itself already discarded for a Jaeger-restart contamination, so no valid data was lost). All 10 Run A trials completed for S2 before this point remain valid (Run A doesn't use the ML model, so this bug never affected them). **Process takeaway:** the v2 PromQL strings are duplicated in two files (`collect-baseline.sh` for training-baseline collection, `run_trial.py` for live trial scoring) with no shared source of truth — any future correction to one must be checked against the other explicitly; consider extracting both into one shared query definition if a third v2 feature is ever added.

## 2026-09-16 — v2 per-service PromQL bug found: `container=` filter never matches (all v2 features NaN)

- **Context:** first real ≥200-sample baseline collected under the v2-extended `collect-baseline.sh` (`evaluation/runs/baseline/20260916T012647Z`, 200/200 samples, passed `check-baseline-quality.sh`). Training via `train_and_compare.py --config decision-engine/model-config-v2.yaml` dropped all 200/200 rows (`ValueError: Found array with 0 sample(s)`).
- **Root cause:** `mem_util_cartservice` and `network_rx_productcatalogservice` were `NaN` on every single row (`missing_v2_features` = both columns, 200/200). The underlying PromQL (`container="cartservice"`, `container="productcatalogservice"`) executed successfully (`status: success`) but matched zero timeseries — Online Boutique's upstream manifests (`kustomize/base` v0.10.6) name **every** container `server` regardless of Deployment name (confirmed in `infra/boutique/patches/cartservice-resources.yaml` and `otel-tracing.yaml`), so a `container=` filter keyed on the service name can never match. `container_network_receive_bytes_total` is additionally reported per-*pod*, not per-container, by cAdvisor, so the network query was wrong for a second, independent reason. This bug has been latent since the v2 queries were added 2026-09-15 — that day's "synthetic-input verification" used hand-constructed rows and never actually issued this query against a live cluster, so it couldn't have caught this.
- **Fix applied:** both v2 queries in `infra/scripts/collect-baseline.sh` changed to filter by `pod=~"<service>-.*"` instead of `container="<service>"`.
- **Consequence:** `evaluation/runs/baseline/20260916T012647Z`'s v2 columns are unrecoverable (the data was genuinely never captured, not just mislabeled) — its 6 v1 features are still valid (197-200/200 non-missing) and this baseline can still be used to train/refresh a v1 model, but a **new** baseline collection is required to get real v2 training data. Not yet re-validated with a live query — next baseline run should confirm `mem_util_cartservice`/`network_rx_productcatalogservice` come back non-empty before spending another ~3h20m collection window on it.

## 2026-09-16 — `collect-baseline.sh` unbounded-curl bug found and fixed (baseline sampling stall)

- **Context:** during a fresh baseline collection (targeting 200 samples at 60s cadence), sample 179→180 took 343s instead of ~60s (`06:33:08Z` → `06:38:51Z`), while every other sample in the run stayed on cadence.
- **Root cause:** each sample makes 13 sequential `curl` calls (8 Prometheus queries, 1 Loki `query_range`, 3 Jaeger calls, 1 v2 per-service trace pull) with no `--connect-timeout`/`--max-time` set — only `-sf`. A single transient hiccup on one of the three `kubectl port-forward` tunnels (expected over a multi-hour, 12000s run) can make one `curl` call hang for minutes with nothing bounding it, stalling that entire sample. This is the same class of bug already found and fixed in `orchestrator.py`'s unbounded CR-creation API call (see the 2026-09-13 entry above) — that fix never covered this script.
- **Fix applied:** added `--connect-timeout 5 --max-time 15` to all 13 `curl` calls in `infra/scripts/collect-baseline.sh`'s sampling loop (plus the initial Prometheus reachability check). A stalled port-forward now degrades to one `{"status":"error"}`/empty-result sample (already handled downstream as `NaN` by `fusion-engine/build_state_vector.py`) instead of a multi-minute stall. Does not change the existing sleep-clamping logic (`ITER_ELAPSED`-based, added 2026-08-27 for the 176/200 shortfall) — that logic already tolerates normal query-time variance; this fix only bounds the abnormal case.
- **Not yet re-validated with a live multi-hour run** — next baseline collection should confirm total wall-clock time stays close to `TARGET_SAMPLES * BASELINE_SAMPLE_INTERVAL_SECONDS` even across a hiccup, rather than needing the `HARD_CAP_TIME` safety valve to intervene.

## 2026-09-15 — Rules added for the 6 untried scenarios (S3/S5/S6/S8/S10/S12); v2 per-service-feature model introduced

- **Context:** S1/S4/S7 have clean, validated Run B rules (R1/R2/R4). S2/S9/S11 have rules (R3/R5/R6) already documented above as non-functional under the frozen v1 model — the model's 6 features are cluster-wide aggregates, so a small-magnitude single-service fault either gets swamped (S2/R3) or loses a rule-matching tie to a larger shared symptom (S9-vs-S11/R5-vs-R6). S3, S5, S6, S8, S10, S12 had fault-injection YAML but no playbook rule and no trial ever run.
- **Rules R7–R12 added** to `decision-engine/playbook.yaml` (S3 disk I/O → paymentservice evict, S5 packet loss → shippingservice scale, S6 DNS failure → CoreDNS restart, S8 node starvation → dynamic-target node evict, S10 HTTP 5xx → frontend restart, S12 config drift → checkoutservice reconcile). **None have been run against a real trial yet** — each is explicitly marked `UNVALIDATED` in its own description, same starting status R1–R6 all had before their own dry runs. Several are added with an explicit, documented expectation that they will very likely reproduce the R5-vs-R6 collision pattern, since all 6 v1 features are already claimed by R1–R6 (R7/R1 both key `cpu_util`; R8/R4 both key `trace_latency_ms`; R9/R2 both key `log_error_rate`; R11/R12 both key `trace_error_pct`, also colliding with R5).
- **New operator actions** in `operator/handlers.py`: `evict` (Deployment-scoped pod delete for R7, and Node-scoped cordon+evict for R10) and `reconcile` (`kubectl apply -k infra/boutique/` for R12) — previously only `scale`/`restart` were implemented; `evict` was accepted by the CRD but explicitly rejected at runtime. `operator/crds/remediationaction-crd.yaml`'s `action` and `targetRef.kind` enums extended to match (`"Node"` added for R10's dynamically-resolved target).
- **Dynamic target resolution added** (`resolve_dynamic_target()` in both `decision-engine/orchestrator.py` and `evaluation/analysis/run_trial.py`, kept in sync per this repo's existing convention for that pair of files): R10 is the first rule whose target isn't fixed in the playbook — `target.kind: Node, target.name: AUTO` gets resolved at match time to whichever node currently hosts a not-Running/not-Ready boutique pod. Verified with a synthetic-input script (non-Node targets pass through unchanged; a Node/AUTO target with no reachable cluster correctly returns `None` rather than guessing) — not yet validated against a real scenario-08 trial.
- **scenario-12 trial support added** to `evaluation/analysis/run_trial.py` (`MANUAL_SCENARIOS`): scenario-12 has no Chaos Mesh CRD (`evaluation/scenarios/scenario-12-config-drift.yaml` is comment-only), so `clear_and_apply()` previously `die()`d immediately on it. The new path applies the documented `kubectl patch` directly, uses the patch timestamp as T0 (no status field exists to read), and reverts via `kubectl apply -k infra/boutique/` instead of `kubectl delete -f`.
- **v2 per-service-feature model introduced** to actually fix R3/R5/R6 (and, potentially, whichever of R7–R12 collides the same way) — deliberately kept as a **separate, clearly-labeled condition**, not spliced into the v1 campaign that S1/S4/S7 (and any future v1 trials) are measured under:
  - `infra/scripts/collect-baseline.sh` extended with per-service Prometheus queries (`mem_util_cartservice`, `network_rx_productcatalogservice`) and a per-service Jaeger trace pull (`trace_error_pct_cartservice`, cartservice being the direct caller of redis-cart) — additive, existing v1 `METRIC_KEYS` and outputs unchanged.
  - `fusion-engine/build_state_vector.py` extended to emit these as optional extra columns (tracked in a separate `missing_v2_features` column, kept out of the v1 `missing_features`/`--max-missing-fraction` gate so re-running this script against an old, pre-v2 baseline directory doesn't spuriously fail).
  - New `decision-engine/model-config-v2.yaml` (9 features: the original 6 + the 3 above) and `decision-engine/playbook-v2.yaml` (`R3v2`/`R5v2`/`R6v2`, same targets/actions as v1, corrected `trigger_feature`s) — both explicitly separate files; `decision-engine/model-config.yaml`/`playbook.yaml` and the frozen `20260906T023729Z` model are untouched.
  - `evaluation/analysis/run_trial.py` gained `--playbook`/`--config` flags and now reads the model's own feature list from `threshold.json` (`score_sample`/`match_rule` no longer hardcode the 6 v1 feature names), so a v2 model-dir + `--playbook decision-engine/playbook-v2.yaml` run entirely independently of the v1 campaign.
  - Synthetic-input verification only so far (hand-constructed z-score-triggering rows correctly match each new rule's own trigger_feature in isolation, including the v2 per-service features cleanly disambiguating what R5/R6 could not). **Still needed before any of this counts as evidence:** collect a fresh ≥200-sample quality-gated baseline with the extended `collect-baseline.sh`, train into a new `evaluation/runs/baseline/<run-id>/` via `train_and_compare.py --config decision-engine/model-config-v2.yaml`, and run real Run A/B trials for S2/S9/S11 (and any of S3/S5/S6/S8/S10/S12 whose v1 rule turns out to collide) under the v2 model.
