"""GET /manager/agency-profile (P2 G04): read-only contract, commission and
SLA for AGENCY_ADMIN only — plan §10's "Also for AGENCY_ADMIN", gated on the
agency.profile.read capability (core/permissions.py)."""
from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.core.security import create_access_token, hash_password
from app.main import app
from app.models.tenancy import Agency, AgencyContract, AgencyContractTerm
from app.models.user import User, UserRole
from tests._db import TEST_AGENCY_ID, TEST_BANK_ID, create_schema, make_engine, make_session_factory, test_id

BASE = "/api/v1/manager/agency-profile"


def _user(db, key, role, *, agency=TEST_AGENCY_ID, phone):
    u = User(id=test_id(f"u:{key}"), email=f"{key}@example.test", phone=phone, full_name=key.title(),
             hashed_password=hash_password("Harbour-Lights-2026"), role=role,
             bank_id=TEST_BANK_ID, agency_id=agency)
    db.add(u)
    return u


@pytest.fixture()
def w():
    engine = make_engine()
    create_schema(engine)
    Session = make_session_factory(engine, info={})
    db = Session()
    # create_schema (seed_tenant=True, the default) already inserts the Agency
    # row for TEST_AGENCY_ID — "Aravalli Field Services Pvt. Ltd." — so this
    # fills in the fields that row leaves at their default (None) rather than
    # inserting a second row with the same id (UNIQUE constraint on
    # agencies.id, agencies.bank_id).
    agency = db.query(Agency).filter(Agency.id == TEST_AGENCY_ID).one()
    agency.rbi_registration_no = "RBI/DRA/2024/0042"
    agency.hq_city = "Gurugram"
    agency.contact_name = "Asha Rao"
    agency.contact_email = "asha@example.test"
    agency.contact_phone = "9800000099"
    db.flush()
    contract = AgencyContract(bank_id=TEST_BANK_ID, agency_id=TEST_AGENCY_ID, contract_no="MTB/FCA/2026-27/014",
                              status="ACTIVE", start_date=date(2026, 4, 1), end_date=date(2027, 3, 31),
                              max_agents=25, max_placed_cases=5000, max_visits_per_month=12000,
                              sla_first_visit_days=5, recall_no_activity_days=14, recall_on_sla_breach=True,
                              recall_at_contract_end=True, performance_bonus_pct=2.5, performance_target_pct=85.0,
                              security_deposit=500000.0)
    db.add(contract)
    db.flush()
    db.add_all([
        AgencyContractTerm(bank_id=TEST_BANK_ID, agency_id=TEST_AGENCY_ID, contract_id=contract.id,
                          loan_type="PERSONAL", dpd_bucket="BUCKET_2", commission_pct=8.5,
                          fixed_fee_per_resolution=None, is_authorised=True),
        AgencyContractTerm(bank_id=TEST_BANK_ID, agency_id=TEST_AGENCY_ID, contract_id=contract.id,
                          loan_type="PERSONAL", dpd_bucket="NPA", commission_pct=12.0,
                          fixed_fee_per_resolution=500.0, is_authorised=True),
        # Not authorised — must not appear in the response.
        AgencyContractTerm(bank_id=TEST_BANK_ID, agency_id=TEST_AGENCY_ID, contract_id=contract.id,
                          loan_type="CREDIT_CARD", dpd_bucket="BUCKET_2", commission_pct=20.0,
                          fixed_fee_per_resolution=None, is_authorised=False),
    ])
    users = {
        "admin": _user(db, "admin", UserRole.AGENCY_ADMIN, phone="9800000001"),
        "mgr": _user(db, "mgr", UserRole.AGENCY_MANAGER, phone="9800000002"),
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
        yield {"db": db, "c": TestClient(app), **users}
    finally:
        app.dependency_overrides.pop(get_db, None)
        db.close()


def _h(user):
    return {"Authorization": "Bearer " + create_access_token(user.id, user.role.value, "dev-1")}


def test_agency_admin_sees_own_contract_and_authorised_terms_only(w):
    r = w["c"].get(BASE, headers=_h(w["admin"]))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["agency"]["legal_name"] == "Aravalli Field Services Pvt. Ltd."
    assert body["agency"]["contact_name"] == "Asha Rao"
    assert body["contract"]["contract_no"] == "MTB/FCA/2026-27/014"
    assert body["contract"]["max_agents"] == 25
    assert body["contract"]["security_deposit"] == 500000.0
    assert {(t["loan_type"], t["dpd_bucket"]) for t in body["commission_terms"]} == {
        ("PERSONAL", "BUCKET_2"), ("PERSONAL", "NPA"),
    }


def test_agency_manager_is_refused(w):
    r = w["c"].get(BASE, headers=_h(w["mgr"]))
    assert r.status_code == 403


def test_agency_with_no_contract_returns_null_not_an_error(w):
    db = w["db"]
    other = test_id("agency:no-contract")
    db.add(Agency(id=other, bank_id=TEST_BANK_ID, code="AGY-NEW", legal_name="New Agency Pvt. Ltd.",
                  status="PENDING", contacts=[], is_demo=True))
    db.flush()
    u = _user(db, "admin2", UserRole.AGENCY_ADMIN, agency=other, phone="9800000003")
    db.commit()

    r = w["c"].get(BASE, headers=_h(u))
    assert r.status_code == 200, r.text
    assert r.json()["contract"] is None
    assert r.json()["commission_terms"] == []
