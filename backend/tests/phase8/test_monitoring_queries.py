"""§7 — can the persisted state actually answer the operator's questions?

Each test here IS the monitoring query from the runbook, run against real
Postgres with a row deliberately put into the state being hunted. If a query
cannot find its condition, the runbook entry is worthless.

WHAT THIS DOES NOT CLAIM. Finding a condition in SQL is not an alert. There is
no operational alert sink in this codebase (services/bank/alerts.py is
PORTFOLIO-risk alerting rendered in the bank UI, not ops paging; no task sends
a notification on failure). These queries are the raw material for one; the
gap is recorded in the Phase 8 report rather than papered over.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import text

# ── the runbook queries, verbatim ──────────────────────────────────────────

Q_STUCK_RECEIVED = """
select id, business_date, received_at,
       round(extract(epoch from (now() - received_at)) / 60) as minutes_waiting
from lending.bank_feed_batches
where status = 'RECEIVED'
  and received_at < now() - interval '30 minutes'
  and bank_id = :b
order by received_at
"""

Q_STALE_VALIDATING = """
select id,
       coalesce((dq_report->>'claimed_at')::timestamptz, received_at) as claimed_at,
       round(extract(epoch from (now() - coalesce((dq_report->>'claimed_at')::timestamptz,
                                                   received_at))) / 60) as minutes_held
from lending.bank_feed_batches
where status = 'VALIDATING'
  and coalesce((dq_report->>'claimed_at')::timestamptz, received_at)
      < now() - (:stale_minutes || ' minutes')::interval
  and bank_id = :b
"""

Q_REJECTED = """
select id, business_date, dq_report->>'ingestion_error' as failure
from lending.bank_feed_batches
where status = 'REJECTED' and bank_id = :b
order by received_at desc
"""

Q_BLOCKED_ALLOCATION = """
select plan_date, manager_user_id,
       summary_metadata->>'outcome' as outcome,
       summary_metadata->'blocked_by' as blocked_by
from planning.allocation_runs
where status = 'FAILED'
  and summary_metadata->>'outcome' = 'BLOCKED_UPSTREAM'
  and bank_id = :b
"""

Q_DUPLICATE_PLACEMENTS = """
select loan_id, count(*) as active_placements
from collections.placements
where status = 'ACTIVE' and bank_id = :b
group by loan_id
having count(*) > 1
"""

Q_QUARANTINE_BACKLOG = """
select coalesce(dq_errors->-1->>'reason', 'UNKNOWN') as reason, count(*) as rows_held
from lending.bank_feed_rows
where status = 'QUARANTINED' and bank_id = :b
group by 1
order by 2 desc
"""


def _rows(Session, sql, **params):
    s = Session()
    try:
        return s.execute(text(sql), params).fetchall()
    finally:
        s.close()


def test_a_batch_stuck_at_received_for_over_30_minutes_is_found(Session, tenant, make_batch):
    fresh = make_batch(status="RECEIVED")
    stuck = make_batch(status="RECEIVED")
    s = Session()
    try:
        s.execute(text("update lending.bank_feed_batches set received_at = now() - "
                       "interval '45 minutes' where id = :i"), {"i": stuck.id})
        s.commit()
    finally:
        s.close()

    found = _rows(Session, Q_STUCK_RECEIVED, b=tenant["bank_id"])
    ids = {str(r.id) for r in found}
    assert str(stuck.id) in ids, "the stuck batch was not detected"
    assert str(fresh.id) not in ids, "a fresh batch must not alarm"
    assert found[0].minutes_waiting >= 30


def test_a_stale_validating_claim_is_found_at_the_configured_threshold(Session, tenant,
                                                                       make_batch):
    """Uses the real STALE_CLAIM_MINUTES (60, unchanged) as the query bound."""
    from app.workers.tasks.feed_ingestion import STALE_CLAIM_MINUTES

    assert STALE_CLAIM_MINUTES == 60
    held = make_batch(status="VALIDATING")
    recent = make_batch(status="VALIDATING")
    when = datetime.now(timezone.utc) - timedelta(minutes=STALE_CLAIM_MINUTES + 10)
    s = Session()
    try:
        s.execute(text("update lending.bank_feed_batches set received_at = :w, "
                       "dq_report = coalesce(dq_report,'{}'::jsonb) "
                       "|| jsonb_build_object('claimed_at', :iso) where id = :i"),
                  {"w": when, "iso": when.isoformat(), "i": held.id})
        s.commit()
    finally:
        s.close()

    found = _rows(Session, Q_STALE_VALIDATING, b=tenant["bank_id"],
                  stale_minutes=STALE_CLAIM_MINUTES)
    ids = {str(r.id) for r in found}
    assert str(held.id) in ids
    assert str(recent.id) not in ids


def test_a_rejected_batch_is_found_with_its_reason(Session, tenant, make_batch):
    batch = make_batch(status="RECEIVED", storage_key=None)
    # Drive it to REJECTED through the real task, so the reason is the real one.
    import app.core.database as dbmod
    from app.workers.tasks import feed_ingestion
    original = dbmod.SessionLocal
    dbmod.SessionLocal = Session
    try:
        feed_ingestion.ingest_feed_batch.run(batch.id)
    finally:
        dbmod.SessionLocal = original

    found = _rows(Session, Q_REJECTED, b=tenant["bank_id"])
    assert any(str(r.id) == str(batch.id) for r in found)
    row = next(r for r in found if str(r.id) == str(batch.id))
    assert row.failure, "a rejected batch must carry an operator-readable reason"


def test_a_blocked_allocation_night_is_found_and_names_what_blocked_it(Session, tenant):
    """The Phase 7 refusal row: status FAILED plus outcome BLOCKED_UPSTREAM."""
    from app.core.security import hash_password
    from app.models.user import User, UserRole

    s = Session()
    try:
        mgr = User(id=str(uuid.uuid4()), email=f"p8mgr.{tenant['tag']}@example.test",
                   phone=f"9{int(tenant['tag'][:6], 16) % 1000000000:09d}",
                   full_name="Phase8 Manager", hashed_password=hash_password("Harbour-Lights-2026"),
                   role=UserRole.AGENCY_MANAGER, bank_id=tenant["bank_id"],
                   agency_id=tenant["agency_id"], is_active=True)
        s.add(mgr)
        s.commit()
        mgr_id = mgr.id
    finally:
        s.close()

    import app.core.database as dbmod
    from app.workers.tasks import allocation
    original = dbmod.SessionLocal
    dbmod.SessionLocal = Session
    try:
        out = allocation.run_nightly_allocation.run()
    finally:
        dbmod.SessionLocal = original

    assert out["blocked"] is True, out
    found = _rows(Session, Q_BLOCKED_ALLOCATION, b=tenant["bank_id"])
    assert found, "the blocked night left no queryable evidence"
    assert found[0].outcome == "BLOCKED_UPSTREAM"
    assert found[0].blocked_by, "the row must name which stage blocked it"


def test_no_loan_ever_carries_two_active_placements(Session, tenant, placeable, ctx_admin):
    """The duplicate-side-effect query. Released once, then released again."""
    from app.services.bank import feed_quarantine_service as svc

    s = Session()
    try:
        svc.release_rows(s, ctx_admin, placeable["batch_id"], [placeable["row_id"]])
    finally:
        s.close()
    s = Session()
    try:
        svc.release_rows(s, ctx_admin, placeable["batch_id"], [placeable["row_id"]])
    finally:
        s.close()

    dupes = _rows(Session, Q_DUPLICATE_PLACEMENTS, b=tenant["bank_id"])
    assert dupes == [], f"duplicate active placements detected: {dupes}"


def test_the_quarantine_backlog_is_queryable_by_reason(Session, tenant, placeable):
    found = _rows(Session, Q_QUARANTINE_BACKLOG, b=tenant["bank_id"])
    assert found, "a held row must appear in the backlog query"
    assert found[0].reason and found[0].rows_held >= 1


def test_stage_durations_are_not_recorded_anywhere(Session, tenant):
    """A GAP, asserted so it cannot be quietly assumed fixed.

    No table records how long ingestion, scoring or allocation TOOK. The
    nightly window has therefore never been measured in production — CLAUDE.md
    says as much ("no stage records its duration yet"). allocation_runs has
    created_at/updated_at only, which bound the run loosely and not per stage.
    """
    s = Session()
    try:
        cols = {r[0] for r in s.execute(text(
            "select column_name from information_schema.columns "
            "where table_schema='planning' and table_name='allocation_runs'"))}
    finally:
        s.close()

    assert not {"duration_seconds", "started_at", "finished_at"} & cols, \
        "a duration column now exists — update the Phase 8 report's monitoring gap"
    assert "created_at" in cols and "updated_at" in cols
