"""The upload -> worker handoff: claiming, retries, recovery and tenancy.

The batch row is the record of work and the CLAIM is a single atomic
UPDATE ... WHERE status='RECEIVED'. These tests drive that directly rather than
through a live broker: Celery delivery is at-least-once by configuration
(task_acks_late), so what has to be proven is that a SECOND delivery of the
same task is harmless, which is a property of the claim, not of the queue.

run_ingestion is stubbed in the tests that are about the handoff — the engine
itself is covered by the ingestion tests, and running a real CSV through it
here would test the engine twice and the handoff not at all.
"""
from datetime import date, datetime, timedelta, timezone

import pytest

from app.core.database import get_db
from app.core.security import hash_password
from app.models.lending import BankFeedBatch
from app.models.tenancy import Bank
from app.models.user import User, UserRole
from app.workers.tasks import feed_ingestion as fi
from tests._db import TEST_BANK_ID, create_schema, make_engine, make_session_factory, test_id

BANK2 = test_id("bank:handoff-two")


def _batch(db, *, bank_id=TEST_BANK_ID, status="RECEIVED", sha=None, key="feeds/x.csv",
           business_date=None, received_at=None, claimed_at=None):
    # claimed_at mirrors what the real claim stamps into dq_report. Staleness is
    # measured from THAT, not from received_at (upload time) — see _claimed_at.
    b = BankFeedBatch(
        bank_id=bank_id, feed_type="DAILY_BOOK",
        business_date=business_date or date(2026, 10, 15),
        file_name="book.csv", file_sha256=sha or ("a" * 64),
        storage_key=key, received_via="UPLOAD", status=status, rows_total=3,
        received_at=received_at or datetime.now(timezone.utc),
        dq_report={"claimed_at": claimed_at.isoformat()} if claimed_at else None,
    )
    db.add(b)
    db.commit()
    db.refresh(b)
    return b


@pytest.fixture()
def world(monkeypatch):
    engine = make_engine()
    create_schema(engine)
    Session = make_session_factory(engine, info={})
    db = Session()
    db.add(Bank(id=BANK2, code="HANDOFF2", legal_name="Handoff Two Ltd.",
                display_name="Handoff Two", timezone="Asia/Kolkata", brand={},
                status="ACTIVE", is_demo=True))
    db.add(User(id=test_id("u:handoff"), email="handoff@t.test", phone="9812200001",
                full_name="Feed Admin", hashed_password=hash_password("x"),
                role=UserRole.BANK_ADMIN, bank_id=TEST_BANK_ID))
    db.commit()

    # The task opens its own session; point it at this engine.
    monkeypatch.setattr("app.core.database.SessionLocal", Session)
    # Storage is not under test here.
    monkeypatch.setattr("app.core.storage.download_bytes", lambda key: b"loan_account_number\nL1\n")

    from app.main import app

    def override():
        s = Session()
        try:
            yield s
        finally:
            s.close()
    app.dependency_overrides[get_db] = override
    try:
        yield {"db": db, "Session": Session}
    finally:
        app.dependency_overrides.pop(get_db, None)
        db.close()


class _Result:
    """What run_ingestion hands back (counts only)."""
    def __init__(self, read=3, skipped=0, quarantined=0):
        self.rows_read, self.rows_skipped, self.rows_quarantined = read, skipped, quarantined


def _stub_engine(monkeypatch, result=None, raises=None, spy=None):
    def _run(path, dry_run=False, *, bank_code, agency_code=None, business_day=None):
        if spy is not None:
            spy.append({"bank_code": bank_code, "business_day": business_day})
        if raises is not None:
            raise raises
        return result or _Result()
    monkeypatch.setattr("scripts.ingest_daily.run_ingestion", _run)


# ── claiming and duplicate delivery ─────────────────────────────────────────

def test_a_received_batch_is_claimed_and_reaches_a_terminal_status(world, monkeypatch):
    _stub_engine(monkeypatch)
    b = _batch(world["db"])

    out = fi.ingest_feed_batch.run(str(b.id))

    assert out["claimed"] is True
    world["db"].expire_all()
    assert world["db"].query(BankFeedBatch).get(b.id).status == "LOADED"


def test_rows_held_back_make_it_partial_not_loaded(world, monkeypatch):
    _stub_engine(monkeypatch, result=_Result(read=10, skipped=2, quarantined=3))
    b = _batch(world["db"])

    fi.ingest_feed_batch.run(str(b.id))

    world["db"].expire_all()
    row = world["db"].query(BankFeedBatch).get(b.id)
    assert row.status == "PARTIAL"
    assert (row.rows_total, row.rows_skipped, row.rows_quarantined) == (10, 2, 3)
    assert row.rows_accepted == 5          # 10 read - 2 skipped - 3 quarantined
    assert row.loaded_at is not None


def test_a_second_delivery_of_the_same_task_does_nothing(world, monkeypatch):
    """Celery is at-least-once (task_acks_late), so this WILL happen."""
    calls: list = []
    _stub_engine(monkeypatch, spy=calls)
    b = _batch(world["db"])

    first = fi.ingest_feed_batch.run(str(b.id))
    second = fi.ingest_feed_batch.run(str(b.id))

    assert first["claimed"] is True
    assert second["claimed"] is False      # terminal, so no longer claimable
    assert len(calls) == 1, "the engine ran twice for one batch"


def test_two_workers_cannot_both_claim_one_batch(world, monkeypatch):
    """Deterministic, not timing-based: the claim is an atomic conditional
    UPDATE, so the second caller's rowcount is 0 whatever the interleaving."""
    from sqlalchemy import update
    b = _batch(world["db"])
    s1, s2 = world["Session"](), world["Session"]()
    try:
        first = s1.execute(update(BankFeedBatch)
                           .where(BankFeedBatch.id == b.id, BankFeedBatch.status == "RECEIVED")
                           .values(status="VALIDATING")).rowcount
        s1.commit()
        second = s2.execute(update(BankFeedBatch)
                            .where(BankFeedBatch.id == b.id, BankFeedBatch.status == "RECEIVED")
                            .values(status="VALIDATING")).rowcount
        s2.commit()
        assert (first, second) == (1, 0)
    finally:
        s1.close(); s2.close()


@pytest.mark.parametrize("status", ["VALIDATING", "LOADED", "PARTIAL", "REJECTED"])
def test_only_a_received_batch_is_eligible(world, monkeypatch, status):
    calls: list = []
    _stub_engine(monkeypatch, spy=calls)
    b = _batch(world["db"], status=status)

    out = fi.ingest_feed_batch.run(str(b.id))

    assert out["claimed"] is False
    assert calls == [], f"a {status} batch was ingested again"


# ── tenancy ─────────────────────────────────────────────────────────────────

def test_the_bank_comes_from_the_batch_row_not_the_task_argument(world, monkeypatch):
    """The only input is a batch id; the bank is read from that row. A task
    cannot be pointed at another bank's book."""
    calls: list = []
    _stub_engine(monkeypatch, spy=calls)
    b = _batch(world["db"], bank_id=BANK2, sha="b" * 64)

    fi.ingest_feed_batch.run(str(b.id))

    assert calls[0]["bank_code"] == "HANDOFF2"      # BANK2's own code


def test_the_batchs_own_business_date_is_used_not_today(world, monkeypatch):
    """run_ingestion defaults to date.today(); a batch for another day must
    land on its OWN row rather than opening a second one."""
    calls: list = []
    _stub_engine(monkeypatch, spy=calls)
    b = _batch(world["db"], business_date=date(2026, 9, 1))

    fi.ingest_feed_batch.run(str(b.id))

    assert calls[0]["business_day"] == date(2026, 9, 1)


# ── permanent failures: terminal, never retried ─────────────────────────────

def test_a_batch_with_no_stored_file_is_rejected_without_calling_the_engine(world, monkeypatch):
    calls: list = []
    _stub_engine(monkeypatch, spy=calls)
    b = _batch(world["db"], key=None)

    out = fi.ingest_feed_batch.run(str(b.id))

    assert out["status"] == "REJECTED"
    assert calls == []
    world["db"].expire_all()
    assert world["db"].query(BankFeedBatch).get(b.id).status == "REJECTED"


def test_a_file_the_engine_refuses_is_rejected_not_retried(world, monkeypatch):
    from scripts.ingest_daily import IngestionError
    _stub_engine(monkeypatch, raises=IngestionError("malformed header"))
    b = _batch(world["db"])

    out = fi.ingest_feed_batch.run(str(b.id))

    assert out["status"] == "REJECTED"
    world["db"].expire_all()
    row = world["db"].query(BankFeedBatch).get(b.id)
    assert row.status == "REJECTED"
    assert "malformed header" in row.dq_report["ingestion_error"]


def test_a_rejection_reason_carries_no_row_data(world, monkeypatch):
    from scripts.ingest_daily import IngestionError
    _stub_engine(monkeypatch, raises=IngestionError("x" * 999))
    b = _batch(world["db"])

    fi.ingest_feed_batch.run(str(b.id))

    world["db"].expire_all()
    assert len(world["db"].query(BankFeedBatch).get(b.id).dq_report["ingestion_error"]) <= 300


# ── transient failures: bounded retry, claim released ───────────────────────

def test_a_transient_failure_releases_the_claim_and_retries(world, monkeypatch):
    """Storage unreachable. The batch must go back to RECEIVED so the retry
    (or the sweep) can take it — not be stranded in VALIDATING."""
    monkeypatch.setattr("app.core.storage.download_bytes",
                        lambda key: (_ for _ in ()).throw(OSError("minio unreachable")))
    b = _batch(world["db"])

    with pytest.raises(Exception):          # Celery's Retry
        fi.ingest_feed_batch.run(str(b.id))

    world["db"].expire_all()
    assert world["db"].query(BankFeedBatch).get(b.id).status == "RECEIVED"


def test_retries_are_bounded_and_end_in_a_terminal_status(world, monkeypatch):
    """A fault that never clears must not retry forever — after MAX_RETRIES the
    batch is REJECTED rather than left looking like untouched work."""
    monkeypatch.setattr("app.core.storage.download_bytes",
                        lambda key: (_ for _ in ()).throw(OSError("still down")))
    b = _batch(world["db"])

    task = fi.ingest_feed_batch
    monkeypatch.setattr(type(task.request), "retries", fi.MAX_RETRIES, raising=False)
    out = task.run(str(b.id))

    assert out["status"] == "REJECTED"
    world["db"].expire_all()
    assert world["db"].query(BankFeedBatch).get(b.id).status == "REJECTED"


# ── recovery: the committed-but-not-dispatched window ───────────────────────

def test_recovery_dispatches_a_received_batch_whose_task_was_never_enqueued(world, monkeypatch):
    """The failure window Phase 4 names: the row committed, the dispatch did
    not. The row IS the queue of record, so the sweep finds it."""
    sent: list = []
    monkeypatch.setattr(fi.ingest_feed_batch, "delay", lambda bid: sent.append(bid))
    b = _batch(world["db"])

    out = fi.recover_stranded_batches.run()

    assert str(b.id) in sent
    assert out["dispatched"] >= 1


def test_recovery_reclaims_a_batch_stranded_by_a_dead_worker(world, monkeypatch):
    """Claimed, then the worker died: VALIDATING with nobody working on it."""
    sent: list = []
    monkeypatch.setattr(fi.ingest_feed_batch, "delay", lambda bid: sent.append(bid))
    old = datetime.now(timezone.utc) - timedelta(minutes=fi.STALE_CLAIM_MINUTES + 5)
    b = _batch(world["db"], status="VALIDATING", claimed_at=old)

    out = fi.recover_stranded_batches.run()

    assert out["reclaimed_stale"] >= 1
    assert str(b.id) in sent


def test_recovery_leaves_a_fresh_claim_alone(world, monkeypatch):
    """A slow-but-alive run must not be stolen mid-flight."""
    sent: list = []
    monkeypatch.setattr(fi.ingest_feed_batch, "delay", lambda bid: sent.append(bid))
    b = _batch(world["db"], status="VALIDATING",
               claimed_at=datetime.now(timezone.utc))    # claimed just now

    fi.recover_stranded_batches.run()

    world["db"].expire_all()
    assert world["db"].query(BankFeedBatch).get(b.id).status == "VALIDATING"
    assert str(b.id) not in sent


# ── audit regressions ───────────────────────────────────────────────────────
# Three defects found by the production-readiness audit, each pinned here.

def test_a_live_claim_is_not_stolen_just_because_the_upload_was_old(world, monkeypatch):
    """DEFECT 1. Staleness was measured from received_at (UPLOAD time), so a
    file uploaded this morning and claimed a minute ago was already "older
    than the cutoff" and the sweep reclaimed it out from under the worker
    holding it — two workers ingesting one file at once."""
    sent: list = []
    monkeypatch.setattr(fi.ingest_feed_batch, "delay", lambda bid: sent.append(bid))
    long_ago = datetime.now(timezone.utc) - timedelta(hours=9)
    b = _batch(world["db"], status="VALIDATING",
               received_at=long_ago,                       # uploaded this morning
               claimed_at=datetime.now(timezone.utc))      # claimed seconds ago

    out = fi.recover_stranded_batches.run()

    assert out["reclaimed_stale"] == 0, "a live claim was stolen"
    world["db"].expire_all()
    assert world["db"].query(BankFeedBatch).get(b.id).status == "VALIDATING"
    assert str(b.id) not in sent


def test_claiming_stamps_the_time_it_happened(world, monkeypatch):
    """The stamp the fix above depends on. Without it there is nothing to
    measure a live claim against."""
    _stub_engine(monkeypatch)
    b = _batch(world["db"])

    fi.ingest_feed_batch.run(str(b.id))

    world["db"].expire_all()
    assert (world["db"].query(BankFeedBatch).get(b.id).dq_report or {}).get("claimed_at")


def test_the_sweep_is_bounded_and_drains_oldest_first(world, monkeypatch):
    """DEFECT 2. The sweep dispatched EVERY RECEIVED batch in one run. A
    backlog must drain over successive sweeps, oldest first, rather than
    enqueueing the whole table at once."""
    sent: list = []
    monkeypatch.setattr(fi.ingest_feed_batch, "delay", lambda bid: sent.append(bid))
    base = datetime.now(timezone.utc) - timedelta(days=1)
    made = [_batch(world["db"], sha=f"{i:064d}",
                   received_at=base + timedelta(minutes=i))
            for i in range(fi.SWEEP_LIMIT + 5)]

    out = fi.recover_stranded_batches.run()

    assert out["dispatched"] == fi.SWEEP_LIMIT, "the sweep was not bounded"
    assert str(made[0].id) in sent, "the oldest batch was not taken first"
    assert str(made[-1].id) not in sent, "a newer batch jumped the queue"


def test_a_broker_outage_during_the_sweep_leaves_everything_recoverable(world, monkeypatch):
    """A dispatch failure must not consume the batch: it stays RECEIVED so the
    next sweep finds it again. Recoverability lives in the row."""
    def _boom(bid):
        raise RuntimeError("broker down")
    monkeypatch.setattr(fi.ingest_feed_batch, "delay", _boom)
    b = _batch(world["db"])

    out = fi.recover_stranded_batches.run()

    assert out["dispatched"] == 0
    world["db"].expire_all()
    assert world["db"].query(BankFeedBatch).get(b.id).status == "RECEIVED"


def test_ingestion_error_can_actually_be_raised(world):
    """DEFECT 3 (engine seam). IngestionError was decorated @dataclass, which
    generated __init__(self) and shadowed Exception's — so every
    `raise IngestionError("...")` in ingest_daily died with a TypeError
    instead of the refusal it meant to raise."""
    from scripts.ingest_daily import IngestionError

    with pytest.raises(IngestionError) as caught:
        raise IngestionError("a message it must carry")
    assert "a message it must carry" in str(caught.value)


def test_a_soft_time_limit_is_terminal_not_retried(world, monkeypatch):
    """AUDIT DEFECT. celery_app raises SoftTimeLimitExceeded INSIDE the task,
    and `except Exception` caught it as a transient fault — so a run that ran
    out of time retried up to MAX_RETRIES, each attempt getting the same file
    and the same ceiling. Three timeouts is ~90 minutes of a worker slot spent
    on work that cannot finish."""
    from celery.exceptions import SoftTimeLimitExceeded

    def _slow(path, dry_run=False, *, bank_code, agency_code=None, business_day=None):
        raise SoftTimeLimitExceeded()
    monkeypatch.setattr("scripts.ingest_daily.run_ingestion", _slow)
    b = _batch(world["db"])

    out = fi.ingest_feed_batch.run(str(b.id))

    assert out["status"] == "REJECTED"
    assert out.get("reason") == "timeout"
    world["db"].expire_all()
    row = world["db"].query(BankFeedBatch).get(b.id)
    assert row.status == "REJECTED", "a timed-out batch was left claimable for a doomed retry"
    assert "time limit" in row.dq_report["ingestion_error"]
