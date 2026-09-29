# ─── CHANGELOG (standalone plan) ─────────────────────────────────────────────
# New file, 2026-09-28 (P2 D01/D02). services/bank/agency_service.py and
# POST/PATCH/GET /bank/agencies* (endpoints/bank_agencies_admin.py).
from __future__ import annotations

import io
import uuid

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.core.database import get_db
from app.core.errors import AppException
from app.core.security import create_access_token, create_agency_doc_upload_token
from app.main import app
from app.models.audit_log import AuditAction, AuditLog
from app.models.tenancy import Agency, AgencyContract, AgencyDocument, Bank, Region
from app.models.user import User, UserRole
from app.services import invite_service
from app.services.bank import agency_service
from tests._db import TEST_AGENCY_ID, TEST_BANK_ID, create_schema, make_engine, make_session_factory, test_id

OTHER_BANK_ID = test_id("bank:other")
OTHER_AGENCY_ID = test_id("agency:other-bank-agency")

_REGION = {"type": "Polygon", "coordinates": [[
    [77.0, 28.4], [77.2, 28.4], [77.2, 28.6], [77.0, 28.6], [77.0, 28.4],
]]}


@pytest.fixture()
def w():
    engine = make_engine()
    create_schema(engine)
    Session = make_session_factory(engine)
    db = Session()

    bank_admin = User(id=test_id("u:bank-admin"), email="deepak.rao@meridiantrust.example", phone="9810005001",
                      full_name="Deepak Rao", hashed_password="x", role=UserRole.BANK_ADMIN, bank_id=TEST_BANK_ID)
    other_bank_admin_user = User(id=test_id("u:bank-admin:other"), email="rina.fernandes@otherbank.example",
                                 phone="9810005002", full_name="Rina Fernandes", hashed_password="x",
                                 role=UserRole.BANK_ADMIN, bank_id=OTHER_BANK_ID)
    field_agent = User(id=test_id("u:field:d02"), email="preexisting.agent@meridiantrust.example",
                       phone="9810005003", full_name="Someone Else", hashed_password="x",
                       role=UserRole.FIELD_AGENT, bank_id=TEST_BANK_ID, agency_id=TEST_AGENCY_ID)
    db.add(Bank(id=OTHER_BANK_ID, code="OTB", legal_name="Other Bank Ltd.", display_name="Other Bank"))
    db.add_all([bank_admin, other_bank_admin_user, field_agent])
    db.flush()

    region = Region(id=test_id("region:d02"), bank_id=TEST_BANK_ID, level="ZONE", code="GGN",
                    name="Gurugram", path="/ncr/haryana/gurugram/", coverage_geojson=_REGION)
    db.add(region)
    db.commit()

    def override():
        s = Session()
        try:
            yield s
        finally:
            s.close()
    app.dependency_overrides[get_db] = override
    try:
        yield {"db": db, "bank_admin": bank_admin, "other_bank_admin": other_bank_admin_user,
              "field_agent": field_agent, "region": region}
    finally:
        app.dependency_overrides.pop(get_db, None)
        db.close()


def _h(user: User) -> dict:
    return {"Authorization": "Bearer " + create_access_token(user.id, user.role.value, "dev-1")}


def _request() -> Request:
    return Request({"type": "http", "method": "POST", "path": "/", "headers": [(b"user-agent", b"pytest")],
                    "client": ("10.0.0.9", 51000), "query_string": b""})


def _draft(db, bank_admin, **overrides) -> dict:
    body = {"legal_name": "Konkan Recovery Services LLP", "trade_name": "Konkan Recovery"}
    body.update(overrides)
    return agency_service.create_draft(db, bank_admin, **body)


# ── website normalisation (owner-reported: type="url" rejected a bare host) ─
@pytest.mark.parametrize("raw,expected", [
    ("google.com", "https://google.com"),
    ("https://foo.co", "https://foo.co"),
    ("www.foo.in", "https://www.foo.in"),
    ("foo.com/x", "https://foo.com/x"),
    ("http://foo.com", "http://foo.com"),          # never silently upgraded to https
    ("https://foo.com/", "https://foo.com"),       # bare-root trailing slash trimmed
    ("https://foo.com/x/", "https://foo.com/x/"),  # a real path's trailing slash is untouched
    ("  foo.com  ", "https://foo.com"),
    ("", None),
    ("   ", None),
    (None, None),
])
def test_website_normalises_on_create(w, raw, expected):
    db, bank_admin = w["db"], w["bank_admin"]
    out = agency_service.create_draft(db, bank_admin, legal_name="Website Test Agency", website=raw)
    assert out["website"] == expected


def test_website_normalises_on_update_too_so_a_paste_matches_a_typed_value(w):
    db, bank_admin = w["db"], w["bank_admin"]
    out = _draft(db, bank_admin)
    agency_service.update_identity(db, bank_admin, out["agency_id"], website="www.foo.com")
    assert db.get(Agency, out["agency_id"]).website == "https://www.foo.com"

    agency_service.update_identity(db, bank_admin, out["agency_id"], website="https://foo.com/")
    assert db.get(Agency, out["agency_id"]).website == "https://foo.com"


# ── create_draft ─────────────────────────────────────────────────────────────
def test_create_draft_creates_a_pending_agency_bound_to_the_bank_admins_bank(w):
    db, bank_admin = w["db"], w["bank_admin"]
    out = _draft(db, bank_admin)

    agency = db.get(Agency, out["agency_id"])
    assert agency.bank_id == TEST_BANK_ID
    assert agency.status == "PENDING"
    assert agency.legal_name == "Konkan Recovery Services LLP"
    assert agency.created_by == bank_admin.id


def test_create_draft_requires_a_legal_name(w):
    db, bank_admin = w["db"], w["bank_admin"]
    with pytest.raises(AppException) as exc:
        agency_service.create_draft(db, bank_admin, legal_name="   ")
    assert exc.value.status_code == 422


def test_create_draft_generates_unique_codes_within_a_bank(w):
    db, bank_admin = w["db"], w["bank_admin"]
    a = _draft(db, bank_admin, legal_name="Same Name Recovery")
    b = _draft(db, bank_admin, legal_name="Same Name Recovery")
    assert a["code"] != b["code"]


def test_create_draft_writes_an_audit_row(w):
    db, bank_admin = w["db"], w["bank_admin"]
    out = _draft(db, bank_admin)
    row = (db.query(AuditLog)
          .filter(AuditLog.entity_type == "Agency", AuditLog.entity_id == out["agency_id"],
                  AuditLog.action == AuditAction.AGENCY_ONBOARDED).first())
    assert row is not None
    assert row.details["step"] == "identity"


def test_a_field_agent_cannot_create_an_agency(w):
    db, field_agent = w["db"], w["field_agent"]
    with pytest.raises(AppException) as exc:
        agency_service.create_draft(db, field_agent, legal_name="Nope")
    assert exc.value.status_code == 403


# ── tenant scoping ───────────────────────────────────────────────────────────
def test_a_bank_admin_from_another_bank_gets_the_uniform_404(w):
    db, bank_admin, other = w["db"], w["bank_admin"], w["other_bank_admin"]
    out = _draft(db, bank_admin)
    with pytest.raises(AppException) as exc:
        agency_service.update_identity(db, other, out["agency_id"], trade_name="Hijacked")
    assert exc.value.status_code == 404


def test_a_missing_agency_id_is_the_same_404_as_a_foreign_one(w):
    db, bank_admin = w["db"], w["bank_admin"]
    with pytest.raises(AppException) as exc:
        agency_service.update_identity(db, bank_admin, test_id("agency:nonexistent"), trade_name="X")
    assert exc.value.status_code == 404


# ── update_identity ──────────────────────────────────────────────────────────
def test_update_identity_tracks_only_the_changed_fields(w):
    db, bank_admin = w["db"], w["bank_admin"]
    out = _draft(db, bank_admin)
    result = agency_service.update_identity(db, bank_admin, out["agency_id"],
                                           trade_name="New Trade Name", pan="ABCDE1234F")
    assert set(result["changed"]) == {"trade_name", "pan"}


def test_update_identity_no_op_returns_empty_changed(w):
    db, bank_admin = w["db"], w["bank_admin"]
    out = _draft(db, bank_admin)
    result = agency_service.update_identity(db, bank_admin, out["agency_id"], trade_name="Konkan Recovery")
    assert result["changed"] == []


def test_update_identity_rejects_an_unknown_field(w):
    db, bank_admin = w["db"], w["bank_admin"]
    out = _draft(db, bank_admin)
    with pytest.raises(AppException) as exc:
        agency_service.update_identity(db, bank_admin, out["agency_id"], status="ACTIVE")
    assert exc.value.status_code == 422


# ── coverage / contract ──────────────────────────────────────────────────────
def test_coverage_and_contract_upserts_one_draft_contract(w):
    db, bank_admin, region = w["db"], w["bank_admin"], w["region"]
    out = _draft(db, bank_admin)
    agency_service.update_coverage_and_contract(
        db, bank_admin, out["agency_id"], max_agents=10, region_ids=[region.id],
        contract_terms=[{"loan_type": "PERSONAL", "dpd_bucket": "BUCKET_1", "commission_pct": 12.5}],
    )
    agency_service.update_coverage_and_contract(db, bank_admin, out["agency_id"], max_agents=20)
    contracts = db.query(AgencyContract).filter(AgencyContract.agency_id == out["agency_id"]).all()
    assert len(contracts) == 1
    assert contracts[0].max_agents == 20


def test_coverage_rejects_an_unknown_region(w):
    db, bank_admin = w["db"], w["bank_admin"]
    out = _draft(db, bank_admin)
    with pytest.raises(AppException) as exc:
        agency_service.update_coverage_and_contract(db, bank_admin, out["agency_id"],
                                                    region_ids=[test_id("region:nonexistent")])
    assert exc.value.status_code == 422


def test_coverage_rejects_a_region_from_another_bank(w):
    db, bank_admin = w["db"], w["bank_admin"]
    other_region = Region(id=test_id("region:otherbank"), bank_id=OTHER_BANK_ID, level="ZONE", code="OTH",
                          name="Other", path="/other/", coverage_geojson=_REGION)
    db.add(other_region)
    db.commit()
    out = _draft(db, bank_admin)
    with pytest.raises(AppException) as exc:
        agency_service.update_coverage_and_contract(db, bank_admin, out["agency_id"], region_ids=[other_region.id])
    assert exc.value.status_code == 422


def test_contract_terms_rejects_a_bad_commission_pct(w):
    db, bank_admin = w["db"], w["bank_admin"]
    out = _draft(db, bank_admin)
    with pytest.raises(AppException) as exc:
        agency_service.update_coverage_and_contract(
            db, bank_admin, out["agency_id"],
            contract_terms=[{"loan_type": "PERSONAL", "dpd_bucket": "BUCKET_1", "commission_pct": 150}],
        )
    assert exc.value.status_code == 422


def test_contract_end_date_before_start_date_is_rejected(w):
    import datetime
    db, bank_admin = w["db"], w["bank_admin"]
    out = _draft(db, bank_admin)
    with pytest.raises(AppException) as exc:
        agency_service.update_coverage_and_contract(
            db, bank_admin, out["agency_id"],
            start_date=datetime.date(2026, 6, 1), end_date=datetime.date(2026, 1, 1),
        )
    assert exc.value.status_code == 422


def test_duplicate_slab_pairs_are_rejected_cleanly_not_a_500(w):
    """Coordinator audit LOW: a duplicate (loan_type, dpd_bucket) pair used
    to reach agency_contract_terms' own UniqueConstraint and surface as an
    unhandled IntegrityError."""
    db, bank_admin = w["db"], w["bank_admin"]
    out = _draft(db, bank_admin)
    with pytest.raises(AppException) as exc:
        agency_service.update_coverage_and_contract(
            db, bank_admin, out["agency_id"],
            contract_terms=[
                {"loan_type": "PERSONAL", "dpd_bucket": "BUCKET_1", "commission_pct": 10},
                {"loan_type": "PERSONAL", "dpd_bucket": "BUCKET_1", "commission_pct": 20},
            ],
        )
    assert exc.value.status_code == 422


# ── documents: confirm trusts nothing from the client ────────────────────────
class _FakeStat:
    def __init__(self, content_type, size):
        self.content_type = content_type
        self.size = size


def _token(agency: dict, doc_type: str, key: str) -> str:
    return create_agency_doc_upload_token(key, bank_id=TEST_BANK_ID, agency_id=agency["agency_id"], doc_type=doc_type)


def test_confirm_document_rejects_a_garbage_upload_token(w, monkeypatch):
    db, bank_admin = w["db"], w["bank_admin"]
    out = _draft(db, bank_admin)
    key = f"agencies/{out['agency_id'][:8]}/agreement_x.pdf"
    with pytest.raises(AppException) as exc:
        agency_service.confirm_document(db, bank_admin, out["agency_id"], doc_type="AGREEMENT", key=key,
                                        upload_token="not-a-real-token")
    assert exc.value.status_code == 422


def test_confirm_document_rejects_a_token_issued_for_a_different_key(w, monkeypatch):
    """Coordinator audit HIGH (51d165b): a prefix check on the key alone
    could not tell "the object this upload_token was issued for" from "an
    object with a similar-looking key" — the token's own `sub` claim must
    match the key EXACTLY."""
    db, bank_admin = w["db"], w["bank_admin"]
    out = _draft(db, bank_admin)
    key = f"agencies/{out['agency_id'][:8]}/agreement_x.pdf"
    wrong_token = _token(out, "AGREEMENT", key + ".different")
    with pytest.raises(AppException) as exc:
        agency_service.confirm_document(db, bank_admin, out["agency_id"], doc_type="AGREEMENT", key=key,
                                        upload_token=wrong_token)
    assert exc.value.status_code == 422


def test_confirm_document_rejects_a_token_issued_for_a_different_doc_type(w, monkeypatch):
    db, bank_admin = w["db"], w["bank_admin"]
    out = _draft(db, bank_admin)
    key = f"agencies/{out['agency_id'][:8]}/agreement_x.pdf"
    token = _token(out, "INSURANCE", key)   # issued for a different doc_type
    with pytest.raises(AppException) as exc:
        agency_service.confirm_document(db, bank_admin, out["agency_id"], doc_type="AGREEMENT", key=key,
                                        upload_token=token)
    assert exc.value.status_code == 422


def test_confirm_document_rejects_a_token_issued_for_a_different_agency(w, monkeypatch):
    db, bank_admin = w["db"], w["bank_admin"]
    out_a = _draft(db, bank_admin, legal_name="Agency A")
    out_b = _draft(db, bank_admin, legal_name="Agency B")
    key = f"agencies/{out_a['agency_id'][:8]}/agreement_x.pdf"
    token = _token(out_b, "AGREEMENT", key)   # issued for agency B, presented against A
    with pytest.raises(AppException) as exc:
        agency_service.confirm_document(db, bank_admin, out_a["agency_id"], doc_type="AGREEMENT", key=key,
                                        upload_token=token)
    assert exc.value.status_code == 422


def test_confirm_document_rejects_when_nothing_was_actually_uploaded(w, monkeypatch):
    from minio.error import S3Error
    db, bank_admin = w["db"], w["bank_admin"]
    out = _draft(db, bank_admin)
    key = f"agencies/{out['agency_id'][:8]}/agreement_x.pdf"

    def fake_stat(k):
        raise S3Error("NoSuchKey", "not found", None, None, None, None)
    monkeypatch.setattr(agency_service.storage, "stat_object", fake_stat)
    with pytest.raises(AppException) as exc:
        agency_service.confirm_document(db, bank_admin, out["agency_id"], doc_type="AGREEMENT", key=key,
                                        upload_token=_token(out, "AGREEMENT", key))
    assert exc.value.status_code == 422


def test_confirm_document_rejects_a_disallowed_content_type_even_if_the_client_claimed_pdf(w, monkeypatch):
    db, bank_admin = w["db"], w["bank_admin"]
    out = _draft(db, bank_admin)
    key = f"agencies/{out['agency_id'][:8]}/agreement_x.exe"
    monkeypatch.setattr(agency_service.storage, "stat_object",
                        lambda k: _FakeStat("application/x-msdownload", 1000))
    deleted = []
    monkeypatch.setattr(agency_service.storage, "delete_object", lambda k: deleted.append(k))
    with pytest.raises(AppException) as exc:
        agency_service.confirm_document(db, bank_admin, out["agency_id"], doc_type="AGREEMENT", key=key,
                                        upload_token=_token(out, "AGREEMENT", key))
    assert exc.value.status_code == 422
    assert deleted == [key]   # coordinator audit MED: a rejected upload does not sit in the bucket


def test_confirm_document_rejects_an_oversized_file(w, monkeypatch):
    db, bank_admin = w["db"], w["bank_admin"]
    out = _draft(db, bank_admin)
    key = f"agencies/{out['agency_id'][:8]}/agreement_x.pdf"
    monkeypatch.setattr(agency_service.storage, "stat_object",
                        lambda k: _FakeStat("application/pdf", 50 * 1024 * 1024))
    deleted = []
    monkeypatch.setattr(agency_service.storage, "delete_object", lambda k: deleted.append(k))
    with pytest.raises(AppException) as exc:
        agency_service.confirm_document(db, bank_admin, out["agency_id"], doc_type="AGREEMENT", key=key,
                                        upload_token=_token(out, "AGREEMENT", key))
    assert exc.value.status_code == 422
    assert deleted == [key]


def test_confirm_document_rejects_content_whose_magic_bytes_dont_match_the_claimed_type(w, monkeypatch):
    """Coordinator audit MED: `stat.content_type` is whatever the CLIENT's
    PUT request claimed — MinIO does not verify it. A caller could claim
    application/pdf on a PUT and confirm with completely different bytes."""
    db, bank_admin = w["db"], w["bank_admin"]
    out = _draft(db, bank_admin)
    key = f"agencies/{out['agency_id'][:8]}/agreement_x.pdf"
    not_actually_a_pdf = b"this is plain text, not a pdf"
    monkeypatch.setattr(agency_service.storage, "stat_object",
                        lambda k: _FakeStat("application/pdf", len(not_actually_a_pdf)))
    monkeypatch.setattr(agency_service.storage, "download_bytes", lambda k: not_actually_a_pdf)
    deleted = []
    monkeypatch.setattr(agency_service.storage, "delete_object", lambda k: deleted.append(k))
    with pytest.raises(AppException) as exc:
        agency_service.confirm_document(db, bank_admin, out["agency_id"], doc_type="AGREEMENT", key=key,
                                        upload_token=_token(out, "AGREEMENT", key))
    assert exc.value.status_code == 422
    assert deleted == [key]


def test_confirm_document_computes_sha256_server_side_not_from_the_client(w, monkeypatch):
    """The client is never asked for a hash at all — confirm_document's own
    signature has no sha256 parameter. This proves the value actually stored
    matches what the fake object bytes really hash to."""
    import hashlib
    db, bank_admin = w["db"], w["bank_admin"]
    out = _draft(db, bank_admin)
    key = f"agencies/{out['agency_id'][:8]}/agreement_x.pdf"
    real_bytes = b"%PDF-1.4 fake agreement contents"
    monkeypatch.setattr(agency_service.storage, "stat_object",
                        lambda k: _FakeStat("application/pdf", len(real_bytes)))
    monkeypatch.setattr(agency_service.storage, "download_bytes", lambda k: real_bytes)
    result = agency_service.confirm_document(db, bank_admin, out["agency_id"], doc_type="AGREEMENT", key=key,
                                             upload_token=_token(out, "AGREEMENT", key))
    doc = db.get(AgencyDocument, result["document_id"])
    assert doc.sha256 == hashlib.sha256(real_bytes).hexdigest()
    assert doc.scan_status == "PENDING"
    assert doc.status == "UPLOADED"


def test_confirm_document_rejects_reuse_of_an_already_confirmed_key(w, monkeypatch):
    """Coordinator audit HIGH: the same uploaded object used to be
    confirmable into more than one AgencyDocument row — e.g. one physical
    file satisfying two different required doc_types."""
    db, bank_admin = w["db"], w["bank_admin"]
    out = _draft(db, bank_admin)
    key = f"agencies/{out['agency_id'][:8]}/agreement_x.pdf"
    monkeypatch.setattr(agency_service.storage, "stat_object", lambda k: _FakeStat("application/pdf", 10))
    monkeypatch.setattr(agency_service.storage, "download_bytes", lambda k: b"%PDF-1.4 x")
    agency_service.confirm_document(db, bank_admin, out["agency_id"], doc_type="AGREEMENT", key=key,
                                    upload_token=_token(out, "AGREEMENT", key))
    with pytest.raises(AppException) as exc:
        agency_service.confirm_document(db, bank_admin, out["agency_id"], doc_type="AGREEMENT", key=key,
                                        upload_token=_token(out, "AGREEMENT", key))
    assert exc.value.status_code == 409


def _upload_doc(db, bank_admin, agency_id, doc_type, monkeypatch, content=b"%PDF-1.4 x") -> str:
    key = f"agencies/{agency_id[:8]}/{doc_type.lower()}_{uuid.uuid4().hex[:8]}.pdf"
    monkeypatch.setattr(agency_service.storage, "stat_object", lambda k: _FakeStat("application/pdf", len(content)))
    monkeypatch.setattr(agency_service.storage, "download_bytes", lambda k: content)
    token = create_agency_doc_upload_token(key, bank_id=TEST_BANK_ID, agency_id=agency_id, doc_type=doc_type)
    return agency_service.confirm_document(db, bank_admin, agency_id, doc_type=doc_type, key=key,
                                           upload_token=token)["document_id"]


# ── document review: four-eyes ───────────────────────────────────────────────
def test_the_uploader_cannot_verify_their_own_document(w, monkeypatch):
    db, bank_admin = w["db"], w["bank_admin"]
    out = _draft(db, bank_admin)
    doc_id = _upload_doc(db, bank_admin, out["agency_id"], "AGREEMENT", monkeypatch)
    with pytest.raises(AppException) as exc:
        agency_service.verify_document(db, bank_admin, out["agency_id"], doc_id)
    assert exc.value.status_code == 409


def test_a_different_bank_admin_can_verify(w, monkeypatch):
    db, bank_admin = w["db"], w["bank_admin"]
    second = User(id=test_id("u:bank-admin:second"), email="second.admin@meridiantrust.example",
                 phone="9810005004", full_name="Second Admin", hashed_password="x",
                 role=UserRole.BANK_ADMIN, bank_id=TEST_BANK_ID)
    db.add(second)
    db.commit()
    out = _draft(db, bank_admin)
    doc_id = _upload_doc(db, bank_admin, out["agency_id"], "AGREEMENT", monkeypatch)
    result = agency_service.verify_document(db, second, out["agency_id"], doc_id)
    assert result["status"] == "VERIFIED"
    assert result["verified_by"] == second.id


def test_an_already_verified_document_refuses_a_second_review_with_the_same_409(w, monkeypatch):
    db, bank_admin = w["db"], w["bank_admin"]
    second = User(id=test_id("u:bank-admin:second2"), email="second2.admin@meridiantrust.example",
                 phone="9810005005", full_name="Second Admin Two", hashed_password="x",
                 role=UserRole.BANK_ADMIN, bank_id=TEST_BANK_ID)
    db.add(second)
    db.commit()
    out = _draft(db, bank_admin)
    doc_id = _upload_doc(db, bank_admin, out["agency_id"], "AGREEMENT", monkeypatch)
    agency_service.verify_document(db, second, out["agency_id"], doc_id)

    with pytest.raises(AppException) as self_exc:
        agency_service.verify_document(db, bank_admin, out["agency_id"], doc_id)
    with pytest.raises(AppException) as already_exc:
        agency_service.verify_document(db, second, out["agency_id"], doc_id)
    assert self_exc.value.status_code == already_exc.value.status_code == 409
    assert self_exc.value.detail == already_exc.value.detail


def test_reject_requires_a_reason(w, monkeypatch):
    db, bank_admin = w["db"], w["bank_admin"]
    second = User(id=test_id("u:bank-admin:third"), email="third.admin@meridiantrust.example",
                 phone="9810005006", full_name="Third Admin", hashed_password="x",
                 role=UserRole.BANK_ADMIN, bank_id=TEST_BANK_ID)
    db.add(second)
    db.commit()
    out = _draft(db, bank_admin)
    doc_id = _upload_doc(db, bank_admin, out["agency_id"], "AGREEMENT", monkeypatch)
    with pytest.raises(AppException) as exc:
        agency_service.reject_document(db, second, out["agency_id"], doc_id, reason="  ")
    assert exc.value.status_code == 422


def test_reject_records_who_and_why(w, monkeypatch):
    db, bank_admin = w["db"], w["bank_admin"]
    second = User(id=test_id("u:bank-admin:fourth"), email="fourth.admin@meridiantrust.example",
                 phone="9810005007", full_name="Fourth Admin", hashed_password="x",
                 role=UserRole.BANK_ADMIN, bank_id=TEST_BANK_ID)
    db.add(second)
    db.commit()
    out = _draft(db, bank_admin)
    doc_id = _upload_doc(db, bank_admin, out["agency_id"], "AGREEMENT", monkeypatch)
    result = agency_service.reject_document(db, second, out["agency_id"], doc_id, reason="Expired certificate")
    assert result["status"] == "REJECTED"
    assert result["rejection_reason"] == "Expired certificate"

    row = (db.query(AuditLog)
          .filter(AuditLog.entity_type == "AgencyDocument", AuditLog.entity_id == doc_id,
                  AuditLog.action == AuditAction.DOCUMENT_REJECTED).first())
    assert row is not None
    assert row.details["reason"] == "Expired certificate"


# ── activation: two independent conditions, both required ───────────────────
def _verify_all_required_docs(db, bank_admin, verifier, agency_id, monkeypatch):
    for doc_type in agency_service.REQUIRED_DOC_TYPES:
        doc_id = _upload_doc(db, bank_admin, agency_id, doc_type, monkeypatch)
        agency_service.verify_document(db, verifier, agency_id, doc_id)


def test_agency_does_not_activate_on_documents_alone(w, monkeypatch):
    db, bank_admin = w["db"], w["bank_admin"]
    verifier = User(id=test_id("u:bank-admin:v1"), email="v1@meridiantrust.example", phone="9810005008",
                    full_name="Verifier One", hashed_password="x", role=UserRole.BANK_ADMIN, bank_id=TEST_BANK_ID)
    db.add(verifier)
    db.commit()
    out = _draft(db, bank_admin)
    _verify_all_required_docs(db, bank_admin, verifier, out["agency_id"], monkeypatch)

    agency = db.get(Agency, out["agency_id"])
    assert agency.status == "PENDING"


def test_agency_does_not_activate_on_invite_acceptance_alone(w):
    db, bank_admin = w["db"], w["bank_admin"]
    out = _draft(db, bank_admin)
    invite_service.create_invite(db, bank_admin, email="admin@konkan-recovery.example", role=UserRole.AGENCY_ADMIN,
                                 full_name="New Agency Admin", phone="9810006001", agency_id=out["agency_id"],
                                 bank_id=TEST_BANK_ID, channel="LINK")
    # No accept: not activated by creating the invite.
    agency = db.get(Agency, out["agency_id"])
    assert agency.status == "PENDING"


def test_agency_activates_once_both_documents_verified_and_invite_accepted(w, monkeypatch):
    from fastapi.testclient import TestClient
    db, bank_admin = w["db"], w["bank_admin"]
    verifier = User(id=test_id("u:bank-admin:v2"), email="v2@meridiantrust.example", phone="9810005009",
                    full_name="Verifier Two", hashed_password="x", role=UserRole.BANK_ADMIN, bank_id=TEST_BANK_ID)
    db.add(verifier)
    db.commit()
    out = _draft(db, bank_admin)
    _verify_all_required_docs(db, bank_admin, verifier, out["agency_id"], monkeypatch)

    invite_result = invite_service.create_invite(
        db, bank_admin, email="admin2@konkan-recovery.example", role=UserRole.AGENCY_ADMIN,
        full_name="New Agency Admin", phone="9810006002", agency_id=out["agency_id"], bank_id=TEST_BANK_ID,
        channel="LINK",
    )
    invite_service.accept_invite(db, invite_result["token"], "Harbour-Lights-2026", "device-1", _request())

    agency = db.get(Agency, out["agency_id"])
    assert agency.status == "ACTIVE"
    assert agency.activated_at is not None
    row = (db.query(AuditLog)
          .filter(AuditLog.entity_type == "Agency", AuditLog.entity_id == out["agency_id"],
                  AuditLog.action == AuditAction.AGENCY_ACTIVATED).first())
    assert row is not None


def test_activation_fires_only_once(w, monkeypatch):
    """A second verify_document call after activation must not re-fire
    AGENCY_ACTIVATED — the PENDING guard in _maybe_activate is what stops it."""
    db, bank_admin = w["db"], w["bank_admin"]
    verifier = User(id=test_id("u:bank-admin:v3"), email="v3@meridiantrust.example", phone="9810005010",
                    full_name="Verifier Three", hashed_password="x", role=UserRole.BANK_ADMIN, bank_id=TEST_BANK_ID)
    db.add(verifier)
    db.commit()
    out = _draft(db, bank_admin)
    _verify_all_required_docs(db, bank_admin, verifier, out["agency_id"], monkeypatch)
    invite_result = invite_service.create_invite(
        db, bank_admin, email="admin3@konkan-recovery.example", role=UserRole.AGENCY_ADMIN,
        full_name="New Agency Admin", phone="9810006003", agency_id=out["agency_id"], bank_id=TEST_BANK_ID,
        channel="LINK",
    )
    invite_service.accept_invite(db, invite_result["token"], "Harbour-Lights-2026", "device-2", _request())

    # Any further wizard action on an already-ACTIVE agency is now refused
    # outright (_require_editable, coordinator audit LOW) — including trying
    # to upload one more document — so there is no path left by which a
    # second AGENCY_ACTIVATED row could be written.
    with pytest.raises(AppException) as exc:
        _upload_doc(db, bank_admin, out["agency_id"], "OTHER", monkeypatch)
    assert exc.value.status_code == 409

    rows = (db.query(AuditLog)
           .filter(AuditLog.entity_type == "Agency", AuditLog.entity_id == out["agency_id"],
                   AuditLog.action == AuditAction.AGENCY_ACTIVATED).all())
    assert len(rows) == 1


def test_identity_edits_are_refused_once_active(w, monkeypatch):
    db, bank_admin = w["db"], w["bank_admin"]
    verifier = User(id=test_id("u:bank-admin:v4"), email="v4@meridiantrust.example", phone="9810005011",
                    full_name="Verifier Four", hashed_password="x", role=UserRole.BANK_ADMIN, bank_id=TEST_BANK_ID)
    db.add(verifier)
    db.commit()
    out = _draft(db, bank_admin)
    _verify_all_required_docs(db, bank_admin, verifier, out["agency_id"], monkeypatch)
    invite_result = invite_service.create_invite(
        db, bank_admin, email="admin4@konkan-recovery.example", role=UserRole.AGENCY_ADMIN,
        full_name="New Agency Admin", phone="9810006005", agency_id=out["agency_id"], bank_id=TEST_BANK_ID,
        channel="LINK",
    )
    invite_service.accept_invite(db, invite_result["token"], "Harbour-Lights-2026", "device-3", _request())
    assert db.get(Agency, out["agency_id"]).status == "ACTIVE"

    with pytest.raises(AppException) as exc:
        agency_service.update_identity(db, bank_admin, out["agency_id"], trade_name="Renamed After Activation")
    assert exc.value.status_code == 409


def test_activation_requires_the_specific_invite_accepted_not_any_active_admin(w, monkeypatch):
    """Coordinator audit LOW: the old check ('any active AGENCY_ADMIN of
    this agency') would have activated on an admin account that reached
    AGENCY_ADMIN some other way — the real condition is that THIS wizard's
    master-login invite was accepted."""
    db, bank_admin = w["db"], w["bank_admin"]
    verifier = User(id=test_id("u:bank-admin:v5"), email="v5@meridiantrust.example", phone="9810005012",
                    full_name="Verifier Five", hashed_password="x", role=UserRole.BANK_ADMIN, bank_id=TEST_BANK_ID)
    db.add(verifier)
    db.commit()
    out = _draft(db, bank_admin)
    _verify_all_required_docs(db, bank_admin, verifier, out["agency_id"], monkeypatch)

    # An AGENCY_ADMIN exists and is active — but NOT via an accepted invite
    # for this agency (created directly, as a seed/migration might).
    from app.core.security import hash_password
    rogue_admin = User(id=test_id("u:rogue-admin"), email="rogue@konkan-recovery.example", phone="9810006099",
                       full_name="Rogue Admin", hashed_password=hash_password("Harbour-Lights-2026"),
                       role=UserRole.AGENCY_ADMIN, bank_id=TEST_BANK_ID, agency_id=out["agency_id"],
                       is_active=True)
    db.add(rogue_admin)
    db.commit()

    # Directly re-runs the activation check (the same one verify_document and
    # accept_invite call) now that a rogue active admin exists but no invite
    # was ever accepted for this agency — must still refuse.
    activated = agency_service._maybe_activate(db, out["agency_id"])
    db.commit()
    assert activated is False
    assert db.get(Agency, out["agency_id"]).status == "PENDING"


# ── invite_master_login ──────────────────────────────────────────────────────
def test_invite_master_login_invites_an_agency_admin_into_the_new_agency(w):
    db, bank_admin = w["db"], w["bank_admin"]
    out = _draft(db, bank_admin)
    result = agency_service.invite_master_login(db, bank_admin, out["agency_id"], full_name="New Admin",
                                               email="master@konkan-recovery.example", phone="9810006004")
    assert result["invite"]["role"] == "AGENCY_ADMIN"
    assert result["invite"]["agency_id"] == out["agency_id"]
    assert "token" in result


# ── HTTP ──────────────────────────────────────────────────────────────────────
def test_http_create_agency_then_read_it_back(w):
    client = TestClient(app)
    r = client.post("/api/v1/bank/agencies", json={"legal_name": "Coastal Recoveries Pvt Ltd"},
                    headers=_h(w["bank_admin"]))
    assert r.status_code == 200, r.text
    agency_id = r.json()["agency_id"]

    r2 = client.get(f"/api/v1/bank/agencies/{agency_id}", headers=_h(w["bank_admin"]))
    assert r2.status_code == 200, r2.text
    assert r2.json()["legal_name"] == "Coastal Recoveries Pvt Ltd"
    assert r2.json()["required_doc_types"] == list(agency_service.REQUIRED_DOC_TYPES)


def test_http_create_is_refused_for_a_field_agent(w):
    client = TestClient(app)
    r = client.post("/api/v1/bank/agencies", json={"legal_name": "Nope"}, headers=_h(w["field_agent"]))
    assert r.status_code == 403


def test_http_get_agency_is_scoped_to_the_bank_admins_own_bank(w):
    client = TestClient(app)
    created = client.post("/api/v1/bank/agencies", json={"legal_name": "Coastal Recoveries Pvt Ltd 2"},
                          headers=_h(w["bank_admin"]))
    agency_id = created.json()["agency_id"]
    r = client.get(f"/api/v1/bank/agencies/{agency_id}", headers=_h(w["other_bank_admin"]))
    assert r.status_code == 404


# ── HTTP: coordinator audit MED — bounded money/rate/count fields ───────────
@pytest.mark.parametrize("field,bad_value", [
    ("commission_pct", -5), ("commission_pct", 150), ("fixed_fee_per_resolution", -1),
])
def test_http_contract_term_rejects_out_of_range_values(w, field, bad_value):
    client = TestClient(app)
    created = client.post("/api/v1/bank/agencies", json={"legal_name": "Bounds Test Agency"},
                          headers=_h(w["bank_admin"]))
    agency_id = created.json()["agency_id"]
    term = {"loan_type": "PERSONAL", "dpd_bucket": "BUCKET_1", "commission_pct": 10}
    term[field] = bad_value
    r = client.patch(f"/api/v1/bank/agencies/{agency_id}/coverage-contract", json={"contract_terms": [term]},
                     headers=_h(w["bank_admin"]))
    assert r.status_code == 422, r.text


@pytest.mark.parametrize("field,bad_value", [
    ("max_placed_cases", 0), ("max_agents", -1), ("max_visits_per_month", 0),
    ("sla_first_visit_days", 0), ("recall_no_activity_days", 0),
    ("performance_bonus_pct", -1), ("performance_target_pct", 101), ("security_deposit", -100),
])
def test_http_coverage_contract_rejects_out_of_range_values(w, field, bad_value):
    client = TestClient(app)
    created = client.post("/api/v1/bank/agencies", json={"legal_name": "Bounds Test Agency 2"},
                          headers=_h(w["bank_admin"]))
    agency_id = created.json()["agency_id"]
    r = client.patch(f"/api/v1/bank/agencies/{agency_id}/coverage-contract", json={field: bad_value},
                     headers=_h(w["bank_admin"]))
    assert r.status_code == 422, r.text


# ── regions (coverage step source) ───────────────────────────────────────────
def test_list_regions_returns_only_the_principals_bank(w):
    db, bank_admin, other = w["db"], w["bank_admin"], w["other_bank_admin"]
    result = agency_service.list_regions(db, bank_admin)
    assert len(result) == 1
    assert result[0]["region_id"] == w["region"].id
    assert agency_service.list_regions(db, other) == []


def test_list_regions_is_empty_for_a_non_bank_principal(w):
    db, field_agent = w["db"], w["field_agent"]
    assert agency_service.list_regions(db, field_agent) == []


def test_http_list_regions(w):
    client = TestClient(app)
    r = client.get("/api/v1/bank/regions", headers=_h(w["bank_admin"]))
    assert r.status_code == 200, r.text
    assert len(r.json()) == 1
    assert r.json()[0]["code"] == "GGN"
