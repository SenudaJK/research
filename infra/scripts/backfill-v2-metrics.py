"""
Backfill the two v2 per-service Prometheus metrics (mem_util_cartservice,
network_rx_productcatalogservice) into an *already-collected* baseline run,
by re-querying Prometheus's instant-query API at each sample's original
timestamp with the corrected PromQL.

Why this is possible: `container="cartservice"` / `container="productcatalogservice"`
filters in the original collect-baseline.sh matched zero timeseries (every
Online Boutique container is actually named "server" — see
docs/experiment-log.md, 2026-09-16), so these two columns are NaN on every
row of an affected baseline. The fix (pod=~"<service>-.*") is a read-only
query correction, not a new measurement — Prometheus already scraped the
underlying container_memory_working_set_bytes / container_network_receive_bytes_total
series at collection time (retention: 7d, infra/observability/values/kube-prometheus-stack.yaml),
so those values can be recovered from history instead of re-running a fresh
~3h20m baseline collection.

This only backfills the two named v2 metrics into existing metrics/sample_*.json
files in place (adds a "backfilled": true marker on each). It does not touch
any other metric, and does not change sample count, timestamps, or any v1
column — re-run fusion-engine/build_state_vector.py afterward to regenerate
state_vector.csv with the corrected values.

Usage:
    kubectl port-forward -n <observability-ns> svc/kube-prometheus-prometheus 19090:9090 &
    python3 infra/scripts/backfill-v2-metrics.py --run-dir evaluation/runs/baseline/<run-id>
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

QUERIES = {
    "memory_working_set_cartservice":
        'sum(container_memory_working_set_bytes{namespace="boutique",pod=~"cartservice-.*"})',
    "network_receive_bytes_productcatalogservice":
        'sum(rate(container_network_receive_bytes_total{namespace="boutique",pod=~"productcatalogservice-.*"}[1m]))',
}

PROM_URL = "http://127.0.0.1:19090/api/v1/query"


def to_unix(ts: str) -> float:
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true",
                         help="Query and report only; don't write any files.")
    args = parser.parse_args()

    metrics_dir = args.run_dir / "metrics"
    files = sorted(metrics_dir.glob("sample_*.json"), key=lambda p: int(p.stem.split("_")[1]))
    if not files:
        sys.exit(f"No metrics/sample_*.json found under {metrics_dir}")

    recovered = {k: 0 for k in QUERIES}
    still_empty = {k: 0 for k in QUERIES}

    for f in files:
        data = json.loads(f.read_text())
        ts = data["timestamp"]
        query_time = to_unix(ts)

        for key, promql in QUERIES.items():
            resp = requests.get(PROM_URL, params={"query": promql, "time": query_time}, timeout=15)
            resp.raise_for_status()
            result = resp.json()
            has_value = bool(result.get("data", {}).get("result"))
            if has_value:
                recovered[key] += 1
            else:
                still_empty[key] += 1

            if not args.dry_run:
                data["queries"][key] = {**result, "promql": promql, "backfilled": True}

        if not args.dry_run:
            f.write_text(json.dumps(data, indent=2))

    print(f"Processed {len(files)} samples.")
    for key in QUERIES:
        print(f"  {key}: recovered {recovered[key]}/{len(files)}, still empty {still_empty[key]}/{len(files)}")
    if args.dry_run:
        print("Dry run — no files written. Re-run without --dry-run to apply.")
    else:
        print("Done. Now re-run: python3 fusion-engine/build_state_vector.py "
              f"--run-dir {args.run_dir}")


if __name__ == "__main__":
    main()
