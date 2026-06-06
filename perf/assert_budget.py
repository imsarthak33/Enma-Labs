#!/usr/bin/env python3
"""Read perf/reports/perf_summary.json and exit non-zero if any p95 latency
or failure rate exceeds the Phase 8 budget.

Usage
-----
    python perf/assert_budget.py [--summary path]

Exit codes
----------
    0 — all endpoints within budget
    1 — at least one endpoint exceeded a latency or failure budget
    2 — summary file missing or unreadable (perf run did not complete)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Failure rate ceiling — 0.5% of acks lost is the limit before we treat
# the run as a regression. Tighter than typical web SLOs because backend
# acks are fire-and-forget and any failure means user-visible silence.
FAILURE_RATE_CEILING = 0.005


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--summary",
        default=str(Path(__file__).with_name("reports") / "perf_summary.json"),
    )
    args = parser.parse_args()
    path = Path(args.summary)
    if not path.exists():
        print(f"perf summary missing: {path}", file=sys.stderr)
        return 2
    data = json.loads(path.read_text(encoding="utf-8"))

    failed: list[str] = []
    for endpoint, stats in data.get("endpoints", {}).items():
        budget = stats.get("budget_ms")
        if budget is None:
            continue
        p95 = stats.get("p95_ms")
        if p95 is not None and p95 > budget:
            failed.append(f"{endpoint}: p95={p95:.0f}ms exceeds budget {budget}ms")
        n = stats.get("num_requests", 0)
        f = stats.get("num_failures", 0)
        if n and (f / n) > FAILURE_RATE_CEILING:
            failed.append(
                f"{endpoint}: failure rate {f}/{n} > {FAILURE_RATE_CEILING:.1%}"
            )

    if failed:
        print("Perf budget breached:")
        for item in failed:
            print(f"  - {item}")
        return 1
    print("Perf budget OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
