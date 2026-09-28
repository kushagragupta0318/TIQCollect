"""Live-site hotfix, 2026-09-24: board AU-2 (browser calling) and PAY-1/PAY-2 (UPI).

AU-2. POST /agent/voice/outbound was unauthenticated, ignored Twilio's
signature and dialled whatever `PhoneTo` the client sent, on the company's
Twilio number. It now honours only a request Twilio signed over the PUBLIC URL,
resolves the number itself from a case ASSIGNED to the calling agent, and
ignores any number a client sends. GET /agent/voice/token refuses unless every
credential is real, and its tokens live five minutes.

PAY-1. A UPI payment could be recorded with no transaction reference: the page
waived the field 10 s after showing a static QR. The server now refuses it.
PAY-2. The QR's payee is read from settings, never hardcoded; unset means no QR.

Every webhook test signs its request with Twilio's own RequestValidator, the
way Twilio does, so "valid" here means valid to the SDK the route uses.
"""
from __future__ import annotations

import uuid
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from twilio.request_validator import RequestValidator

from app.core.config import settings
from app.core.database import Base, get_db
from app.core.errors import AppException, ErrorCode
from app.core.security import create_access_token
from app.main import app
from app.models.agent import Agent, AgentSpecialization, AgentStatus, AgentTier
from app.models.audit_log import AuditAction, AuditLog
from app.models.case import Case, CaseStatus
from app.models.customer import Customer
from app.models.loan import DPDBucket, Loan, LoanStatus, LoanType
from app.models.payment import Payment, PaymentMode
from app.models.user import User, UserRole
from app.schemas.agent import CollectPaymentRequest
from app.services import payment_service as ps
from app.services import voice_service as voice

engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)

AUTH_TOKEN = "test-twilio-auth-token-0123456789"
ACCOUNT_SID = "ACtest00000000000000000000000000"
APP_SID = "APtest00000000000000000000000000"
PUBLIC = "https://fieldops.example.in"
PATH = "/api/v1/agent/voice/outbound"


def _uid():
    return str(uuid.uuid4())


def _user(db, email, role, name, phone):
    u = User(id=_uid(), email=email, phone=phone, full_name=name, hashed_password="x",
             role=role, is_active=True, is_verified=True)
    db.add(u)
    return u


def _agent(db, user, code, mgr):
    a = Agent(id=_uid(), user_id=user.id, employee_code=code, id_card_number=code + "-ID",
              agency_id="AG1", manager_user_id=mgr.id, gender="M",
              base_latitude=28.63, base_longitude=77.21, territory="Delhi",
              languages_spoken=["HINDI"], status=AgentStatus.ON_DUTY, tier=AgentTier.TIER_1,
              specialization=AgentSpecialization.BOTH, ranking_score=80.0, max_cases_per_day=5)
    db.add(a)
    return a


def _case(db, agent, ref, name, phone, dnc=False):
    c = Customer(id=_uid(), customer_ref=ref, full_name=name, date_of_birth="1988-04-12", gender="F",
                 pan_masked="ABCDE1234F", aadhaar_masked="123456789012", phone_primary=phone,
                 address_line1="Sector 44", city="Gurugram", state="Haryana", pincode="122003",
                 latitude=28.45, longitude=77.07, language_preference="HINDI", do_not_contact=dnc)
    db.add(c)
    db.flush()
    loan = Loan(id=_uid(), customer_id=c.id, loan_account_number="L" + ref, loan_type=LoanType.PERSONAL,
                bank_name="HDFC", branch_code="GGN044", sanctioned_amount=100000.0, disbursed_amount=100000.0,
                outstanding_principal=50000.0, total_outstanding=50000.0, overdue_amount=10000.0,
                emi_amount=5000.0, interest_rate=12.0, disbursement_date="2022-01-01",
                maturity_date="2027-01-01", dpd=45, dpd_bucket=DPDBucket.BUCKET_2, status=LoanStatus.ACTIVE)
    db.add(loan)
    db.flush()
    k = Case(id=_uid(), case_number="C-" + ref, customer_id=c.id, loan_id=loan.id,
             agent_id=agent.id if agent else None, status=CaseStatus.ASSIGNED, target_amount=20000.0,
             collected_amount=0.0, allocation_date=date.today().isoformat())
    db.add(k)
    return k


@pytest.fixture(scope="module")
def world():
    Base.metadata.create_all(engine)
    db = TestingSession()
    mgr = _user(db, "vikram.malhotra@aravallifs.in", UserRole.AGENCY_MANAGER, "Vikram Malhotra", "9000000101")
    ua = _user(db, "neha.bansal@aravallifs.in", UserRole.FIELD_AGENT, "Neha Bansal", "9000000102")
    ub = _user(db, "arjun.sehgal@aravallifs.in", UserRole.FIELD_AGENT, "Arjun Sehgal", "9000000103")
    db.flush()
    ag = _agent(db, ua, "EMP101", mgr)
    peer = _agent(db, ub, "EMP102", mgr)          # same manager: the loose helper would let A reach these
    db.flush()
    world = {
        "db": db, "mgr": mgr, "ua": ua, "ub": ub, "ag": ag,
        "own": _case(db, ag, "OWN", "Kavita Rathore", "9812300001"),
        "peer": _case(db, peer, "PEER", "Sunil Dahiya", "9812300002"),
        "pool": _case(db, None, "POOL", "Meena Chauhan", "9812300003"),
        "dnc": _case(db, ag, "DNC", "Harish Tomar", "9812300004", dnc=True),
        "pay": _case(db, ag, "PAY", "Anjali Kohli", "9812300005"),
    }
    db.commit()
    yield world
    db.close()
    Base.metadata.drop_all(engine)


@pytest.fixture
def configured(monkeypatch):
    for k, v in {"TWILIO_ACCOUNT_SID": ACCOUNT_SID, "TWILIO_AUTH_TOKEN": AUTH_TOKEN,
                 "TWILIO_API_KEY_SID": "SKtest00000000000000000000000000", "TWILIO_API_KEY_SECRET": "secret",
                 "TWILIO_TWIML_APP_SID": APP_SID, "TWILIO_PHONE_NUMBER": "+15550100000",
                 "PUBLIC_BASE_URL": PUBLIC}.items():
        monkeypatch.setattr(settings, k, v)
    monkeypatch.setattr(voice, "is_within_contact_hours", lambda now=None: True)


@pytest.fixture
def client(world):
    def override():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()
    app.dependency_overrides[get_db] = override
    yield TestClient(app)
    app.dependency_overrides.pop(get_db, None)


def _rows(action=None):
    db = TestingSession()
    q = db.query(AuditLog)
    if action:
        q = q.filter(AuditLog.action == action)
    out = q.order_by(AuditLog.created_at).all()
    db.close()
    return out


def _call(client, params, *, sign_url=PUBLIC + PATH, token=AUTH_TOKEN, signature=None):
    """Post as Twilio does: from our account and TwiML app (unless the test
    overrides them), signed over the public URL."""
    params = {"AccountSid": ACCOUNT_SID, "ApplicationSid": APP_SID, **params}
    sig = signature if signature is not None else RequestValidator(token).compute_signature(sign_url, params)
    headers = {"X-Twilio-Signature": sig} if sig else {}
    return client.post(PATH, data=params, headers=headers)


def _from(user):
    return f"client:agent_{uuid.UUID(user.id).hex}"


def _count(action):
    return len(_rows(action))


# ── AU-2: the webhook ────────────────────────────────────────────────────────
def test_a_signed_call_dials_the_borrower_resolved_on_the_server(client, world, configured):
    r = _call(client, {"From": _from(world["ua"]), "CaseId": world["own"].id, "PhoneTo": "+15559990000"})
    assert r.status_code == 200
    assert "<Dial" in r.text and "+919812300001" in r.text
    assert "+15559990000" not in r.text            # the client's number is ignored


def test_phone_to_alone_dials_nothing(client, world, configured):
    """The old contract: a number and no case. There is nothing to dial."""
    r = _call(client, {"From": _from(world["ua"]), "PhoneTo": "+15559990000"})
    assert r.status_code == 200 and "<Dial" not in r.text


@pytest.mark.parametrize("which", ["peer", "pool"])
def test_a_case_not_assigned_to_the_caller_is_refused(client, world, configured, which):
    """Strict Case.agent_id == agent.id: a teammate's case and an unassigned
    one both pass the looser _get_accessible_case_or_404, and both must fail here."""
    before = len(_rows(AuditAction.ROLE_VIOLATION_ATTEMPT))
    r = _call(client, {"From": _from(world["ua"]), "CaseId": world[which].id})
    assert r.status_code == 200 and "<Dial" not in r.text and "cannot be placed" in r.text
    rows = _rows(AuditAction.ROLE_VIOLATION_ATTEMPT)
    assert len(rows) == before + 1
    assert rows[-1].failure_reason == voice.NOT_YOUR_CASE and rows[-1].entity_id == world[which].id


def test_a_do_not_contact_borrower_is_never_dialled(client, world, configured):
    r = _call(client, {"From": _from(world["ua"]), "CaseId": world["dnc"].id})
    assert "<Dial" not in r.text
    assert _rows(AuditAction.ROLE_VIOLATION_ATTEMPT)[-1].failure_reason == voice.DO_NOT_CONTACT


def test_outside_contact_hours_is_refused_and_recorded_as_such(client, world, configured, monkeypatch):
    monkeypatch.setattr(voice, "is_within_contact_hours", lambda now=None: False)
    r = _call(client, {"From": _from(world["ua"]), "CaseId": world["own"].id})
    assert "<Dial" not in r.text
    assert _rows(AuditAction.CONTACT_HOUR_VIOLATION_ATTEMPT)[-1].failure_reason == voice.OUTSIDE_CONTACT_HOURS


@pytest.mark.parametrize("from_param", ["", "client:someone", "client:agent_not-hex", "+919812300001",
                                        "client:agent:5b0c7a52-3d1e-4f6a-9c2b-8e4d1a7f6c30"])   # the old format
def test_a_caller_that_is_not_an_agent_token_is_refused(client, world, configured, from_param):
    r = _call(client, {"From": from_param, "CaseId": world["own"].id})
    assert "<Dial" not in r.text


def test_the_identity_is_twilio_safe_and_round_trips(world):
    """Alphanumerics and underscore only (audit of 4dcd9dc: ':' and '-' may be
    rejected by Twilio, which would fail every call as BAD_IDENTITY)."""
    import re
    ident = voice.identity_for(world["ua"].id)
    assert re.fullmatch(r"[A-Za-z0-9_]+", ident)
    assert voice.parse_identity("client:" + ident) == world["ua"].id


def test_a_manager_identity_cannot_dial(client, world, configured):
    r = _call(client, {"From": _from(world["mgr"]), "CaseId": world["own"].id})
    assert "<Dial" not in r.text


@pytest.mark.parametrize("override", [{"AccountSid": "ACsomeone0000000000000000000000"},
                                      {"ApplicationSid": "APanotherapp00000000000000000000"},
                                      {"AccountSid": ""}, {"ApplicationSid": ""}])
def test_a_request_from_another_account_or_app_cannot_dial(client, world, configured, override):
    """Signed with our auth token, but not from our TwiML app: another app in
    the account could otherwise send identities of its own."""
    before = _count(AuditAction.ROLE_VIOLATION_ATTEMPT)
    r = _call(client, {"From": _from(world["ua"]), "CaseId": world["own"].id, **override})
    assert r.status_code == 200 and "<Dial" not in r.text
    rows = _rows(AuditAction.ROLE_VIOLATION_ATTEMPT)
    assert len(rows) == before + 1 and rows[-1].failure_reason == voice.NOT_OUR_APP


def test_an_unknown_user_is_audited_without_a_dangling_user_id(client, world, configured):
    """Audit of 4dcd9dc: a user id with no users row violates the audit FK on
    Postgres, and the refusal row would be lost."""
    ghost = f"client:agent_{uuid.uuid4().hex}"
    r = _call(client, {"From": ghost, "CaseId": world["own"].id})
    assert "<Dial" not in r.text
    row = _rows(AuditAction.ROLE_VIOLATION_ATTEMPT)[-1]
    assert row.failure_reason == voice.BAD_IDENTITY and row.user_id is None


def test_an_invalid_signature_is_403_and_logged_not_audited(client, world, configured):
    """Anyone can POST to the webhook; an audit row per junk request would let
    them fill the table (audit of 4dcd9dc). Refused requests are logged."""
    before = _count(AuditAction.ROLE_VIOLATION_ATTEMPT)
    r = _call(client, {"From": _from(world["ua"]), "CaseId": world["own"].id}, token="not-the-real-token")
    assert r.status_code == 403
    assert _count(AuditAction.ROLE_VIOLATION_ATTEMPT) == before


def test_a_missing_signature_is_403(client, world, configured):
    r = _call(client, {"From": _from(world["ua"]), "CaseId": world["own"].id}, signature="")
    assert r.status_code == 403


def test_a_signature_over_the_internal_url_is_not_accepted(client, world, configured):
    """Behind Caddy the app sees http://api:8000/...; Twilio signed the public
    URL. A signature over the internal URL must not verify."""
    r = _call(client, {"From": _from(world["ua"]), "CaseId": world["own"].id},
              sign_url="http://testserver" + PATH)
    assert r.status_code == 403


def test_tampered_params_break_the_signature(client, world, configured):
    params = {"From": _from(world["ua"]), "CaseId": world["own"].id}
    sig = RequestValidator(AUTH_TOKEN).compute_signature(PUBLIC + PATH, params)
    r = client.post(PATH, data={**params, "CaseId": world["peer"].id}, headers={"X-Twilio-Signature": sig})
    assert r.status_code == 403


@pytest.mark.parametrize("unset", [{"PUBLIC_BASE_URL": ""}, {"PUBLIC_BASE_URL": "${PUBLIC_BASE_URL}"},
                                   {"TWILIO_AUTH_TOKEN": ""}, {"TWILIO_AUTH_TOKEN": "${TWILIO_AUTH_TOKEN}"}])
def test_the_webhook_fails_closed_when_not_configured(client, world, configured, monkeypatch, unset):
    params = {"From": _from(world["ua"]), "CaseId": world["own"].id}
    for k, v in unset.items():
        monkeypatch.setattr(settings, k, v)
    before = _count(AuditAction.ROLE_VIOLATION_ATTEMPT)
    r = _call(client, params)                   # signed correctly with the real token and URL
    assert r.status_code == 403
    assert _count(AuditAction.ROLE_VIOLATION_ATTEMPT) == before       # logged, not audited


# ── AU-2: the token ──────────────────────────────────────────────────────────
def _agent_hdr(world):
    return {"Authorization": f"Bearer {create_access_token(world['ua'].id, 'FIELD_AGENT', 'dev')}"}


@pytest.mark.parametrize("unset", ["TWILIO_API_KEY_SECRET", "TWILIO_TWIML_APP_SID", "PUBLIC_BASE_URL"])
def test_no_token_without_real_credentials(client, world, configured, monkeypatch, unset):
    monkeypatch.setattr(settings, unset, "${" + unset + "}")
    r = client.get("/api/v1/agent/voice/token", headers=_agent_hdr(world))
    assert r.status_code == 503


def test_the_token_is_short_lived_and_names_the_agent(client, world, configured):
    import jwt as pyjwt
    r = client.get("/api/v1/agent/voice/token", headers=_agent_hdr(world))
    assert r.status_code == 200 and r.json()["ttl_seconds"] == 300
    claims = pyjwt.decode(r.json()["token"], options={"verify_signature": False})
    assert claims["grants"]["identity"] == f"agent_{uuid.UUID(world['ua'].id).hex}"
    import time
    assert claims["exp"] - time.time() <= 300 + 5     # Twilio's JWT has no iat; exp is now + ttl


def test_a_token_failure_does_not_echo_sdk_internals(client, world, configured, monkeypatch):
    def boom(_uid):
        raise RuntimeError("secret=SKtest... internal detail")
    monkeypatch.setattr(voice, "mint_token", boom)
    r = client.get("/api/v1/agent/voice/token", headers=_agent_hdr(world))
    assert r.status_code == 503 and "internal detail" not in r.text


# ── PAY-1: a UPI payment carries its reference ───────────────────────────────
@pytest.mark.parametrize("ref", [None, "", "   "])
def test_a_upi_payment_without_a_reference_is_refused_and_writes_nothing(world, ref):
    db = TestingSession()
    agent = db.get(Agent, world["ag"].id)
    before = db.query(Payment).count()
    with pytest.raises(AppException) as e:
        ps.PaymentService(db).collect_payment(agent, world["pay"].id,
                                              CollectPaymentRequest(amount=500.0, mode=PaymentMode.UPI, upi_reference=ref))
    assert e.value.code == ErrorCode.UPI_REFERENCE_REQUIRED
    assert db.query(Payment).count() == before
    db.close()


def test_a_upi_payment_with_its_reference_is_recorded(world, monkeypatch):
    monkeypatch.setattr(ps.NotificationService, "send_twilio", staticmethod(lambda *a, **k: False))
    db = TestingSession()
    agent = db.get(Agent, world["ag"].id)
    out = ps.PaymentService(db).collect_payment(
        agent, world["pay"].id, CollectPaymentRequest(amount=500.0, mode=PaymentMode.UPI, upi_reference="412345678901"))
    assert db.query(Payment).filter(Payment.upi_reference == "412345678901").count() == 1
    assert out
    db.close()


def test_cash_needs_no_upi_reference(world, monkeypatch):
    monkeypatch.setattr(ps.NotificationService, "send_twilio", staticmethod(lambda *a, **k: False))
    db = TestingSession()
    agent = db.get(Agent, world["ag"].id)
    ps.PaymentService(db).collect_payment(agent, world["pay"].id,
                                          CollectPaymentRequest(amount=300.0, mode=PaymentMode.CASH))
    db.close()


def test_the_route_answers_the_typed_code(client, world):
    r = client.post(f"/api/v1/agent/cases/{world['pay'].id}/payment", headers=_agent_hdr(world),
                    json={"amount": 200.0, "mode": "UPI"})
    assert r.status_code == 422 and r.json()["code"] == "UPI_REFERENCE_REQUIRED"


# ── PAY-2: the payee comes from settings ─────────────────────────────────────
@pytest.mark.parametrize("vpa,name", [("", ""), ("collections@examplebank", ""), ("", "Example Desk"),
                                      ("${UPI_VPA}", "${UPI_PAYEE_NAME}")])
def test_no_payee_means_no_qr(client, world, monkeypatch, vpa, name):
    monkeypatch.setattr(settings, "UPI_VPA", vpa)
    monkeypatch.setattr(settings, "UPI_PAYEE_NAME", name)
    r = client.get("/api/v1/agent/upi-config", headers=_agent_hdr(world))
    assert r.status_code == 200 and r.json() == {"available": False, "vpa": None, "payee_name": None}


def test_a_configured_payee_is_served(client, world, monkeypatch):
    monkeypatch.setattr(settings, "UPI_VPA", "collections@examplebank")
    monkeypatch.setattr(settings, "UPI_PAYEE_NAME", "Example Recovery Desk")
    r = client.get("/api/v1/agent/upi-config", headers=_agent_hdr(world))
    assert r.json() == {"available": True, "vpa": "collections@examplebank", "payee_name": "Example Recovery Desk"}


def test_the_payee_settings_have_no_default():
    from app.core.config import Settings
    assert Settings.model_fields["UPI_VPA"].default == ""
    assert Settings.model_fields["UPI_PAYEE_NAME"].default == ""
    assert Settings.model_fields["PUBLIC_BASE_URL"].default == ""
    assert Settings.model_fields["DEMO_UPI_ACCEPT"].default == ""
    assert Settings.model_fields["BORROWER_HELPLINE"].default == ""


def test_only_the_dev_compose_admits_a_demo_upi_reference():
    """The dev web auto-confirms, so the dev api accepts; the prod image sets
    neither (the frontend test checks the web half of the Dockerfile)."""
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    compose = (root / "docker-compose.yml").read_text(encoding="utf-8")
    assert 'DEMO_UPI_ACCEPT: "true"' in compose and 'VITE_DEMO_UPI_AUTOCONFIRM: "1"' in compose
    assert "DEMO_UPI_ACCEPT" not in (root / "Dockerfile").read_text(encoding="utf-8")


# ── PL-1: a payment link only for the caller's own case ──────────────────────
@pytest.mark.parametrize("which", ["peer", "pool", "missing"])
def test_a_payment_link_for_a_case_not_assigned_to_the_caller_is_the_same_404(client, world, which):
    case_id = str(uuid.uuid4()) if which == "missing" else world[which].id
    r = client.post(f"/api/v1/agent/cases/{case_id}/payment-link", headers=_agent_hdr(world), json={"amount": 500})
    assert r.status_code == 404
    assert r.json()["detail"] == "Case not found"       # "not yours" reads exactly like "no such case"


def test_the_ownership_check_runs_before_anything_else(client, world, monkeypatch):
    """Own case, Razorpay unconfigured: the 503 proves the ownership check
    passed and came first — a foreign case never learns the gateway state."""
    monkeypatch.setattr(settings, "RAZORPAY_TEST_API", "")
    r = client.post(f"/api/v1/agent/cases/{world['own'].id}/payment-link", headers=_agent_hdr(world), json={"amount": 500})
    assert r.status_code == 503


def test_a_payment_link_for_the_callers_own_case_is_created(client, world, monkeypatch):
    import razorpay

    sent = {}

    class _QR:
        def create(self, body):
            sent.update(body)
            return {"image_url": "https://rzp.example/qr.png", "id": "qr_test_1"}

    class _Client:
        def __init__(self, auth):
            self.qrcode = _QR()

    monkeypatch.setattr(settings, "RAZORPAY_TEST_API", "rzp_test_key")
    monkeypatch.setattr(settings, "RAZORPAY_TEST_KEY_SECRET", "rzp_test_secret")
    monkeypatch.setattr(settings, "UPI_PAYEE_NAME", "Example Recovery Desk")
    monkeypatch.setattr(razorpay, "Client", _Client)
    r = client.post(f"/api/v1/agent/cases/{world['own'].id}/payment-link", headers=_agent_hdr(world), json={"amount": 500})
    assert r.status_code == 200 and r.json()["qr_id"] == "qr_test_1"
    assert sent["name"] == "Example Recovery Desk"      # the payee from settings, not "ABC Bank"


@pytest.mark.parametrize("payee", ["", "${UPI_PAYEE_NAME}"])
def test_no_gateway_qr_without_a_configured_payee(client, world, monkeypatch, payee):
    monkeypatch.setattr(settings, "RAZORPAY_TEST_API", "rzp_test_key")
    monkeypatch.setattr(settings, "RAZORPAY_TEST_KEY_SECRET", "rzp_test_secret")
    monkeypatch.setattr(settings, "UPI_PAYEE_NAME", payee)
    r = client.post(f"/api/v1/agent/cases/{world['own'].id}/payment-link", headers=_agent_hdr(world), json={"amount": 500})
    assert r.status_code == 503


@pytest.mark.parametrize("ref", ["DEMO-UPI-1727164800000", "demo-upi-1"])
def test_a_demo_upi_reference_is_refused_without_demo_upi_accept(world, monkeypatch, ref):
    """DEMO_UPI_ACCEPT, not DEMO_MODE: the live site runs with DEMO_MODE on
    (audit of 4dcd9dc), so DEMO_MODE cannot be what admits a fake UTR."""
    monkeypatch.setattr(settings, "DEMO_MODE", True)
    monkeypatch.setattr(settings, "DEMO_UPI_ACCEPT", False)
    db = TestingSession()
    agent = db.get(Agent, world["ag"].id)
    before = db.query(Payment).count()
    with pytest.raises(AppException) as e:
        ps.PaymentService(db).collect_payment(agent, world["pay"].id,
                                              CollectPaymentRequest(amount=100.0, mode=PaymentMode.UPI, upi_reference=ref))
    assert e.value.code == ErrorCode.UPI_REFERENCE_REQUIRED
    assert db.query(Payment).count() == before
    db.close()


def test_a_demo_upi_reference_is_accepted_only_with_demo_upi_accept(world, monkeypatch):
    monkeypatch.setattr(settings, "DEMO_UPI_ACCEPT", True)
    monkeypatch.setattr(ps.NotificationService, "send_twilio", staticmethod(lambda *a, **k: False))
    db = TestingSession()
    agent = db.get(Agent, world["ag"].id)
    ps.PaymentService(db).collect_payment(agent, world["pay"].id,
                                          CollectPaymentRequest(amount=100.0, mode=PaymentMode.UPI,
                                                                upi_reference="DEMO-UPI-1727164800000"))
    db.close()


def test_a_body_the_form_parser_cannot_read_is_403_not_500(client, world, configured):
    """Review of 4dcd9dc: request.form() on a body the parser rejects raised
    before the signature check. (The review expected a 500; measured, Starlette
    turns it into a 400.) Now it is refused like a bad signature: 403, logged
    and not audited — the request cannot be attributed to anybody."""
    broken_multipart = b"--broken" + bytes([13, 10]) + b"Content-Disposition: form-data" + bytes([13, 10, 13, 10]) + b"no-end"
    r = client.post(PATH, content=broken_multipart,
                    headers={"Content-Type": "multipart/form-data; boundary=broken", "X-Twilio-Signature": "x"})
    assert r.status_code == 403


# ── PAY-1 (round 3): every mode carries the evidence the server requires ──────
@pytest.mark.parametrize("ref", ["12345", "41234567890", "4123456789012", "41234567890a", "UTR412345678901"])
def test_a_upi_reference_that_is_not_a_12_digit_utr_is_refused(world, ref):
    db = TestingSession()
    agent = db.get(Agent, world["ag"].id)
    before = db.query(Payment).count()
    with pytest.raises(AppException) as e:
        ps.PaymentService(db).collect_payment(agent, world["pay"].id,
                                              CollectPaymentRequest(amount=100.0, mode=PaymentMode.UPI, upi_reference=ref))
    assert e.value.code == ErrorCode.UPI_REFERENCE_REQUIRED
    assert db.query(Payment).count() == before
    db.close()


def test_a_utr_typed_with_spaces_is_accepted(world, monkeypatch):
    monkeypatch.setattr(ps.NotificationService, "send_twilio", staticmethod(lambda *a, **k: False))
    db = TestingSession()
    agent = db.get(Agent, world["ag"].id)
    ps.PaymentService(db).collect_payment(agent, world["pay"].id,
                                          CollectPaymentRequest(amount=100.0, mode=PaymentMode.UPI,
                                                                upi_reference="4123 4567 8902"))
    db.close()


@pytest.mark.parametrize("mode,fields", [
    (PaymentMode.NEFT, {}), (PaymentMode.RTGS, {"bank_reference": "  "}), (PaymentMode.DD, {}),
    (PaymentMode.CHEQUE, {"cheque_number": ""}),
])
def test_a_bank_or_cheque_payment_without_its_reference_is_refused(world, mode, fields):
    """201 of the demo book's 1,036 payments are NEFT rows with no reference:
    the page asked for one, the server never did."""
    db = TestingSession()
    agent = db.get(Agent, world["ag"].id)
    before = db.query(Payment).count()
    with pytest.raises(AppException) as e:
        ps.PaymentService(db).collect_payment(agent, world["pay"].id,
                                              CollectPaymentRequest(amount=100.0, mode=mode, **fields))
    assert e.value.code == ErrorCode.PAYMENT_REFERENCE_REQUIRED
    assert db.query(Payment).count() == before
    db.close()


@pytest.mark.parametrize("mode,fields", [
    (PaymentMode.NEFT, {"bank_reference": "UTIBN52026092400123"}),
    (PaymentMode.RTGS, {"bank_reference": "HDFCR52026092400456"}),
    (PaymentMode.CHEQUE, {"cheque_number": "004512"}),
])
def test_a_bank_or_cheque_payment_with_its_reference_passes_the_rule(mode, fields):
    assert ps.payment_reference_problem(mode, upi_reference=None,
                                        bank_reference=fields.get("bank_reference"),
                                        cheque_number=fields.get("cheque_number")) is None


# ── DEMO-LOGIN (round 3): no four-eyes gate while one password opens two roles ─
@pytest.fixture
def master_login_on(monkeypatch):
    monkeypatch.setattr(settings, "DEMO_MASTER_PASSWORD", "a-long-test-master-password-0123")


def _mgr_hdr(world):
    return {"Authorization": f"Bearer {create_access_token(world['mgr'].id, 'AGENCY_MANAGER', 'dev')}"}


@pytest.mark.parametrize("step", ["approve", "promote"])
def test_model_approval_and_promotion_are_refused_while_the_master_login_is_on(client, world, master_login_on, step):
    cid = str(uuid.uuid4())
    before = _count(AuditAction.ROLE_VIOLATION_ATTEMPT)
    r = client.post(f"/api/v1/manager/ml/candidates/{cid}/{step}", headers=_mgr_hdr(world))
    assert r.status_code == 409
    rows = _rows(AuditAction.ROLE_VIOLATION_ATTEMPT)
    assert len(rows) == before + 1
    assert rows[-1].entity_type == "ModelCandidate" and rows[-1].entity_id == cid
    assert rows[-1].user_id == world["mgr"].id


@pytest.mark.parametrize("value", ["", "${DEMO_MASTER_PASSWORD}"])
@pytest.mark.parametrize("step", ["approve", "promote"])
def test_without_the_master_login_the_ml_gate_is_not_the_refusal(client, world, monkeypatch, value, step):
    """No master login: the request reaches the candidate lookup (404 for an
    id that does not exist), not the demo refusal."""
    monkeypatch.setattr(settings, "DEMO_MASTER_PASSWORD", value)
    before = _count(AuditAction.ROLE_VIOLATION_ATTEMPT)
    r = client.post(f"/api/v1/manager/ml/candidates/{uuid.uuid4()}/{step}", headers=_mgr_hdr(world))
    assert r.status_code == 404
    assert _count(AuditAction.ROLE_VIOLATION_ATTEMPT) == before


def _request():
    from starlette.requests import Request
    return Request({"type": "http", "method": "POST", "path": "/api/v1/auth/quick-login", "headers": [],
                    "client": ("127.0.0.1", 5000), "query_string": b""})


def test_a_retired_password_retires_the_accounts_quick_login_links(world):
    """A quick-login link skips the password; retiring the password must
    retire the links too, or an outstanding one outlives the retirement."""
    from fastapi import HTTPException
    from app.core.security import create_quick_login_token, disabled_password_hash
    from app.services import auth_service
    db = TestingSession()
    u = _user(db, "retired.manager@aravallifs.in", UserRole.AGENCY_MANAGER, "Rohit Khanna", "9000000199")
    u.hashed_password = disabled_password_hash()
    db.commit()
    with pytest.raises(HTTPException) as e:
        auth_service.quick_login(db, create_quick_login_token(u.id, "AG1"), _request())
    assert e.value.status_code == 401
    row = db.query(AuditLog).filter(AuditLog.action == AuditAction.LOGIN_FAILED,
                                    AuditLog.user_id == u.id).one()
    assert "retired" in row.failure_reason
    assert db.get(User, u.id).is_active is True     # retired, not deactivated
    db.close()


def test_a_live_accounts_quick_login_still_works(world):
    from app.core.security import create_quick_login_token, hash_password
    from app.services import auth_service
    db = TestingSession()
    u = _user(db, "live.manager@aravallifs.in", UserRole.AGENCY_MANAGER, "Pooja Saini", "9000000198")
    u.hashed_password = hash_password("a-real-password-0123")
    db.commit()
    out = auth_service.quick_login(db, create_quick_login_token(u.id, "AG1"), _request())
    assert out["user_id"] == u.id
    db.close()


# ── BL-5: the borrower's post-visit message is neutral ────────────────────────
from app.models.visit import VisitOutcome  # noqa: E402
from app.schemas.agent import RecordVisitRequest  # noqa: E402
from app.services import visit_service as vs  # noqa: E402


@pytest.fixture
def sent(monkeypatch):
    out = []
    monkeypatch.setattr(vs.NotificationService, "send_twilio",
                        staticmethod(lambda phone, sms, wa: out.append((phone, sms, wa)) or True))
    monkeypatch.setattr(vs, "is_within_contact_hours", lambda *a, **k: True)
    monkeypatch.setattr(vs.AIReportService, "generate_visit_report", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(settings, "BORROWER_HELPLINE", "")
    return out


def _new_case(world, *, hostile=False, tags=None, bank="HDFC"):
    db = TestingSession()
    agent = db.get(Agent, world["ag"].id)
    ref = "V" + uuid.uuid4().hex[:8].upper()
    case = _case(db, agent, ref, "Ritu Bhardwaj", "9812300077")
    db.flush()
    cust = db.get(Customer, case.customer_id)
    cust.is_hostile = hostile
    cust.tags = tags
    db.get(Loan, case.loan_id).bank_name = bank
    db.commit()
    loan = db.get(Loan, case.loan_id)
    facts = {"case_id": case.id, "loan_number": loan.loan_account_number, "agent": world["ua"].full_name}
    db.close()
    return facts


def _record(world, case_id, outcome, **extra):
    db = TestingSession()
    agent = db.get(Agent, world["ag"].id)
    vs.VisitService(db).record_visit(agent, case_id, RecordVisitRequest(
        check_in_latitude=28.45, check_in_longitude=77.07, customer_met=True, outcome=outcome, **extra))
    db.close()


def _visit(world, outcome, *, hostile=False, **extra):
    facts = _new_case(world, hostile=hostile)
    _record(world, facts["case_id"], outcome, **extra)
    return facts


def test_a_refusal_visit_sends_a_neutral_message(world, sent):
    facts = _visit(world, VisitOutcome.RTP)
    assert len(sent) == 1
    phone, sms, wa = sent[0]
    assert phone == "+919812300077"
    assert sms == wa == "Our representative visited you today regarding your account with HDFC."
    for body in (sms, wa):
        assert facts["loan_number"] not in body and facts["loan_number"][-4:] not in body
        assert facts["agent"] not in body
        assert not any(ch.isdigit() for ch in body)      # no amount, no account digits, no date
        assert "Rs" not in body and "payment" not in body.lower() and "outstanding" not in body.lower()


def test_the_helpline_is_added_only_when_configured(world, sent, monkeypatch):
    monkeypatch.setattr(settings, "BORROWER_HELPLINE", "1800 200 3344")
    _visit(world, VisitOutcome.REVISIT)
    assert sent[-1][1].endswith(" For queries call 1800 200 3344.")
    monkeypatch.setattr(settings, "BORROWER_HELPLINE", "${BORROWER_HELPLINE}")
    _visit(world, VisitOutcome.REVISIT)
    assert "For queries" not in sent[-1][1]


@pytest.mark.parametrize("outcome", [VisitOutcome.DECEASED, VisitOutcome.DISPUTE])
def test_no_message_after_a_death_or_a_dispute(world, sent, outcome):
    _visit(world, outcome)
    assert sent == []


def test_no_message_to_a_hostile_borrower(world, sent):
    _visit(world, VisitOutcome.RTP, hostile=True)
    assert sent == []


def test_the_notice_rule_reads_the_borrower_at_send_time():
    """do_not_contact set during the visit (DECEASED sets it) is read when the
    notice is decided; no phone or no customer sends nothing."""
    c = Customer(phone_primary="9812300078", is_hostile=False, do_not_contact=True)
    assert vs.VisitService._should_send_visit_notice(VisitOutcome.RTP, c) is False
    c.do_not_contact = False
    assert vs.VisitService._should_send_visit_notice(VisitOutcome.RTP, c) is True
    assert vs.VisitService._should_send_visit_notice(VisitOutcome.PAID_FULL, c) is False
    c.phone_primary = ""
    assert vs.VisitService._should_send_visit_notice(VisitOutcome.RTP, c) is False
    assert vs.VisitService._should_send_visit_notice(VisitOutcome.RTP, None) is False


# ── BL-5, coordinator re-audit of bb4371a ─────────────────────────────────────
def test_a_later_visit_to_a_disputed_case_sends_nothing(world, sent):
    """The first visit records the dispute (and sends nothing); a REVISIT to
    the same case, still escalated as a dispute, must not send either."""
    c = _new_case(world)
    _record(world, c["case_id"], VisitOutcome.DISPUTE)
    _record(world, c["case_id"], VisitOutcome.REVISIT)
    assert sent == []


def test_a_later_visit_to_a_borrower_tagged_deceased_sends_nothing(world, sent):
    """do_not_contact is set with the tag, and a DNC borrower cannot be
    visited at all; the tag alone must still stop the notice, e.g. after the
    DNC flag is cleared by hand."""
    c = _new_case(world, tags=["DECEASED"])
    _record(world, c["case_id"], VisitOutcome.REVISIT)
    assert sent == []


def test_a_visit_to_an_rtp_escalated_case_still_notifies(world, sent):
    """Only a DISPUTE escalation silences the notice; a refusal does not."""
    c = _new_case(world)
    _record(world, c["case_id"], VisitOutcome.RTP)
    _record(world, c["case_id"], VisitOutcome.REVISIT)
    assert len(sent) == 2


@pytest.mark.parametrize("bank", ["", "   ", "${BANK_NAME}"])        # bank_name is NOT NULL
def test_no_lender_name_means_no_message(world, sent, bank):
    c = _new_case(world, bank=bank)
    _record(world, c["case_id"], VisitOutcome.RTP)
    assert sent == []


# ── LOW 3 / LOW 5 ────────────────────────────────────────────────────────────
@pytest.mark.parametrize("value,on", [("true", True), ("TRUE", True), (" True ", True), ("false", False),
                                      ("", False), ("1", False), ("yes", False), ("${DEMO_UPI_ACCEPT}", False),
                                      (True, True), (False, False), (None, False)])
def test_a_demo_switch_is_on_only_for_an_explicit_true(value, on):
    from app.core.security import explicit_true
    assert explicit_true(value) is on


def test_an_unresolved_demo_switch_does_not_break_settings(monkeypatch):
    """Read as str: a literal ${VAR} left in an env file used to fail the
    settings at boot, taking the API down with it."""
    from app.core.config import Settings
    monkeypatch.setenv("DEMO_MASTER_DISABLE_OTHERS", "${DEMO_MASTER_DISABLE_OTHERS}")
    monkeypatch.setenv("DEMO_UPI_ACCEPT", "${DEMO_UPI_ACCEPT}")
    s = Settings()
    from app.core.security import explicit_true
    assert not explicit_true(s.DEMO_MASTER_DISABLE_OTHERS) and not explicit_true(s.DEMO_UPI_ACCEPT)


def test_an_unresolved_demo_upi_accept_refuses_the_demo_reference(world, monkeypatch):
    monkeypatch.setattr(settings, "DEMO_UPI_ACCEPT", "${DEMO_UPI_ACCEPT}")
    assert ps.payment_reference_problem(PaymentMode.UPI, upi_reference="DEMO-UPI-1", bank_reference=None,
                                        cheque_number=None) is not None


def test_the_stored_utr_is_the_one_the_rule_checked(world, monkeypatch):
    monkeypatch.setattr(ps.NotificationService, "send_twilio", staticmethod(lambda *a, **k: False))
    db = TestingSession()
    agent = db.get(Agent, world["ag"].id)
    ps.PaymentService(db).collect_payment(agent, world["pay"].id,
                                          CollectPaymentRequest(amount=137.0, mode=PaymentMode.UPI,   # its own amount:
                                                                upi_reference=" 5123 4567 8903 "))    # not a recent duplicate
    assert db.query(Payment).filter(Payment.upi_reference == "512345678903").count() == 1
    assert db.query(Payment).filter(Payment.upi_reference.like("% %")).count() == 0
    db.close()
