"""
GET /manager/analytics/ptp-outcomes — how promises ended, by the month they
fell due. 2026-09-18.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.core.security import create_access_token
from app.main import app
from app.models.agent import Agent, AgentSpecialization, AgentStatus, AgentTier
from app.models.beat import Beat, BeatStatus
from app.models.case import Case, CaseStatus
from app.models.customer import Customer, RiskCategory
from app.models.loan import DPDBucket, Loan, LoanStatus, LoanType
from app.models.ptp import PTP, PTPStatus
from app.models.user import User, UserRole
from tests._db import create_schema, drop_schema, make_engine, make_session_factory, test_id  # noqa: F401

engine = make_engine()


@event.listens_for(engine, "connect")
def _sqlite_helpers(dbapi_conn, _):
    dbapi_conn.create_function("to_char", 2, lambda v, f: str(v)[:7] if v else None)


Session = make_session_factory(autocommit=False, autoflush=False, bind=engine)
TODAY = date.today()
THIS = TODAY.strftime("%Y-%m")


def _ym(months_back: int) -> date:
    y, m = TODAY.year, TODAY.month - months_back
    while m <= 0:
        m += 12; y -= 1
    return date(y, m, 15)


def _user(db, email, role, name):
    u = User(email=email, phone="9" + str(abs(hash(email)) % 10**9).zfill(9), full_name=name,
             hashed_password="x", role=role, is_active=True, is_verified=True)
    db.add(u); db.flush(); return u


def _agent(db, code, user, mgr):
    a = Agent(user_id=user.id, employee_code=code, id_card_number=code + "-ID", 
              base_latitude=28.6, base_longitude=77.2, tier=AgentTier.TIER_1, specialization=AgentSpecialization.BOTH,
              status=AgentStatus.ON_DUTY, territory="Delhi", languages_spoken=["HINDI"], ranking_score=80.0,
              manager_user_id=mgr.id)
    db.add(a); db.flush(); return a


@pytest.fixture(scope="module")
def book():
    create_schema(bind=engine)
    db = Session()
    mgr = _user(db, "po_mgr@t.in", UserRole.AGENCY_MANAGER, "Mgr")
    other = _user(db, "po_other@t.in", UserRole.AGENCY_MANAGER, "Other")
    a1 = _agent(db, "PO001", _user(db, "po_a1@t.in", UserRole.FIELD_AGENT, "A1"), mgr)
    a2 = _agent(db, "PO002", _user(db, "po_a2@t.in", UserRole.FIELD_AGENT, "A2"), mgr)
    ax = _agent(db, "PO003", _user(db, "po_ax@t.in", UserRole.FIELD_AGENT, "X"), other)
    cust = Customer(customer_ref="POC1", full_name="B", date_of_birth=date(1990, 1, 1), gender="M", pan_masked="X",
                    aadhaar_masked="X", phone_primary="9000000001", address_line1="1", city="Delhi", state="DL",
                    pincode="110001", latitude=28.6, longitude=77.2, risk_category=RiskCategory.MEDIUM)
    db.add(cust); db.flush()
    loan = Loan(loan_account_number="POL1", customer_id=cust.id, loan_type=LoanType.PERSONAL,
                branch_code="BR", sanctioned_amount=1.0, disbursed_amount=1.0, outstanding_principal=1.0,
                total_outstanding=1.0, overdue_amount=1.0, emi_amount=1.0, disbursement_date=date(2025, 1, 1),
                maturity_date=date(2027, 1, 1), dpd=45, dpd_bucket=DPDBucket.BUCKET_2, status=LoanStatus.ACTIVE,
                interest_rate=1.0, penal_charges=0.0)
    db.add(loan); db.flush()

    def case(agent):
        c = Case(case_number=f"PO-{agent.employee_code}-{db.query(Case).count()}", customer_id=cust.id, loan_id=loan.id,
                 agent_id=agent.id, status=CaseStatus.PTP_SET, target_amount=10000.0, collected_amount=0.0,
                 allocation_date=TODAY)
        db.add(c); db.flush(); return c

    def ptp(agent, due, status, amount=1000.0, paid=0.0):
        db.add(PTP(case_id=case(agent).id, agent_id=agent.id, committed_amount=amount, committed_date=due,
                   status=status, actual_paid_amount=paid))

    # Two months ago: 2 honoured, 1 partly, 1 broken, 1 rescheduled  -> kept 2/3
    m2 = _ym(2)
    ptp(a1, m2, PTPStatus.HONORED, 1000, 1000); ptp(a1, m2, PTPStatus.HONORED, 500, 600)
    ptp(a2, m2, PTPStatus.PARTIALLY_HONORED, 1000, 300); ptp(a2, m2, PTPStatus.BROKEN)
    ptp(a1, m2, PTPStatus.RESCHEDULED)
    # Last month: 1 broken, 1 EXPIRED (counts as broken)  -> kept 0/2
    m1 = _ym(1)
    ptp(a1, m1, PTPStatus.BROKEN); ptp(a2, m1, PTPStatus.EXPIRED)
    # This month: 1 honoured, 2 still open -> kept 1/1, open 2
    ptp(a1, date(TODAY.year, TODAY.month, 1), PTPStatus.HONORED, 700, 700)
    ptp(a1, TODAY + timedelta(days=3), PTPStatus.ACTIVE); ptp(a2, TODAY + timedelta(days=5), PTPStatus.ACTIVE)
    # Other manager: loud, must never appear
    for _ in range(5):
        ptp(ax, m2, PTPStatus.BROKEN)
    # a beat today so the effective date is today
    db.add(Beat(agent_id=a1.id, beat_date=TODAY, beat_number="PO-B", ordered_case_ids=[], status=BeatStatus.PLANNED))
    db.commit()
    yield {"db": db, "mgr": mgr, "other": other, "a1": a1, "a2": a2, "ax": ax}
    db.close()


@pytest.fixture(scope="module")
def client(book):
    def override():
        db = Session()
        try:
            yield db
        finally:
            db.close()
    app.dependency_overrides[get_db] = override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


def _h(user):
    return {"Authorization": f"Bearer {create_access_token(user.id, user.role.value, 'test-device')}"}


def _get(client, user, **params):
    r = client.get("/api/v1/manager/analytics/ptp-outcomes", headers=_h(user), params=params)
    assert r.status_code == 200, r.text
    return r.json()


def test_buckets_by_due_month_and_kept_rate_ignores_open(client, book):
    d = _get(client, book["mgr"], months=6)
    by = {m["month"]: m for m in d["months"]}
    m2 = by[_ym(2).strftime("%Y-%m")]
    assert (m2["honored"], m2["partly"], m2["broken"], m2["rescheduled"], m2["open"]) == (2, 1, 1, 1, 0)
    assert m2["kept_rate_pct"] == pytest.approx(66.7, abs=0.05)
    assert m2["promised_amount"] == 4500.0 and m2["paid_amount"] == 1900.0
    m1 = by[_ym(1).strftime("%Y-%m")]
    assert m1["broken"] == 2 and m1["kept_rate_pct"] == 0.0, "EXPIRED counts as broken"
    cur = by[THIS]
    assert cur["honored"] == 1 and cur["open"] == 2 and cur["is_current"] is True
    assert cur["kept_rate_pct"] == 100.0, "open promises never enter the rate"


def test_every_window_month_is_present_zero_filled(client, book):
    d = _get(client, book["mgr"], months=6)
    months = [m["month"] for m in d["months"]]
    assert d["window"] == months[:6] or set(d["window"]) <= set(months)
    empty = {m["month"]: m for m in d["months"]}[_ym(4).strftime("%Y-%m")]
    assert empty["total"] == 0 and empty["kept_rate_pct"] is None


def test_tenant_scoped_and_agent_filter(client, book):
    d = _get(client, book["mgr"], months=6)
    total_broken = sum(m["broken"] for m in d["months"])
    assert total_broken == 3, "the other manager's five broken promises are invisible"
    theirs = _get(client, book["other"], months=6)
    assert sum(m["broken"] for m in theirs["months"]) == 5 and sum(m["honored"] for m in theirs["months"]) == 0
    # per-agent: a2 holds 1 partly, 1 broken (m2), 1 expired (m1), 1 open
    a2 = _get(client, book["mgr"], months=6, agent_id=book["a2"].id)
    assert sum(m["honored"] for m in a2["months"]) == 0
    assert sum(m["partly"] for m in a2["months"]) == 1 and sum(m["broken"] for m in a2["months"]) == 2
    assert sum(m["open"] for m in a2["months"]) == 1


def test_another_managers_agent_is_404(client, book):
    r = client.get("/api/v1/manager/analytics/ptp-outcomes", headers=_h(book["mgr"]), params={"agent_id": book["ax"].id})
    assert r.status_code == 404


def test_months_is_clamped(client, book):
    assert len(_get(client, book["mgr"], months=0)["window"]) == 1
    assert len(_get(client, book["mgr"], months=99)["window"]) == 24
