"""GET /api/v1/bank/agencies/{agency_id}/profile (P3 D07): contract +
commission (shared with G04's build_agency_profile), placed volume, and the
agency's people. Bank-scoped via scope.agency_or_404 — a foreign bank's
agency id is the same uniform 404 every other single-agency route in
bank_agencies_admin.py gives."""
from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.core.security import create_access_token, hash_password
from app.main import app
from app.models.agent import Agent, AgentSpecialization, AgentStatus, AgentTier
from app.models.placement import Placement
from app.models.tenancy import Agency, AgencyContract, Bank
from app.models.user import User, UserRole
from tests._db import TEST_AGENCY_ID, TEST_BANK_ID, create_schema, make_engine, make_session_factory, test_id
from tests._placement import make_loan

BASE = f"/api/v1/bank/agencies/{TEST_AGENCY_ID}/profile"
BANK2 = test_id("bank:second")
AGENCY2 = test_id("agency:second-bank-agency")


def _user(db, key, role, *, bank, agency=None, phone):
    u = User(id=test_id(f"u:{key}"), email=f"{key}@example.test", phone=phone, full_name=key.title(),
             hashed_password=hash_password("Harbour-Lights-2026"), role=role, bank_id=bank, agency_id=agency)
    db.add(u)
    return u


@pytest.fixture()
def w():
    engine = make_engine()
    create_schema(engine)
    Session = make_session_factory(engine)
    db = Session()

    db.add(Bank(id=BANK2, code="SCND", legal_name="Second Bank Ltd.", display_name="Second Bank",
               timezone="Asia/Kolkata", brand={}, status="ACTIVE", is_demo=True))
    db.flush()
    db.add(Agency(id=AGENCY2, bank_id=BANK2, code="AGY-SCND", legal_name="A Different Agency Pvt. Ltd.",
                  status="ACTIVE", contacts=[], is_demo=True))
    db.flush()
    contract = AgencyContract(bank_id=TEST_BANK_ID, agency_id=TEST_AGENCY_ID, contract_no="MTB/D07/2026-27/001",
                              status="ACTIVE", start_date=date(2026, 4, 1), end_date=date(2027, 3, 31),
                              sla_first_visit_days=5, recall_on_sla_breach=True, recall_at_contract_end=True)
    db.add(contract)
    db.flush()
    loan = make_loan(db, 1, bank_id=TEST_BANK_ID)
    db.add(Placement(bank_id=TEST_BANK_ID, agency_id=TEST_AGENCY_ID, loan_id=loan.id,
                     contract_id=contract.id, source="MANUAL", status="ACTIVE",
                     placed_on=date(2026, 9, 1), dpd_at_placement=45, dpd_bucket_at_placement="BUCKET_2",
                     exposure_at_placement=80000.0, overdue_at_placement=5000.0))

    manager_user = _user(db, "mgr1", UserRole.AGENCY_MANAGER, bank=TEST_BANK_ID, agency=TEST_AGENCY_ID, phone="9800000001")
    db.flush()
    db.add(Agent(user_id=manager_user.id, employee_code="D07-01", id_card_number="D07-01-ID",
                base_latitude=28.6, base_longitude=77.2, tier=AgentTier.TIER_2,
                specialization=AgentSpecialization.BOTH, status=AgentStatus.ON_DUTY,
                territory="Gurugram", languages_spoken=["HINDI"], ranking_score=50.0,
                manager_user_id=manager_user.id, agency_id=TEST_AGENCY_ID, bank_id=TEST_BANK_ID))

    users = {
        "ba": _user(db, "ba", UserRole.BANK_ADMIN, bank=TEST_BANK_ID, phone="9800000002"),
        "ba2": _user(db, "ba2", UserRole.BANK_ADMIN, bank=BANK2, phone="9800000003"),
        "mgr": manager_user,
    }
    db.commit()

    def override():
        s = Session()
        try:
            yield s
        finally:
            s.close()
    app.dependency_overrides[get_db] = override
    try:
        yield {"c": TestClient(app), **users}
    finally:
        app.dependency_overrides.pop(get_db, None)
        db.close()


def _h(user):
    return {"Authorization": "Bearer " + create_access_token(user.id, user.role.value, "dev-1")}


def test_bank_admin_sees_contract_volume_and_people(w):
    r = w["c"].get(BASE, headers=_h(w["ba"]))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["contract"]["contract_no"] == "MTB/D07/2026-27/001"
    assert body["contract"]["is_current"] is True
    assert body["placed_volume"] == {"active_count": 1, "active_exposure": 80000.0, "lifetime_count": 1}
    assert body["people"]["agent_count"] == 1
    assert body["people"]["agents_on_duty"] == 1
    assert {m["full_name"] for m in body["people"]["managers"]} == {"Mgr1"}


def test_another_banks_admin_gets_a_uniform_404(w):
    r = w["c"].get(BASE, headers=_h(w["ba2"]))
    assert r.status_code == 404


def test_an_agency_manager_is_refused(w):
    r = w["c"].get(BASE, headers=_h(w["mgr"]))
    assert r.status_code == 403
