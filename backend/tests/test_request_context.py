# ─── CHANGELOG (standalone plan) ─────────────────────────────────────────────
# New file, 2026-09-28 (A02). Covers app/core/request_context.py and the
# `perms` claim added to core/security.create_access_token /
# services/auth_service._open_session.
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.core.dependencies import _get_token_payload, get_current_user
from app.core.permissions import CAPABILITIES, role_capabilities
from app.core.request_context import CurrentContext, RequestContext, get_request_context
from app.core.security import create_access_token, decode_token
from app.models.user import User, UserRole
from app.services import auth_service
from tests._db import DEFAULT_TENANT, create_schema, make_engine, make_session_factory


# ── create_access_token's perms claim ────────────────────────────────────────
def test_perms_omitted_when_not_supplied_not_an_empty_list():
    """"Nobody computed this" must stay distinguishable from "this role holds
    nothing" — an empty list would read as the latter."""
    token = create_access_token("u1", "AGENCY_MANAGER", "dev1")
    assert "perms" not in decode_token(token)


def test_perms_claim_matches_the_registry_for_the_given_role():
    role = UserRole.BANK_TECHOPS
    token = create_access_token("u1", role.value, "dev1", perms=sorted(role_capabilities(role)))
    assert decode_token(token)["perms"] == sorted(role_capabilities(role))


def test_perms_claim_is_not_recomputed_by_create_access_token_itself():
    """security.py must not import the capability registry (its own
    docstring's promise) — proven by passing a deliberately WRONG list and
    confirming it is trusted verbatim, not silently corrected."""
    token = create_access_token("u1", "FIELD_AGENT", "dev1", perms=["not.a.real.capability"])
    assert decode_token(token)["perms"] == ["not.a.real.capability"]


# ── auth_service._open_session actually embeds it ────────────────────────────
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


class _Req:
    headers: dict = {}
    client = None


def _make_user(db, role: UserRole, **extra) -> User:
    from app.core.security import hash_password
    user = User(
        email=f"{role.value.lower()}-{id(extra)}@meridiantrust.example", phone="9876500003",
        full_name=f"Test {role.value}", hashed_password=hash_password("a-strong-password-1"),
        role=role, bank_id=DEFAULT_TENANT["bank_id"], **extra,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def test_login_mints_a_token_whose_perms_claim_matches_the_users_role(db):
    user = _make_user(db, UserRole.BANK_ADMIN)
    result = auth_service.login(db, user.email, "a-strong-password-1", "dev1", _Req())
    claims = decode_token(result["access_token"])
    assert claims["perms"] == sorted(role_capabilities(UserRole.BANK_ADMIN))
    assert "ml.promote" not in claims["perms"]      # BANK_ADMIN does not hold it (known issue 11)


# ── RequestContext derivation ────────────────────────────────────────────────
def test_get_request_context_reads_role_and_tenant_from_the_db_user_not_the_token():
    """The token's OWN claims are deliberately wrong here; get_request_context
    must use current_user (the DB row), never trust the payload for these."""
    user = User(id="u1", role=UserRole.AGENCY_ADMIN, bank_id="bank-real", agency_id="agency-real",
               email="x@example.com", phone="9", full_name="X", hashed_password="x")
    stale_payload = {"sub": "u1", "role": "FIELD_AGENT", "bank_id": "bank-stale",
                     "agency_id": "agency-stale", "sid": "sid-1", "perms": ["stale.claim"]}
    ctx = get_request_context(user, stale_payload)
    assert ctx.user_id == "u1"
    assert ctx.role is UserRole.AGENCY_ADMIN
    assert ctx.bank_id == "bank-real" and ctx.agency_id == "agency-real"
    assert ctx.sid == "sid-1"                                    # sid has nowhere else to come from
    assert ctx.perms == role_capabilities(UserRole.AGENCY_ADMIN)  # recomputed, not "stale.claim"
    assert "stale.claim" not in ctx.perms


def test_has_matches_the_registry():
    ctx = RequestContext(user_id="u1", role=UserRole.FIELD_AGENT, bank_id="b", agency_id=None,
                         sid=None, perms=role_capabilities(UserRole.FIELD_AGENT))
    assert ctx.has("field.sos")
    assert not ctx.has("ml.promote")


def test_a_request_context_with_no_sid_is_allowed():
    """A token minted directly (no login session — tests, a future service
    account) has no sid; RequestContext must not require one."""
    ctx = get_request_context(
        User(id="u2", role=UserRole.SERVICE, bank_id="b", email="s@example.com", phone="9",
            full_name="S", hashed_password="x"),
        {"sub": "u2"},
    )
    assert ctx.sid is None
    assert ctx.perms == role_capabilities(UserRole.SERVICE)


# ── through a real request ───────────────────────────────────────────────────
def test_current_context_end_to_end_through_http(db):
    """CurrentContext resolves current_user AND the raw token payload as two
    INDEPENDENT sub-dependencies (get_request_context's own signature) — in
    production both trace back to the same request's bearer token and FastAPI
    dedupes the underlying decode within one request, but a test that only
    overrides get_current_user does not also bypass _get_token_payload (it
    still demands a real Authorization header). Both are overridden here for
    exactly that reason — found by running this test, not by reading the
    dependency graph."""
    user = _make_user(db, UserRole.AGENCY_MANAGER, agency_id=DEFAULT_TENANT["agency_id"])
    app = FastAPI()

    @app.get("/whoami")
    def whoami(ctx: CurrentContext):
        return {"user_id": ctx.user_id, "role": ctx.role.value, "bank_id": ctx.bank_id,
                "agency_id": ctx.agency_id, "sid": ctx.sid, "has_team_read": ctx.has("team.read"),
                "has_ml_promote": ctx.has("ml.promote")}

    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[_get_token_payload] = lambda: {"sub": user.id, "type": "access",
                                                             "sid": "sid-e2e-1"}
    client = TestClient(app)

    r = client.get("/whoami")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body == {
        "user_id": user.id, "role": "AGENCY_MANAGER", "bank_id": DEFAULT_TENANT["bank_id"],
        "agency_id": DEFAULT_TENANT["agency_id"], "sid": "sid-e2e-1",
        "has_team_read": True, "has_ml_promote": False,
    }
