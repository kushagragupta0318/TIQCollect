# ─── CHANGELOG (standalone plan) ─────────────────────────────────────────────
# New file, 2026-10-07 (K01 Admin → Settings). services/bank/
# bank_settings_service.py and GET/PATCH /bank/settings
# (endpoints/bank_settings.py, schemas/bank_settings.py).
#
# Behaviour, not source text: a bank admin reads and writes their own bank's
# settings; a non-admin is refused; another bank's admin never sees or touches
# this bank's values; the change lands as a BANK_SETTINGS_UPDATED audit row;
# the contact-window order is enforced; read-only values come from config.
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.database import get_db
from app.main import app
from app.models.audit_log import AuditAction, AuditLog
from app.models.loan import dpd_bucket_for
from app.models.tenancy import Bank
from app.models.user import User, UserRole
from app.core.security import create_access_token
from tests._db import TEST_BANK_ID, create_schema, make_engine, make_session_factory, test_id

OTHER_BANK_ID = test_id("bank:settings-other")


@pytest.fixture()
def w():
    engine = make_engine()
    create_schema(engine)
    Session = make_session_factory(engine)
    db = Session()

    bank_admin = User(id=test_id("u:settings:admin"), email="asha.nair@meridiantrust.example",
                      phone="9810007001", full_name="Asha Nair", hashed_password="x",
                      role=UserRole.BANK_ADMIN, bank_id=TEST_BANK_ID)
    analyst = User(id=test_id("u:settings:analyst"), email="rahul.das@meridiantrust.example",
                   phone="9810007002", full_name="Rahul Das", hashed_password="x",
                   role=UserRole.BANK_ANALYST, bank_id=TEST_BANK_ID)
    other_admin = User(id=test_id("u:settings:other-admin"), email="nisha.iyer@otherbank.example",
                       phone="9810007003", full_name="Nisha Iyer", hashed_password="x",
                       role=UserRole.BANK_ADMIN, bank_id=OTHER_BANK_ID)
    db.add(Bank(id=OTHER_BANK_ID, code="OTB2", legal_name="Kumaon Finance Ltd.",
                display_name="Kumaon Finance", timezone="Asia/Kolkata", brand={}))
    db.add_all([bank_admin, analyst, other_admin])
    db.commit()

    def override():
        s = Session()
        try:
            yield s
        finally:
            s.close()
    app.dependency_overrides[get_db] = override
    client = TestClient(app)
    try:
        yield {"db": db, "client": client, "bank_admin": bank_admin, "analyst": analyst,
               "other_admin": other_admin}
    finally:
        app.dependency_overrides.pop(get_db, None)
        db.close()


def _h(user: User) -> dict:
    return {"Authorization": "Bearer " + create_access_token(user.id, user.role.value, "dev-1")}


# ── read ──────────────────────────────────────────────────────────────────
def test_bank_admin_reads_their_own_settings_with_engine_constants(w):
    r = w["client"].get("/api/v1/bank/settings", headers=_h(w["bank_admin"]))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["bank"]["id"] == TEST_BANK_ID
    # defaults, no override stored yet
    assert body["contact_hours"]["start"] == settings.CONTACT_HOUR_START
    assert body["contact_hours"]["is_override"] is False
    assert body["geofence_metres"]["value"] == settings.GEO_FENCE_METRES
    # engine constants are surfaced from config, read-only
    keyed = {c["key"]: c for c in body["engine_constants"]}
    assert keyed["allocator_exploration_rate"]["value"] == pytest.approx(settings.ALLOCATOR_EXPLORATION_RATE)
    assert keyed["location_retention_days"]["value"] == float(settings.LOCATION_RETENTION_DAYS)
    assert all(c["editable"] is False for c in body["engine_constants"])


def test_dpd_buckets_are_derived_from_the_one_rule(w):
    body = w["client"].get("/api/v1/bank/settings", headers=_h(w["bank_admin"])).json()
    buckets = {b["bucket"]: b for b in body["dpd_buckets"]}
    # every row agrees with dpd_bucket_for at its own boundaries
    for row in body["dpd_buckets"]:
        assert dpd_bucket_for(row["min_dpd"]).value == row["bucket"]
        if row["max_dpd"] is not None:
            assert dpd_bucket_for(row["max_dpd"]).value == row["bucket"]
    assert buckets["NPA"]["max_dpd"] is None          # open-ended top bucket
    assert buckets["CURRENT"]["min_dpd"] == 0


# ── write ─────────────────────────────────────────────────────────────────
def test_bank_admin_writes_overrides_and_an_audit_row_is_written(w):
    r = w["client"].patch("/api/v1/bank/settings", headers=_h(w["bank_admin"]),
                          json={"geofence_metres": 150, "contact_hour_start": 9, "contact_hour_end": 18})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["geofence_metres"]["value"] == 150
    assert body["geofence_metres"]["is_override"] is True
    assert body["contact_hours"]["start"] == 9 and body["contact_hours"]["end"] == 18

    # persisted on the bank's brand bag, not a new table
    w["db"].expire_all()
    bank = w["db"].get(Bank, TEST_BANK_ID)
    assert bank.brand["ops"]["geofence_metres"] == 150

    rows = w["db"].query(AuditLog).filter(AuditLog.action == AuditAction.BANK_SETTINGS_UPDATED).all()
    assert len(rows) == 1
    assert rows[0].user_id == w["bank_admin"].id
    assert rows[0].bank_id == TEST_BANK_ID
    assert rows[0].details["changed"]["geofence_metres"] == 150


def test_partial_patch_leaves_other_fields_untouched(w):
    c, h = w["client"], _h(w["bank_admin"])
    c.patch("/api/v1/bank/settings", headers=h, json={"geofence_metres": 200})
    body = c.patch("/api/v1/bank/settings", headers=h, json={"sla_first_visit_days": 10}).json()
    assert body["geofence_metres"]["value"] == 200     # survived the second patch
    assert body["sla_first_visit_days"]["value"] == 10


def test_contact_window_must_be_ordered(w):
    r = w["client"].patch("/api/v1/bank/settings", headers=_h(w["bank_admin"]),
                          json={"contact_hour_start": 19, "contact_hour_end": 8})
    assert r.status_code == 422
    assert r.json()["code"] == "VALIDATION_ERROR"
    # nothing was written
    assert w["db"].query(AuditLog).filter(AuditLog.action == AuditAction.BANK_SETTINGS_UPDATED).count() == 0


def test_out_of_range_geofence_is_refused_by_the_schema(w):
    r = w["client"].patch("/api/v1/bank/settings", headers=_h(w["bank_admin"]),
                          json={"geofence_metres": 999999})
    assert r.status_code == 422


# ── capability + tenant isolation ───────────────────────────────────────────
def test_non_admin_is_refused_read_and_write(w):
    h = _h(w["analyst"])
    assert w["client"].get("/api/v1/bank/settings", headers=h).status_code == 403
    assert w["client"].patch("/api/v1/bank/settings", headers=h,
                             json={"geofence_metres": 120}).status_code == 403


def test_another_banks_admin_never_touches_this_banks_settings(w):
    # the other bank's admin writes against their OWN tenant
    r = w["client"].patch("/api/v1/bank/settings", headers=_h(w["other_admin"]),
                          json={"geofence_metres": 300})
    assert r.status_code == 200
    assert r.json()["bank"]["id"] == OTHER_BANK_ID

    # this bank's row is unchanged — isolation holds
    w["db"].expire_all()
    assert (w["db"].get(Bank, TEST_BANK_ID).brand or {}).get("ops") in (None, {})
    other = w["db"].get(Bank, OTHER_BANK_ID)
    assert other.brand["ops"]["geofence_metres"] == 300
