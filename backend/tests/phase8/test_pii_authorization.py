"""Phase 9 — raw-PII authorization and audit, on real PostgreSQL via real HTTP.

WHAT IS UNDER TEST. `include_raw=true` returns the quarantined row AS RECEIVED
(borrower name, phone, address, masked PAN). Phase 8 measured that every holder
of `bank_feed.upload` could read it — including BANK_TECHOPS and the SERVICE
account — with no audit row. Phase 9 split that into a separate capability
(`bank_feed.pii.read`, BANK_ADMIN only) and made every successful read write a
DATA_EXPORT audit row, failing closed if it cannot.

NO REAL PII. Every borrower is synthetic; the masked columns carry obvious
placeholder tokens. The assertions check WHICH FIELDS are present and that the
AUDIT ROW contains none of them — never a borrower value, and nothing is
printed.

Lives in tests/phase8/ to reuse that package's isolated-database fixtures
(a dedicated `fieldops_phase8`, guarded). Run:

    docker compose exec -T api sh -lc \\
      'cd /app && PHASE8_DB=1 python -m pytest tests/phase8/test_pii_authorization.py -q'
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.services.bank import feed_quarantine_service as svc

# The synthetic row the fixtures stage. Values are placeholders by design.
SYNTH = {
    "loan_account_number": "LNPII0001",
    "customer_ref": "CPII0001",
    "customer_name": "Synthetic Person",
    "phone_primary": "9000000000",
    "address_line1": "Synthetic Addr 1",
    "pan_masked": "XXXXX0000X",
    "branch_code": "BPII",
}
# Every borrower value that must never appear in an audit row.
PII_VALUES = ("Synthetic Person", "9000000000", "Synthetic Addr 1", "XXXXX0000X")


@pytest.fixture()
def api(Session):
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
def held(db, tenant):
    """A batch with one quarantined row carrying synthetic borrower data, plus
    one user per role under test."""
    from app.core.security import hash_password
    from app.models.lending import BankFeedBatch, BankFeedRow
    from app.models.user import User, UserRole

    tag = tenant["tag"]
    users = {}
    for i, (key, role) in enumerate((("techops", UserRole.BANK_TECHOPS),
                                     ("analyst", UserRole.BANK_ANALYST),
                                     ("service", UserRole.SERVICE))):
        u = User(id=str(uuid.uuid4()), email=f"p9{key}.{tag}@example.test",
                 phone=f"9{(int(tag[:6], 16) + i * 13) % 1000000000:09d}",
                 full_name=f"P9 {key}", hashed_password=hash_password("Harbour-Lights-2026"),
                 role=role, bank_id=tenant["bank_id"], agency_id=None, is_active=True)
        db.add(u)
        users[key] = u
    db.flush()

    batch = BankFeedBatch(id=str(uuid.uuid4()), bank_id=tenant["bank_id"],
                          feed_type="DAILY_BOOK", business_date=date.today(),
                          file_name="p9.csv", file_sha256=uuid.uuid4().hex * 2,
                          received_via="UPLOAD", status="PARTIAL", rows_total=1,
                          rows_accepted=0, rows_quarantined=1, dq_report={},
                          received_at=datetime.now(timezone.utc))
    db.add(batch)
    db.flush()
    row = BankFeedRow(id=str(uuid.uuid4()), bank_id=tenant["bank_id"], batch_id=batch.id,
                      row_no=1, loan_account_number=SYNTH["loan_account_number"],
                      customer_ref=SYNTH["customer_ref"], case_number="CASEPII01",
                      raw=dict(SYNTH), status="QUARANTINED",
                      dq_errors=[{"reason": "UNKNOWN_BRANCH", "detail": "unknown branch"}],
                      loan_id=None, processed_at=datetime.now(timezone.utc))
    db.add(row)
    db.commit()
    return {"batch_id": batch.id, "row_id": row.id,
            "techops_id": users["techops"].id, "analyst_id": users["analyst"].id,
            "service_id": users["service"].id}


def _hdr(user_id, role="BANK_ADMIN"):
    from app.core.security import create_access_token
    return {"Authorization": f"Bearer {create_access_token(user_id, role, 'dev-device-01')}"}


def _audits(Session, bank_id, *, access="raw_pii_read"):
    s = Session()
    try:
        return s.execute(text(
            "select action, user_id, bank_id, details, created_at, success "
            "from audit.audit_logs where bank_id = :b and details->>'access' = :a "
            "order by created_at desc"), {"b": bank_id, "a": access}).fetchall()
    finally:
        s.close()


# ── the capability itself ───────────────────────────────────────────────────

def test_the_pii_capability_is_declared_and_held_by_bank_admin_only():
    from app.core.permissions import CAPABILITIES, role_capabilities
    from app.models.user import UserRole

    assert svc.PII_CAPABILITY in CAPABILITIES
    holders = {r.value for r in UserRole if svc.PII_CAPABILITY in role_capabilities(r)}
    assert holders == {"BANK_ADMIN"}, holders
    assert CAPABILITIES[svc.PII_CAPABILITY].is_sensitive is True


def test_feed_upload_alone_does_not_imply_raw_pii_access():
    """The RISK-1 finding, asserted: the two grants are separate."""
    from app.core.permissions import role_capabilities
    from app.models.user import UserRole

    techops = role_capabilities(UserRole.BANK_TECHOPS)
    service = role_capabilities(UserRole.SERVICE)
    assert "bank_feed.upload" in techops and svc.PII_CAPABILITY not in techops
    assert "bank_feed.upload" in service and svc.PII_CAPABILITY not in service


# ── authorized reads ───────────────────────────────────────────────────────

def test_an_authorized_user_receives_only_the_approved_raw_fields(api, tenant, held):
    r = api.get(f"/api/v1/bank/feed/{held['batch_id']}/quarantine?include_raw=true",
                headers=_hdr(tenant["admin_id"]))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["raw_pii_available"] is True
    raw = body["items"][0]["raw"]
    # Only allow-listed keys, and nothing the bank's file might add later.
    assert set(raw) <= set(svc.RAW_FIELDS_ALLOWED)
    assert "phone_primary" in raw and "address_line1" in raw


def test_an_unexpected_extra_column_is_not_leaked(api, Session, tenant, held):
    """A bank adding a column must not widen the disclosure by itself."""
    s = Session()
    try:
        s.execute(text("update lending.bank_feed_rows set raw = raw || "
                       "'{\"secret_internal_note\":\"should not leave\"}'::jsonb "
                       "where id = :r"), {"r": held["row_id"]})
        s.commit()
    finally:
        s.close()

    r = api.get(f"/api/v1/bank/feed/{held['batch_id']}/quarantine?include_raw=true",
                headers=_hdr(tenant["admin_id"]))
    raw = r.json()["items"][0]["raw"]
    assert "secret_internal_note" not in raw


def test_the_single_row_endpoint_is_gated_identically(api, tenant, held):
    """The SECOND path. A check on one endpoint only would be no check."""
    ok = api.get(f"/api/v1/bank/feed/quarantine/rows/{held['row_id']}?include_raw=true",
                 headers=_hdr(tenant["admin_id"]))
    assert ok.status_code == 200
    assert ok.json()["raw_pii_available"] is True and "raw" in ok.json()

    denied = api.get(f"/api/v1/bank/feed/quarantine/rows/{held['row_id']}?include_raw=true",
                     headers=_hdr(held["techops_id"], "BANK_TECHOPS"))
    assert denied.status_code == 200
    assert denied.json()["raw_pii_available"] is False
    assert "raw" not in denied.json()


def test_raw_is_still_absent_by_default_for_an_authorized_user(api, tenant, held):
    """Default behaviour preserved: not requested means not returned."""
    r = api.get(f"/api/v1/bank/feed/{held['batch_id']}/quarantine",
                headers=_hdr(tenant["admin_id"]))
    assert r.json()["raw_pii_available"] is False
    assert "raw" not in r.json()["items"][0]


# ── unauthorized reads ─────────────────────────────────────────────────────

def test_tech_ops_asking_for_raw_pii_receives_none(api, tenant, held):
    r = api.get(f"/api/v1/bank/feed/{held['batch_id']}/quarantine?include_raw=true",
                headers=_hdr(held["techops_id"], "BANK_TECHOPS"))
    assert r.status_code == 200, "review must not be refused outright"
    body = r.json()
    assert body["raw_pii_available"] is False
    assert body["raw_pii_withheld_reason"] == "not_authorised"
    assert "raw" not in body["items"][0]
    # And no borrower value leaked anywhere in the body.
    for value in PII_VALUES:
        if value == SYNTH["loan_account_number"]:
            continue
        assert value not in r.text, "a borrower value appeared in an unauthorised response"


def test_the_service_account_cannot_read_raw_pii(api, tenant, held):
    r = api.get(f"/api/v1/bank/feed/{held['batch_id']}/quarantine?include_raw=true",
                headers=_hdr(held["service_id"], "SERVICE"))
    # SERVICE holds bank_feed.upload, so the route opens; the PII does not.
    assert r.status_code == 200
    assert r.json()["raw_pii_available"] is False
    assert "raw" not in r.json()["items"][0]


def test_safe_quarantine_metadata_is_still_served_without_the_pii_grant(api, tenant, held):
    """The whole review workflow must survive the restriction."""
    r = api.get(f"/api/v1/bank/feed/{held['batch_id']}/quarantine?include_raw=true",
                headers=_hdr(held["techops_id"], "BANK_TECHOPS"))
    item = r.json()["items"][0]
    for field in ("row_id", "row_no", "status", "loan_account_number", "customer_ref",
                  "reason", "detail", "releasable", "blocked_because"):
        assert field in item, field
    assert r.json()["total_quarantined"] == 1
    assert item["reason"] == "UNKNOWN_BRANCH"


def test_a_role_without_review_permission_keeps_its_existing_403(api, tenant, held):
    """Unchanged behaviour: BANK_ANALYST was already refused the endpoint."""
    r = api.get(f"/api/v1/bank/feed/{held['batch_id']}/quarantine?include_raw=true",
                headers=_hdr(held["analyst_id"], "BANK_ANALYST"))
    assert r.status_code == 403
    for value in PII_VALUES:
        assert value not in r.text


# ── audit of every successful read ─────────────────────────────────────────

def test_a_successful_raw_read_writes_one_audit_row(api, Session, tenant, held):
    before = len(_audits(Session, tenant["bank_id"]))
    r = api.get(f"/api/v1/bank/feed/{held['batch_id']}/quarantine?include_raw=true",
                headers=_hdr(tenant["admin_id"]))
    assert r.json()["raw_pii_available"] is True

    rows = _audits(Session, tenant["bank_id"])
    assert len(rows) == before + 1
    a = rows[0]
    assert a.action == "DATA_EXPORT"
    assert str(a.user_id) == str(tenant["admin_id"])
    assert str(a.bank_id) == str(tenant["bank_id"])
    assert a.created_at is not None and a.success is True
    d = a.details
    assert d["access"] == "raw_pii_read"
    assert str(d["batch_id"]) == str(held["batch_id"])
    assert d["rows_disclosed"] == 1
    assert d["capability"] == svc.PII_CAPABILITY
    assert "quarantine" in d["endpoint"]


def test_audit_metadata_contains_no_borrower_pii(api, Session, tenant, held):
    """The requirement that matters most: the trail records the ACCESS, never
    the data. Checked against the serialised row, not just the keys."""
    import json

    api.get(f"/api/v1/bank/feed/{held['batch_id']}/quarantine?include_raw=true",
            headers=_hdr(tenant["admin_id"]))
    a = _audits(Session, tenant["bank_id"])[0]
    blob = json.dumps(a.details)

    for value in PII_VALUES:
        assert value not in blob, "a borrower value was written to the audit trail"
    # The field NAMES are recorded (so a reviewer knows what was exposed);
    # the values are not.
    assert "phone_primary" in blob and "9000000000" not in blob
    # And no CSV content.
    assert "," not in blob.replace('", "', "").replace('","', "") or "raw" not in a.details


def test_each_read_is_audited_not_just_the_first(api, Session, tenant, held):
    for _ in range(3):
        api.get(f"/api/v1/bank/feed/{held['batch_id']}/quarantine?include_raw=true",
                headers=_hdr(tenant["admin_id"]))
    assert len(_audits(Session, tenant["bank_id"])) == 3


def test_a_withheld_read_writes_no_data_export_row(api, Session, tenant, held):
    """A denied request discloses nothing, so recording DATA_EXPORT would make
    the trail read as though borrower data had been handed over. The route's
    own refusal path (ROLE_VIOLATION_ATTEMPT) is unchanged and separate."""
    api.get(f"/api/v1/bank/feed/{held['batch_id']}/quarantine?include_raw=true",
            headers=_hdr(held["techops_id"], "BANK_TECHOPS"))
    assert _audits(Session, tenant["bank_id"]) == []


def test_a_request_that_does_not_ask_for_raw_writes_no_audit_row(api, Session, tenant, held):
    api.get(f"/api/v1/bank/feed/{held['batch_id']}/quarantine",
            headers=_hdr(tenant["admin_id"]))
    assert _audits(Session, tenant["bank_id"]) == []


# ── fail closed ────────────────────────────────────────────────────────────

def test_raw_pii_is_withheld_when_the_audit_row_cannot_be_persisted(api, Session, tenant,
                                                                    held, monkeypatch):
    """FAIL CLOSED. write_audit never raises and returns False on failure; the
    service must treat that as a refusal rather than serving unlogged PII."""
    monkeypatch.setattr("app.services.bank.feed_quarantine_service.write_audit",
                        lambda *a, **k: False)
    r = api.get(f"/api/v1/bank/feed/{held['batch_id']}/quarantine?include_raw=true",
                headers=_hdr(tenant["admin_id"]))
    assert r.status_code == 200
    body = r.json()
    assert body["raw_pii_available"] is False
    assert body["raw_pii_withheld_reason"] == "audit_unavailable"
    assert "raw" not in body["items"][0]
    for value in PII_VALUES:
        if value == SYNTH["loan_account_number"]:
            continue
        assert value not in r.text


def test_the_single_row_path_also_fails_closed(api, tenant, held, monkeypatch):
    monkeypatch.setattr("app.services.bank.feed_quarantine_service.write_audit",
                        lambda *a, **k: False)
    r = api.get(f"/api/v1/bank/feed/quarantine/rows/{held['row_id']}?include_raw=true",
                headers=_hdr(tenant["admin_id"]))
    assert r.json()["raw_pii_available"] is False
    assert "raw" not in r.json()


# ── tenant isolation ───────────────────────────────────────────────────────

def test_another_banks_raw_pii_cannot_be_reached(api, Session, db, tenant, held):
    """A second bank's admin HOLDS the PII capability, and still gets a 404 for
    this bank's batch — the grant is not a cross-tenant key."""
    from app.core.security import hash_password
    from app.models.tenancy import Bank
    from app.models.user import User, UserRole

    tag = uuid.uuid4().hex[:8]
    bank2 = str(uuid.uuid4())
    db.add(Bank(id=bank2, code=f"PN{tag[:6].upper()}", legal_name="Other Bank",
                display_name="Other", timezone="Asia/Kolkata", brand={},
                status="ACTIVE", is_demo=True))
    db.flush()
    other = User(id=str(uuid.uuid4()), email=f"p9other.{tag}@example.test",
                 phone=f"9{int(tag[:6], 16) % 1000000000:09d}", full_name="Other Admin",
                 hashed_password=hash_password("Harbour-Lights-2026"),
                 role=UserRole.BANK_ADMIN, bank_id=bank2, agency_id=None, is_active=True)
    db.add(other)
    db.commit()

    try:
        listing = api.get(f"/api/v1/bank/feed/{held['batch_id']}/quarantine?include_raw=true",
                          headers=_hdr(other.id))
        single = api.get(f"/api/v1/bank/feed/quarantine/rows/{held['row_id']}?include_raw=true",
                         headers=_hdr(other.id))
        assert listing.status_code == 404 and single.status_code == 404
        for value in PII_VALUES:
            assert value not in listing.text and value not in single.text
        # And no audit row was attributed to the foreign bank.
        assert _audits(Session, bank2) == []
        assert _audits(Session, tenant["bank_id"]) == []
    finally:
        from tests.phase8.conftest import _cleanup
        _cleanup(db, bank2)


# ── the release path is untouched ──────────────────────────────────────────

def test_the_existing_feed_release_audit_behaviour_is_unchanged(api, Session, tenant,
                                                                placeable):
    """PLACEMENT_CREATED / source=FEED_RELEASE must still be exactly as Phase 6
    left it — Phase 9 added a read event, it did not alter the write event."""
    r = api.post(f"/api/v1/bank/feed/{placeable['batch_id']}/quarantine/release",
                 headers=_hdr(tenant["admin_id"]), json={"row_ids": [placeable["row_id"]]})
    assert r.status_code == 200 and r.json()["released"] == 1

    s = Session()
    try:
        rows = s.execute(text("select action, details from audit.audit_logs "
                              "where bank_id = :b order by created_at desc"),
                         {"b": tenant["bank_id"]}).fetchall()
    finally:
        s.close()
    rel = [x for x in rows if (x.details or {}).get("source") == "FEED_RELEASE"]
    assert rel, "the release audit row is missing"
    assert rel[0].action == "PLACEMENT_CREATED"
    assert str((rel[0].details or {}).get("feed_row_id")) == str(placeable["row_id"])


def test_releasing_a_row_does_not_require_the_pii_capability(api, Session, tenant,
                                                             placeable, held):
    """Release is a WRITE on business state, not a PII read. Tech Ops keeps it."""
    r = api.post(f"/api/v1/bank/feed/{placeable['batch_id']}/quarantine/release",
                 headers=_hdr(held["techops_id"], "BANK_TECHOPS"),
                 json={"row_ids": [placeable["row_id"]]})
    assert r.status_code == 200, r.text
    assert r.json()["released"] == 1
    # No DATA_EXPORT row: nothing was disclosed.
    assert _audits(Session, tenant["bank_id"]) == []
