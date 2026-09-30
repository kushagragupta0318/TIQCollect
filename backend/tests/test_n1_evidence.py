"""N1 (docs/business/PRIORITIES.md): evidence an agent captures is saved, and is this case's own.

Pinned here:
  1. a payment's receipt photo (cheque or payment confirmation) gets its own upload
     subject and is stored on the Payment;
  2. a payment or a visit may only name an object this case's upload route could
     have issued: another case's key is refused before anything is written;
  3. what an agent types on an escalating visit (notes, witness) and the documents
     collected are stored on the visit, and the typed notes reach the case the
     visit escalated;
  4. the document upload route, and the category list the client mirrors.
"""
from __future__ import annotations

import re
import uuid
from datetime import date, datetime, time, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core import storage
from app.core.database import get_db
from app.core.errors import AppException, ErrorCode
from app.core.geo import IST
from app.core.security import create_access_token
from app.main import app
from app.models.agent import Agent, AgentSpecialization, AgentStatus, AgentTier
from app.models.case import Case, CaseStatus
from app.models.customer import Customer
from app.models.loan import DPDBucket, Loan, LoanStatus, LoanType
from app.models.payment import Payment, PaymentMode
from app.models.user import User, UserRole
from app.models.visit import VISIT_DOCUMENT_CATEGORIES, PersonMet, Visit, VisitOutcome
from app.schemas.agent import CollectPaymentRequest, RecordVisitRequest
from app.services import media_service as ms
from app.services import payment_service as ps
from app.services import visit_service as vs
from tests._db import create_schema, drop_schema, make_engine, make_session_factory

engine = make_engine()
Session = make_session_factory(autocommit=False, autoflush=False, bind=engine)

NOON = datetime.combine(date(2026, 9, 29), time(12, 0), tzinfo=IST).astimezone(timezone.utc)


def _uid() -> str:
    return str(uuid.uuid4())


def _agent(db, code):
    u = User(id=_uid(), email=f"{code.lower()}@t.io", phone=("9" + code).ljust(10, "0")[:10],
             full_name=f"Agent {code}", hashed_password="x", role=UserRole.FIELD_AGENT,
             is_active=True, is_verified=True)
    db.add(u)
    db.flush()
    a = Agent(id=_uid(), user_id=u.id, employee_code=code, id_card_number=code + "-ID", gender="M",
              base_latitude=28.63, base_longitude=77.21, territory="Delhi", languages_spoken=["HINDI"],
              status=AgentStatus.ON_DUTY, tier=AgentTier.TIER_1, specialization=AgentSpecialization.BOTH,
              ranking_score=80.0, max_cases_per_day=5)
    db.add(a)
    db.flush()
    return a


def _case(db, agent, ref):
    c = Customer(id=_uid(), customer_ref=ref, full_name=f"Borrower {ref}", date_of_birth=date(1990, 1, 1),
                 gender="M", pan_masked="ABCDE1234F", aadhaar_masked="123456789012",
                 phone_primary="98" + ref.ljust(8, "0"), address_line1="Delhi", city="Delhi", state="Delhi",
                 pincode="110001", latitude=28.6315, longitude=77.2167, language_preference="HINDI")
    db.add(c)
    db.flush()
    loan = Loan(id=_uid(), customer_id=c.id, loan_account_number="L" + ref, loan_type=LoanType.PERSONAL,
                branch_code="DL01", sanctioned_amount=100000.0, disbursed_amount=100000.0,
                outstanding_principal=50000.0, total_outstanding=50000.0, overdue_amount=10000.0,
                emi_amount=5000.0, interest_rate=12.0, disbursement_date=date(2022, 1, 1),
                maturity_date=date(2027, 1, 1), dpd=45, dpd_bucket=DPDBucket.BUCKET_2, status=LoanStatus.ACTIVE)
    db.add(loan)
    db.flush()
    k = Case(id=_uid(), case_number="C-" + ref, customer_id=c.id, loan_id=loan.id, agent_id=agent.id,
             status=CaseStatus.ASSIGNED, target_amount=20000.0, collected_amount=0.0,
             allocation_date=date(2026, 9, 29))
    db.add(k)
    db.flush()
    return k


@pytest.fixture(scope="module", autouse=True)
def _schema():
    """One schema for the module: every test works on its own agent and cases."""
    create_schema(engine)
    yield
    drop_schema(engine)


@pytest.fixture()
def w(monkeypatch):
    db = Session()
    tag = uuid.uuid4().hex[:6].upper()
    agent = _agent(db, "E" + tag)
    mine = _case(db, agent, tag + "1")
    other = _case(db, agent, tag + "2")
    third = _case(db, agent, tag + "3")
    db.commit()
    monkeypatch.setattr(ms.storage, "presigned_upload_url", lambda key, **k: f"https://minio.test/{key}")
    monkeypatch.setattr(ms.storage, "presigned_download_url", lambda key, **k: f"https://minio.test/get/{key}")

    # Contact hours are judged at the server's clock: pin it inside them.
    class _Noon(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOON.astimezone(tz) if tz else NOON.replace(tzinfo=None)
    for mod in (vs, ps, ms):
        monkeypatch.setattr(mod, "datetime", _Noon)
    # Best-effort side effects are not under test.
    monkeypatch.setattr(vs.VisitService, "_notify_visit_completed", lambda self, agent, case, now: None)
    monkeypatch.setattr(vs.AIReportService, "generate_visit_report", staticmethod(lambda *a, **k: None))
    yield type("W", (), {"db": db, "agent": agent, "case": mine, "other": other, "third": third})
    db.close()


def _collect(w, **over):
    req = CollectPaymentRequest(amount=1000.0, mode=PaymentMode.CASH, **over)
    return ps.PaymentService(w.db).collect_payment(w.agent, w.case.id, req)


# ═══════════════════════════════════════════════════════════════════════════
# 1. The receipt photo has a subject of its own and is stored on the payment
# ═══════════════════════════════════════════════════════════════════════════

def test_a_receipt_photo_gets_an_upload_url_keyed_to_its_case(w):
    out = ms.MediaService(w.db).get_photo_upload_url(w.agent, w.case.id, "receipt")
    assert out["photo_type"] == "RECEIPT"
    assert "RECEIPT" in out["key"] and out["key"].endswith(".jpg")
    assert out["upload_url"] == "https://minio.test/" + out["key"]
    assert storage.is_case_evidence_key(w.case.id, out["key"])


def test_an_unknown_upload_subject_is_still_refused(w):
    with pytest.raises(HTTPException) as exc:
        ms.MediaService(w.db).get_photo_upload_url(w.agent, w.case.id, "selfie-of-the-moon")
    assert exc.value.status_code == 400


def test_the_receipt_photo_is_stored_on_the_payment(w):
    key = ms.MediaService(w.db).get_photo_upload_url(w.agent, w.case.id, "receipt")["key"]
    out = _collect(w, receipt_photo_key=key)
    assert w.db.get(Payment, out["id"]).receipt_photo_key == key


def test_a_payment_without_a_receipt_photo_still_records(w):
    out = _collect(w)
    assert w.db.get(Payment, out["id"]).receipt_photo_key is None


# ═══════════════════════════════════════════════════════════════════════════
# 2. Only this case's own objects may be named
# ═══════════════════════════════════════════════════════════════════════════

def test_a_payment_naming_another_cases_photo_is_refused_and_writes_nothing(w):
    foreign = ms.MediaService(w.db).get_photo_upload_url(w.agent, w.other.id, "receipt")["key"]
    with pytest.raises(AppException) as exc:
        _collect(w, receipt_photo_key=foreign)
    assert exc.value.status_code == 422 and exc.value.code == ErrorCode.EVIDENCE_KEY_INVALID
    w.db.expire_all()
    assert w.db.query(Payment).filter(Payment.case_id == w.case.id).count() == 0
    assert w.db.get(Case, w.case.id).collected_amount == 0.0


@pytest.mark.parametrize("key", [
    "",
    "not-a-key",
    "collections/2026/09/../../etc/passwd",
    "agencies/abcd1234/GST_abcdef012345.pdf",
    "recordings/00000000-0000-0000-0000-000000000000/agent/x.webm",
])
def test_keys_no_upload_route_issued_are_not_evidence_keys(w, key):
    assert storage.is_case_evidence_key(w.case.id, key) is False


def test_recording_keys_are_the_cases_own_and_from_a_known_recorder(w):
    own = storage.recording_key(w.case.id, "borrower", "webm")
    assert storage.is_case_evidence_key(w.case.id, own)
    assert not storage.is_case_evidence_key(w.other.id, own)
    assert not storage.is_case_evidence_key(w.case.id, f"recordings/{w.case.id}/someone/x.webm")
    assert not storage.is_case_evidence_key(w.case.id, f"recordings/{w.case.id}/agent/../../x.webm")
    assert not storage.is_case_evidence_key(w.case.id, f"recordings/{w.case.id}/agent/x.webm\n")


def test_a_key_cannot_walk_out_of_its_case_prefix(w):
    case8 = str(w.case.id)[:8]
    assert storage.is_case_evidence_key(w.case.id, f"collections/2026/09/{case8}/RECEIPT_abc.jpg")
    assert not storage.is_case_evidence_key(w.case.id, f"collections/2026/09/{case8}/../{str(w.other.id)[:8]}/RECEIPT_abc.jpg")


def test_the_keys_the_upload_routes_issue_are_accepted(w):
    live = storage.photo_key(w.case.id, "RECEIPT")
    offline = storage.submission_photo_key(w.case.id, "AGENT_SELFIE", _uid())
    assert storage.is_case_evidence_key(w.case.id, live)
    assert storage.is_case_evidence_key(w.case.id, offline)
    assert not storage.is_case_evidence_key(w.other.id, live)


# ═══════════════════════════════════════════════════════════════════════════
# 3. What the agent typed and collected is saved on the visit
# ═══════════════════════════════════════════════════════════════════════════

def _visit(**kw) -> RecordVisitRequest:
    base = dict(check_in_latitude=28.6315, check_in_longitude=77.2167, customer_met=True,
                person_met=PersonMet.BORROWER, outcome=VisitOutcome.RTP)
    base.update(kw)
    return RecordVisitRequest(**base)


def _record(w, req, case=None):
    return vs.VisitService(w.db).record_visit(w.agent, (case or w.case).id, req)


def _doc(w, category="ID_PROOF", case=None, content_type="application/pdf"):
    key = ms.MediaService(w.db).get_photo_upload_url(
        w.agent, (case or w.case).id, "document", category=category, content_type=content_type)["key"]
    return {"category": category, "key": key, "sha256": "a" * 64, "content_type": content_type}


def _stored(w, out) -> Visit:
    w.db.expire_all()
    return w.db.get(Visit, out["id"])


def test_a_visit_keeps_the_typed_escalation_notes_the_witness_and_the_documents(w):
    doc = _doc(w)
    out = _record(w, _visit(escalation_notes="  Refused at the door and shut it on me  ",
                            witness_present=True, witness_name=" Neighbour, flat 4 ", documents=[doc]))
    v = _stored(w, out)
    assert v.escalation_notes == "Refused at the door and shut it on me"      # trimmed
    assert v.witness_present is True and v.witness_name == "Neighbour, flat 4"
    assert v.documents == [doc]


def test_an_rtp_or_address_issue_keeps_its_notes_on_the_visit_and_leaves_the_case_field_alone(w):
    """Only a dispute writes Case.escalation_notes (as before N1): ai_report_service inlines that
    field in a prompt, so writing it for more outcomes would widen what reaches the model."""
    rtp = _record(w, _visit(outcome=VisitOutcome.RTP, escalation_notes="Borrower refuses, says he will pay in March"))
    addr = _record(w, _visit(outcome=VisitOutcome.ADDRESS_ISSUE, customer_met=False, person_met=None,
                             escalation_notes="The flat is let to someone else; he left in July"), case=w.other)
    assert _stored(w, rtp).escalation_notes == "Borrower refuses, says he will pay in March"
    assert _stored(w, addr).escalation_notes == "The flat is let to someone else; he left in July"
    assert w.db.get(Case, w.case.id).is_escalated and w.db.get(Case, w.case.id).escalation_notes is None
    assert w.db.get(Case, w.other.id).is_escalated and w.db.get(Case, w.other.id).escalation_notes is None


def test_a_dispute_prefers_the_typed_notes_then_the_general_notes(w):
    _record(w, _visit(outcome=VisitOutcome.DISPUTE, notes="general", escalation_notes="typed in the box"))
    assert w.db.get(Case, w.case.id).escalation_notes == "typed in the box"
    _record(w, _visit(outcome=VisitOutcome.DISPUTE, notes="general only"), case=w.other)
    assert w.db.get(Case, w.other.id).escalation_notes == "general only"      # what it did before N1


def test_a_broken_ptp_keeps_its_notes_on_the_visit_and_does_not_escalate_the_case(w):
    out = _record(w, _visit(outcome=VisitOutcome.BROKEN_PTP, escalation_notes="Said the salary was late"))
    assert _stored(w, out).escalation_notes == "Said the salary was late"
    c = w.db.get(Case, w.case.id)
    assert not c.is_escalated and c.escalation_notes is None


def test_a_witness_name_without_a_witness_is_not_kept_and_blank_notes_are_none(w):
    out = _record(w, _visit(outcome=VisitOutcome.BROKEN_PTP, escalation_notes="   ",
                            witness_present=False, witness_name="Somebody"))
    v = _stored(w, out)
    assert v.witness_present is False and v.witness_name is None and v.escalation_notes is None


def test_a_visit_from_an_older_client_carries_none_of_it(w):
    v = _stored(w, _record(w, _visit(outcome=VisitOutcome.NOT_AVAILABLE, customer_met=False, person_met=None)))
    assert (v.escalation_notes, v.witness_present, v.witness_name, v.documents) == (None, None, None, None)


# ═══════════════════════════════════════════════════════════════════════════
# 2b. The same rule for a document: this case's own object, or nothing is written
# ═══════════════════════════════════════════════════════════════════════════

def _foreign_key_for(w, field: str) -> str:
    """A well-formed key for a DIFFERENT case of the same agent, as the upload route would issue it."""
    svc = ms.MediaService(w.db)
    subject = {"agent_photo_key": "agent", "borrower_photo_key": "borrower", "object_photo_key": "object",
               "signature_key": "signature", "selfie_photo_key": "agent"}.get(field)
    if subject:
        return svc.get_photo_upload_url(w.agent, w.other.id, subject)["key"]
    return storage.recording_key(w.other.id, "agent" if field == "agent_recording_key" else "borrower", "webm")


def _own_key_for(w, field: str) -> str:
    svc = ms.MediaService(w.db)
    subject = {"agent_photo_key": "agent", "borrower_photo_key": "borrower", "object_photo_key": "object",
               "signature_key": "signature", "selfie_photo_key": "agent"}.get(field)
    if subject:
        return svc.get_photo_upload_url(w.agent, w.case.id, subject)["key"]
    return storage.recording_key(w.case.id, "agent" if field == "agent_recording_key" else "borrower", "webm")


EVIDENCE_FIELDS = ["agent_photo_key", "borrower_photo_key", "object_photo_key", "signature_key",
                   "selfie_photo_key", "agent_recording_key", "borrower_recording_key"]


@pytest.mark.parametrize("field", EVIDENCE_FIELDS)
def test_a_visit_naming_another_cases_object_is_refused_for_every_evidence_field(w, field):
    with pytest.raises(AppException) as exc:
        _record(w, _visit(**{field: _foreign_key_for(w, field)}))
    assert exc.value.status_code == 422 and exc.value.code == ErrorCode.EVIDENCE_KEY_INVALID
    w.db.expire_all()
    assert w.db.query(Visit).filter(Visit.case_id == w.case.id).count() == 0
    assert not w.db.get(Case, w.case.id).is_escalated                  # the transition did not run


def test_a_visit_naming_only_its_own_objects_records_them_all(w):
    keys = {f: _own_key_for(w, f) for f in EVIDENCE_FIELDS}
    v = _stored(w, _record(w, _visit(**keys)))
    assert {f: getattr(v, f) for f in EVIDENCE_FIELDS} == keys


def test_an_out_of_hours_visit_with_a_bad_key_still_gets_the_hours_refusal_and_its_audit_row(w, monkeypatch):
    """The key check comes after the contact-hours refusal, so the compliance row is never skipped."""
    from app.models.audit_log import AuditAction, AuditLog
    monkeypatch.setattr(vs, "is_within_contact_hours", lambda *a, **k: False)
    with pytest.raises(HTTPException) as exc:
        _record(w, _visit(agent_photo_key=_foreign_key_for(w, "agent_photo_key")))
    assert exc.value.status_code == 403
    rows = (w.db.query(AuditLog).filter(AuditLog.action == AuditAction.CONTACT_HOUR_VIOLATION_ATTEMPT,
                                        AuditLog.entity_id == w.case.id).all())
    assert len(rows) == 1


def test_a_visit_naming_another_cases_document_is_refused_and_writes_nothing(w):
    foreign = _doc(w, case=w.other)
    with pytest.raises(AppException) as exc:
        _record(w, _visit(documents=[foreign]))
    assert exc.value.status_code == 422 and exc.value.code == ErrorCode.EVIDENCE_KEY_INVALID
    w.db.expire_all()
    assert w.db.query(Visit).filter(Visit.case_id == w.case.id).count() == 0
    assert not w.db.get(Case, w.case.id).is_escalated


def test_one_document_per_category_and_only_known_categories(w):
    doc = _doc(w)
    with pytest.raises(ValidationError):
        _visit(documents=[doc, dict(doc)])                                    # two of one category
    with pytest.raises(ValidationError):
        _visit(documents=[{**doc, "category": "SELFIE_OF_THE_DOG"}])
    with pytest.raises(ValidationError):
        _visit(documents=[{**doc, "sha256": "not-a-hash"}])
    with pytest.raises(ValidationError):
        _visit(documents=[_doc(w, c) for c in VISIT_DOCUMENT_CATEGORIES] + [_doc(w, "ID_PROOF", case=w.other)])


def test_a_document_carries_no_file_name(w):
    out = _record(w, _visit(documents=[{**_doc(w), "name": "aadhaar_ravi_kumar.pdf"}]))
    assert "name" not in _stored(w, out).documents[0]


# ═══════════════════════════════════════════════════════════════════════════
# 4. The document upload route, and the list the client mirrors
# ═══════════════════════════════════════════════════════════════════════════

def test_a_document_upload_url_is_keyed_by_category_and_type(w):
    svc = ms.MediaService(w.db)
    pdf = svc.get_photo_upload_url(w.agent, w.case.id, "document", category="BANK_STMT", content_type="application/pdf")
    assert pdf["photo_type"] == "DOC_BANK_STMT" and pdf["key"].endswith(".pdf")
    png = svc.get_photo_upload_url(w.agent, w.case.id, "document", category="ID_PROOF", content_type="image/png")
    assert png["key"].endswith(".png")
    default = svc.get_photo_upload_url(w.agent, w.case.id, "document", category="INCOME_PROOF")
    assert default["key"].endswith(".jpg")
    assert all(storage.is_case_evidence_key(w.case.id, o["key"]) for o in (pdf, png, default))


@pytest.mark.parametrize("kw", [
    {},                                                                     # no category
    {"category": "PASSPORT"},
    {"category": "ID_PROOF", "content_type": "application/x-msdownload"},
    {"category": "ID_PROOF", "content_type": "image/heic"},
])
def test_a_document_upload_url_refuses_an_unknown_category_or_type(w, kw):
    with pytest.raises(HTTPException) as exc:
        ms.MediaService(w.db).get_photo_upload_url(w.agent, w.case.id, "document", **kw)
    assert exc.value.status_code == 400


def test_saved_documents_come_back_with_a_download_link_and_no_key(w):
    doc = _doc(w)
    v = _stored(w, _record(w, _visit(documents=[doc])))
    [entry] = ms.MediaService.document_entries(v)
    assert entry == {"category": "ID_PROOF", "content_type": "application/pdf",
                     "view_url": "https://minio.test/get/" + doc["key"]}


def test_a_link_that_cannot_be_signed_is_none_not_invented(w, monkeypatch):
    v = _stored(w, _record(w, _visit(documents=[_doc(w)])))

    def boom(key, **k):
        raise RuntimeError("storage unreachable")
    monkeypatch.setattr(ms.storage, "presigned_download_url", boom)
    assert ms.MediaService.document_entries(v)[0]["view_url"] is None


def _client_list(name: str) -> list[str]:
    """A string-array constant from frontend/src/lib/visitEvidence.ts, which mirrors the server's lists."""
    ts = Path(__file__).resolve().parents[2] / "frontend" / "src" / "lib" / "visitEvidence.ts"
    if not ts.exists():
        pytest.skip("frontend not in this checkout")
    declared = re.search(rf"{name}\s*=\s*\[([^\]]*)\]", ts.read_text(encoding="utf-8"))
    assert declared, f"visitEvidence.ts no longer declares {name}"
    return re.findall(r'"([^"]+)"', declared.group(1))


def test_the_client_lists_the_same_document_categories():
    assert tuple(_client_list("DOCUMENT_CATEGORY_IDS")) == VISIT_DOCUMENT_CATEGORIES


def test_the_client_lists_the_same_document_file_types():
    assert sorted(_client_list("DOCUMENT_CONTENT_TYPES")) == sorted(ms.MediaService._DOCUMENT_CONTENT_TYPES)


# ═══════════════════════════════════════════════════════════════════════════
# 5. What the APIs hand back
# ═══════════════════════════════════════════════════════════════════════════

@pytest.fixture()
def http(w):
    """The real routes, on this module's database, as the agent."""
    def override():
        db = Session()
        try:
            yield db
        finally:
            db.close()
    app.dependency_overrides[get_db] = override
    user = w.db.get(User, w.agent.user_id)
    headers = {"Authorization": "Bearer " + create_access_token(user.id, user.role.value, "phone-e")}
    try:
        yield TestClient(app), headers
    finally:
        app.dependency_overrides.pop(get_db, None)


def _kept(w):
    doc = _doc(w)
    _record(w, _visit(outcome=VisitOutcome.RTP, escalation_notes="Refused at the door", witness_present=True,
                      witness_name="Neighbour, flat 4", documents=[doc]))
    return doc


def test_the_agent_case_detail_returns_what_the_visit_kept(w, http):
    client, headers = http
    doc = _kept(w)
    r = client.get(f"/api/v1/agent/cases/{w.case.id}", headers=headers)
    assert r.status_code == 200, r.text
    [v] = r.json()["visits"]
    assert (v["escalation_notes"], v["witness_present"], v["witness_name"]) == \
        ("Refused at the door", True, "Neighbour, flat 4")
    assert v["documents"] == [{"category": "ID_PROOF", "content_type": "application/pdf",
                               "view_url": "https://minio.test/get/" + doc["key"]}]
    assert "key" not in v["documents"][0]                                   # no storage key on the wire


def test_a_visit_with_none_of_it_returns_empty_fields_not_missing_ones(w, http):
    client, headers = http
    _record(w, _visit(outcome=VisitOutcome.NOT_AVAILABLE, customer_met=False, person_met=None))
    [v] = client.get(f"/api/v1/agent/cases/{w.case.id}", headers=headers).json()["visits"]
    assert (v["escalation_notes"], v["witness_present"], v["witness_name"], v["documents"]) == (None, None, None, [])


def test_the_manager_case_detail_returns_them_too(w):
    from app.api.v1.endpoints import manager as manager_ep
    tag = uuid.uuid4().hex[:8]
    mgr = User(id=_uid(), email=f"m{tag}@t.io", phone="97" + str(int(tag, 16))[:8].ljust(8, "0"),
               full_name="Team Manager", hashed_password="x", role=UserRole.AGENCY_MANAGER,
               is_active=True, is_verified=True)
    w.db.add(mgr)
    w.db.flush()
    w.agent.manager_user_id = mgr.id
    w.db.commit()
    doc = _kept(w)
    db = Session()        # a request's own session: Case.visits is lazy=noload and only a fresh load fills it
    try:
        out = manager_ep.get_case_detail(w.case.id, mgr, db)
    finally:
        db.close()
    [v] = out["visits"]
    assert (v["escalation_notes"], v["witness_present"], v["witness_name"]) == \
        ("Refused at the door", True, "Neighbour, flat 4")
    assert v["documents"] == [{"category": "ID_PROOF", "content_type": "application/pdf",
                               "view_url": "https://minio.test/get/" + doc["key"]}]
