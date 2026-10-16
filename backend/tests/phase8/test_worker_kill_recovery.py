"""§4 — worker kill, redelivery and recovery, on real PostgreSQL.

THE REAL KILL. `test_a_killed_worker_leaves_the_batch_claimed_and_recoverable`
launches ingestion in a SUBPROCESS and sends it SIGKILL mid-processing, so the
process genuinely dies with its transaction open — no `finally`, no rollback
handler, nothing Python gets to run. That is the only honest way to test what a
crashed worker leaves behind.

SIMULATED vs OBSERVED — stated explicitly, because the brief asks for it:
  * The SIGKILL test is a REAL kill of a real process against real Postgres.
  * The stale-claim recovery tests are SIMULATED: STALE_CLAIM_MINUTES stays at
    60 and is never modified; instead the batch's claim timestamp is backdated
    so the same comparison the sweep makes at minute 61 is exercised in
    seconds. No test waits 60 minutes, and none is presented as a 60-minute
    operational observation.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import time
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import text

KILL_TIMEOUT = 30


def _batch_state(Session, batch_id):
    s = Session()
    try:
        return s.execute(text("select status, rows_accepted, rows_quarantined, loaded_at, dq_report "
                              "from lending.bank_feed_batches where id=:i"),
                         {"i": batch_id}).first()
    finally:
        s.close()


# ── the real kill ──────────────────────────────────────────────────────────

def test_a_killed_worker_leaves_the_batch_claimed_and_recoverable(Session, tenant, make_batch):
    """Kill -9 a process holding a claimed batch, then prove recovery works.

    1. stage a batch, 2. child claims it and blocks, 3. SIGKILL the child,
    4. inspect committed state, 5. run the recovery sweep, 6. assert no
    duplicate side effects.
    """
    from app.core.config import settings

    batch = make_batch(status="RECEIVED")
    dsn = settings.DATABASE_URL.rsplit("/", 1)[0] + "/fieldops_phase8"

    # The child claims the batch, commits the claim, then sleeps forever while
    # holding an OPEN transaction — exactly the state a worker is in when it
    # dies mid-ingest.
    child_src = textwrap.dedent(f"""
        import sys, time
        from sqlalchemy import create_engine, text
        e = create_engine({dsn!r}, future=True)
        c = e.connect()
        n = c.execute(text("UPDATE lending.bank_feed_batches SET status='VALIDATING' "
                           "WHERE id=:i AND status='RECEIVED'"), {{"i": {batch.id!r}}}).rowcount
        c.commit()
        # Open a second transaction and leave it uncommitted: partial work.
        c.execute(text("UPDATE lending.bank_feed_batches SET rows_accepted=99 WHERE id=:i"),
                  {{"i": {batch.id!r}}})
        print("CLAIMED", n, flush=True)
        time.sleep(600)
    """)
    proc = subprocess.Popen([sys.executable, "-c", child_src],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        # Wait (bounded) for the child to report it holds the claim.
        line, deadline = "", time.time() + KILL_TIMEOUT
        while time.time() < deadline:
            line = proc.stdout.readline()
            if line.startswith("CLAIMED"):
                break
        assert line.startswith("CLAIMED 1"), f"child never claimed the batch: {line!r}"

        # (2) confirmed claimed as VALIDATING, from an independent connection
        assert _batch_state(Session, batch.id).status == "VALIDATING"

        # (3) kill -9: no cleanup code runs at all
        proc.kill()
        proc.wait(timeout=KILL_TIMEOUT)
    finally:
        if proc.poll() is None:
            proc.kill()

    # (4) committed state only: the claim survived, the uncommitted partial
    # work did NOT. This is the property that makes recovery safe.
    state = _batch_state(Session, batch.id)
    assert state.status == "VALIDATING", "the claim should survive the crash"
    assert state.rows_accepted != 99, "uncommitted work must not be visible"

    # (5) recovery. The claim is fresh, so the sweep must NOT steal it yet —
    # that is the guard against reclaiming a batch from a live slow worker.
    from app.workers.tasks import feed_ingestion
    import app.core.database as dbmod
    original = dbmod.SessionLocal
    dbmod.SessionLocal = Session
    try:
        out = feed_ingestion.recover_stranded_batches.run()
        assert out["reclaimed_stale"] == 0, "a fresh claim must not be reclaimed"
        assert _batch_state(Session, batch.id).status == "VALIDATING"

        # Backdate the claim past STALE_CLAIM_MINUTES (unchanged, still 60) so
        # the same comparison the sweep makes at minute 61 runs now. SIMULATED.
        _backdate_claim(Session, batch.id, minutes=feed_ingestion.STALE_CLAIM_MINUTES + 5)
        out2 = feed_ingestion.recover_stranded_batches.run()
        assert out2["reclaimed_stale"] >= 1
        assert _batch_state(Session, batch.id).status == "RECEIVED", \
            "a stale claim must return to RECEIVED so it can be re-claimed"
    finally:
        dbmod.SessionLocal = original

    # (6) no duplicate side effects: nothing was ingested, so no loans/cases.
    s = Session()
    try:
        loans = s.execute(text("select count(*) from lending.loans where bank_id=:b"),
                          {"b": tenant["bank_id"]}).scalar()
        cases = s.execute(text("select count(*) from collections.cases where bank_id=:b"),
                          {"b": tenant["bank_id"]}).scalar()
    finally:
        s.close()
    assert loans == 0 and cases == 0


def _backdate_claim(Session, batch_id, *, minutes: int) -> None:
    """Make a claim look `minutes` old WITHOUT touching STALE_CLAIM_MINUTES.

    The sweep reads dq_report['claimed_at'] and falls back to received_at, so
    both are moved. This is the SIMULATION the module docstring flags.
    """
    when = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    s = Session()
    try:
        s.execute(text("update lending.bank_feed_batches "
                       "set received_at = :w, "
                       "    dq_report = coalesce(dq_report,'{}'::jsonb) "
                       "                || jsonb_build_object('claimed_at', :iso) "
                       "where id = :i"),
                  {"w": when, "iso": when.isoformat(), "i": batch_id})
        s.commit()
    finally:
        s.close()


# ── redelivery after a kill ────────────────────────────────────────────────

def test_redelivery_after_a_kill_ingests_once_not_twice(Session, tenant, make_batch, monkeypatch):
    """The batch is back at RECEIVED after recovery. Redelivering the task must
    ingest it exactly once and leave one terminal state."""
    from app.workers.tasks import feed_ingestion

    batch = make_batch(status="RECEIVED")
    csv = (b"customer_ref,loan_account_number,case_number,dpd,total_outstanding\n"
           b"P8K1,P8KLN1,P8KCASE1,45,100000\n")
    calls = []

    class Result:
        rows_read, rows_skipped, rows_quarantined, cases_inserted = 1, 0, 0, 1
        status = "LOADED"

    monkeypatch.setattr("scripts.ingest_daily.run_ingestion",
                        lambda *a, **k: (calls.append(1), Result())[1])
    monkeypatch.setattr("app.core.storage.download_bytes", lambda key: csv)
    monkeypatch.setattr("app.core.database.SessionLocal", Session)

    first = feed_ingestion.ingest_feed_batch.run(batch.id)
    second = feed_ingestion.ingest_feed_batch.run(batch.id)

    assert len(calls) == 1, f"ingested {len(calls)} times"
    assert first["claimed"] is True and second["claimed"] is False
    assert _batch_state(Session, batch.id).status == "LOADED"


# ── stranded RECEIVED batches (dispatch lost) ──────────────────────────────

def test_the_sweep_dispatches_a_batch_whose_enqueue_was_lost(Session, make_batch, monkeypatch):
    """The committed-but-never-dispatched window: the row is the queue of
    record, so the 15-minute sweep must find it."""
    from app.workers.tasks import feed_ingestion

    batch = make_batch(status="RECEIVED")
    sent = []
    monkeypatch.setattr(feed_ingestion.ingest_feed_batch, "delay",
                        lambda *a, **k: sent.append(str(a[0])))
    monkeypatch.setattr("app.core.database.SessionLocal", Session)

    out = feed_ingestion.recover_stranded_batches.run()
    assert str(batch.id) in sent
    assert out["dispatched"] >= 1


def test_the_sweep_leaves_terminal_batches_alone(Session, make_batch, monkeypatch):
    from app.workers.tasks import feed_ingestion

    loaded = make_batch(status="LOADED")
    rejected = make_batch(status="REJECTED")
    sent = []
    monkeypatch.setattr(feed_ingestion.ingest_feed_batch, "delay",
                        lambda *a, **k: sent.append(str(a[0])))
    monkeypatch.setattr("app.core.database.SessionLocal", Session)

    feed_ingestion.recover_stranded_batches.run()
    assert str(loaded.id) not in sent
    assert str(rejected.id) not in sent
    assert _batch_state(Session, loaded.id).status == "LOADED"
    assert _batch_state(Session, rejected.id).status == "REJECTED"


# ── stale VALIDATING claims (SIMULATED timestamps, threshold unchanged) ────

def test_a_stale_validating_claim_is_reclaimed(Session, make_batch, monkeypatch):
    from app.workers.tasks import feed_ingestion

    assert feed_ingestion.STALE_CLAIM_MINUTES == 60, "the threshold must stay at 60"
    batch = make_batch(status="VALIDATING")
    _backdate_claim(Session, batch.id, minutes=61)

    monkeypatch.setattr(feed_ingestion.ingest_feed_batch, "delay", lambda *a, **k: None)
    monkeypatch.setattr("app.core.database.SessionLocal", Session)

    out = feed_ingestion.recover_stranded_batches.run()
    assert out["reclaimed_stale"] >= 1
    assert _batch_state(Session, batch.id).status == "RECEIVED"


def test_a_fresh_validating_claim_is_not_stolen(Session, make_batch, monkeypatch):
    """A slow-but-alive worker must keep its batch."""
    from app.workers.tasks import feed_ingestion

    batch = make_batch(status="VALIDATING")
    _backdate_claim(Session, batch.id, minutes=5)

    monkeypatch.setattr(feed_ingestion.ingest_feed_batch, "delay", lambda *a, **k: None)
    monkeypatch.setattr("app.core.database.SessionLocal", Session)

    out = feed_ingestion.recover_stranded_batches.run()
    assert out["reclaimed_stale"] == 0
    assert _batch_state(Session, batch.id).status == "VALIDATING"


# ── retryable vs terminal failures ─────────────────────────────────────────

def test_a_missing_staged_file_is_terminal_not_retried(Session, make_batch, monkeypatch):
    from app.workers.tasks import feed_ingestion

    batch = make_batch(status="RECEIVED", storage_key=None)
    monkeypatch.setattr("app.core.database.SessionLocal", Session)
    retried = []
    monkeypatch.setattr(feed_ingestion.ingest_feed_batch, "retry",
                        lambda **k: retried.append(1))

    out = feed_ingestion.ingest_feed_batch.run(batch.id)
    assert out["status"] == "REJECTED"
    assert retried == []
    state = _batch_state(Session, batch.id)
    assert state.status == "REJECTED"
    assert "ingestion_error" in (state.dq_report or {})


def test_storage_unavailable_releases_the_claim_and_retries(Session, make_batch, monkeypatch):
    """Transient: the batch must go back to RECEIVED so a retry can claim it."""
    from app.workers.tasks import feed_ingestion

    batch = make_batch(status="RECEIVED")
    monkeypatch.setattr("app.core.database.SessionLocal", Session)
    monkeypatch.setattr("app.core.storage.download_bytes",
                        lambda key: (_ for _ in ()).throw(OSError("minio unreachable")))
    captured = {}
    monkeypatch.setattr(feed_ingestion.ingest_feed_batch, "retry",
                        lambda **k: captured.setdefault("exc", k.get("exc")) or Exception("retry"))

    with pytest.raises(Exception):
        feed_ingestion.ingest_feed_batch.run(batch.id)

    assert isinstance(captured["exc"], OSError)
    assert _batch_state(Session, batch.id).status == "RECEIVED"


def test_an_engine_refusal_is_terminal(Session, make_batch, monkeypatch):
    from app.workers.tasks import feed_ingestion
    from scripts.ingest_daily import IngestionError

    batch = make_batch(status="RECEIVED")
    csv = b"customer_ref,loan_account_number,case_number\nA,B,C\n"
    monkeypatch.setattr("app.core.database.SessionLocal", Session)
    monkeypatch.setattr("app.core.storage.download_bytes", lambda key: csv)
    monkeypatch.setattr("scripts.ingest_daily.run_ingestion",
                        lambda *a, **k: (_ for _ in ()).throw(IngestionError("bad header")))
    retried = []
    monkeypatch.setattr(feed_ingestion.ingest_feed_batch, "retry",
                        lambda **k: retried.append(1))

    out = feed_ingestion.ingest_feed_batch.run(batch.id)
    assert out["status"] == "REJECTED"
    assert retried == []
