"""POST /bank/feed and friends: upload a daily loan feed over HTTP.

The cases that matter are the ones a reviewer cannot eyeball: that the bank
comes from the TOKEN and not the request, that one bank cannot read or write
another's ingestion, and that re-uploading the same file does not ingest twice.

Fixture shape follows tests/test_bank_users_admin.py (two banks, one session
factory, rate limiter off) so the cross-tenant assertions are real.
"""
import io

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.core.security import create_access_token, hash_password
from app.main import app
from app.models.lending import BankFeedBatch
from app.models.tenancy import Bank
from app.models.user import User, UserRole
from app.services.bank import feed_upload_service as svc
from tests._db import TEST_BANK_ID, create_schema, make_engine, make_session_factory, test_id

BANK2 = test_id("bank:girivan-feed")

HEADERS = "customer_ref,loan_account_number,case_number,dpd,total_outstanding\n"
GOOD_CSV = (HEADERS + "CUST001,LN0001,CASE0001,45,100000\n"
                      "CUST002,LN0002,CASE0002,95,250000\n").encode()


def _user(db, key, role, *, bank=TEST_BANK_ID, phone):
    u = User(id=test_id(f"u:{key}"), email=f"{key}@example.in", phone=phone, full_name=key.title(),
             hashed_password=hash_password("Harbour-Lights-2026"), role=role, bank_id=bank,
             agency_id=None, is_active=True)
    db.add(u)
    return u


@pytest.fixture()
def world():
    engine = make_engine()
    create_schema(engine)
    Session = make_session_factory(engine, info={})          # no default tenant
    db = Session()
    db.add(Bank(id=BANK2, code="GFLFEED", legal_name="Girivan Finance Ltd.",
                display_name="Girivan Finance", timezone="Asia/Kolkata", brand={},
                status="ACTIVE", is_demo=True))
    db.flush()
    w = {
        "db": db, "Session": Session,
        "bank_admin": _user(db, "feedadmin", UserRole.BANK_ADMIN, phone="9811100001"),
        "techops": _user(db, "feedtechops", UserRole.BANK_TECHOPS, phone="9811100002"),
        "analyst": _user(db, "feedanalyst", UserRole.BANK_ANALYST, phone="9811100003"),
        "bank2_admin": _user(db, "feedbank2admin", UserRole.BANK_ADMIN, bank=BANK2, phone="9811100004"),
    }
    db.commit()

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
    try:
        yield w
    finally:
        limiter.enabled = was
        app.dependency_overrides.pop(get_db, None)
        db.close()


def _hdr(user):
    return {"Authorization": f"Bearer {create_access_token(user.id, user.role.value, 'dev-device-01')}"}


def _upload(client, user, data=GOOD_CSV, name="book.csv", **form):
    return client.post("/api/v1/bank/feed", headers=_hdr(user),
                       files={"file": (name, io.BytesIO(data), "text/csv")}, data=form)


# ── authorisation ───────────────────────────────────────────────────────────

def test_an_authorised_bank_user_can_upload_a_valid_feed(world):
    c = TestClient(app)
    r = _upload(c, world["bank_admin"])
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["duplicate"] is False
    assert body["rows_total"] == 2
    assert body["status"] == "RECEIVED"
    assert body["received_via"] == "UPLOAD"


def test_tech_ops_may_also_upload_the_capability_is_not_admin_only(world):
    c = TestClient(app)
    assert _upload(c, world["techops"], name="techops.csv").status_code == 201


def test_a_role_without_the_capability_is_refused(world):
    # BANK_ANALYST reads; it does not submit feeds.
    c = TestClient(app)
    assert _upload(c, world["analyst"]).status_code == 403


def test_an_anonymous_request_is_refused(world):
    c = TestClient(app)
    r = c.post("/api/v1/bank/feed", files={"file": ("x.csv", io.BytesIO(GOOD_CSV), "text/csv")})
    assert r.status_code in (401, 403)


# ── tenancy ─────────────────────────────────────────────────────────────────

def test_the_batch_lands_in_the_callers_own_bank_not_one_named_by_the_client(world):
    """A bank_id in the form must not steer where the feed goes."""
    c = TestClient(app)
    r = _upload(c, world["bank_admin"], bank_id=BANK2, bank_code="GFLFEED")
    assert r.status_code == 201
    batch = world["db"].query(BankFeedBatch).filter(
        BankFeedBatch.id == r.json()["batch_id"]).first()
    assert batch.bank_id == TEST_BANK_ID          # the token's bank, not the form's


def test_history_shows_only_the_callers_own_bank(world):
    c = TestClient(app)
    _upload(c, world["bank_admin"], name="mine.csv")
    _upload(c, world["bank2_admin"], data=HEADERS.encode() + b"CUST9,LN9,CASE9,10,1\n", name="theirs.csv")

    mine = c.get("/api/v1/bank/feed", headers=_hdr(world["bank_admin"])).json()
    names = [b["file_name"] for b in mine["items"]]
    assert "mine.csv" in names
    assert "theirs.csv" not in names


def test_another_banks_batch_is_the_uniform_404(world):
    c = TestClient(app)
    theirs = _upload(c, world["bank2_admin"], name="theirs.csv").json()["batch_id"]

    foreign = c.get(f"/api/v1/bank/feed/{theirs}", headers=_hdr(world["bank_admin"]))
    missing = c.get(f"/api/v1/bank/feed/{test_id('batch:nope')}", headers=_hdr(world["bank_admin"]))
    assert foreign.status_code == 404
    # Indistinguishable from a nonexistent id, so this cannot confirm existence.
    assert foreign.status_code == missing.status_code and foreign.json() == missing.json()


# ── file validation ─────────────────────────────────────────────────────────

def test_an_empty_file_is_refused(world):
    c = TestClient(app)
    assert _upload(c, world["bank_admin"], data=b"").status_code == 422


def test_a_file_with_no_data_rows_is_refused(world):
    c = TestClient(app)
    assert _upload(c, world["bank_admin"], data=HEADERS.encode()).status_code == 422


def test_missing_required_columns_are_named_in_the_error(world):
    c = TestClient(app)
    r = _upload(c, world["bank_admin"], data=b"a,b,c\n1,2,3\n")
    assert r.status_code == 422
    assert "loan_account_number" in r.text


def test_a_binary_file_called_csv_is_refused_on_content_not_extension(world):
    c = TestClient(app)
    r = _upload(c, world["bank_admin"], data=b"\x89PNG\r\n\x1a\n\x00\x00binary", name="feed.csv")
    assert r.status_code == 422


def test_an_oversized_file_is_refused_with_413(world):
    c = TestClient(app)
    big = HEADERS.encode() + b"x" * (svc.MAX_UPLOAD_BYTES + 10)
    assert _upload(c, world["bank_admin"], data=big).status_code == 413


def test_validate_endpoint_reports_without_writing_anything(world):
    c = TestClient(app)
    before = world["db"].query(BankFeedBatch).count()
    r = c.post("/api/v1/bank/feed/validate", headers=_hdr(world["bank_admin"]),
               files={"file": ("dry.csv", io.BytesIO(GOOD_CSV), "text/csv")})
    assert r.status_code == 200 and r.json()["ok"] is True and r.json()["rows"] == 2
    assert world["db"].query(BankFeedBatch).count() == before


# ── idempotency ─────────────────────────────────────────────────────────────

def test_uploading_the_same_file_twice_returns_the_same_batch(world):
    c = TestClient(app)
    first = _upload(c, world["bank_admin"]).json()
    second = _upload(c, world["bank_admin"]).json()

    assert second["duplicate"] is True
    assert second["batch_id"] == first["batch_id"]
    assert world["db"].query(BankFeedBatch).filter(
        BankFeedBatch.file_sha256 == first["file_sha256"],
        BankFeedBatch.bank_id == TEST_BANK_ID).count() == 1


def test_the_same_file_from_two_banks_is_two_batches_not_a_clash(world):
    """The uniqueness key is scoped by bank, so an identical file from another
    bank is its own ingestion, not a duplicate of ours."""
    c = TestClient(app)
    a = _upload(c, world["bank_admin"]).json()
    b = _upload(c, world["bank2_admin"]).json()
    assert b["duplicate"] is False
    assert a["batch_id"] != b["batch_id"]


def test_a_different_file_on_the_same_day_is_a_new_batch(world):
    c = TestClient(app)
    a = _upload(c, world["bank_admin"]).json()
    other = HEADERS.encode() + b"CUST003,LN0003,CASE0003,12,5000\n"
    b = _upload(c, world["bank_admin"], data=other, name="book2.csv").json()
    assert b["duplicate"] is False and a["batch_id"] != b["batch_id"]


# ── safe file handling ──────────────────────────────────────────────────────

def test_a_traversal_filename_is_never_used_as_a_path(world):
    c = TestClient(app)
    r = _upload(c, world["bank_admin"], name="../../../../etc/passwd")
    assert r.status_code == 201
    stored = r.json()["file_name"]
    assert "/" not in stored and ".." not in stored


def test_the_storage_key_is_generated_from_ids_not_from_the_filename():
    from datetime import date
    key = svc.storage_key("../../evil", date(2026, 10, 15), "a" * 64)
    assert ".." not in key
    assert key.startswith("bank-feeds/")
    assert key.endswith(".csv")


def test_csv_values_that_look_like_formulas_are_neutralised():
    # Protects a reviewer opening a downloaded error report in Excel.
    for hostile in ("=cmd|' /C calc'!A0", "+1+1", "-1+1", "@SUM(A1)"):
        assert svc.sanitize_for_csv(hostile).startswith("'")
    assert svc.sanitize_for_csv("LN0001") == "LN0001"
    assert svc.sanitize_for_csv(None) == ""


def test_the_error_body_carries_no_stack_trace(world):
    c = TestClient(app)
    r = _upload(c, world["bank_admin"], data=b"\x00\x01\x02", name="x.csv")
    assert r.status_code == 422
    assert "Traceback" not in r.text and "File \"" not in r.text
