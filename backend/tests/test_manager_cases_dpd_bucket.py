"""GET /manager/cases?dpd_bucket= (audit finding on P2 G04's sibling fix):
server-side DPD-bucket filtering, added 2026-10-01 alongside the frontend fix
for the donut/list count mismatch. No test existed for the backend param
itself — this is that test.
"""
from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.core.security import create_access_token, hash_password
from app.main import app
from app.models.agent import Agent, AgentSpecialization, AgentStatus, AgentTier
from app.models.case import Case, CaseStatus
from app.models.customer import Customer, RiskCategory
from app.models.loan import DPDBucket, Loan, LoanStatus, LoanType
from app.models.user import User, UserRole
from tests._db import TEST_AGENCY_ID, TEST_BANK_ID, create_schema, make_engine, make_session_factory, test_id

BASE = "/api/v1/manager/cases"
ALL_BUCKETS = ["CURRENT", "BUCKET_1", "BUCKET_2", "BUCKET_3", "NPA"]


def _user(db, key, role, *, phone):
    u = User(id=test_id(f"u:{key}"), email=f"{key}@example.test", phone=phone, full_name=key.title(),
             hashed_password=hash_password("Harbour-Lights-2026"), role=role,
             bank_id=TEST_BANK_ID, agency_id=TEST_AGENCY_ID)
    db.add(u)
    return u


def _agent(db, code, user, mgr):
    a = Agent(user_id=user.id, employee_code=code, id_card_number=code + "-ID",
             base_latitude=28.6, base_longitude=77.2, tier=AgentTier.TIER_2,
             specialization=AgentSpecialization.BOTH, status=AgentStatus.ON_DUTY,
             territory="Gurugram", languages_spoken=["HINDI"], ranking_score=50.0,
             manager_user_id=mgr.id)
    db.add(a)
    db.flush()
    return a


def _loan_and_case(db, n, agent, bucket):
    cust = Customer(customer_ref=f"DPD-{n}", full_name="Priya Nair", date_of_birth=date(1990, 1, 1), gender="FEMALE",
                    pan_masked="XXXXX1234K", aadhaar_masked="XXXXXXXX9988", phone_primary="9000000001",
                    address_line1="12 MG Road", city="Gurugram", state="Haryana", pincode="122001",
                    latitude=28.6, longitude=77.2, risk_category=RiskCategory.MEDIUM)
    db.add(cust)
    db.flush()
    loan = Loan(loan_account_number=f"DPDL-{n}", customer_id=cust.id, loan_type=LoanType.PERSONAL,
               branch_code="BR", sanctioned_amount=100000.0, disbursed_amount=100000.0,
               outstanding_principal=80000.0, total_outstanding=85000.0, overdue_amount=5000.0,
               emi_amount=4000.0, disbursement_date=date(2025, 1, 1), maturity_date=date(2027, 1, 1),
               dpd=30, dpd_bucket=bucket, status=LoanStatus.ACTIVE, interest_rate=14.0, penal_charges=0.0)
    db.add(loan)
    db.flush()
    case = Case(case_number=f"DPD-{n}", customer_id=cust.id, loan_id=loan.id, agent_id=agent.id,
               status=CaseStatus.ASSIGNED, target_amount=5000.0, collected_amount=0.0, allocation_date=date.today())
    db.add(case)
    db.flush()
    return case


@pytest.fixture()
def w():
    engine = make_engine()
    create_schema(engine)
    # No info= override: make_session_factory's default tenant context fills
    # bank_id/agency_id on Agent/Customer/Loan/Case via tenancy_listener.py —
    # _agent() and _loan_and_case() below rely on it rather than passing
    # bank_id/agency_id on every row, same as test_leave_requests.py's
    # _agent()/case() helpers this was copied from.
    Session = make_session_factory(engine)
    db = Session()

    mgr1 = _user(db, "mgr1", UserRole.AGENCY_MANAGER, phone="9800000001")
    mgr2 = _user(db, "mgr2", UserRole.AGENCY_MANAGER, phone="9800000002")
    db.flush()
    agent1 = _agent(db, "DP001", _user(db, "a1", UserRole.FIELD_AGENT, phone="9800000003"), mgr1)
    agent2 = _agent(db, "DP002", _user(db, "a2", UserRole.FIELD_AGENT, phone="9800000004"), mgr2)

    # One case per bucket under mgr1's agent, plus an NPA case under mgr2's
    # agent so the cross-manager isolation test has something to leak.
    cases = {b: _loan_and_case(db, f"m1-{b}", agent1, DPDBucket[b]) for b in ALL_BUCKETS}
    other_npa_case = _loan_and_case(db, "m2-NPA", agent2, DPDBucket.NPA)
    db.commit()

    def override():
        s = Session()
        try:
            yield s
        finally:
            s.close()
    app.dependency_overrides[get_db] = override
    try:
        yield {"c": TestClient(app), "mgr1": mgr1, "mgr2": mgr2, "cases": cases, "other_npa": other_npa_case}
    finally:
        app.dependency_overrides.pop(get_db, None)
        db.close()


def _h(user):
    return {"Authorization": "Bearer " + create_access_token(user.id, user.role.value, "dev-1")}


@pytest.mark.parametrize("bucket", ALL_BUCKETS)
def test_each_bucket_filters_to_exactly_that_case(w, bucket):
    r = w["c"].get(BASE, headers=_h(w["mgr1"]), params={"dpd_bucket": bucket})
    assert r.status_code == 200, r.text
    body = r.json()
    case_numbers = {c["case_number"] for c in body["cases"]}
    assert case_numbers == {w["cases"][bucket].case_number}, f"bucket={bucket}"


def test_an_unknown_bucket_value_is_refused(w):
    r = w["c"].get(BASE, headers=_h(w["mgr1"]), params={"dpd_bucket": "NOT_A_BUCKET"})
    assert r.status_code == 422


def test_the_filter_is_case_insensitive(w):
    r = w["c"].get(BASE, headers=_h(w["mgr1"]), params={"dpd_bucket": "npa"})
    assert r.status_code == 200, r.text
    case_numbers = {c["case_number"] for c in r.json()["cases"]}
    assert case_numbers == {w["cases"]["NPA"].case_number}


def test_another_managers_case_in_the_same_bucket_never_appears(w):
    r = w["c"].get(BASE, headers=_h(w["mgr1"]), params={"dpd_bucket": "NPA"})
    assert r.status_code == 200, r.text
    case_numbers = {c["case_number"] for c in r.json()["cases"]}
    assert w["other_npa"].case_number not in case_numbers
    assert case_numbers == {w["cases"]["NPA"].case_number}
