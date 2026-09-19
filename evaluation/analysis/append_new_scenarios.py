"""
One-off script: append S5/S6/S8/S10/S12 trial results to experiment_results.csv.

Parses every valid (non-`invalid/`, `trial_valid: true`) trial JSON in
evaluation/runs/trials/ for these five scenarios and appends one CSV row per
trial, matching the existing Scenario_ID / Run_Type / MTTD / MTTR / Success
convention already used for S1/S2/S3/S4/S7 (see experiment_results.csv,
docs/measurement-protocol.md). Does NOT touch existing rows.

A trial with trial_valid: false is excluded here the same way trials moved to
evaluation/runs/trials/invalid/ are excluded elsewhere in this project's
convention (see docs/experiment-log.md's 2026-09-17 entries) — trial_valid is
set false by run_trial.py when the Jaeger restart count changed during the
trial window (span-data contamination), not because of a bad result.

Run once: python3 evaluation/analysis/append_new_scenarios.py
"""
import csv
import glob
import json
import os

TRIALS_DIR = "evaluation/runs/trials"
CSV_PATH = "experiment_results.csv"

# scenario glob prefix -> Scenario_ID used in the CSV
SCENARIOS = {
    "scenario-05-packet-loss": "S5_Packet_Loss",
    "scenario-06-dns-failure": "S6_DNS_Failure",
    "scenario-08-node-unresponsive": "S8_Node_Unresponsive",
    "scenario-10-http-5xx": "S10_HTTP_5xx",
    "scenario-12-config-drift": "S12_Config_Drift",
}

RUN_TYPE = {"A": "Run A (Legacy)", "B": "Run B (Proposed)"}


def load_trials(prefix):
    files = sorted(glob.glob(os.path.join(TRIALS_DIR, f"{prefix}-Run*-*.json")))
    rows = []
    excluded = []
    for f in files:
        with open(f) as fh:
            d = json.load(fh)
        if not d.get("trial_valid", True):
            excluded.append(f)
            continue
        rows.append({
            "Scenario_ID": SCENARIOS[prefix],
            "Run_Type": RUN_TYPE[d["condition"]],
            "MTTD": d["mttd_seconds"],
            "MTTR": d["mttr_seconds"],
            "Success": not d["tr_censored"],
        })
    return rows, excluded


def main():
    all_new_rows = []
    for prefix in SCENARIOS:
        rows, excluded = load_trials(prefix)
        print(f"{prefix}: {len(rows)} valid trials appended, "
              f"{len(excluded)} excluded (trial_valid=false / Jaeger-restart contamination)")
        for f in excluded:
            print(f"    excluded: {f}")
        all_new_rows.extend(rows)

    with open(CSV_PATH, "a", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["Scenario_ID", "Run_Type", "MTTD", "MTTR", "Success"])
        for row in all_new_rows:
            writer.writerow(row)

    print(f"\nAppended {len(all_new_rows)} rows to {CSV_PATH}")


if __name__ == "__main__":
    main()
