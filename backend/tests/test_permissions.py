# ─── CHANGELOG (standalone plan) ─────────────────────────────────────────────
# New file, 2026-09-28 (A01). Covers app/core/permissions.py: the capability
# registry and the require_perm() dependency.
#
# The registry itself is checked against docs/DATA-MODEL-V2.md §5.1/§5.2 at
# TEST TIME, not just once by hand — a structural tripwire in the style this
# repo already uses for the DPD-bucket rule and the id-validator sweep: the
# doc is re-parsed here, so a future edit to either the doc or the registry
# that drifts from the other fails a test instead of being trusted on sight.
from __future__ import annotations

import pathlib
import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core import permissions as perm_mod
from app.core.dependencies import get_current_user
from app.core.database import get_db
from app.core.permissions import (
    ALL_ROLES, CAPABILITIES, ROLE_CAPABILITIES, has_capability, require_perm,
    role_capabilities, seed_permission_tables,
)
from app.models.audit_log import AuditAction, AuditLog
from app.models.user import User, UserRole
from tests._db import DEFAULT_TENANT, create_schema, make_engine, make_session_factory

DOC_PATH = pathlib.Path(__file__).resolve().parents[2] / "docs" / "DATA-MODEL-V2.md"
_ROLE_ORDER = ["PA", "BA", "BN", "BT", "AA", "AM", "FA", "SV"]
_LETTER_TO_ROLE = {
    "PA": UserRole.PLATFORM_ADMIN, "BA": UserRole.BANK_ADMIN, "BN": UserRole.BANK_ANALYST,
    "BT": UserRole.BANK_TECHOPS, "AA": UserRole.AGENCY_ADMIN, "AM": UserRole.AGENCY_MANAGER,
    "FA": UserRole.FIELD_AGENT, "SV": UserRole.SERVICE,
}


def _doc_catalog_codes() -> list[str]:
    doc = DOC_PATH.read_text(encoding="utf-8")
    section = doc[doc.index("### 5.1 Capability catalog"):doc.index("### 5.2 Role")]
    return re.findall(r"^\| `([a-zA-Z_.]+)`", section, re.MULTILINE)


def _doc_role_grants() -> dict[str, frozenset[UserRole]]:
    """code -> the set of roles §5.2 grants it to, at ANY scope (Y/T/S)."""
    doc = DOC_PATH.read_text(encoding="utf-8")
    section = doc[doc.index("### 5.2 Role"):doc.index("**Consequences worth stating.**")]
    all_codes = _doc_catalog_codes()
    grants: dict[str, set[str]] = {}
    for line in section.splitlines():
        if not (line.startswith("| `") or line.startswith("| self")):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) != 9:
            continue
        label_cell, role_cells = cells[0], cells[1:9]
        granted_letters = [_ROLE_ORDER[i] for i, v in enumerate(role_cells) if v in ("Y", "T", "S")]
        for code in re.findall(r"`([a-zA-Z_.*]+)`", label_cell):
            expanded = ([c for c in all_codes if c.split(".", 1)[0] == code[:-2]]
                        if code.endswith(".*") else [code])
            for c in expanded:
                grants.setdefault(c, set()).update(granted_letters)
    return {code: frozenset(_LETTER_TO_ROLE[l] for l in letters) for code, letters in grants.items()}


# ── the registry against the doc, mechanically ───────────────────────────────
def test_every_documented_capability_is_registered_and_nothing_extra():
    doc_codes = _doc_catalog_codes()
    assert len(doc_codes) == len(set(doc_codes)), "the DOC has a duplicate code"
    assert set(doc_codes) == set(CAPABILITIES)


def test_registry_role_grants_match_the_doc_matrix_exactly():
    doc_grants = _doc_role_grants()
    assert set(doc_grants) == set(CAPABILITIES), "every catalog code must appear in the §5.2 matrix"
    mismatches = {code: (sorted(r.value for r in doc_grants[code]),
                         sorted(r.value for r in CAPABILITIES[code].roles))
                 for code in doc_grants if doc_grants[code] != CAPABILITIES[code].roles}
    assert mismatches == {}


def test_no_duplicate_capability_codes():
    # _CATALOG is a tuple; CAPABILITIES is built from it via a dict comprehension
    # that would silently keep only the LAST of a duplicate — the module raises
    # RuntimeError at import time if that ever happens (see its own guard), so
    # reaching this line at all is half the proof; the count check is the rest.
    assert len(perm_mod._CATALOG) == len(CAPABILITIES)


# ── has_capability / role_capabilities ───────────────────────────────────────
def test_has_capability_matches_role_capabilities_for_every_role_and_code():
    for role in ALL_ROLES:
        held = role_capabilities(role)
        assert held == ROLE_CAPABILITIES[role]
        for code in CAPABILITIES:
            assert has_capability(role, code) == (code in held)


def test_an_unknown_code_fails_closed_not_silently():
    with pytest.raises(KeyError):
        has_capability(UserRole.BANK_ADMIN, "not.a.real.capability")


def test_require_perm_fails_at_build_time_for_an_undeclared_code():
    """Wiring a route to a typo'd capability must break when the app starts,
    not the first time someone with the "right" role happens to call it."""
    with pytest.raises(KeyError):
        require_perm("nope.not.declared")


# ── spot checks a reader can verify by eye against §5.2 ──────────────────────
def test_field_agent_holds_only_self_and_field_and_copilot():
    held = role_capabilities(UserRole.FIELD_AGENT)
    assert held == {
        "self.profile", "self.password.change", "self.sessions.manage", "self.mfa.manage",
        "copilot.use",
        "field.cases.read", "field.visit.record", "field.payment.collect", "field.ptp.manage",
        "field.call.log", "field.location.report", "field.leave.request", "field.sos",
    }


def test_service_role_holds_only_the_two_service_capabilities_and_bank_feed_upload():
    assert role_capabilities(UserRole.SERVICE) == {
        "bank_feed.upload", "service.field_ops.read", "service.manager_api.read",
    }


def test_only_bank_techops_can_promote_a_model_closing_known_issue_11():
    """The live system's every-manager-can-promote defect (known issue 11):
    ml.promote must belong to BANK_TECHOPS and nobody else."""
    for role in ALL_ROLES:
        assert has_capability(role, "ml.promote") == (role is UserRole.BANK_TECHOPS)


def test_agency_admin_and_agency_manager_both_hold_team_scoped_capabilities():
    """Y vs T is a scope distinction this registry deliberately does not model
    (scope.py's job) — both AA and AM must show as "granted" here."""
    for code in ("team.read", "cases.read", "agents.manage", "allocation.plan"):
        assert has_capability(UserRole.AGENCY_ADMIN, code)
        assert has_capability(UserRole.AGENCY_MANAGER, code)


def test_agency_admin_only_capabilities_are_not_held_by_agency_manager():
    """The known-issue-11 companion: AGENCY_ADMIN stops being decorative by
    holding a few things AGENCY_MANAGER does not (plan §5, "Roles")."""
    for code in ("agency.profile.read", "agency.users.manage", "agents.import", "agency.audit.read"):
        assert has_capability(UserRole.AGENCY_ADMIN, code)
        assert not has_capability(UserRole.AGENCY_MANAGER, code)


# ── seeding ───────────────────────────────────────────────────────────────────
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


def test_seed_permission_tables_matches_the_registry_and_is_idempotent(db):
    from app.models.tenancy import Permission, RolePermission

    seed_permission_tables(db)
    perms = {p.code: p for p in db.query(Permission).all()}
    assert set(perms) == set(CAPABILITIES)
    for code, cap in CAPABILITIES.items():
        row = perms[code]
        assert row.category == cap.category
        assert row.description == cap.description
        assert row.requires_second_person == cap.requires_second_person
        assert row.is_sensitive == cap.is_sensitive

    grants = {(rp.role, rp.permission_code) for rp in db.query(RolePermission).all()}
    expected = {(role, code) for code, cap in CAPABILITIES.items() for role in cap.roles}
    assert grants == expected

    # idempotent: calling again must not duplicate or error (composite PK would
    # raise on a re-insert if this were not guarded).
    seed_permission_tables(db)
    assert db.query(Permission).count() == len(CAPABILITIES)
    assert db.query(RolePermission).count() == len(expected)


# ── require_perm through a real HTTP request ─────────────────────────────────
@pytest.fixture()
def app_and_db():
    engine = make_engine()
    create_schema(engine)
    Session = make_session_factory(engine)

    app = FastAPI()

    @app.get("/agencies/{agency_id}/suspend")
    def suspend(agency_id: str, current_user: User = require_perm("agency.suspend")):
        return {"suspended": agency_id, "by": current_user.id}

    session = Session()
    app.dependency_overrides[get_db] = lambda: session
    yield app, session
    session.close()


def _make_user(db, role: UserRole) -> User:
    user = User(
        email=f"{role.value.lower()}@meridiantrust.example", phone="9876500000",
        full_name=f"Test {role.value}", hashed_password="not-a-real-hash", role=role,
        bank_id=DEFAULT_TENANT["bank_id"],
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def test_a_role_that_holds_the_capability_is_let_through(app_and_db):
    app, db = app_and_db
    user = _make_user(db, UserRole.BANK_ADMIN)
    app.dependency_overrides[get_current_user] = lambda: user
    client = TestClient(app)

    r = client.get("/agencies/agency-1/suspend")
    assert r.status_code == 200, r.text
    assert r.json() == {"suspended": "agency-1", "by": user.id}
    assert db.query(AuditLog).count() == 0   # nothing to audit on a success


def test_a_role_without_the_capability_is_refused_and_the_refusal_is_audited(app_and_db):
    app, db = app_and_db
    user = _make_user(db, UserRole.AGENCY_MANAGER)   # holds no agency.* capability
    app.dependency_overrides[get_current_user] = lambda: user
    client = TestClient(app)

    r = client.get("/agencies/agency-1/suspend")
    assert r.status_code == 403
    assert "agency.suspend" in r.json()["detail"]

    rows = db.query(AuditLog).all()
    assert len(rows) == 1
    row = rows[0]
    assert row.action == AuditAction.ROLE_VIOLATION_ATTEMPT
    assert row.user_id == user.id
    assert row.success is False
    assert row.details["required_capability"] == "agency.suspend"
    assert row.details["actual_role"] == "AGENCY_MANAGER"


def test_service_role_cannot_reach_a_human_only_capability(app_and_db):
    app, db = app_and_db
    user = _make_user(db, UserRole.SERVICE)
    app.dependency_overrides[get_current_user] = lambda: user
    client = TestClient(app)

    assert client.get("/agencies/agency-1/suspend").status_code == 403
