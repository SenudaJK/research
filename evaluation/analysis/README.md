# Analysis

Statistical evaluation of experiment results.

## Contents

- `academic_analyzer.py` — Run A vs Run B statistical summary + MTTR/MTTD
  charts. Fixed 2026-09-07: previously auto-generated fake data and printed
  it as a real result whenever `experiment_results.csv` was missing, with
  no warning. Now requires a real CSV, or `--synthetic-smoke-test`
  explicitly (output clearly labeled "NOT RESEARCH EVIDENCE").
- `automated_tester.py` — **known broken, do not run as-is.** Predates the
  actual built pipeline and doesn't match it:
  - References `infra/chaos/*.yaml` (wrong path — real scenarios are in
    `evaluation/scenarios/`) and only 3 of the 12 scenarios
  - Targets service `recommendation-service` in namespace `default` (wrong
    name/namespace — Online Boutique's actual deployment is
    `recommendationservice` in `boutique`, no hyphen)
  - Run A "recovery" is detected via container restart count > 0 — does
    not match `docs/measurement-protocol.md`'s actual Run A detection rule
    (frontend success rate < 0.99, OR pod not ready, OR restart increase)
    or its recovery rule (frontend SLO held for 2 consecutive 60s windows)
  - Run B expects a `decision-engine/detector.py --live-eval` process
    printing magic strings `"Anomaly Detected!"` / `"Self-healing
    complete."` — no such script/interface exists; the real pipeline is
    `fusion-engine/build_state_vector.py` -> `decision-engine/score.py` /
    `orchestrator.py` -> `operator/handlers.py`
  - Needs a full rewrite against the real protocol and pipeline before
    Phase 4's actual 240-trial campaign runs — not yet scheduled.

## Metrics

- MTTD, MTTR
- Precision, Recall, F1
- System availability
- Run A vs Run B comparison

## Planned Contents

- Rewrite of `automated_tester.py` as the real campaign runner (see above)
- Summary tables and charts for thesis
- Exported CSV/JSON from `evaluation/runs/`
