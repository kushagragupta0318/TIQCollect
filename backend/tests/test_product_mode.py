# ─── CHANGELOG (standalone plan) ─────────────────────────────────────────────
# New file, 2026-09-28 (A15). Covers:
#   - PRODUCT_MODE: its four validation tests were removed with the setting
#     on 2026-09-28 (docs/adr/0009). The file keeps its name because the
#     permission seed (v2_0008, frozen) cites it for the SERVICE role tests.
#   - the field-ops preservation claim originally here (a live mount check
#     plus a structural PRODUCT_MODE-conditional tripwire) is WITHDRAWN,
#     2026-09-28: /api/field-ops/* is now actually deleted — lead-structure's
#     Wave 1 D4 (0c32082) merged into this branch's own lineage — so the
#     two tests that asserted preservation were dropped, and so was
#     service.field_ops.read (permissions.py), which named a route that no
#     longer exists. This entry was written BEFORE that merge and predicted
#     it correctly; kept rather than rewritten so the prediction and its
#     confirmation are both on record.
#   - SERVICE role accounts replace manager-password service logins: a
#     dedicated User row with role=SERVICE logs in through the ORDINARY
#     /auth/login (no new credential scheme, no schema change — see the
#     header note in core/config.py), and the resulting token can reach
#     service.manager_api.read but CANNOT reach anything ManagerOnly gates.
#     That second half is the actual security property "replaces
#     manager-password service logins" is asking for: a service account is
#     no longer a full manager account by another name.
#
#   NOT done here, recorded rather than silently deferred: wiring
#   require_perm("service.manager_api.read") into manager.py's own 48 routes
#   so a SERVICE principal can actually reach live data through them.
#   manager.py has no service layer (known issue 6) and a blanket mechanical
#   swap across it is a separate, larger, separately-reviewable change — this
#   task delivers the MECHANISM (the role, its capabilities, its login path)
#   and proves it works in isolation; wiring individual manager.py routes to
#   accept it is follow-up work, not silently skipped.
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.dependencies import ManagerOnly, get_current_user
from app.core.database import get_db
from app.core.permissions import require_perm
from app.core.security import verify_password
from app.models.user import User, UserRole
from app.services import auth_service
from tests._db import DEFAULT_TENANT, create_schema, make_engine, make_session_factory


# ── SERVICE role accounts ────────────────────────────────────────────────────
@pytest.fixture()
def db():
    engine = make_engine()
    create_schema(engine)
    Session = make_session_factory(engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()


def _make_service_user(db, *, password: str = "a-strong-service-password-1") -> User:
    from app.core.security import hash_password
    user = User(
        email="command-center@meridiantrust.example", phone="9876500001",
        full_name="Command Center (service account)", hashed_password=hash_password(password),
        role=UserRole.SERVICE, bank_id=DEFAULT_TENANT["bank_id"], is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


class _Req:
    """A minimal stand-in for fastapi.Request, matching what auth_service._log
    and _enforce_device_binding actually read (headers, client)."""
    headers: dict = {}
    client = None


def test_a_service_account_logs_in_through_the_ordinary_password_flow(db):
    """No new credential scheme: role=SERVICE is not special-cased anywhere in
    login() — verified by reading it (no role check exists in the function at
    all) and now proven by actually logging one in."""
    password = "a-strong-service-password-1"
    user = _make_service_user(db, password=password)
    result = auth_service.login(db, user.email, password, "svc-device-1", _Req())
    assert result["role"] == "SERVICE"
    assert result["access_token"]


def test_a_wrong_password_still_fails_for_a_service_account(db):
    user = _make_service_user(db)
    with pytest.raises(Exception):   # HTTPException, 401 — no special-casing either way
        auth_service.login(db, user.email, "wrong-password", "svc-device-1", _Req())


@pytest.fixture()
def service_capability_app(db):
    """Two routes: one gated the way Command Center's read endpoints would be
    (require_perm), one gated the way a human-only manager endpoint is today
    (ManagerOnly) — proving a SERVICE principal reaches the first and not the
    second, which is the actual point of this task."""
    app = FastAPI()

    @app.get("/manager/read-only-thing")
    def read_only(current_user: User = require_perm("service.manager_api.read")):
        return {"ok": True, "role": current_user.role.value}

    @app.get("/manager/human-only-thing")
    def human_only(current_user: ManagerOnly):
        return {"ok": True}

    app.dependency_overrides[get_db] = lambda: db
    yield app, db


def test_a_service_principal_reaches_its_own_capability_but_not_manageronly(service_capability_app):
    app, db = service_capability_app
    user = _make_service_user(db)
    app.dependency_overrides[get_current_user] = lambda: user
    client = TestClient(app)

    ok = client.get("/manager/read-only-thing")
    assert ok.status_code == 200 and ok.json()["role"] == "SERVICE"

    refused = client.get("/manager/human-only-thing")
    assert refused.status_code == 403, (
        "a SERVICE account must NOT be able to reach a route gated for real "
        "managers — that is exactly the over-privilege this task closes")


def test_a_real_manager_cannot_reach_the_service_only_capability(service_capability_app):
    """The other direction: service.manager_api.read is not accidentally
    granted to AGENCY_MANAGER/AGENCY_ADMIN too (checked mechanically against
    the doc already in test_permissions.py; this is the live-route version)."""
    app, db = service_capability_app
    manager = User(
        email="manager@meridiantrust.example", phone="9876500002", full_name="A Manager",
        hashed_password="x", role=UserRole.AGENCY_MANAGER, bank_id=DEFAULT_TENANT["bank_id"],
        agency_id=DEFAULT_TENANT["agency_id"],
    )
    db.add(manager)
    db.commit()
    app.dependency_overrides[get_current_user] = lambda: manager
    client = TestClient(app)

    assert client.get("/manager/read-only-thing").status_code == 403
    assert client.get("/manager/human-only-thing").status_code == 200
