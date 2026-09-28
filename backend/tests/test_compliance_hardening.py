"""Four audit actions wired, one retention floor named, one receipt result surfaced.

2026-09-11. AuditLog declared 25 action types; by this morning 13 were
written somewhere in the code and the live database had ever seen 6. This
change wires four more at write points that already existed — each one
additive, after the business write has committed, on its own commit, unable
to change what happened. Every test here also checks the thing the audit row
describes still happened exactly as before.
"""
from __future__ import annotations

import sys
import types
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.audit import write_audit
from app.core.config import settings
from app.core.database import Base, get_db
from app.core.geo import IST
from app.core.security import create_access_token
from app.main import app
from app.models.agent import Agent, AgentSpecialization, AgentStatus, AgentTier
from app.models.agent_location import AgentLocation
from app.models.audit_log import AuditAction, AuditLog
from app.models.case import Case, CaseStatus
from app.models.customer import Customer
from app.models.loan import DPDBucket, Loan, LoanStatus, LoanType
from app.models.payment import Payment, PaymentMode
from app.models.ptp import PTP
from app.models.user import User, UserRole
from app.models.visit import Visit, VisitOutcome
from app.schemas.agent import CollectPaymentRequest, RecordVisitRequest, SetPTPRequest
from app.services import notification_service as ns
from app.services import payment_service as ps
from app.services import visit_service as vs

engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                       poolclass=StaticPool)
TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
TODAY = date.today()


def _uid():
    return str(uuid.uuid4())


def _user(db, email, role, name, phone):
    u = User(id=_uid(), email=email, phone=phone, full_name=name, hashed_password="x",
             role=role, is_active=True, is_verified=True)
    db.add(u); return u


def _agent(db, user, code, mgr):
    a = Agent(id=_uid(), user_id=user.id, employee_code=code, id_card_number=code + "-ID",
              agency_id="AG1", manager_user_id=mgr.id, gender="M",
              base_latitude=28.63, base_longitude=77.21, territory="Delhi",
              languages_spoken=["HINDI"], status=AgentStatus.ON_DUTY, tier=AgentTier.TIER_1,
              specialization=AgentSpecialization.BOTH, ranking_score=80.0, max_cases_per_day=5)
    db.add(a); return a


def _case(db, agent, ref, phone="9812345678"):
    c = Customer(id=_uid(), customer_ref=ref, full_name=f"Borrower {ref}",
                 date_of_birth="1990-01-01", gender="M", pan_masked="ABCDE1234F",
                 aadhaar_masked="123456789012", phone_primary=phone,
                 address_line1="Delhi", city="Delhi", state="Delhi", pincode="110001",
                 latitude=28.6315, longitude=77.2167, language_preference="HINDI")
    db.add(c); db.flush()
    loan = Loan(id=_uid(), customer_id=c.id, loan_account_number="L" + ref,
                loan_type=LoanType.PERSONAL, bank_name="HDFC", branch_code="DL01",
                sanctioned_amount=100000.0, disbursed_amount=100000.0,
                outstanding_principal=50000.0, total_outstanding=50000.0,
                overdue_amount=10000.0, emi_amount=5000.0, interest_rate=12.0,
                disbursement_date="2022-01-01", maturity_date="2027-01-01",
                dpd=45, dpd_bucket=DPDBucket.BUCKET_2, status=LoanStatus.ACTIVE)
    db.add(loan); db.flush()
    k = Case(id=_uid(), case_number="C-" + ref, customer_id=c.id, loan_id=loan.id,
             agent_id=agent.id, status=CaseStatus.ASSIGNED, target_amount=20000.0,
             collected_amount=0.0, allocation_date=TODAY.isoformat())
    db.add(k); return k


@pytest.fixture(scope="module")
def world():
    Base.metadata.create_all(engine)
    db = TestingSession()
    mgr = _user(db, "m@t.io", UserRole.AGENCY_MANAGER, "Manager", "9000000001")
    ua = _user(db, "a@t.io", UserRole.FIELD_AGENT, "Agent A", "9000000002")
    db.flush()
    ag = _agent(db, ua, "EMP001", mgr)
    db.flush()
    cases = {k: _case(db, ag, k) for k in ("VISIT", "PTP", "PAY", "PAY2")}
    cases["NOPHONE"] = _case(db, ag, "NOPHONE", phone="")
    db.commit()
    yield {"db": db, "mgr": mgr, "ua": ua, "ag": ag, **cases}
    db.close()
    Base.metadata.drop_all(engine)


@pytest.fixture(scope="module")
def client(world):
    def override():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()
    app.dependency_overrides[get_db] = override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


def _rows(db, action, entity_id=None):
    q = db.query(AuditLog).filter(AuditLog.action == action)
    if entity_id:
        q = q.filter(AuditLog.entity_id == entity_id)
    return q.all()


def _freeze_in_hours(monkeypatch, module):
    fixed = datetime.combine(TODAY, datetime.min.time()).replace(hour=11, tzinfo=IST).astimezone(timezone.utc)

    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed.astimezone(tz) if tz else fixed.replace(tzinfo=None)
    monkeypatch.setattr(module, "datetime", _Frozen)


def _quiet_side_effects(monkeypatch):
    monkeypatch.setattr(vs.AIReportService, "generate_visit_report", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(vs.VisitService, "_notify_visit_completed", lambda self, *a, **k: None)


# ═══════════════════════════════════════════════════════════════════════════
# ROLE_VIOLATION_ATTEMPT
# ═══════════════════════════════════════════════════════════════════════════

def test_an_agent_token_on_a_manager_route_is_still_403_and_now_leaves_a_row(client, world):
    hdr = {"Authorization": f"Bearer {create_access_token(world['ua'].id, 'FIELD_AGENT', 'dev')}"}
    r = client.get("/api/v1/manager/dashboard", headers=hdr)
    assert r.status_code == 403
    assert r.json()["detail"].startswith("Access denied. Required roles:")   # unchanged

    db = TestingSession()
    rows = _rows(db, AuditAction.ROLE_VIOLATION_ATTEMPT)
    assert len(rows) == 1
    row = rows[0]
    assert row.user_id == world["ua"].id           # attributed to the caller
    assert row.success is False
    assert row.entity_type == "Route"
    assert row.entity_id == "GET /api/v1/manager/dashboard"
    assert row.details["actual_role"] == "FIELD_AGENT"
    assert "AGENCY_MANAGER" in row.details["required_roles"]
    assert row.ip_address is not None
    db.close()


def test_a_permitted_role_leaves_no_violation_row(client, world):
    db = TestingSession(); before = len(_rows(db, AuditAction.ROLE_VIOLATION_ATTEMPT)); db.close()
    hdr = {"Authorization": f"Bearer {create_access_token(world['mgr'].id, 'AGENCY_MANAGER', 'dev')}"}
    assert client.get("/api/v1/manager/dashboard", headers=hdr).status_code == 200
    db = TestingSession(); assert len(_rows(db, AuditAction.ROLE_VIOLATION_ATTEMPT)) == before; db.close()


# ═══════════════════════════════════════════════════════════════════════════
# VISIT_RECORDED
# ═══════════════════════════════════════════════════════════════════════════

def test_a_recorded_visit_leaves_one_visit_recorded_row(world, monkeypatch):
    db = TestingSession()
    _freeze_in_hours(monkeypatch, vs); _quiet_side_effects(monkeypatch)
    agent = db.get(Agent, world["ag"].id); case = world["VISIT"]
    out = vs.VisitService(db).record_visit(agent, case.id, RecordVisitRequest(
        check_in_latitude=28.6315, check_in_longitude=77.2167, customer_met=True,
        outcome=VisitOutcome.REVISIT))
    visit_id = out["id"] if isinstance(out, dict) else out.id
    assert db.query(Visit).filter(Visit.id == visit_id).count() == 1     # the visit itself
    rows = _rows(db, AuditAction.VISIT_RECORDED, visit_id)
    assert len(rows) == 1
    assert rows[0].user_id == agent.user_id and rows[0].success is True
    assert rows[0].details["case_number"] == case.case_number
    assert rows[0].details["outcome"] == "REVISIT"
    db.close()


# ═══════════════════════════════════════════════════════════════════════════
# PTP_SET
# ═══════════════════════════════════════════════════════════════════════════

def test_taking_a_promise_leaves_one_ptp_set_row(world):
    db = TestingSession()
    agent = db.get(Agent, world["ag"].id); case = world["PTP"]
    out = ps.PaymentService(db).set_ptp(agent, case.id, SetPTPRequest(
        committed_amount=5000.0, committed_date=TODAY + timedelta(days=3)))
    ptp_id = out["id"]
    assert db.get(PTP, ptp_id) is not None
    assert db.get(Case, case.id).status == CaseStatus.PTP_SET                 # unchanged behaviour
    rows = _rows(db, AuditAction.PTP_SET, ptp_id)
    assert len(rows) == 1
    assert rows[0].user_id == agent.user_id
    assert rows[0].details["committed_amount"] == 5000.0
    assert rows[0].details["committed_date"] == (TODAY + timedelta(days=3)).isoformat()
    db.close()


# ═══════════════════════════════════════════════════════════════════════════
# PAYMENT_SUBMITTED, and receipt_sent
# ═══════════════════════════════════════════════════════════════════════════

class _FakeTwilio:
    """Stands in for twilio.rest.Client. `fail` makes every send raise."""
    calls: list = []
    fail = False

    def __init__(self, *a, **k):
        pass

    class messages:  # noqa: N801 — mirrors the SDK's attribute shape
        @staticmethod
        def create(**kw):
            if _FakeTwilio.fail:
                raise RuntimeError("twilio down")
            _FakeTwilio.calls.append(kw)
            return types.SimpleNamespace(sid="SM1")


def _configure_twilio(monkeypatch):
    monkeypatch.setattr(settings, "TWILIO_ACCOUNT_SID", "AC_test", raising=False)
    monkeypatch.setattr(settings, "TWILIO_AUTH_TOKEN", "tok", raising=False)
    monkeypatch.setattr(settings, "TWILIO_PHONE_NUMBER", "+10000000000", raising=False)
    monkeypatch.setattr(settings, "TWILIO_WHATSAPP_FROM", "", raising=False)
    fake = types.ModuleType("twilio.rest"); fake.Client = _FakeTwilio
    monkeypatch.setitem(sys.modules, "twilio", types.ModuleType("twilio"))
    monkeypatch.setitem(sys.modules, "twilio.rest", fake)
    _FakeTwilio.calls = []; _FakeTwilio.fail = False


def test_a_submitted_payment_leaves_one_row_and_reports_the_receipt_delivered(world, monkeypatch):
    _configure_twilio(monkeypatch)
    db = TestingSession()
    agent = db.get(Agent, world["ag"].id); case = world["PAY"]
    out = ps.PaymentService(db).collect_payment(agent, case.id, CollectPaymentRequest(
        amount=1500.0, mode=PaymentMode.CASH))
    assert db.get(Payment, out["id"]) is not None
    assert out["total_collected"] == 1500.0                                   # unchanged behaviour
    assert out["receipt_sent"] is True
    assert len(_FakeTwilio.calls) == 1 and _FakeTwilio.calls[0]["to"].endswith("9812345678")
    rows = _rows(db, AuditAction.PAYMENT_SUBMITTED, out["id"])
    assert len(rows) == 1
    assert rows[0].user_id == agent.user_id
    assert rows[0].details["amount"] == 1500.0 and rows[0].details["mode"] == "CASH"
    assert rows[0].details["receipt_number"] == out["receipt_number"]
    db.close()


def test_a_failed_receipt_is_reported_false_and_does_not_block_the_payment(world, monkeypatch):
    _configure_twilio(monkeypatch); _FakeTwilio.fail = True
    db = TestingSession()
    agent = db.get(Agent, world["ag"].id); case = world["PAY2"]
    out = ps.PaymentService(db).collect_payment(agent, case.id, CollectPaymentRequest(
        amount=700.0, mode=PaymentMode.UPI, upi_reference="412345678901"))   # a UTR: required since hotfix PAY-1
    assert db.get(Payment, out["id"]) is not None                             # recorded anyway
    assert out["receipt_sent"] is False
    assert len(_rows(db, AuditAction.PAYMENT_SUBMITTED, out["id"])) == 1     # still audited
    db.close()


def test_receipt_sent_is_false_when_there_is_nobody_to_send_to(world, monkeypatch):
    _configure_twilio(monkeypatch)
    db = TestingSession()
    agent = db.get(Agent, world["ag"].id); case = world["NOPHONE"]
    out = ps.PaymentService(db).collect_payment(agent, case.id, CollectPaymentRequest(
        amount=100.0, mode=PaymentMode.CASH))
    assert out["receipt_sent"] is False and _FakeTwilio.calls == []
    db.close()


def test_send_functions_report_truthfully(monkeypatch):
    # Unconfigured: nothing sent, and it says so.
    monkeypatch.setattr(settings, "TWILIO_ACCOUNT_SID", "", raising=False)
    assert ns.NotificationService.send_twilio("+911234567890", "a", "b") is False
    assert ns.NotificationService.send_sms("+911234567890", "a") is False
    # Configured and working.
    _configure_twilio(monkeypatch)
    assert ns.NotificationService.send_twilio("+911234567890", "a", "b") is True
    assert ns.NotificationService.send_sms("+911234567890", "a") is True
    # Configured and broken: False, never an exception.
    _FakeTwilio.fail = True
    assert ns.NotificationService.send_twilio("+911234567890", "a", "b") is False
    assert ns.NotificationService.send_sms("+911234567890", "a") is False


# ═══════════════════════════════════════════════════════════════════════════
# write_audit cannot take its caller down
# ═══════════════════════════════════════════════════════════════════════════

def test_write_audit_never_raises_and_reports_the_failure(world, monkeypatch):
    db = TestingSession()
    monkeypatch.setattr(db, "commit", lambda: (_ for _ in ()).throw(RuntimeError("disk on fire")))
    ok = write_audit(db, action=AuditAction.VISIT_RECORDED, user_id=world["ua"].id,
                     entity_type="Visit", entity_id="x")
    assert ok is False
    db.close()


# ═══════════════════════════════════════════════════════════════════════════
# Retention: audit rows are outside every sweep
# ═══════════════════════════════════════════════════════════════════════════

def test_the_retention_floor_is_named_and_is_five_years():
    assert settings.AUDIT_LOG_RETENTION_DAYS == 1825
    assert settings.AUDIT_LOG_RETENTION_DAYS > settings.LOCATION_RETENTION_DAYS


def test_no_code_path_deletes_audit_rows():
    """Tripwire. A structural scan, so it proves absence of a pattern, not
    of behaviour — the dynamic test below is the one that runs the sweep."""
    import pathlib, re
    root = pathlib.Path(__file__).resolve().parents[1] / "app"
    offenders = []
    for p in root.rglob("*.py"):
        src = p.read_text(encoding="utf-8", errors="ignore")
        if re.search(r"query\(AuditLog\)[^\n]*\.delete\(|delete\(AuditLog\)|DELETE FROM audit_logs", src):
            offenders.append(str(p.relative_to(root)))
    assert offenders == [], offenders


def test_the_location_sweep_leaves_audit_rows_older_than_its_cutoff_untouched(world, monkeypatch):
    from app.core import database as core_db
    from app.workers.tasks import location_retention as lr

    db = TestingSession()
    ancient = datetime.now(timezone.utc) - timedelta(days=settings.LOCATION_RETENTION_DAYS + 400)
    # An audit row far older than the location cutoff (and than the audit floor).
    db.add(AuditLog(id=_uid(), created_at=ancient, user_id=world["ua"].id,
                    action=AuditAction.LOGIN, entity_type="User", entity_id=world["ua"].id, success=True))
    # A location fix the sweep SHOULD delete, to prove it ran.
    db.add(AgentLocation(id=_uid(), agent_id=world["ag"].id, latitude=28.6, longitude=77.2,
                         recorded_at=ancient, is_sos=False))
    db.commit()
    audit_before = db.query(AuditLog).count()
    assert db.query(AgentLocation).count() == 1

    monkeypatch.setattr(core_db, "SessionLocal", TestingSession)
    result = lr.prune_location_trail.apply().get()
    assert result["deleted"] == 1
    db.expire_all()
    assert db.query(AgentLocation).count() == 0
    assert db.query(AuditLog).count() == audit_before, "the sweep touched audit_logs"
    db.close()
