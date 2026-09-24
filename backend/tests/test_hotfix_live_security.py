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
    for k, v in {"TWILIO_ACCOUNT_SID": "ACtest00000000000000000000000000", "TWILIO_AUTH_TOKEN": AUTH_TOKEN,
                 "TWILIO_API_KEY_SID": "SKtest00000000000000000000000000", "TWILIO_API_KEY_SECRET": "secret",
                 "TWILIO_TWIML_APP_SID": "APtest00000000000000000000000000", "TWILIO_PHONE_NUMBER": "+15550100000",
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
    sig = signature if signature is not None else RequestValidator(token).compute_signature(sign_url, params)
    headers = {"X-Twilio-Signature": sig} if sig else {}
    return client.post(PATH, data=params, headers=headers)


def _from(user):
    return f"client:agent:{user.id}"


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


@pytest.mark.parametrize("from_param", ["", "client:someone", "client:agent:not-a-uuid", "+919812300001"])
def test_a_caller_that_is_not_an_agent_token_is_refused(client, world, configured, from_param):
    r = _call(client, {"From": from_param, "CaseId": world["own"].id})
    assert "<Dial" not in r.text


def test_a_manager_identity_cannot_dial(client, world, configured):
    r = _call(client, {"From": f"client:agent:{world['mgr'].id}", "CaseId": world["own"].id})
    assert "<Dial" not in r.text


def test_an_invalid_signature_is_403_and_audited(client, world, configured):
    before = len(_rows(AuditAction.ROLE_VIOLATION_ATTEMPT))
    r = _call(client, {"From": _from(world["ua"]), "CaseId": world["own"].id}, token="not-the-real-token")
    assert r.status_code == 403
    rows = _rows(AuditAction.ROLE_VIOLATION_ATTEMPT)
    assert len(rows) == before + 1 and rows[-1].failure_reason == voice.BAD_SIGNATURE


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
    r = _call(client, params)                   # signed correctly with the real token and URL
    assert r.status_code == 403
    assert _rows(AuditAction.ROLE_VIOLATION_ATTEMPT)[-1].failure_reason == voice.NOT_CONFIGURED


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
    assert claims["grants"]["identity"] == f"agent:{world['ua'].id}"
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

    class _QR:
        def create(self, body):
            return {"image_url": "https://rzp.example/qr.png", "id": "qr_test_1"}

    class _Client:
        def __init__(self, auth):
            self.qrcode = _QR()

    monkeypatch.setattr(settings, "RAZORPAY_TEST_API", "rzp_test_key")
    monkeypatch.setattr(settings, "RAZORPAY_TEST_KEY_SECRET", "rzp_test_secret")
    monkeypatch.setattr(razorpay, "Client", _Client)
    r = client.post(f"/api/v1/agent/cases/{world['own'].id}/payment-link", headers=_agent_hdr(world), json={"amount": 500})
    assert r.status_code == 200 and r.json()["qr_id"] == "qr_test_1"
