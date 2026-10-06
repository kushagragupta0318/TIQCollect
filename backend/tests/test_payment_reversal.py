"""Two-stage payment reversal (#2, ADR 0015). Service-level, SQLite.

SQLite has no RLS, so this covers the state machine, the 409s, the DB
CheckConstraints, the four _unwind effects, and the two defect fixes (current-month
counter, re-open only a payment-driven close). The cross-tenant RLS write (a bank
session UPDATEing an agency row) is a Postgres-only concern and lives in tests/pg/.

The bank stage is gated by _L8_SCOPE_AVAILABLE; tests that exercise the unwind set
it True to stand in for the merged scope bind, and one test asserts it is 503 when
False.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from app.core.geo import IST
from app.models.agent import Agent, AgentSpecialization, AgentStatus, AgentTier
from app.models.case import Case, CaseStatus
from app.models.customer import Customer
from app.models.loan import DPDBucket, Loan, LoanStatus, LoanType
from app.models.payment import Payment, PaymentMode, PaymentStatus
from app.models.payment_reversal import PaymentReversalRequest, ReversalStatus
from app.models.ptp import PTP, PTPStatus
from app.models.user import User, UserRole
from app.services import payment_reversal_service as prs
from app.services.payment_reversal_service import PaymentReversalService
from tests._db import create_schema, make_engine, make_session_factory, test_id

engine = make_engine()
Session = make_session_factory(bind=engine)

BANK = test_id("bank")
AGENCY = test_id("agency")
OTHER_AGENCY = test_id("agency2")


def _uid() -> str:
    return test_id("u")


def _user(db, role, agency=AGENCY, bank=BANK):
    u = User(id=_uid(), email=f"{role.value.lower()}-{_uid()[:6]}@t.io", phone="9810000000",
             full_name=role.value, hashed_password="x", role=role, is_active=True, is_verified=True,
             bank_id=bank, agency_id=(None if role in (UserRole.BANK_ADMIN,) else agency))
    db.add(u); db.flush(); return u


def _payment(db, *, agent_id, amount=1000.0, when=None, status=PaymentStatus.VERIFIED, agency=AGENCY):
    cust = Customer(id=_uid(), customer_ref=_uid()[:8], full_name="B", date_of_birth=date(1990, 1, 1),
                    gender="M", pan_masked="ABCDE1234F", aadhaar_masked="123456789012",
                    phone_primary="9812345678", address_line1="D", city="D", state="D", pincode="110001",
                    latitude=28.6, longitude=77.2, language_preference="HINDI")
    db.add(cust); db.flush()
    loan = Loan(id=_uid(), customer_id=cust.id, loan_account_number="L" + _uid()[:6],
                loan_type=LoanType.PERSONAL, branch_code="DL01", sanctioned_amount=100000.0,
                disbursed_amount=100000.0, outstanding_principal=50000.0, total_outstanding=50000.0,
                overdue_amount=10000.0, emi_amount=5000.0, interest_rate=12.0,
                disbursement_date=date(2022, 1, 1), maturity_date=date(2027, 1, 1),
                dpd=45, dpd_bucket=DPDBucket.BUCKET_2, status=LoanStatus.ACTIVE)
    db.add(loan); db.flush()
    case = Case(id=_uid(), case_number="C-" + _uid()[:6], customer_id=cust.id, loan_id=loan.id,
                agent_id=agent_id, status=CaseStatus.PAID, target_amount=1000.0,
                collected_amount=amount, resolved_at=datetime.now(timezone.utc), allocation_date=date.today())
    db.add(case); db.flush()
    pay = Payment(id=_uid(), bank_id=BANK, agency_id=agency, case_id=case.id, loan_id=loan.id,
                  agent_id=agent_id, amount=amount, mode=PaymentMode.CASH, status=status,
                  receipt_number="R" + _uid()[:8], payment_date=when or datetime.now(timezone.utc))
    db.add(pay); db.flush()
    return pay, case


def _agent(db, mgr):
    u = _user(db, UserRole.FIELD_AGENT)
    a = Agent(id=_uid(), user_id=u.id, employee_code="E" + _uid()[:5], id_card_number="E-ID" + _uid()[:4],
              manager_user_id=mgr.id, gender="M", base_latitude=28.6, base_longitude=77.2, territory="Delhi",
              languages_spoken=["HINDI"], status=AgentStatus.ON_DUTY, tier=AgentTier.TIER_1,
              specialization=AgentSpecialization.BOTH, ranking_score=80.0, max_cases_per_day=5,
              current_month_collections=5000.0)
    db.add(a); db.flush(); return a


@pytest.fixture(scope="module", autouse=True)
def _schema():
    create_schema(engine)


def _request_and_agency_approve(db, svc, mgr, admin, pay):
    req = svc.request_reversal(mgr, pay.id, "wrong amount keyed")
    svc.agency_approve(admin, req.id)
    return req


def test_request_sets_pending_agency_and_needs_a_reason():
    db = Session()
    mgr, admin = _user(db, UserRole.AGENCY_MANAGER), _user(db, UserRole.AGENCY_ADMIN)
    agent = _agent(db, mgr)
    pay, _ = _payment(db, agent_id=agent.id)
    svc = PaymentReversalService(db)
    req = svc.request_reversal(mgr, pay.id, "wrong amount")
    assert req.status == ReversalStatus.PENDING_AGENCY and req.agency_requested_by_id == mgr.id
    with pytest.raises(HTTPException) as e:
        svc.request_reversal(mgr, pay.id, "   ")
    assert e.value.status_code in (409, 422)   # already open OR empty reason
    db.close()


def test_agency_approve_routes_to_bank_without_touching_the_ledger():
    db = Session()
    mgr, admin = _user(db, UserRole.AGENCY_MANAGER), _user(db, UserRole.AGENCY_ADMIN)
    agent = _agent(db, mgr)
    pay, case = _payment(db, agent_id=agent.id, amount=1000.0)
    svc = PaymentReversalService(db)
    req = svc.request_reversal(mgr, pay.id, "r")
    svc.agency_approve(admin, req.id)
    assert req.status == ReversalStatus.PENDING_BANK and req.agency_approved_by_id == admin.id
    assert db.get(Case, case.id).collected_amount == 1000.0       # ledger UNCHANGED at agency stage
    assert db.get(Payment, pay.id).status == PaymentStatus.VERIFIED
    db.close()


def test_bank_stage_is_503_until_the_scope_bind_is_available(monkeypatch):
    monkeypatch.setattr(prs, "_L8_SCOPE_AVAILABLE", False)
    db = Session()
    mgr, admin, bank = _user(db, UserRole.AGENCY_MANAGER), _user(db, UserRole.AGENCY_ADMIN), _user(db, UserRole.BANK_ADMIN)
    agent = _agent(db, mgr)
    pay, _ = _payment(db, agent_id=agent.id)
    svc = PaymentReversalService(db)
    req = _request_and_agency_approve(db, svc, mgr, admin, pay)
    with pytest.raises(HTTPException) as e:
        svc.bank_approve(bank, req.id, scope=_ctx(bank))
    assert e.value.status_code == 503
    db.close()


class _Ctx:
    def __init__(self, user): self.bank_id, self.agency_id, self.scope = user.bank_id, user.agency_id, "BANK"


def _ctx(user):
    return _Ctx(user)


def test_bank_approve_unwinds_every_effect(monkeypatch):
    monkeypatch.setattr(prs, "_L8_SCOPE_AVAILABLE", True)
    db = Session()
    mgr, admin, bank = _user(db, UserRole.AGENCY_MANAGER), _user(db, UserRole.AGENCY_ADMIN), _user(db, UserRole.BANK_ADMIN)
    agent = _agent(db, mgr)
    before = db.get(Agent, agent.id).current_month_collections
    pay, case = _payment(db, agent_id=agent.id, amount=1000.0)
    svc = PaymentReversalService(db)
    req = _request_and_agency_approve(db, svc, mgr, admin, pay)
    svc.bank_approve(bank, req.id, scope=_ctx(bank))
    assert db.get(PaymentReversalRequest, req.id).status == ReversalStatus.APPROVED
    assert db.get(Payment, pay.id).status == PaymentStatus.REVERSED          # 1. payment reversed
    c = db.get(Case, case.id)
    assert c.collected_amount == 0.0 and c.status == CaseStatus.IN_PROGRESS  # 2. ledger + status
    assert c.resolved_at is None                                             # 3. resolved_at cleared
    assert db.get(Agent, agent.id).current_month_collections == before - 1000.0   # 4. month figure
    db.close()


def test_a_prior_month_reversal_does_not_touch_this_months_counter(monkeypatch):
    monkeypatch.setattr(prs, "_L8_SCOPE_AVAILABLE", True)
    db = Session()
    mgr, admin, bank = _user(db, UserRole.AGENCY_MANAGER), _user(db, UserRole.AGENCY_ADMIN), _user(db, UserRole.BANK_ADMIN)
    agent = _agent(db, mgr)
    before = db.get(Agent, agent.id).current_month_collections
    last_month = datetime.now(IST).replace(day=1) - timedelta(days=2)
    pay, _ = _payment(db, agent_id=agent.id, amount=1000.0, when=last_month.astimezone(timezone.utc))
    svc = PaymentReversalService(db)
    req = _request_and_agency_approve(db, svc, mgr, admin, pay)
    svc.bank_approve(bank, req.id, scope=_ctx(bank))
    assert db.get(Agent, agent.id).current_month_collections == before   # counter untouched (defect 1)
    db.close()


def test_a_written_off_case_is_not_reopened(monkeypatch):
    monkeypatch.setattr(prs, "_L8_SCOPE_AVAILABLE", True)
    db = Session()
    mgr, admin, bank = _user(db, UserRole.AGENCY_MANAGER), _user(db, UserRole.AGENCY_ADMIN), _user(db, UserRole.BANK_ADMIN)
    agent = _agent(db, mgr)
    pay, case = _payment(db, agent_id=agent.id, amount=1000.0)
    closed_at = datetime.now(timezone.utc)
    case.status = CaseStatus.WRITTEN_OFF; case.resolved_at = closed_at
    db.flush()
    svc = PaymentReversalService(db)
    req = _request_and_agency_approve(db, svc, mgr, admin, pay)
    svc.bank_approve(bank, req.id, scope=_ctx(bank))
    c = db.get(Case, case.id)
    assert c.status == CaseStatus.WRITTEN_OFF and c.resolved_at == closed_at  # NOT reopened (defect 2)
    assert db.get(Payment, pay.id).status == PaymentStatus.REVERSED           # ledger still adjusted
    db.close()


def test_the_bank_signer_cannot_be_an_agency_actor(monkeypatch):
    monkeypatch.setattr(prs, "_L8_SCOPE_AVAILABLE", True)
    db = Session()
    mgr, admin = _user(db, UserRole.AGENCY_MANAGER), _user(db, UserRole.AGENCY_ADMIN)
    agent = _agent(db, mgr)
    pay, _ = _payment(db, agent_id=agent.id)
    svc = PaymentReversalService(db)
    req = _request_and_agency_approve(db, svc, mgr, admin, pay)
    # the agency admin tries to also sign off as the bank (same person both sides)
    with pytest.raises(HTTPException) as e:
        svc.bank_approve(admin, req.id, scope=_ctx(admin))
    assert e.value.status_code == 403
    db.close()


def test_another_agency_cannot_see_the_request():
    db = Session()
    mgr, admin = _user(db, UserRole.AGENCY_MANAGER), _user(db, UserRole.AGENCY_ADMIN)
    other = _user(db, UserRole.AGENCY_MANAGER, agency=OTHER_AGENCY)
    agent = _agent(db, mgr)
    pay, _ = _payment(db, agent_id=agent.id)
    svc = PaymentReversalService(db)
    req = svc.request_reversal(mgr, pay.id, "r")
    with pytest.raises(HTTPException) as e:
        svc.agency_approve(other, req.id)
    assert e.value.status_code == 404      # out-of-agency reads as not-found
    db.close()


def test_a_ptp_honoured_by_another_payment_stays_honoured(monkeypatch):
    monkeypatch.setattr(prs, "_L8_SCOPE_AVAILABLE", True)
    db = Session()
    mgr, admin, bank = _user(db, UserRole.AGENCY_MANAGER), _user(db, UserRole.AGENCY_ADMIN), _user(db, UserRole.BANK_ADMIN)
    agent = _agent(db, mgr)
    pay, case = _payment(db, agent_id=agent.id, amount=1000.0)
    # a second verified payment on the same case/agent that also covers the PTP
    pay2 = Payment(id=_uid(), bank_id=BANK, agency_id=AGENCY, case_id=case.id, loan_id=pay.loan_id,
                   agent_id=agent.id, amount=1000.0, mode=PaymentMode.CASH, status=PaymentStatus.VERIFIED,
                   receipt_number="R" + _uid()[:8], payment_date=datetime.now(timezone.utc))
    db.add(pay2)
    ptp = PTP(id=_uid(), case_id=case.id, agent_id=agent.id, committed_amount=1000.0,
              committed_date=date.today(), status=PTPStatus.HONORED, actual_paid_amount=2000.0)
    db.add(ptp); db.flush()
    svc = PaymentReversalService(db)
    req = _request_and_agency_approve(db, svc, mgr, admin, pay)
    svc.bank_approve(bank, req.id, scope=_ctx(bank))
    assert db.get(PTP, ptp.id).status == PTPStatus.HONORED   # pay2 still covers it
    db.close()
