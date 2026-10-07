"""GET /api/v1/bank/regions/tree: the zone/region/state/city/branch roll-up
(loans, exposure, active-placement agencies), region-limited the same way
the placement screens are (scope_region_id), by segment. Gated
bank.regions.manage — DATA-MODEL-V2.md §5.1 defines no read-only sibling."""
from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.core.security import create_access_token, hash_password
from app.main import app
from app.models.placement import Placement
from app.models.tenancy import AgencyContract, Bank
from app.models.user import User, UserRole
from tests._db import TEST_AGENCY_ID, TEST_BANK_ID, create_schema, make_engine, make_session_factory, test_id
from tests._placement import make_loan, put_branches_in
from tests._placement import region_tree as build_region_tree

BASE = "/api/v1/bank/regions/tree"
BANK2 = test_id("bank:second-regions")


def _user(db, key, role, *, bank, scope_region_id=None, phone):
    u = User(id=test_id(f"u:{key}"), email=f"{key}@example.test", phone=phone, full_name=key.title(),
             hashed_password=hash_password("Harbour-Lights-2026"), role=role, bank_id=bank,
             scope_region_id=scope_region_id)
    db.add(u)
    return u


@pytest.fixture()
def w():
    engine = make_engine()
    create_schema(engine)
    Session = make_session_factory(engine)
    db = Session()

    region_ids = build_region_tree(db)  # NORTH (ZONE) > HR (STATE) > GGN (CITY)
    put_branches_in(db, "GGN", branch_codes=["GGN044", "GG01"])  # leaves the other 6 test branches unassigned

    loan1 = make_loan(db, 1, branch_code="GGN044")
    make_loan(db, 2, branch_code="GG01")

    contract = AgencyContract(bank_id=TEST_BANK_ID, agency_id=TEST_AGENCY_ID, contract_no="MTB/REG/2026-27/001",
                              status="ACTIVE", start_date=date(2026, 4, 1), end_date=date(2027, 3, 31))
    db.add(contract)
    db.flush()
    db.add(Placement(bank_id=TEST_BANK_ID, agency_id=TEST_AGENCY_ID, loan_id=loan1.id, contract_id=contract.id,
                     source="MANUAL", status="ACTIVE", placed_on=date(2026, 9, 1), dpd_at_placement=45,
                     dpd_bucket_at_placement="BUCKET_2", exposure_at_placement=281000.0, overdue_at_placement=5000.0))

    db.add(Bank(id=BANK2, code="SCND2", legal_name="Second Regions Bank Ltd.", display_name="Second Regions Bank",
               timezone="Asia/Kolkata", brand={}, status="ACTIVE", is_demo=True))
    db.flush()

    users = {
        "ba": _user(db, "ba", UserRole.BANK_ADMIN, bank=TEST_BANK_ID, phone="9800000011"),
        "ba2": _user(db, "ba2", UserRole.BANK_ADMIN, bank=BANK2, phone="9800000012"),
        # scope_region_id is documented as "the BANK_ANALYST region limit",
        # but bank.regions.manage (this route's capability, DATA-MODEL-V2.md
        # §5.1) is BANK_ADMIN only and a BANK_ANALYST holds no capability
        # that reaches here at all. region_limit_path() itself is role-
        # agnostic (placement_read_service reads it the same way off any
        # caller's row), so the real test of the limiting behaviour is a
        # region-scoped BANK_ADMIN, not an analyst who'd get a 403 first.
        "admin_hr": _user(db, "admin_hr", UserRole.BANK_ADMIN, bank=TEST_BANK_ID,
                          scope_region_id=region_ids["HR"], phone="9800000013"),
        "mgr": _user(db, "mgr", UserRole.AGENCY_MANAGER, bank=TEST_BANK_ID, phone="9800000014"),
    }
    users["mgr"].agency_id = TEST_AGENCY_ID
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


def test_bank_admin_sees_the_full_tree_rolled_up(w):
    r = w["c"].get(BASE, headers=_h(w["ba"]))
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["unassigned"] == {"branch_count": 6, "loan_count": 0, "exposure": 0.0}

    assert len(body["roots"]) == 1
    zone = body["roots"][0]
    assert (zone["level"], zone["code"], zone["loan_count"], zone["exposure"], zone["agency_count"]) \
        == ("ZONE", "NORTH", 2, 562000.0, 1)

    state = zone["children"][0]
    assert (state["level"], state["code"]) == ("STATE", "HR")
    assert (state["loan_count"], state["exposure"], state["agency_count"]) == (2, 562000.0, 1)

    city = state["children"][0]
    assert (city["level"], city["code"]) == ("CITY", "GGN")
    assert (city["loan_count"], city["exposure"], city["agency_count"]) == (2, 562000.0, 1)

    branches = {b["code"]: b for b in city["children"]}
    assert branches["GGN044"]["loan_count"] == 1 and branches["GGN044"]["agency_count"] == 1
    assert branches["GG01"]["loan_count"] == 1 and branches["GG01"]["agency_count"] == 0


def test_region_limited_admin_sees_only_their_subtree(w):
    r = w["c"].get(BASE, headers=_h(w["admin_hr"]))
    assert r.status_code == 200, r.text
    body = r.json()

    # Unassigned belongs to no region, so it is outside a limited caller's
    # scope entirely — suppressed, never shown as a (misleading) 0-of-6.
    assert body["unassigned"] == {"branch_count": 0, "loan_count": 0, "exposure": 0.0}

    assert len(body["roots"]) == 1
    root = body["roots"][0]
    assert (root["level"], root["code"]) == ("STATE", "HR")
    assert (root["loan_count"], root["exposure"], root["agency_count"]) == (2, 562000.0, 1)
    assert root["children"][0]["code"] == "GGN"


def test_another_banks_admin_sees_an_empty_tree(w):
    r = w["c"].get(BASE, headers=_h(w["ba2"]))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body == {"roots": [], "unassigned": {"branch_count": 0, "loan_count": 0, "exposure": 0.0}}


def test_an_agency_manager_is_refused(w):
    r = w["c"].get(BASE, headers=_h(w["mgr"]))
    assert r.status_code == 403
