# ─── CHANGELOG (standalone plan) ─────────────────────────────────────────────
# New file, 2026-09-28 (P2 G02). services/agent_management_service.create_agent
# and POST /manager/agents (endpoints/manager_agents_admin.py).
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.core.errors import AppException
from app.core.security import create_access_token
from app.main import app
from app.models.agent import Agent
from app.models.audit_log import AuditAction, AuditLog
from app.models.tenancy import Agency, Bank, Region
from app.models.user import User, UserRole
from app.services.agent_management_service import create_agent
from tests._db import TEST_AGENCY_ID, TEST_BANK_ID, create_schema, make_engine, make_session_factory, test_id

OTHER_AGENCY = test_id("agency:manage-agents-other")

# A simple square: lon 77.0..77.2, lat 28.4..28.6 (Gurugram-ish), GeoJSON
# coordinate order (lon, lat).
_SQUARE = {"type": "Polygon", "coordinates": [[
    [77.0, 28.4], [77.2, 28.4], [77.2, 28.6], [77.0, 28.6], [77.0, 28.4],
]]}


@pytest.fixture()
def w():
    engine = make_engine()
    create_schema(engine)
    Session = make_session_factory(engine)
    db = Session()
    db.add(Agency(id=OTHER_AGENCY, bank_id=TEST_BANK_ID, code="AGENCY-MA-OTHER",
                  legal_name="Konkan Recovery Services LLP", trade_name="Konkan Recovery Services",
                  status="ACTIVE", contacts=[], is_demo=True))
    db.flush()

    mgr = User(id=test_id("u:mgr:ma"), email="anita.kulkarni@meridiantrust.example", phone="9810003001",
              full_name="Anita Kulkarni", hashed_password="x", role=UserRole.AGENCY_MANAGER,
              bank_id=TEST_BANK_ID, agency_id=TEST_AGENCY_ID)
    mgr_other = User(id=test_id("u:mgr:ma:other"), email="rohan.deshmukh@meridiantrust.example",
                     phone="9810003002", full_name="Rohan Deshmukh", hashed_password="x",
                     role=UserRole.AGENCY_MANAGER, bank_id=TEST_BANK_ID, agency_id=OTHER_AGENCY)
    field_agent = User(id=test_id("u:agent:ma"), email="preexisting.agent@meridiantrust.example",
                       phone="9810003003", full_name="Preexisting Agent", hashed_password="x",
                       role=UserRole.FIELD_AGENT, bank_id=TEST_BANK_ID, agency_id=TEST_AGENCY_ID)
    db.add_all([mgr, mgr_other, field_agent])
    db.flush()

    region = Region(id=test_id("region:ma"), bank_id=TEST_BANK_ID, level="ZONE", code="GGN",
                    name="Gurugram", path="/ncr/haryana/gurugram/", coverage_geojson=_SQUARE)
    db.add(region)
    db.commit()

    def override():
        s = Session()
        try:
            yield s
        finally:
            s.close()
    app.dependency_overrides[get_db] = override
    try:
        yield {"db": db, "mgr": mgr, "mgr_other": mgr_other, "field_agent": field_agent, "region": region}
    finally:
        app.dependency_overrides.pop(get_db, None)
        db.close()


def _valid_body(**overrides) -> dict:
    body = {
        "full_name": "Kavita Menon", "email": "kavita.menon.agent@meridiantrust.example",
        "phone": "9810004001", "employee_code": "MER0099", "id_card_number": "MER-ID-0099",
        "base_latitude": 28.45, "base_longitude": 77.07, "territory": "Sector 44, Gurugram",
    }
    body.update(overrides)
    return body


def _h(user: User) -> dict:
    return {"Authorization": "Bearer " + create_access_token(user.id, user.role.value, "dev-1")}


# ── service-level ────────────────────────────────────────────────────────────
def test_create_agent_creates_a_user_and_an_agent_row_bound_to_the_managers_agency(w):
    db, mgr = w["db"], w["mgr"]
    out = create_agent(db, mgr, **_valid_body())

    agent = db.get(Agent, out["agent_id"])
    user = db.get(User, out["user_id"])
    assert agent is not None and user is not None
    assert user.role == UserRole.FIELD_AGENT
    assert agent.bank_id == mgr.bank_id and agent.agency_id == mgr.agency_id
    assert user.bank_id == mgr.bank_id and user.agency_id == mgr.agency_id
    assert agent.manager_user_id == mgr.id
    assert user.must_change_password is True
    assert user.is_active is True


def test_the_agents_own_password_is_never_derivable_from_anything_returned(w):
    out = create_agent(w["db"], w["mgr"], **_valid_body())
    assert "password" not in out and "hashed_password" not in out


def test_an_audit_row_is_staged_for_the_creating_manager(w):
    db, mgr = w["db"], w["mgr"]
    out = create_agent(db, mgr, **_valid_body())
    row = db.query(AuditLog).filter(AuditLog.entity_type == "Agent", AuditLog.entity_id == out["agent_id"]).one()
    assert row.action == AuditAction.USER_CREATED
    assert row.user_id == mgr.id
    assert row.details["role"] == "FIELD_AGENT"
    assert row.details["via"] == "manager_created"


def test_an_agency_admin_can_also_create_an_agent(w):
    db = w["db"]
    admin = User(id=test_id("u:admin:ma"), email="admin.ma@meridiantrust.example", phone="9810003009",
                full_name="Agency Admin", hashed_password="x", role=UserRole.AGENCY_ADMIN,
                bank_id=TEST_BANK_ID, agency_id=TEST_AGENCY_ID)
    db.add(admin)
    db.commit()
    out = create_agent(db, admin, **_valid_body(email="admin-created@meridiantrust.example",
                                                  phone="9810004002", employee_code="MER0100"))
    assert db.get(Agent, out["agent_id"]).manager_user_id == admin.id


@pytest.mark.parametrize("role", [UserRole.FIELD_AGENT, UserRole.BANK_ADMIN, UserRole.BANK_ANALYST])
def test_a_non_manager_role_is_refused_at_the_service_level(w, role):
    principal = User(id=test_id(f"u:notmgr:{role.value}"), email=f"{role.value.lower()}@meridiantrust.example",
                     phone="9810003008", full_name="Not A Manager", hashed_password="x", role=role,
                     bank_id=TEST_BANK_ID, agency_id=TEST_AGENCY_ID if role == UserRole.FIELD_AGENT else None)
    w["db"].add(principal)
    w["db"].commit()
    with pytest.raises(AppException) as exc:
        create_agent(w["db"], principal, **_valid_body())
    assert exc.value.status_code == 403


def test_duplicate_email_or_phone_is_refused(w):
    with pytest.raises(AppException) as exc:
        create_agent(w["db"], w["mgr"], **_valid_body(email=w["field_agent"].email))
    assert exc.value.status_code == 409
    with pytest.raises(AppException) as exc:
        create_agent(w["db"], w["mgr"], **_valid_body(phone=w["field_agent"].phone))
    assert exc.value.status_code == 409


def test_duplicate_employee_code_in_the_same_agency_is_refused(w):
    db, mgr = w["db"], w["mgr"]
    create_agent(db, mgr, **_valid_body())
    # id_card_number changed too, so this collides on employee_code alone —
    # id_card_number is unique per BANK, not per agency, and reusing the
    # default here would raise on the wrong constraint.
    with pytest.raises(AppException) as exc:
        create_agent(db, mgr, **_valid_body(email="second@meridiantrust.example", phone="9810004003",
                                            id_card_number="MER-ID-0199"))
    assert exc.value.status_code == 409


def test_a_full_name_over_200_characters_is_refused(w):
    """Matches User.full_name's own column width (String(200)) — Postgres
    enforces it and raises a raw DB error on INSERT; SQLite does not, which
    is exactly the shape of gap a sqlite-only test suite would miss."""
    with pytest.raises(AppException) as exc:
        create_agent(w["db"], w["mgr"], **_valid_body(full_name="A" * 201))
    assert exc.value.status_code == 422


def test_a_territory_over_100_characters_is_refused(w):
    with pytest.raises(AppException) as exc:
        create_agent(w["db"], w["mgr"], **_valid_body(territory="A" * 101))
    assert exc.value.status_code == 422


def test_a_malformed_territory_region_id_in_the_body_is_a_422_not_a_500(w):
    """core/ids.UUIDStr on the request model — a malformed id in a BODY
    field is caught by pydantic before create_agent's own DB lookup ever
    runs (the same boundary rule UUIDPath applies to path parameters)."""
    client = TestClient(app)
    r = client.post("/api/v1/manager/agents",
                    json=_valid_body(territory_region_id="not-a-uuid"), headers=_h(w["mgr"]))
    assert r.status_code == 422


def test_base_location_outside_the_named_regions_coverage_is_refused(w):
    with pytest.raises(AppException) as exc:
        create_agent(w["db"], w["mgr"], **_valid_body(
            territory_region_id=w["region"].id, base_latitude=12.9, base_longitude=77.6))   # Bangalore, not Gurugram
    assert exc.value.status_code == 422


def test_base_location_inside_the_named_regions_coverage_is_accepted(w):
    out = create_agent(w["db"], w["mgr"], **_valid_body(territory_region_id=w["region"].id))
    assert out["agent_id"]


def test_a_region_with_no_coverage_geojson_is_not_checked(w):
    db, mgr = w["db"], w["mgr"]
    open_region = Region(id=test_id("region:ma:open"), bank_id=TEST_BANK_ID, level="ZONE", code="OPEN",
                         name="Open Territory", path="/x/", coverage_geojson=None)
    db.add(open_region)
    db.commit()
    out = create_agent(db, mgr, **_valid_body(territory_region_id=open_region.id,
                                               base_latitude=12.9, base_longitude=77.6))
    assert out["agent_id"]


def test_a_region_from_another_bank_reads_the_same_404_as_a_missing_one(w):
    db = w["db"]
    other_bank_id = test_id("bank:ma-other")
    db.add(Bank(id=other_bank_id, code="MAOTHER", legal_name="Konkan Trust Bank Ltd.",
               display_name="Konkan Trust Bank", timezone="Asia/Kolkata", brand={},
               status="ACTIVE", is_demo=True))
    db.flush()
    other_bank_region = Region(id=test_id("region:ma:otherbank"), bank_id=other_bank_id,
                               level="ZONE", code="X", name="Elsewhere", path="/x/", coverage_geojson=None)
    db.add(other_bank_region)
    db.commit()
    with pytest.raises(AppException) as exc_foreign:
        create_agent(db, w["mgr"], **_valid_body(territory_region_id=other_bank_region.id))
    with pytest.raises(AppException) as exc_missing:
        create_agent(db, w["mgr"], **_valid_body(territory_region_id=test_id("region:nope")))
    assert exc_foreign.value.status_code == exc_missing.value.status_code == 404
    assert exc_foreign.value.detail == exc_missing.value.detail


# ── HTTP / capability gate / cross-tenant ────────────────────────────────────
def test_http_create_succeeds_for_a_manager_and_is_scoped_to_their_agency(w):
    client = TestClient(app)
    r = client.post("/api/v1/manager/agents", json=_valid_body(), headers=_h(w["mgr"]))
    assert r.status_code == 200, r.text
    agent_id = r.json()["agent_id"]
    w["db"].expire_all()
    assert w["db"].get(Agent, agent_id).agency_id == TEST_AGENCY_ID


def test_http_create_is_refused_for_a_field_agent_principal(w):
    client = TestClient(app)
    r = client.post("/api/v1/manager/agents", json=_valid_body(), headers=_h(w["field_agent"]))
    assert r.status_code == 403


def test_a_manager_cannot_smuggle_a_different_agency_into_the_request_body(w):
    """The request schema has no agency_id/bank_id field at all — this proves
    it structurally, not just by omission: even a client that adds unknown
    fields cannot move where the new agent lands, because create_agent never
    reads the body for tenancy, only `manager`."""
    client = TestClient(app)
    body = _valid_body(agency_id=OTHER_AGENCY, bank_id=test_id("bank:nope"))
    r = client.post("/api/v1/manager/agents", json=body, headers=_h(w["mgr"]))
    assert r.status_code == 200, r.text
    w["db"].expire_all()
    assert w["db"].get(Agent, r.json()["agent_id"]).agency_id == TEST_AGENCY_ID


def test_two_managers_in_different_agencies_get_independently_scoped_agents(w):
    client = TestClient(app)
    mine = client.post("/api/v1/manager/agents", json=_valid_body(), headers=_h(w["mgr"]))
    theirs = client.post("/api/v1/manager/agents",
                         json=_valid_body(email="other-agency@meridiantrust.example", phone="9810004004",
                                          employee_code="OTH0001", id_card_number="OTH-ID-0001"),
                         headers=_h(w["mgr_other"]))
    assert mine.status_code == theirs.status_code == 200
    w["db"].expire_all()
    assert w["db"].get(Agent, mine.json()["agent_id"]).agency_id == TEST_AGENCY_ID
    assert w["db"].get(Agent, theirs.json()["agent_id"]).agency_id == OTHER_AGENCY
