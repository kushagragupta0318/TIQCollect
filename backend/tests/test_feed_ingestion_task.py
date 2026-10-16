"""The asynchronous handoff: a staged batch becomes an ingested one, exactly once.

What is worth testing here is not "does it call run_ingestion" but the four
things a reviewer cannot verify by reading:

  * THE CLAIM is exclusive. Two deliveries of the same task, and two workers
    racing, must produce ONE ingestion.
  * THE BANK comes from the batch row, never from the task argument.
  * A PERMANENT failure is terminal (REJECTED, no retry); a TRANSIENT one
    releases the claim and retries.
  * NOTHING is ingested inside the HTTP request.

run_ingestion itself is stubbed: it has its own suite (test_ingest_feed_v2.py)
and it needs Postgres. What is exercised for real is the state machine around
it, against the real CHECK constraint on `status`.
"""
import io
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.core.security import create_access_token, hash_password
from app.main import app
from app.models.lending import FEED_STATUSES, BankFeedBatch
from app.models.tenancy import Bank
from app.models.user import User, UserRole
from app.workers.tasks import feed_ingestion as task_mod
from tests._db import TEST_BANK_ID, create_schema, make_engine, make_session_factory, test_id

BANK2 = test_id("bank:girivan-async")

HEADERS = "customer_ref,loan_account_number,case_number,dpd,total_outstanding\n"
GOOD_CSV = (HEADERS + "CUST001,LN0001,CASE0001,45,100000\n").encode()


class FakeResult:
    """Stands in for scripts.ingest_daily.IngestionResult."""
    def __init__(self, rows_read=1, rows_skipped=0, rows_quarantined=0):
        self.rows_read = rows_read
        self.rows_skipped = rows_skipped
        self.rows_quarantined = rows_quarantined
        self.cases_inserted = rows_read
        self.status = "LOADED" if not (rows_skipped or rows_quarantined) else "PARTIAL"


@pytest.fixture()
def world(monkeypatch):
    engine = make_engine()
    create_schema(engine)
    Session = make_session_factory(engine, info={})
    db = Session()
    db.add(Bank(id=BANK2, code="GFLASYNC", legal_name="Girivan Finance Ltd.",
                display_name="Girivan Finance", timezone="Asia/Kolkata", brand={},
                status="ACTIVE", is_demo=True))
    db.add(User(id=test_id("u:asyncadmin"), email="asyncadmin@example.in", phone="9822200001",
                full_name="Async Admin", hashed_password=hash_password("Harbour-Lights-2026"),
                role=UserRole.BANK_ADMIN, bank_id=TEST_BANK_ID, agency_id=None, is_active=True))
    db.add(User(id=test_id("u:asyncadmin2"), email="asyncadmin2@example.in", phone="9822200002",
                full_name="Other Bank Admin", hashed_password=hash_password("Harbour-Lights-2026"),
                role=UserRole.BANK_ADMIN, bank_id=BANK2, agency_id=None, is_active=True))
    db.commit()

    # The task opens its OWN session (it runs in a worker, not a request), so it
    # must be pointed at the test database too.
    monkeypatch.setattr("app.core.database.SessionLocal", Session, raising=False)

    def override():
        s = Session()
        try:
            yield s
        finally:
            s.close()
    app.dependency_overrides[get_db] = override
    from app.core.ratelimit import limiter
    was = limiter.enabled
    limiter.enabled = False

    # Storage is a seam; the bytes come back verbatim.
    stored: dict[str, bytes] = {}
    monkeypatch.setattr("app.core.storage.ensure_bucket", lambda *a, **k: None)
    monkeypatch.setattr("app.core.storage.upload_bytes",
                        lambda key, data, ctype=None, **k: stored.__setitem__(key, data))
    monkeypatch.setattr("app.core.storage.download_bytes", lambda key: stored[key])

    try:
        yield {"db": db, "Session": Session, "stored": stored,
               "admin": db.query(User).filter(User.id == test_id("u:asyncadmin")).first(),
               "admin2": db.query(User).filter(User.id == test_id("u:asyncadmin2")).first()}
    finally:
        limiter.enabled = was
        app.dependency_overrides.pop(get_db, None)
        db.close()


@pytest.fixture()
def calls(monkeypatch):
    """Record every run_ingestion call, and let a test choose what it does."""
    seen = []

    def fake(path, dry_run=False, *, bank_code, agency_code=None, business_day=None):
        seen.append({"path": path, "bank_code": bank_code, "business_day": business_day,
                     "content": path.read_bytes()})
        behaviour = seen[-1]["behaviour"] = getattr(fake, "behaviour", None)
        if callable(behaviour):
            return behaviour()
        return FakeResult()

    monkeypatch.setattr("scripts.ingest_daily.run_ingestion", fake)
    fake.seen = seen
    return fake


def _hdr(user):
    return {"Authorization": f"Bearer {create_access_token(user.id, user.role.value, 'dev-device-01')}"}


def _upload(client, user, data=GOOD_CSV, name="book.csv", **form):
    return client.post("/api/v1/bank/feed", headers=_hdr(user),
                       files={"file": (name, io.BytesIO(data), "text/csv")}, data=form)


def _batch(db, batch_id):
    db.expire_all()
    return db.query(BankFeedBatch).filter(BankFeedBatch.id == batch_id).first()


# ── the upload itself stays synchronous-free ────────────────────────────────

def test_the_http_upload_does_not_ingest(world, calls, monkeypatch):
    """The whole point of Phase 5: the request stages and returns."""
    monkeypatch.setattr(task_mod.ingest_feed_batch, "delay", lambda *a, **k: None)
    c = TestClient(app)
    r = _upload(c, world["admin"])
    assert r.status_code == 201
    assert r.json()["status"] == "RECEIVED"
    assert calls.seen == []                      # nothing ingested in the request


def test_the_upload_enqueues_exactly_one_task_for_a_new_batch(world, monkeypatch):
    sent = []
    monkeypatch.setattr(task_mod.ingest_feed_batch, "delay", lambda *a, **k: sent.append(a))
    c = TestClient(app)
    body = _upload(c, world["admin"]).json()
    assert sent == [(str(body["batch_id"]),)]
    assert body["queued"] is True


def test_a_duplicate_upload_does_not_enqueue_a_second_ingestion(world, monkeypatch):
    sent = []
    monkeypatch.setattr(task_mod.ingest_feed_batch, "delay", lambda *a, **k: sent.append(a))
    c = TestClient(app)
    _upload(c, world["admin"])
    second = _upload(c, world["admin"]).json()
    assert second["duplicate"] is True
    assert len(sent) == 1                        # the duplicate added no work


def test_a_broker_failure_is_reported_not_swallowed(world, monkeypatch):
    """The upload still succeeds — the batch is durable — but the response says
    the prompt did not go out, so the UI cannot claim it is queued."""
    def boom(*a, **k):
        raise RuntimeError("broker down")
    monkeypatch.setattr(task_mod.ingest_feed_batch, "delay", boom)
    c = TestClient(app)
    body = _upload(c, world["admin"]).json()
    assert body["queued"] is False
    assert body["status"] == "RECEIVED"          # still recorded, still recoverable


# ── the claim ───────────────────────────────────────────────────────────────

def test_processing_a_batch_ingests_it_and_records_the_outcome(world, calls, monkeypatch):
    monkeypatch.setattr(task_mod.ingest_feed_batch, "delay", lambda *a, **k: None)
    c = TestClient(app)
    bid = _upload(c, world["admin"]).json()["batch_id"]

    out = task_mod.ingest_feed_batch.run(bid)

    assert len(calls.seen) == 1
    assert out["claimed"] is True
    assert _batch(world["db"], bid).status == "LOADED"


def test_a_redelivered_task_does_not_ingest_twice(world, calls, monkeypatch):
    """task_acks_late means redelivery is normal. The claim is what stops it."""
    monkeypatch.setattr(task_mod.ingest_feed_batch, "delay", lambda *a, **k: None)
    c = TestClient(app)
    bid = _upload(c, world["admin"]).json()["batch_id"]

    first = task_mod.ingest_feed_batch.run(bid)
    second = task_mod.ingest_feed_batch.run(bid)

    assert len(calls.seen) == 1                  # the second delivery did nothing
    assert first["claimed"] is True
    assert second["claimed"] is False
    assert second["status"] == "LOADED"


def test_two_workers_racing_produce_one_ingestion(world, calls, monkeypatch):
    """The second worker's task starts while the first still holds the claim.
    Simulated by re-entering the task from inside run_ingestion, which is the
    moment the claim is held and the work is in flight."""
    monkeypatch.setattr(task_mod.ingest_feed_batch, "delay", lambda *a, **k: None)
    c = TestClient(app)
    bid = _upload(c, world["admin"]).json()["batch_id"]

    reentrant = {}

    def behaviour():
        # A second worker picks up the same batch mid-run.
        reentrant["result"] = task_mod.ingest_feed_batch.run(bid)
        return FakeResult()
    calls.behaviour = behaviour

    task_mod.ingest_feed_batch.run(bid)

    assert len(calls.seen) == 1                  # the intruder never reached the engine
    assert reentrant["result"]["claimed"] is False
    assert reentrant["result"]["status"] == "VALIDATING"


def test_a_terminal_batch_is_never_reprocessed(world, calls, monkeypatch):
    monkeypatch.setattr(task_mod.ingest_feed_batch, "delay", lambda *a, **k: None)
    c = TestClient(app)
    bid = _upload(c, world["admin"]).json()["batch_id"]
    b = _batch(world["db"], bid)
    b.status = "LOADED"
    world["db"].commit()

    out = task_mod.ingest_feed_batch.run(bid)
    assert out["claimed"] is False and calls.seen == []


# ── tenancy ─────────────────────────────────────────────────────────────────

def test_the_bank_comes_from_the_batch_row_not_the_argument(world, calls, monkeypatch):
    """A task argument names a batch id and nothing else. The bank_code handed
    to the engine is looked up from THAT ROW, so a task cannot be induced to
    ingest one bank's file into another's book."""
    monkeypatch.setattr(task_mod.ingest_feed_batch, "delay", lambda *a, **k: None)
    c = TestClient(app)
    theirs = _upload(c, world["admin2"], name="theirs.csv").json()["batch_id"]

    task_mod.ingest_feed_batch.run(theirs)

    assert calls.seen[0]["bank_code"] == "GFLASYNC"       # bank 2's own code


def test_the_engine_receives_the_batchs_own_business_date(world, calls, monkeypatch):
    """A feed uploaded for a past date must land on its own batch row, not on
    today's — otherwise the run opens a second batch for the same file."""
    monkeypatch.setattr(task_mod.ingest_feed_batch, "delay", lambda *a, **k: None)
    c = TestClient(app)
    bid = _upload(c, world["admin"], business_date="2026-10-01").json()["batch_id"]

    task_mod.ingest_feed_batch.run(bid)

    assert calls.seen[0]["business_day"] == date(2026, 10, 1)


def test_the_engine_receives_the_exact_bytes_that_were_staged(world, calls, monkeypatch):
    monkeypatch.setattr(task_mod.ingest_feed_batch, "delay", lambda *a, **k: None)
    c = TestClient(app)
    bid = _upload(c, world["admin"]).json()["batch_id"]

    task_mod.ingest_feed_batch.run(bid)

    assert calls.seen[0]["content"] == GOOD_CSV


# ── permanent failures: terminal, not retried ───────────────────────────────

def test_a_batch_whose_file_was_never_stored_is_rejected(world, calls, monkeypatch):
    """upload_feed records the batch even when staging the object failed, so
    storage_key can genuinely be NULL. No retry will produce the file."""
    monkeypatch.setattr(task_mod.ingest_feed_batch, "delay", lambda *a, **k: None)
    c = TestClient(app)
    bid = _upload(c, world["admin"]).json()["batch_id"]
    b = _batch(world["db"], bid)
    b.storage_key = None
    world["db"].commit()

    out = task_mod.ingest_feed_batch.run(bid)

    assert out["status"] == "REJECTED"
    assert calls.seen == []
    assert _batch(world["db"], bid).status == "REJECTED"


def test_a_file_the_engine_refuses_is_rejected_with_a_reason(world, calls, monkeypatch):
    monkeypatch.setattr(task_mod.ingest_feed_batch, "delay", lambda *a, **k: None)
    from scripts.ingest_daily import IngestionError

    def behaviour():
        raise IngestionError("Missing required column: loan_account_number")
    calls.behaviour = behaviour

    c = TestClient(app)
    bid = _upload(c, world["admin"]).json()["batch_id"]
    out = task_mod.ingest_feed_batch.run(bid)

    assert out["status"] == "REJECTED"
    b = _batch(world["db"], bid)
    assert b.status == "REJECTED"
    assert "loan_account_number" in b.dq_report["ingestion_error"]
    # The earlier validation report is kept, not overwritten.
    assert "validation" in b.dq_report


def test_a_missing_batch_is_not_an_error(world, calls, monkeypatch):
    out = task_mod.ingest_feed_batch.run(test_id("batch:nonexistent"))
    assert out["claimed"] is False and calls.seen == []


# ── transient failures: release and retry ───────────────────────────────────

def test_a_transient_failure_releases_the_claim_and_retries(world, calls, monkeypatch):
    """Storage unreachable. The batch must go back to RECEIVED — left at
    VALIDATING it would look like work in flight that nobody is doing."""
    monkeypatch.setattr(task_mod.ingest_feed_batch, "delay", lambda *a, **k: None)
    c = TestClient(app)
    bid = _upload(c, world["admin"]).json()["batch_id"]

    monkeypatch.setattr("app.core.storage.download_bytes",
                        lambda key: (_ for _ in ()).throw(OSError("minio unreachable")))

    retried = {}
    monkeypatch.setattr(task_mod.ingest_feed_batch, "retry",
                        lambda **k: retried.setdefault("exc", k.get("exc")) or Exception("retry"))

    with pytest.raises(Exception):
        task_mod.ingest_feed_batch.run(bid)

    assert isinstance(retried["exc"], OSError)
    assert _batch(world["db"], bid).status == "RECEIVED"   # claimable again


def test_an_exhausted_retry_is_rejected_rather_than_left_looking_pending(world, calls, monkeypatch):
    """After the last attempt the batch must say it failed. Left at RECEIVED it
    would read as work nobody has started, and the sweep would retry forever."""
    monkeypatch.setattr(task_mod.ingest_feed_batch, "delay", lambda *a, **k: None)
    c = TestClient(app)
    bid = _upload(c, world["admin"]).json()["batch_id"]

    def behaviour():
        raise RuntimeError("database went away")
    calls.behaviour = behaviour

    # `request` is a read-only property over Celery's own context stack, so the
    # attempt count is set the way Celery sets it rather than by patching.
    task_mod.ingest_feed_batch.push_request(retries=task_mod.MAX_RETRIES)
    try:
        out = task_mod.ingest_feed_batch.run(bid)
    finally:
        task_mod.ingest_feed_batch.pop_request()

    assert out["status"] == "REJECTED"
    b = _batch(world["db"], bid)
    assert b.status == "REJECTED"
    assert "attempts" in b.dq_report["ingestion_error"]


# ── the outcome recorded ────────────────────────────────────────────────────

def test_a_run_with_quarantined_rows_is_partial_not_loaded(world, calls, monkeypatch):
    monkeypatch.setattr(task_mod.ingest_feed_batch, "delay", lambda *a, **k: None)
    calls.behaviour = lambda: FakeResult(rows_read=10, rows_quarantined=3)

    c = TestClient(app)
    bid = _upload(c, world["admin"]).json()["batch_id"]
    task_mod.ingest_feed_batch.run(bid)

    b = _batch(world["db"], bid)
    assert b.status == "PARTIAL"
    assert b.rows_quarantined == 3
    assert b.rows_accepted == 7
    assert b.loaded_at is not None


def test_every_status_this_task_writes_is_one_the_database_allows(world, calls, monkeypatch):
    """The CHECK constraint permits only FEED_STATUSES. A status this task
    invented (PROCESSING, FAILED) would raise on write in Postgres — which
    SQLite would not catch, so it is asserted directly."""
    import inspect
    src = inspect.getsource(task_mod)
    written = {"RECEIVED", "VALIDATING", "LOADED", "PARTIAL", "REJECTED"}
    for status in written:
        assert status in FEED_STATUSES
    for invented in ("PROCESSING", "INGESTING", "ERROR"):
        assert f'"{invented}"' not in src and f"'{invented}'" not in src


# ── recovery ────────────────────────────────────────────────────────────────

def test_recovery_dispatches_a_batch_whose_enqueue_was_lost(world, monkeypatch):
    """The committed-but-never-dispatched window: the row is the queue of
    record, so the sweep must find it."""
    monkeypatch.setattr(task_mod.ingest_feed_batch, "delay", lambda *a, **k: None)
    c = TestClient(app)
    bid = _upload(c, world["admin"]).json()["batch_id"]       # still RECEIVED

    sent = []
    monkeypatch.setattr(task_mod.ingest_feed_batch, "delay", lambda *a, **k: sent.append(a[0]))
    out = task_mod.recover_stranded_batches.run()

    assert str(bid) in [str(s) for s in sent]
    assert out["dispatched"] >= 1


def test_recovery_reclaims_a_batch_stranded_by_a_dead_worker(world, monkeypatch):
    monkeypatch.setattr(task_mod.ingest_feed_batch, "delay", lambda *a, **k: None)
    c = TestClient(app)
    bid = _upload(c, world["admin"]).json()["batch_id"]
    b = _batch(world["db"], bid)
    b.status = "VALIDATING"
    b.received_at = datetime.now(timezone.utc) - timedelta(minutes=task_mod.STALE_CLAIM_MINUTES + 5)
    world["db"].commit()

    out = task_mod.recover_stranded_batches.run()

    assert out["reclaimed_stale"] == 1
    assert _batch(world["db"], bid).status == "RECEIVED"


def test_recovery_leaves_a_fresh_claim_alone(world, monkeypatch):
    """A slow-but-alive run must not have its batch stolen mid-ingestion."""
    monkeypatch.setattr(task_mod.ingest_feed_batch, "delay", lambda *a, **k: None)
    c = TestClient(app)
    bid = _upload(c, world["admin"]).json()["batch_id"]
    b = _batch(world["db"], bid)
    b.status = "VALIDATING"
    b.received_at = datetime.now(timezone.utc) - timedelta(minutes=2)
    world["db"].commit()

    out = task_mod.recover_stranded_batches.run()

    assert out["reclaimed_stale"] == 0
    assert _batch(world["db"], bid).status == "VALIDATING"


def test_recovery_does_not_touch_terminal_batches(world, monkeypatch):
    monkeypatch.setattr(task_mod.ingest_feed_batch, "delay", lambda *a, **k: None)
    c = TestClient(app)
    bid = _upload(c, world["admin"]).json()["batch_id"]
    b = _batch(world["db"], bid)
    b.status = "LOADED"
    world["db"].commit()

    sent = []
    monkeypatch.setattr(task_mod.ingest_feed_batch, "delay", lambda *a, **k: sent.append(a[0]))
    task_mod.recover_stranded_batches.run()

    assert str(bid) not in [str(s) for s in sent]
    assert _batch(world["db"], bid).status == "LOADED"
