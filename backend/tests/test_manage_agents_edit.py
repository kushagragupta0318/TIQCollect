# ─── CHANGELOG (standalone plan) ─────────────────────────────────────────────
# New file, 2026-09-28 (P2 G02). edit_agent (agent_management_service.py)
# and PATCH /manager/agents/{agent_id} (endpoints/manager_agents_admin.py).
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.core.errors import AppException
from app.core.security import create_access_token
from app.main import app
from app.models.agent import Agent
from app.models.audit_log import AuditAction, AuditLog
from app.models.tenancy import Agency, Region
from app.models.user import User, UserRole
from app.services.agent_management_service import create_agent, edit_agent
from tests._db import TEST_AGENCY_ID, TEST_BANK_ID, create_schema, make_engine, make_session_factory, test_id

OTHER_AGENCY = test_id("agency:manage-agents-edit-other")
_SQUARE = {"type": "Polygon", "coordinates": [[
    [77.0, 28.4], [77.2, 28.4], [77.2, 28.6], [77.0, 28.6], [77.0, 28.4],
]]}


@pytest.fixture()
def w():
    engine = make_engine()
    create_schema(engine)
    Session = make_session_factory(engine)
    db = Session()
    db.add(Agency(id=OTHER_AGENCY, bank_id=TEST_BANK_ID, code="AGENCY-MAE-OTHER",
                  legal_name="Malabar Field Recovery LLP", trade_name="Malabar Field Recovery",
                  status="ACTIVE", contacts=[], is_demo=True))
    db.flush()

    mgr = User(id=test_id("u:mgr:mae"), email="nilesh.kapoor@meridiantrust.example", phone="9810006001",
              full_name="Nilesh Kapoor", hashed_password="x", role=UserRole.AGENCY_MANAGER,
              bank_id=TEST_BANK_ID, agency_id=TEST_AGENCY_ID)
    mgr2 = User(id=test_id("u:mgr2:mae"), email="second.manager.mae@meridiantrust.example", phone="9810006002",
               full_name="Second Manager", hashed_password="x", role=UserRole.AGENCY_MANAGER,
               bank_id=TEST_BANK_ID, agency_id=TEST_AGENCY_ID)
    mgr_other = User(id=test_id("u:mgr:mae:other"), email="other.agency.mgr.mae@meridiantrust.example",
                     phone="9810006003", full_name="Other Agency Manager", hashed_password="x",
                     role=UserRole.AGENCY_MANAGER, bank_id=TEST_BANK_ID, agency_id=OTHER_AGENCY)
    db.add_all([mgr, mgr2, mgr_other])
    db.commit()

    region = Region(id=test_id("region:mae"), bank_id=TEST_BANK_ID, level="ZONE", code="GGN",
                    name="Gurugram", path="/ncr/haryana/gurugram/", coverage_geojson=_SQUARE)
    db.add(region)
    db.commit()

    out = create_agent(
        db, mgr, full_name="Field Agent One", email="fieldagent1.mae@meridiantrust.example", phone="9810006004",
        employee_code="MAE0001", id_card_number="MAE-ID-0001", base_latitude=28.45, base_longitude=77.07,
        territory="Sector 44, Gurugram",
    )
    db.commit()

    def override():
        s = Session()
        try:
            yield s
        finally:
            s.close()
    app.dependency_overrides[get_db] = override
    try:
        yield {"db": db, "mgr": mgr, "mgr2": mgr2, "mgr_other": mgr_other, "region": region,
              "agent_id": out["agent_id"], "agent_user_id": out["user_id"]}
    finally:
        app.dependency_overrides.pop(get_db, None)
        db.close()


def _h(user: User) -> dict:
    return {"Authorization": "Bearer " + create_access_token(user.id, user.role.value, "dev-1")}


# ── service-level ────────────────────────────────────────────────────────────
def test_edit_updates_only_the_fields_given(w):
    db = w["db"]
    out = edit_agent(db, w["mgr"], w["agent_id"], territory="Sector 56, Gurugram")
    assert out["changed"] == ["territory"]
    agent = db.get(Agent, w["agent_id"])
    assert agent.territory == "Sector 56, Gurugram"
    # Untouched fields keep their create_agent values.
    assert agent.base_latitude == 28.45 and agent.base_longitude == 77.07


def test_edit_with_no_actual_changes_is_not_an_error(w):
    """Unlike suspend on an already-suspended agent, resubmitting a form
    with nothing different is not a conflict — there is nothing to refuse."""
    out = edit_agent(w["db"], w["mgr"], w["agent_id"], territory="Sector 44, Gurugram")
    assert out["changed"] == []


def test_edit_full_name_over_200_characters_is_refused(w):
    with pytest.raises(AppException) as exc:
        edit_agent(w["db"], w["mgr"], w["agent_id"], full_name="A" * 201)
    assert exc.value.status_code == 422


def test_edit_an_unrecognised_gender_or_vehicle_type_is_refused(w):
    with pytest.raises(AppException) as exc:
        edit_agent(w["db"], w["mgr"], w["agent_id"], gender="ALIEN")
    assert exc.value.status_code == 422
    with pytest.raises(AppException) as exc:
        edit_agent(w["db"], w["mgr"], w["agent_id"], vehicle_type="HOVERCRAFT")
    assert exc.value.status_code == 422


def test_edit_max_cases_per_day_out_of_range_is_refused(w):
    with pytest.raises(AppException) as exc:
        edit_agent(w["db"], w["mgr"], w["agent_id"], max_cases_per_day=0)
    assert exc.value.status_code == 422


def test_edit_phone_to_one_already_in_use_is_refused(w):
    db = w["db"]
    other = User(id=test_id("u:other:mae"), email="taken.mae@meridiantrust.example", phone="9810006099",
                full_name="Taken Phone", hashed_password="x", role=UserRole.FIELD_AGENT,
                bank_id=TEST_BANK_ID, agency_id=TEST_AGENCY_ID)
    db.add(other)
    db.commit()
    with pytest.raises(AppException) as exc:
        edit_agent(db, w["mgr"], w["agent_id"], phone="9810006099")
    assert exc.value.status_code == 409


def test_edit_phone_to_its_own_current_value_is_not_a_conflict(w):
    out = edit_agent(w["db"], w["mgr"], w["agent_id"], phone="9810006004")
    assert out["changed"] == []


def test_edit_base_location_requires_both_coordinates_together(w):
    with pytest.raises(AppException) as exc:
        edit_agent(w["db"], w["mgr"], w["agent_id"], base_latitude=28.5)
    assert exc.value.status_code == 422


def test_edit_base_location_outside_the_current_regions_coverage_is_refused(w):
    db, mgr = w["db"], w["mgr"]
    edit_agent(db, mgr, w["agent_id"], territory_region_id=w["region"].id)
    with pytest.raises(AppException) as exc:
        edit_agent(db, mgr, w["agent_id"], base_latitude=12.9, base_longitude=77.6)   # Bangalore, not Gurugram
    assert exc.value.status_code == 422


def test_edit_moving_region_without_moving_the_pin_is_checked_against_the_existing_location(w):
    """The agent's base (28.45, 77.07) sits inside `region`'s square — this
    must succeed. A region whose square did NOT contain it would have to
    fail the same call, proving the existing location is what gets
    checked, not a fresh one the caller didn't send."""
    out = edit_agent(w["db"], w["mgr"], w["agent_id"], territory_region_id=w["region"].id)
    assert out["changed"] == ["territory_region_id"]


def test_edit_is_audited_with_before_and_after_values(w):
    db = w["db"]
    edit_agent(db, w["mgr"], w["agent_id"], territory="Sector 56, Gurugram")
    rows = db.query(AuditLog).filter(AuditLog.entity_type == "Agent", AuditLog.entity_id == w["agent_id"]).all()
    row = next(r for r in rows if r.action == AuditAction.AGENT_UPDATED)
    assert row.user_id == w["mgr"].id
    assert row.details["changed"]["territory"] == {"from": "Sector 44, Gurugram", "to": "Sector 56, Gurugram"}


def test_a_manager_cannot_edit_another_agencys_agent(w):
    with pytest.raises(AppException) as exc:
        edit_agent(w["db"], w["mgr_other"], w["agent_id"], territory="Nowhere")
    assert exc.value.status_code == 404


def test_a_peer_manager_in_the_same_agency_cannot_edit_this_agent(w):
    with pytest.raises(AppException) as exc:
        edit_agent(w["db"], w["mgr2"], w["agent_id"], territory="Nowhere")
    assert exc.value.status_code == 404


# ── HTTP ─────────────────────────────────────────────────────────────────────
def test_http_edit_succeeds_and_is_scoped_to_the_managers_agency(w):
    client = TestClient(app)
    r = client.patch(f"/api/v1/manager/agents/{w['agent_id']}", json={"territory": "Sector 56, Gurugram"},
                     headers=_h(w["mgr"]))
    assert r.status_code == 200, r.text
    assert r.json()["changed"] == ["territory"]


def test_http_edit_refused_for_another_agencys_manager(w):
    client = TestClient(app)
    r = client.patch(f"/api/v1/manager/agents/{w['agent_id']}", json={"territory": "Nowhere"},
                     headers=_h(w["mgr_other"]))
    assert r.status_code == 404


def test_http_edit_refused_for_a_field_agent_principal(w):
    client = TestClient(app)
    agent_user = User(id=test_id("u:fa:mae"), email="fa-principal.mae@meridiantrust.example", phone="9810006098",
                      full_name="A Field Agent", hashed_password="x", role=UserRole.FIELD_AGENT,
                      bank_id=TEST_BANK_ID, agency_id=TEST_AGENCY_ID)
    w["db"].add(agent_user)
    w["db"].commit()
    r = client.patch(f"/api/v1/manager/agents/{w['agent_id']}", json={"territory": "Nowhere"},
                     headers=_h(agent_user))
    assert r.status_code == 403
