"""
GET /manager/analytics/payment-modes — collections by mode, VERIFIED only,
tenant-scoped, optionally one calendar month, every agent mode zero-filled,
with the cash and digital shares the card exists to show.
"""
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.core.security import create_access_token
from app.main import app
from app.models.agent import Agent, AgentSpecialization, AgentStatus, AgentTier
from app.models.case import Case, CaseStatus
from app.models.customer import Customer, RiskCategory
from app.models.loan import DPDBucket, Loan, LoanStatus, LoanType
from app.models.payment import Payment, PaymentMode, PaymentStatus
from app.models.user import User, UserRole
from tests._db import create_schema, drop_schema, make_engine, make_session_factory, test_id  # noqa: F401

engine = make_engine()


@event.listens_for(engine, "connect")
def _sqlite_helpers(dbapi_conn, _):
    dbapi_conn.create_function("to_char", 2, lambda v, f: str(v)[:7] if v else None)


Session = make_session_factory(autocommit=False, autoflush=False, bind=engine)
NOW = datetime.now(timezone.utc).replace(day=15, hour=12, minute=0, second=0, microsecond=0)
THIS_MONTH = NOW.strftime("%Y-%m")
LAST_MONTH_DT = (NOW.replace(day=1) - timedelta(days=1)).replace(day=10)


def _user(db, email, role, name):
    u = User(email=email, phone="9" + str(abs(hash(email)) % 10**9).zfill(9), full_name=name,
             hashed_password="x", role=role, is_active=True, is_verified=True)
    db.add(u); db.flush(); return u


def _agent(db, code, user, mgr):
    a = Agent(user_id=user.id, employee_code=code, id_card_number=code + "-ID", agency_id="AG1",
              base_latitude=28.6, base_longitude=77.2, tier=AgentTier.TIER_1,
              specialization=AgentSpecialization.BOTH, status=AgentStatus.ON_DUTY,
              territory="Delhi", languages_spoken=["HINDI"], ranking_score=80.0, manager_user_id=mgr.id)
    db.add(a); db.flush(); return a


@pytest.fixture(scope="module")
def book():
    create_schema(bind=engine)
    db = Session()
    mgr = _user(db, "pm_mgr@t.in", UserRole.AGENCY_MANAGER, "Mgr")
    other = _user(db, "pm_other@t.in", UserRole.AGENCY_MANAGER, "Other")
    ag = _agent(db, "PM001", _user(db, "pm_a1@t.in", UserRole.FIELD_AGENT, "A1"), mgr)
    ag_o = _agent(db, "PM002", _user(db, "pm_a2@t.in", UserRole.FIELD_AGENT, "A2"), other)
    cust = Customer(customer_ref="PMC1", full_name="B", date_of_birth="1990-01-01", gender="M",
                    pan_masked="X", aadhaar_masked="X", phone_primary="9000000002", address_line1="1",
                    city="Delhi", state="DL", pincode="110001", latitude=28.6, longitude=77.2,
                    risk_category=RiskCategory.MEDIUM)
    db.add(cust); db.flush()
    loan = Loan(loan_account_number="PML1", customer_id=cust.id, loan_type=LoanType.PERSONAL,
                branch_code="BR", sanctioned_amount=1.0, disbursed_amount=1.0,
                outstanding_principal=1.0, total_outstanding=1.0, overdue_amount=1.0, emi_amount=1.0,
                disbursement_date="2025-01-01", maturity_date="2027-01-01", dpd=45,
                dpd_bucket=DPDBucket.BUCKET_2, status=LoanStatus.ACTIVE, interest_rate=1.0, penal_charges=0.0)
    db.add(loan); db.flush()
    c = Case(case_number="PM-1", customer_id=cust.id, loan_id=loan.id, agent_id=ag.id,
             status=CaseStatus.IN_PROGRESS, target_amount=100000.0, collected_amount=0.0,
             allocation_date=date.today().isoformat())
    co = Case(case_number="PM-X", customer_id=cust.id, loan_id=loan.id, agent_id=ag_o.id,
              status=CaseStatus.IN_PROGRESS, target_amount=100000.0, collected_amount=0.0,
              allocation_date=date.today().isoformat())
    db.add_all([c, co]); db.flush()

    def pay(case, agent, mode, amt, when, status=PaymentStatus.VERIFIED, n=[0]):
        n[0] += 1
        db.add(Payment(case_id=case.id, agent_id=agent.id, amount=amt, mode=mode, status=status,
                       receipt_number=f"PM-RCPT-{n[0]}", payment_date=when))

    pay(c, ag, PaymentMode.CASH, 5000, NOW)
    pay(c, ag, PaymentMode.CASH, 3000, NOW)
    pay(c, ag, PaymentMode.UPI, 2000, NOW)
    pay(c, ag, PaymentMode.NEFT, 4000, LAST_MONTH_DT)                       # last month
    pay(c, ag, PaymentMode.UPI, 9999, NOW, status=PaymentStatus.PENDING_VERIFICATION)  # not verified
    pay(co, ag_o, PaymentMode.CASH, 70000, NOW)                             # other tenant
    db.commit()
    yield {"mgr": mgr, "other": other}
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


def test_all_time_verified_only_and_tenant_scoped(client, book):
    d = client.get("/api/v1/manager/analytics/payment-modes", headers=_h(book["mgr"])).json()
    by = {r["mode"]: r for r in d["modes"]}
    assert by["CASH"]["count"] == 2 and by["CASH"]["amount"] == 8000.0
    assert by["UPI"]["count"] == 1 and by["UPI"]["amount"] == 2000.0     # the PENDING one is out
    assert by["NEFT"]["amount"] == 4000.0                                  # last month counts all-time
    assert d["total_amount"] == 14000.0 and d["total_count"] == 4
    assert by["CASH"]["amount"] < 70000                                    # the other tenant's cash is absent


def test_every_agent_mode_is_present_zero_filled_and_dead_modes_are_not(client, book):
    d = client.get("/api/v1/manager/analytics/payment-modes", headers=_h(book["mgr"])).json()
    modes = {r["mode"] for r in d["modes"]}
    # The six modes an agent can record. Not BANK_DIRECT (never an agent
    # collection) and not ONLINE (a prototype leftover nothing writes).
    assert modes == {"CASH", "UPI", "NEFT", "RTGS", "CHEQUE", "DD"}
    assert all(r["count"] == 0 and r["amount"] == 0.0 for r in d["modes"] if r["mode"] in {"RTGS", "CHEQUE", "DD"})


def test_sorted_largest_first_with_shares_and_flags(client, book):
    d = client.get("/api/v1/manager/analytics/payment-modes", headers=_h(book["mgr"])).json()
    assert [r["mode"] for r in d["modes"][:3]] == ["CASH", "NEFT", "UPI"]
    cash = d["modes"][0]
    assert cash["is_cash"] and not cash["is_digital"]
    assert cash["share_pct"] == pytest.approx(8000 / 14000 * 100, abs=0.06)
    assert cash["avg_ticket"] == 4000.0
    assert d["cash_share_pct"] == cash["share_pct"]
    assert d["digital_share_pct"] == pytest.approx(6000 / 14000 * 100, abs=0.06)   # UPI + NEFT


def test_month_filter_uses_the_dpd_cards_convention(client, book):
    d = client.get(f"/api/v1/manager/analytics/payment-modes?month={THIS_MONTH}", headers=_h(book["mgr"])).json()
    by = {r["mode"]: r for r in d["modes"]}
    assert d["month"] == THIS_MONTH
    assert by["NEFT"]["amount"] == 0.0            # last month's payment is out
    assert d["total_amount"] == 10000.0
    assert d["cash_share_pct"] == 80.0 and d["digital_share_pct"] == 20.0


def test_other_manager_sees_only_their_own(client, book):
    d = client.get("/api/v1/manager/analytics/payment-modes", headers=_h(book["other"])).json()
    assert d["total_amount"] == 70000.0 and d["total_count"] == 1
    assert d["cash_share_pct"] == 100.0


def test_requires_manager(client, book):
    assert client.get("/api/v1/manager/analytics/payment-modes").status_code in (401, 403)


def test_monthly_split_groups_cash_digital_paper_over_six_months(client, book):
    d = client.get("/api/v1/manager/analytics/payment-modes", headers=_h(book["mgr"])).json()
    months = [m["month"] for m in d["monthly"]]
    assert len(months) == 6 and months[-1] == THIS_MONTH
    this = d["monthly"][-1]
    assert this["cash"] == 8000.0 and this["digital"] == 2000.0 and this["paper"] == 0.0
    assert this["total"] == 10000.0 and this["cash_share_pct"] == 80.0
    last = next(m for m in d["monthly"] if m["month"] == LAST_MONTH_DT.strftime("%Y-%m"))
    assert last["digital"] == 4000.0 and last["cash"] == 0.0 and last["cash_share_pct"] == 0.0
    # the month filter does NOT narrow the trend — it is the context
    d2 = client.get(f"/api/v1/manager/analytics/payment-modes?month={THIS_MONTH}", headers=_h(book["mgr"])).json()
    assert d2["monthly"] == d["monthly"]


def test_no_demo_writer_uses_the_dead_online_mode():
    """ONLINE is a prototype leftover no agent can select. The demo scripts are
    the only things that ever wrote it; keep it that way — at zero. A comment
    may NAME the member; code may not USE it."""
    import pathlib, re
    root = pathlib.Path(__file__).resolve().parents[1] / "scripts"
    offenders = []
    for f in root.glob("*.py"):
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("#", 1)[0]
            if re.search(r"PaymentMode\.ONLINE", code):
                offenders.append(f"{f.name}:{n}")
    assert offenders == [], f"PaymentMode.ONLINE written by: {offenders}"
