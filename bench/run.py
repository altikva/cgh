# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-11-17
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Benchmark orchestrator. `python -m bench.run [--tier 1,2]` fetches
#              and verifies the pinned checkouts, indexes them, runs the selected
#              tiers, and regenerates the report. Tier 2 reads the committed
#              pr_truth files; it does not re-select PRs (that is the explicit
#              `python -m bench.impact_metric --select` step, which needs gh).

from __future__ import annotations

import argparse
import sys

from bench import context_metric, fetch, impact_metric, report
from bench import index as index_mod
from bench._util import BenchError


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="bench.run", description="cgh benchmark harness")
    ap.add_argument(
        "--tier",
        default="1,2",
        help="comma-separated tiers to run (default: 1,2)",
    )
    ap.add_argument(
        "--no-fetch", action="store_true", help="skip clone/verify (reuse cache)"
    )
    ap.add_argument(
        "--no-index", action="store_true", help="skip the tier-1 index step"
    )
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)
    tiers = {t.strip() for t in args.tier.split(",") if t.strip()}

    try:
        if not args.no_fetch:
            fetch.fetch_all()
        if "1" in tiers and not args.no_index:
            index_mod.index_all()
        if "1" in tiers:
            context_metric.run_all()
        if "2" in tiers:
            impact_metric.evaluate_all()
        report.run_all()
    except BenchError as exc:
        print(f"[run] ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
