"""Browser calling cannot be used as a free dialler (coordinator audit gate 2).

2026-09-24. Before this, POST /agent/voice/outbound was unauthenticated, did
not check X-Twilio-Signature and dialled whatever `PhoneTo` the client sent,
on the company's number. These tests sign requests exactly as Twilio does
(RequestValidator.compute_signature over the PUBLIC URL) and read the TwiML
that comes back, so "refused" means Twilio would not have dialled.
"""
from __future__ import annotations

import base64
import json
from datetime import date

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.core.config import settings
from app.core.database import get_db
from app.core.security import decode_token, hash_password
from app.main import app
from app.models import Agent, Case, Customer, Loan, LoanType, User, UserRole
from app.models.audit_log import AuditAction, AuditLog
from app.models.tenancy import Agency, Bank, Branch
from app.services import auth_service, voice_service
from tests._db import TEST_AGENCY_ID, TEST_BANK_ID, create_schema, make_engine, make_session_factory, test_id

twilio_validator = pytest.importorskip("twilio.request_validator")

PUBLIC = "https://fieldops.narmadabank.test"
PATH = "/api/v1/agent/voice/outbound"
AUTH_TOKEN = "twilio-auth-token-for-tests"
BORROWER = "9425012345"          # the case's borrower
ELSEWHERE = "+19005550100"       # what an attacker would put in PhoneTo
PASSWORD = "Harsh@2026"
BANK, AGENCY = test_id("bank:narmada"), test_id("agency:satpura")


def _req() -> Request:
    return Request({"type": "http", "method": "POST", "path": "/", "headers": [(b"user-agent", b"pytest")],
                    "client": ("10.0.0.9", 50000), "query_string": b""})


@pytest.fixture()
def world(monkeypatch):
    for k, v in {"TWILIO_ACCOUNT_SID": "AC" + "0" * 32, "TWILIO_AUTH_TOKEN": AUTH_TOKEN,
                 "TWILIO_API_KEY_SID": "SK" + "1" * 32, "TWILIO_API_KEY_SECRET": "api-secret-for-tests",
                 "TWILIO_TWIML_APP_SID": "AP" + "2" * 32, "TWILIO_PHONE_NUMBER": "+15550009999",
                 "PUBLIC_BASE_URL": PUBLIC, "DEMO_MODE": False, "DEMO_DEVICE_REBIND": False}.items():
        monkeypatch.setattr(settings, k, v)
    monkeypatch.setattr(voice_service, "is_within_contact_hours", lambda now=None: True)

    engine = make_engine()
    create_schema(engine)
    Session = make_session_factory(engine, info={})
    db = Session()
    db.add(Bank(id=BANK, code="NPB", legal_name="Narmada Peoples Bank Ltd.", display_name="Narmada Peoples Bank",
                brand={}, is_demo=False))
    db.flush()
    db.add_all([Agency(id=AGENCY, bank_id=BANK, code="NPB-AG-01", legal_name="Satpura Recoveries Pvt. Ltd.",
                       trade_name="Satpura Recoveries", status="ACTIVE", contacts=[], is_demo=False),
                Branch(bank_id=BANK, branch_code="BPL01", name="Bhopal MP Nagar")])
    db.flush()
    tenant = {"bank_id": BANK, "agency_id": AGENCY}
    mgr = User(id=test_id("u:vmgr"), email="anita.rao@satpura.test", phone="9425000001", full_name="Anita Rao",
               hashed_password=hash_password(PASSWORD), role=UserRole.AGENCY_MANAGER, **tenant)
    au = User(id=test_id("u:vagent"), email="harsh.patel@satpura.test", phone="9425000002",
              full_name="Harsh Patel", hashed_password=hash_password(PASSWORD), role=UserRole.FIELD_AGENT, **tenant)
    au2 = User(id=test_id("u:vagent2"), email="neha.soni@satpura.test", phone="9425000003",
               full_name="Neha Soni", hashed_password=hash_password(PASSWORD), role=UserRole.FIELD_AGENT, **tenant)
    db.add_all([mgr, au, au2])
    db.flush()
    ag = Agent(id=test_id("vagent"), user_id=au.id, employee_code="SRP0011", id_card_number="SRP-ID-0011",
               base_latitude=23.23, base_longitude=77.43, territory="MP Nagar, Bhopal", manager_user_id=mgr.id, **tenant)
    ag2 = Agent(id=test_id("vagent2"), user_id=au2.id, employee_code="SRP0012", id_card_number="SRP-ID-0012",
                base_latitude=23.23, base_longitude=77.43, territory="Arera Colony, Bhopal", manager_user_id=mgr.id,
                **tenant)
    db.add_all([ag, ag2])
    db.flush()
    cust = Customer(id=test_id("vcust"), bank_id=BANK, customer_ref="NPB-C-0001", full_name="Ramesh Chouhan",
                    date_of_birth=date(1980, 5, 5), gender="MALE", pan_masked="XXXXX2222X",
                    aadhaar_masked="XXXXXXXX2222", phone_primary=BORROWER, address_line1="44, Shahpura",
                    city="Bhopal", state="Madhya Pradesh", pincode="462039", latitude=23.20, longitude=77.42)
    loan = Loan(id=test_id("vloan"), bank_id=BANK, loan_account_number="NPB0000001", customer_id=cust.id,
                loan_type=LoanType.PERSONAL, branch_code="BPL01", sanctioned_amount=1.0, disbursed_amount=1.0,
                outstanding_principal=1.0, total_outstanding=1.0, emi_amount=1.0,
                disbursement_date=date(2024, 1, 1), maturity_date=date(2027, 1, 1), interest_rate=12.0)
    db.add_all([cust, loan])
    db.flush()
    mine = Case(id=test_id("vcase:mine"), case_number="NPB-CASE-1", customer_id=cust.id, loan_id=loan.id,
                agent_id=ag.id, target_amount=1.0, **tenant)
    theirs = Case(id=test_id("vcase:theirs"), case_number="NPB-CASE-2", customer_id=cust.id, loan_id=loan.id,
                  agent_id=ag2.id, target_amount=1.0, **tenant)
    db.add_all([mine, theirs])
    db.commit()
    tokens = auth_service.login(db, au.email, PASSWORD, "moto-g84-77aa", _req())

    def override():
        s = Session()
        try:
            yield s
        finally:
            s.close()
    app.dependency_overrides[get_db] = override
    try:
        yield {"db": db, "tokens": tokens, "sid": decode_token(tokens["access_token"])["sid"], "user": au,
               "mine": mine.id, "theirs": theirs.id}
    finally:
        app.dependency_overrides.pop(get_db, None)
        db.close()


def _call(world, *, case_id, sign=True, url=PUBLIC + PATH, extra=None):
    params = {"From": f"client:{voice_service.identity_for(world['user'].id, world['sid'])}",
              "CaseId": case_id, "PhoneTo": ELSEWHERE, "CallSid": "CA" + "3" * 32}
    params.update(extra or {})
    headers = {}
    if sign:
        headers["X-Twilio-Signature"] = twilio_validator.RequestValidator(AUTH_TOKEN).compute_signature(url, params)
    return TestClient(app).post(PATH, data=params, headers=headers)


def _refusals(world):
    world["db"].expire_all()
    return [r.failure_reason for r in world["db"].query(AuditLog)
            .filter(AuditLog.action == AuditAction.VOICE_CALL_REFUSED).all()]


def test_an_unsigned_request_is_refused_and_audited(world):
    r = _call(world, case_id=world["mine"], sign=False)
    assert r.status_code == 403
    assert _refusals(world) == [voice_service.BAD_SIGNATURE]


def test_a_signature_over_the_wrong_url_is_refused(world):
    """Behind a proxy the app sees http://api:8000/...; a signature must be
    checked against the PUBLIC URL Twilio called, never request.url."""
    r = _call(world, case_id=world["mine"], url="http://testserver" + PATH)
    assert r.status_code == 403


def test_without_a_public_base_url_nothing_is_honoured(world, monkeypatch):
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "")
    r = _call(world, case_id=world["mine"])
    assert r.status_code == 403


def test_a_signed_call_dials_the_cases_borrower_and_ignores_phoneto(world):
    r = _call(world, case_id=world["mine"])
    assert r.status_code == 200, r.text
    assert "<Number>+91" + BORROWER + "</Number>" in r.text
    assert ELSEWHERE not in r.text
    assert 'callerId="+15550009999"' in r.text
    placed = world["db"].query(AuditLog).filter(AuditLog.action == AuditAction.VOICE_CALL_PLACED).all()
    assert len(placed) == 1 and placed[0].entity_id == world["mine"]
    assert BORROWER not in json.dumps(placed[0].details)          # last 4 only


def _refused_twiml(r):
    return r.status_code == 200 and "<Dial" not in r.text and "cannot be placed" in r.text


def test_another_agents_case_is_refused(world):
    r = _call(world, case_id=world["theirs"])
    assert _refused_twiml(r)
    assert _refusals(world) == [voice_service.NOT_YOUR_CASE]


def test_a_malformed_or_missing_case_is_refused(world):
    assert _refused_twiml(_call(world, case_id="not-a-uuid"))
    assert _refused_twiml(_call(world, case_id=""))


def test_logging_out_ends_calling(world):
    auth_service.logout(world["db"], world["user"], _req(), sid=world["sid"])
    assert _refused_twiml(_call(world, case_id=world["mine"]))
    assert _refusals(world) == [voice_service.SESSION_ENDED]


def test_outside_contact_hours_is_refused(world, monkeypatch):
    monkeypatch.setattr(voice_service, "is_within_contact_hours", lambda now=None: False)
    assert _refused_twiml(_call(world, case_id=world["mine"]))
    assert _refusals(world) == [voice_service.OUTSIDE_CONTACT_HOURS]


def test_a_demo_tenant_is_never_dialled(world):
    world["db"].get(Bank, BANK).is_demo = True
    world["db"].commit()
    assert _refused_twiml(_call(world, case_id=world["mine"]))
    assert _refusals(world) == [voice_service.DEMO_SUPPRESSED]


def test_a_do_not_contact_borrower_is_refused(world):
    world["db"].get(Customer, test_id("vcust")).do_not_contact = True
    world["db"].commit()
    assert _refused_twiml(_call(world, case_id=world["mine"]))
    assert _refusals(world) == [voice_service.DO_NOT_CONTACT]


def test_the_token_is_short_lived_and_bound_to_the_session(world):
    r = TestClient(app).get("/api/v1/agent/voice/token",
                            headers={"Authorization": f"Bearer {world['tokens']['access_token']}"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ttl_seconds"] == 300
    seg = body["token"].split(".")[1]
    claims = json.loads(base64.urlsafe_b64decode(seg + "=" * (-len(seg) % 4)))
    assert claims["grants"]["identity"] == voice_service.identity_for(world["user"].id, world["sid"])
    assert claims["exp"] - claims["iat"] <= 300 + 5


@pytest.mark.parametrize("unset", ["PUBLIC_BASE_URL", "TWILIO_API_KEY_SECRET"])
def test_the_token_is_refused_while_voice_is_not_fully_configured(world, monkeypatch, unset):
    monkeypatch.setattr(settings, unset, "${" + unset + "}")        # an uninterpolated value is not a value
    r = TestClient(app).get("/api/v1/agent/voice/token",
                            headers={"Authorization": f"Bearer {world['tokens']['access_token']}"})
    assert r.status_code == 503
