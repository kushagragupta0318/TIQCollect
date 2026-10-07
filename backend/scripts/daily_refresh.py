"""Daily refresh for a hand-driven demo book: score, plan today's beats, refresh analytics.

The product's real data arrives from a nightly Celery chain (app/workers/celery_app.py):

    19:15  model_outcomes.attach_model_outcomes      label matured predictions
    19:45  repayment_scoring.run_nightly_...          score every loan, snapshot on change
    20:00  allocation.run_nightly_allocation          per manager: pool -> gates -> solve -> PLANNED beats
    20:30  analytics_refresh.refresh_analytics        rebuild the materialized views the tabs read

A demo book is driven forward by hand, not by that clock, so the Analytics tabs that
depend on fresh scores (Recovery) and on today's routed day (Field Operations) sit empty
until these run. This script runs the same four steps, in the same order, synchronously
and on demand. It is idempotent: re-running re-scores, re-plans the same plan date, and
re-refreshes the views.

Run it inside the api/worker container (it needs DATABASE_URL and the app on the path):

    docker exec fieldops_dev_api python -m scripts.daily_refresh
    docker exec fieldops_dev_api python -m scripts.daily_refresh --date 2026-10-07 --strategy SMART

Order matters: scoring and allocation WRITE the rows; the view refresh runs LAST so the
tabs reflect them. refresh_all already waits for any live allocation before refreshing, so
the two never race.
"""
from __future__ import annotations

import argparse
import sys
import time

import structlog

logger = structlog.get_logger(__name__)


def _run_task(label: str, task, **kwargs) -> dict:
    """Run a Celery task synchronously in-process via .apply() and return its result.

    .apply() ignores the broker and runs the task body here, binding `self` so a task's
    own self.retry still works. .get(propagate=True) re-raises anything the task raised,
    so a failed step stops the script with a non-zero exit instead of passing silently.
    """
    print(f"  - {label} ...", flush=True)
    started = time.monotonic()
    result = task.apply(kwargs=kwargs).get(propagate=True)
    print(f"    done in {time.monotonic() - started:.1f}s", flush=True)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--date", dest="plan_date", default=None,
                        help="Plan date for the beats, ISO YYYY-MM-DD. Default: the planner's target day.")
    parser.add_argument("--strategy", default="SMART", help="Allocation strategy (default: SMART).")
    parser.add_argument("--model", default="recovery_risk", help="Model to label/monitor (default: recovery_risk).")
    parser.add_argument("--no-outcomes", action="store_true", help="Skip labelling matured predictions.")
    parser.add_argument("--no-score", action="store_true", help="Skip repayment scoring.")
    parser.add_argument("--no-allocate", action="store_true", help="Skip beat allocation.")
    parser.add_argument("--no-refresh", action="store_true", help="Skip the analytics view refresh.")
    args = parser.parse_args(argv)

    # Imported here, not at module load, so --help works without a database.
    from app.core.database import engine
    from app.workers.tasks import allocation, analytics_refresh, model_outcomes, repayment_scoring

    print("Daily refresh — running the nightly chain on demand.", flush=True)
    summary: dict[str, object] = {}

    try:
        if not args.no_outcomes:
            print("[1/4] Label matured predictions", flush=True)
            summary["outcomes"] = _run_task("attach_model_outcomes", model_outcomes.attach_model_outcomes,
                                            model_name=args.model)
        if not args.no_score:
            print("[2/4] Score every loan, snapshot on change", flush=True)
            summary["scoring"] = _run_task("run_nightly_repayment_scoring",
                                           repayment_scoring.run_nightly_repayment_scoring)
        if not args.no_allocate:
            print(f"[3/4] Plan beats (strategy={args.strategy}, date={args.plan_date or 'target'})", flush=True)
            summary["allocation"] = _run_task("run_nightly_allocation", allocation.run_nightly_allocation,
                                              strategy=args.strategy, plan_date_str=args.plan_date)
        if not args.no_refresh:
            # A plain function, not a task: it takes the engine and waits out any live allocation.
            print("[4/4] Refresh analytics materialized views", flush=True)
            started = time.monotonic()
            summary["refresh"] = analytics_refresh.refresh_all(engine)
            print(f"    done in {time.monotonic() - started:.1f}s", flush=True)
    except Exception as exc:  # noqa: BLE001 — a script: report the failing step and exit non-zero.
        logger.error("daily_refresh.failed", error=str(exc))
        print(f"\nFAILED: {exc}", file=sys.stderr, flush=True)
        return 1

    print("\nDone. Summary:", flush=True)
    for step, result in summary.items():
        print(f"  {step}: {result}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
