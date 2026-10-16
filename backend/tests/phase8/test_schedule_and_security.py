"""§6 nightly-workflow validation and §8 security acceptance, on real Postgres.

The security half issues REAL HTTP requests through the FastAPI app with real
JWTs for two different banks, so tenant isolation is exercised end to end
rather than asserted at the service layer.

NO REAL PII. Every borrower here is synthetic and the masked columns carry
placeholder tokens (XXXXX0000X). The PII assertions check WHICH FIELDS are
exposed, never the values, and no borrower field is printed.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text


# ── §6 the nightly schedule ────────────────────────────────────────────────

def test_the_three_stages_are_scheduled_once_each_at_the_intended_ist_times():
    from app.workers.celery_app import celery_app

    sched = celery_app.conf.beat_schedule
    expected = {
        "app.workers.tasks.feed_cutoff.check_feed_cutoff": (19, 30),
        "app.workers.tasks.repayment_scoring.run_nightly_repayment_scoring": (19, 45),
        "app.workers.tasks.allocation.run_nightly_allocation": (20, 0),
    }
    for task, (hour, minute) in expected.items():
        entries = [e for e in sched.values() if e["task"] == task]
        assert len(entries) == 1, f"{task}: {len(entries)} schedules (expected exactly 1)"
        cron = entries[0]["schedule"]
        assert cron.hour == {hour} and cron.minute == {minute}, (task, cron)
    assert celery_app.conf.timezone == "Asia/Kolkata"


def test_every_stage_task_is_registered_in_this_process():
    """Registration after a restart: `include` is what the worker loads."""
    from app.workers.celery_app import celery_app

    for module in ("app.workers.tasks.feed_cutoff",
                   "app.workers.tasks.repayment_scoring",
                   "app.workers.tasks.allocation",
                   "app.workers.tasks.feed_ingestion"):
        assert module in celery_app.conf.include, module

    # Importing the module is what registers the task object.
    import app.workers.tasks.feed_cutoff  # noqa: F401
    assert "app.workers.tasks.feed_cutoff.check_feed_cutoff" in celery_app.tasks


def test_the_recovery_sweep_runs_every_fifteen_minutes():
    from app.workers.celery_app import celery_app

    entry = celery_app.conf.beat_schedule["bank-feed-recovery"]
    assert entry["task"] == "app.workers.tasks.feed_ingestion.recover_stranded_batches"
    assert entry["schedule"].minute == {0, 15, 30, 45}


def test_the_stale_claim_threshold_is_unchanged_at_sixty_minutes():
    from app.workers.tasks import feed_ingestion
    assert feed_ingestion.STALE_CLAIM_MINUTES == 60


def test_the_business_date_is_ist_and_rolls_at_the_right_moment():
    from app.core.geo import IST
    from app.services.pipeline_readiness import business_date

    # 00:30 IST on the 16th is 19:00 UTC on the 15th: UTC says the 15th.
    moment = datetime(2026, 10, 16, 0, 30, tzinfo=IST)
    assert moment.astimezone(timezone.utc).date() == date(2026, 10, 15)
    assert business_date(moment) == date(2026, 10, 16)


def test_the_gate_reads_persisted_rows_on_postgres(Session, tenant, make_batch):
    """Downstream gates must read the DATABASE, not task results."""
    from app.services import pipeline_readiness as pr

    on = date.today()
    s = Session()
    try:
        assert pr.ingestion_status(s, on).ready is False      # nothing yet
        make_batch(status="LOADED", on=on, loaded_at=datetime.now(timezone.utc))
        s.expire_all()
        assert pr.ingestion_status(s, on).ready is True       # the row changed the answer
    finally:
        s.close()


@pytest.mark.parametrize("status,ready", [
    ("RECEIVED", False), ("VALIDATING", False), ("REJECTED", False),
    ("LOADED", True), ("PARTIAL", True),
])
def test_each_batch_status_produces_the_intended_gate_verdict(Session, tenant, make_batch,
                                                              status, ready):
    from app.services import pipeline_readiness as pr

    on = date.today()
    make_batch(status=status, on=on, loaded_at=datetime.now(timezone.utc))
    s = Session()
    try:
        assert pr.ingestion_status(s, on).ready is ready
    finally:
        s.close()


def test_a_stale_backfill_is_not_accepted_as_tonights_feed(Session, tenant, make_batch):
    from app.services import pipeline_readiness as pr

    on = date.today()
    make_batch(status="LOADED", on=on,
               loaded_at=datetime.now(timezone.utc) - timedelta(hours=30))
    s = Session()
    try:
        st = pr.ingestion_status(s, on)
        assert st.ready is False and "old" in st.reason
    finally:
        s.close()


# ── §8 security: real HTTP, two banks ──────────────────────────────────────

@pytest.fixture()
def api(Session):
    """The real app, bound to the Phase 8 database."""
    from app.core.database import get_db
    from app.main import app

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
    yield TestClient(app)
    limiter.enabled = was
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture()
def two_banks(db, tenant):
    """A SECOND tenant, so cross-bank access can be attempted for real."""
    from app.core.security import hash_password
    from app.models.lending import BankFeedBatch, BankFeedRow
    from app.models.tenancy import Agency, Bank, Branch
    from app.models.user import User, UserRole

    tag = uuid.uuid4().hex[:8]
    bank2 = str(uuid.uuid4())
    db.add(Bank(id=bank2, code=f"P9{tag[:6].upper()}", legal_name=f"Other Bank {tag}",
                display_name="Other", timezone="Asia/Kolkata", brand={}, status="ACTIVE",
                is_demo=True))
    db.flush()
    admin2 = User(id=str(uuid.uuid4()), email=f"p8other.{tag}@example.test",
                  phone=f"9{int(tag[:6], 16) % 1000000000:09d}", full_name="Other Admin",
                  hashed_password=hash_password("Harbour-Lights-2026"),
                  role=UserRole.BANK_ADMIN, bank_id=bank2, agency_id=None, is_active=True)
    analyst = User(id=str(uuid.uuid4()), email=f"p8analyst.{tag}@example.test",
                   phone=f"9{(int(tag[:6], 16) + 7) % 1000000000:09d}", full_name="Analyst",
                   hashed_password=hash_password("Harbour-Lights-2026"),
                   role=UserRole.BANK_ANALYST, bank_id=tenant["bank_id"], agency_id=None,
                   is_active=True)
    db.add_all([admin2, analyst])
    db.flush()

    # Bank 1's batch with one quarantined row carrying synthetic borrower data.
    batch = BankFeedBatch(id=str(uuid.uuid4()), bank_id=tenant["bank_id"],
                          feed_type="DAILY_BOOK", business_date=date.today(),
                          file_name="p8sec.csv", file_sha256=uuid.uuid4().hex * 2,
                          received_via="UPLOAD", status="PARTIAL", rows_total=1,
                          rows_accepted=0, rows_quarantined=1, dq_report={},
                          received_at=datetime.now(timezone.utc))
    db.add(batch)
    db.flush()
    row = BankFeedRow(id=str(uuid.uuid4()), bank_id=tenant["bank_id"], batch_id=batch.id,
                      row_no=1, loan_account_number="LNSEC0001", customer_ref="CSEC0001",
                      case_number="CASESEC01",
                      raw={"loan_account_number": "LNSEC0001", "customer_ref": "CSEC0001",
                           "phone_primary": "9000000000", "address_line1": "Synthetic Addr",
                           "pan_masked": "XXXXX0000X", "customer_name": "Synthetic Person"},
                      status="QUARANTINED",
                      dq_errors=[{"reason": "UNKNOWN_BRANCH", "detail": "unknown branch"}],
                      loan_id=None, processed_at=datetime.now(timezone.utc))
    db.add(row)
    db.commit()

    yield {"batch_id": batch.id, "row_id": row.id, "admin2_id": admin2.id,
           "analyst_id": analyst.id, "bank2": bank2}

    from tests.phase8.conftest import _cleanup
    _cleanup(db, bank2)


def _hdr(user_id, role="BANK_ADMIN"):
    from app.core.security import create_access_token
    return {"Authorization": f"Bearer {create_access_token(user_id, role, 'dev-device-01')}"}


def test_another_banks_batch_history_is_not_visible(api, tenant, two_banks):
    r = api.get("/api/v1/bank/feed", headers=_hdr(two_banks["admin2_id"]))
    assert r.status_code == 200
    names = [b["file_name"] for b in r.json()["items"]]
    assert "p8sec.csv" not in names


def test_another_banks_quarantine_listing_is_the_uniform_404(api, tenant, two_banks):
    foreign = api.get(f"/api/v1/bank/feed/{two_banks['batch_id']}/quarantine",
                      headers=_hdr(two_banks["admin2_id"]))
    missing = api.get(f"/api/v1/bank/feed/{uuid.uuid4()}/quarantine",
                      headers=_hdr(two_banks["admin2_id"]))
    assert foreign.status_code == 404
    assert foreign.json() == missing.json()


def test_a_user_cannot_release_another_banks_row(api, Session, tenant, two_banks):
    r = api.post(f"/api/v1/bank/feed/{two_banks['batch_id']}/quarantine/release",
                 headers=_hdr(two_banks["admin2_id"]),
                 json={"row_ids": [two_banks["row_id"]]})
    assert r.status_code == 404, r.text

    s = Session()
    try:
        status = s.execute(text("select status from lending.bank_feed_rows where id=:r"),
                           {"r": two_banks["row_id"]}).scalar()
    finally:
        s.close()
    assert status == "QUARANTINED", "the row must be untouched"


def test_a_read_only_role_cannot_review_or_release(api, tenant, two_banks):
    listing = api.get(f"/api/v1/bank/feed/{two_banks['batch_id']}/quarantine",
                      headers=_hdr(two_banks["analyst_id"], "BANK_ANALYST"))
    release = api.post(f"/api/v1/bank/feed/{two_banks['batch_id']}/quarantine/release",
                       headers=_hdr(two_banks["analyst_id"], "BANK_ANALYST"),
                       json={"row_ids": [two_banks["row_id"]]})
    assert listing.status_code == 403
    assert release.status_code == 403


def test_include_raw_is_off_by_default_and_opt_in_only(api, tenant, two_banks):
    """THE PII BOUNDARY, measured. Default: business keys only. With
    include_raw=true the WHOLE received row is returned, which includes
    borrower name, phone and address."""
    default = api.get(f"/api/v1/bank/feed/{two_banks['batch_id']}/quarantine",
                      headers=_hdr(tenant["admin_id"])).json()
    assert "raw" not in default["items"][0]

    withraw = api.get(f"/api/v1/bank/feed/{two_banks['batch_id']}/quarantine?include_raw=true",
                      headers=_hdr(tenant["admin_id"])).json()
    raw = withraw["items"][0]["raw"]

    # Report the FIELD NAMES exposed, never the values.
    exposed = sorted(raw.keys())
    assert "phone_primary" in exposed and "address_line1" in exposed
    print("\n[PII] include_raw=true exposes fields:", exposed)
    # Same capability gates both; there is no separate PII capability today.
    assert set(default["items"][0]) >= {"loan_account_number", "customer_ref"}


def test_error_bodies_carry_no_stack_trace_or_internal_paths(api, tenant, two_banks):
    bad = api.post(f"/api/v1/bank/feed/{two_banks['batch_id']}/quarantine/release",
                   headers=_hdr(tenant["admin_id"]), json={"row_ids": ["not-a-uuid"]})
    assert bad.status_code == 404
    body = bad.text
    for leak in ("Traceback", 'File "', "/app/", "psycopg2", "sqlalchemy"):
        assert leak not in body, f"error body leaked {leak!r}"


def test_a_release_is_audited_as_a_feed_release(api, Session, tenant, two_banks, placeable):
    """§8: audit metadata must identify the action as a FEED release."""
    r = api.post(f"/api/v1/bank/feed/{placeable['batch_id']}/quarantine/release",
                 headers=_hdr(tenant["admin_id"]), json={"row_ids": [placeable["row_id"]]})
    assert r.status_code == 200, r.text
    assert r.json()["released"] == 1

    s = Session()
    try:
        rows = s.execute(text("select action, user_id, details from audit.audit_logs "
                              "where bank_id=:b order by created_at desc limit 5"),
                         {"b": tenant["bank_id"]}).fetchall()
    finally:
        s.close()
    assert rows, "no audit row was written for the release"
    rel = [x for x in rows if (x.details or {}).get("source") == "FEED_RELEASE"]
    assert rel, f"no FEED_RELEASE audit row; got {[x.action for x in rows]}"
    # Raw SQL returns a uuid.UUID for a UUID column; compare as strings.
    assert str(rel[0].user_id) == str(tenant["admin_id"])
    assert str((rel[0].details or {}).get("feed_row_id")) == str(placeable["row_id"])
    assert rel[0].action == "PLACEMENT_CREATED"
    assert (rel[0].details or {}).get("released_reason")
