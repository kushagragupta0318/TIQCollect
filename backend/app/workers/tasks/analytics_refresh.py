# ─── CHANGELOG (standalone plan) ────────────────────────────────────────────
# 2026-09-28 (B13) — NEW. Nightly refresh of the analytics materialized views
#   (design §6.2): 20:30 IST, after the 19:30 ingest, 19:45 scoring and 20:00
#   allocation. It WAITS (up to 30 min) for the business date's allocation
#   runs to leave RUNNING rather than trusting the clock; one refresher at a
#   time (a transaction-level advisory lock); each view is refreshed
#   CONCURRENTLY and committed separately, with a row in
#   analytics.mv_refresh_log.
# ────────────────────────────────────────────────────────────────────────────
"""Refresh the analytics MVs, in order, each logged."""
import time
from datetime import datetime, timezone

import structlog
from sqlalchemy import text

from app.workers.celery_app import celery_app

logger = structlog.get_logger()

# Refresh order (design §6.2). Part A of B13; part B appends its views here.
# B13a (v2_0007) then B13b (v2_0013); each is built from base tables, so order cannot corrupt one.
MATERIALIZED_VIEWS = ("mv_collections_daily", "mv_field_activity_daily",
                      "mv_portfolio_daily", "mv_bucket_transitions_monthly", "mv_agency_scorecard_monthly")
_LOCK_KEY = 0x7419_6A13          # one advisory-lock key for the refresher
_WAIT_SECONDS, _POLL_SECONDS = 30 * 60, 60


def _allocation_running(conn) -> bool:
    return bool(conn.execute(text(
        "SELECT count(*) FROM planning.allocation_runs WHERE status = 'RUNNING'")).scalar())


def refresh_all(engine, *, wait_seconds: int = _WAIT_SECONDS, poll_seconds: int = _POLL_SECONDS) -> dict:
    waited = 0
    with engine.connect() as conn:
        while _allocation_running(conn) and waited < wait_seconds:
            conn.rollback()
            time.sleep(poll_seconds)
            waited += poll_seconds
    result = {"refreshed": [], "failed": [], "skipped": None, "waited_seconds": waited}
    with engine.connect() as conn:
        got = conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": _LOCK_KEY}).scalar()
        conn.commit()
        if not got:
            result["skipped"] = "another refresh holds the lock"
            return result
        try:
            for view in MATERIALIZED_VIEWS:
                started = datetime.now(timezone.utc)
                try:
                    conn.execute(text(f"REFRESH MATERIALIZED VIEW CONCURRENTLY analytics.{view}"))
                    n = conn.execute(text(f"SELECT count(*) FROM analytics.{view}")).scalar()
                    status, err = "OK", None
                    conn.commit()
                    result["refreshed"].append(view)
                except Exception as exc:   # noqa: BLE001 — logged, recorded, and the next view still runs
                    conn.rollback()
                    n, status, err = None, "FAILED", f"{type(exc).__name__}: {exc}"[:2000]
                    result["failed"].append(view)
                    logger.error("analytics.refresh_failed", view=view, error=err)
                conn.execute(text(
                    "INSERT INTO analytics.mv_refresh_log (view_name, started_at, finished_at, status, row_count, error) "
                    "VALUES (:v, :s, :f, :st, :n, :e)"),
                    {"v": view, "s": started, "f": datetime.now(timezone.utc), "st": status, "n": n, "e": err})
                conn.commit()
        finally:
            conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": _LOCK_KEY})
            conn.commit()
    return result


@celery_app.task(name="app.workers.tasks.analytics_refresh.refresh_analytics", bind=True)
def refresh_analytics(self):
    from app.core.database import engine
    result = refresh_all(engine)
    logger.info("analytics.refreshed", **{k: v for k, v in result.items()})
    if result["failed"]:
        raise RuntimeError(f"analytics refresh failed for {result['failed']}")   # the beat alert fires
    return result
