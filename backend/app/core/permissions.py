# ─── CHANGELOG (standalone plan) ─────────────────────────────────────────────
# 2026-09-28 (A01) — NEW. The capability registry: "routes check capabilities,
#   never role names" (plan §2.1), and this is the one place that says which
#   role holds which capability. `tenancy.permissions` and
#   `tenancy.role_permissions` (models/tenancy.py) are seeded FROM this file —
#   never the other way round — and `test_registry_matches_seeded_tables`
#   holds the two equal so they cannot drift.
#
#   Transcribed row-for-row from docs/DATA-MODEL-V2.md §5.1 (the capability
#   catalog) and §5.2 (the role -> capability matrix), in the doc's own order,
#   so a reviewer can check this file against that table line by line rather
#   than trusting a re-derivation. Y ("granted, tenant-wide"), T ("granted,
#   own team only") and S ("granted, self only") from §5.2's legend are three
#   different SCOPES of the same grant, and this registry does not distinguish
#   them: `require_perm()` asks only "does this role hold this capability at
#   all", a yes/no gate. WHICH rows a granted principal may see — their own
#   team's cases versus every case in the tenant — is a separate question,
#   answered by `services/scope.py` (agents_in_scope, cases_in_scope), never
#   by this file. `RolePermission` has no scope column for the same reason:
#   scope is a property of the QUERY, not of the grant.
#
#   No migration seeds `tenancy.permissions` / `tenancy.role_permissions` yet
#   — that lands with 43's v2_0005+ (B-chain, alembic and models are 43's).
#   Until then, `seed_permission_tables()` below is what test fixtures call
#   (tests/_db.py's `create_schema` does not, deliberately: most suites never
#   touch a capability and the extra 158 inserts on every test would cost
#   more than it buys them — call it explicitly where it matters).
# ────────────────────────────────────────────────────────────────────────────
"""The capability registry (plan §2.1) and the `require_perm()` dependency.

    from app.core.permissions import require_perm
    from app.models.user import User

    @router.post("/agency/{agency_id}/suspend")
    def suspend_agency(agency_id: UUIDPath, current_user: User = require_perm("agency.suspend")):
        ...

Do NOT also annotate the parameter as `CurrentUser` — that embeds a SECOND
`Depends(get_current_user)` in the type itself, which FastAPI would then hold
alongside the explicit `= require_perm(...)` default. Annotate the plain
return type (`User` here) and let the default carry the one dependency.

`require_perm(code)` returns a FastAPI dependency exactly like
`core.dependencies.require_roles(*roles)` — same shape, same audit write on
refusal (ROLE_VIOLATION_ATTEMPT; there is no separate PERMISSION_VIOLATION_
ATTEMPT action, and adding one is an enum migration, which belongs to 43) —
so a route reads identically whether it is gated by role or by capability,
and a reviewer does not have to learn two idioms.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from app.models.user import UserRole

# Role shorthand matching docs/DATA-MODEL-V2.md §5.2's own legend exactly, so
# the matrix below can be transcribed in the doc's column order without
# renaming anything.
PA = UserRole.PLATFORM_ADMIN
BA = UserRole.BANK_ADMIN
BN = UserRole.BANK_ANALYST
BT = UserRole.BANK_TECHOPS
AA = UserRole.AGENCY_ADMIN
AM = UserRole.AGENCY_MANAGER
FA = UserRole.FIELD_AGENT
SV = UserRole.SERVICE

ALL_ROLES: tuple[UserRole, ...] = (PA, BA, BN, BT, AA, AM, FA, SV)


@dataclass(frozen=True)
class Capability:
    """One row of §5.1, plus the roles that hold it (§5.2) — the two tables
    are ONE entry here on purpose, so they cannot be edited out of step."""
    code: str
    description: str
    roles: frozenset[UserRole]
    requires_second_person: bool = False
    is_sensitive: bool = False

    @property
    def category(self) -> str:
        """`tenancy.permissions.category` — the code's first segment. Derived,
        never a second thing to keep in sync with the code."""
        return self.code.split(".", 1)[0]


def _cap(code: str, description: str, roles: tuple[UserRole, ...], *,
        second_person: bool = False, sensitive: bool = False) -> Capability:
    return Capability(code=code, description=description, roles=frozenset(roles),
                      requires_second_person=second_person, is_sensitive=sensitive)


# ── §5.1 the catalog, §5.2 the grant — one row each, in the doc's order ──────
_CATALOG: tuple[Capability, ...] = (
    # self.* (4) — Y for every human role (§5.2 row 1), · for SERVICE.
    _cap("self.profile", "read and edit own profile", (PA, BA, BN, BT, AA, AM, FA)),
    _cap("self.password.change", "change own password", (PA, BA, BN, BT, AA, AM, FA), sensitive=True),
    _cap("self.sessions.manage", "list and revoke own sessions", (PA, BA, BN, BT, AA, AM, FA)),
    _cap("self.mfa.manage", "enrol or reset own TOTP", (PA, BA, BN, BT, AA, AM, FA), sensitive=True),

    _cap("platform.banks.manage", "create or suspend banks", (PA,), sensitive=True),
    _cap("platform.bank_admins.invite", "invite a bank's first BANK_ADMIN", (PA,), sensitive=True),
    _cap("platform.support.read", "audited read-only support access to a bank (Q20)", (PA,), sensitive=True),
    _cap("platform.simulator.use", "/simulator outside DEMO_MODE (plan §11.1)", (PA, BA, BT)),

    _cap("bank.settings.manage", "bank profile, brand, timezone", (BA,), sensitive=True),
    _cap("bank.users.manage", "invite, deactivate and change the role of bank users; reset their passwords",
        (BA,), sensitive=True),
    _cap("bank.users.sessions.revoke", "revoke another user's sessions", (BA,), sensitive=True),
    _cap("bank.regions.manage", "edit the region hierarchy and branches", (BA,)),
    _cap("bank.audit.read", "the bank-wide audit trail", (BA, BT)),
    _cap("bank_feed.upload", "submit a feed file (a person or a SERVICE account)", (BA, BT, SV), sensitive=True),

    _cap("agency.read", "directory, scorecards, agency drill (read-only)", (BA, BN, BT)),
    _cap("agency.onboard", "create a draft agency, send the master-login invite", (BA,), sensitive=True),
    _cap("agency.update", "edit agency identity and contacts", (BA,)),
    _cap("agency.documents.verify", "verify or reject agency documents", (BA,)),
    _cap("agency.contract.manage", "create or renew contracts, terms and coverage", (BA,), sensitive=True),
    _cap("agency.suspend", "suspend or reactivate an agency", (BA,), sensitive=True),
    _cap("agency.offboard", "offboard (recall and archive)", (BA,), second_person=True, sensitive=True),
    _cap("agency.profile.read", "own agency's contract, commission and SLA (plan §10)", (AA,)),
    _cap("agency.users.manage", "create and manage agency managers", (AA,), sensitive=True),

    _cap("placement.read", "placements received or made", (BA, BN, AA, AM)),
    _cap("placement.manual", "place loans by hand", (BA,), sensitive=True),
    _cap("placement.run", "run or apply the placement engine", (BA,), sensitive=True),
    _cap("placement.recall", "recall placements", (BA,), sensitive=True),

    _cap("cc.read", "Command Center KPIs, tabs and alerts", (BA, BN, BT)),
    _cap("cc.drill.accounts", "account-level drill, including PII", (BA, BN), sensitive=True),
    _cap("alerts.manage", "edit alert thresholds", (BA,)),

    _cap("strategy.simulate", "Monte Carlo and scenario runs", (BA, BN)),
    _cap("strategy.forecast", "forecasts", (BA, BN)),
    _cap("strategy.approve", "approve a simulation memo", (BA,), sensitive=True),

    _cap("reports.generate", "generate a board or MIS pack", (BA, BN)),
    _cap("reports.download", "download a pack (writes DATA_EXPORT)", (BA, BN), sensitive=True),
    _cap("reports.schedule", "schedule packs and set recipients", (BA,)),

    _cap("copilot.use", "chat with the portfolio or field copilot", (BA, BN, BT, AA, AM, FA)),

    _cap("ai_agents.read", "agent studio, read-only", (BA, BT)),
    _cap("ai_agents.manage", "create agents, versions, prompts, tools", (BT,), sensitive=True),
    _cap("ai_agents.run", "run agents and use the test console", (BT,)),
    _cap("ai_actions.approve", "decide approval-queue items", (BA,), sensitive=True),

    _cap("ml.read", "MLOps console", (BA, BT)),
    # ml.approve / ml.promote: "S, 2P with ml.promote" / "S, 2P" in §5.1 — the
    # promoter must differ from the approver, enforced by lifecycle.promote
    # (services/registry, not here) exactly as it is today.
    _cap("ml.approve", "approve a candidate", (BT,), second_person=True, sensitive=True),
    _cap("ml.promote", "promote a candidate (rewrites champion.txt)", (BT,), second_person=True, sensitive=True),
    _cap("ml.retrain", "start a retrain job", (BT,), sensitive=True),

    _cap("data_quality.read", "feed data-quality results", (BA, BT)),
    _cap("data_quality.release", "release quarantined feed rows", (BT,), sensitive=True),
    _cap("llm_usage.read", "LLM usage and cost", (BA, BT)),

    _cap("team.read", "the manager view: own team's agents and cases", (AA, AM)),
    _cap("agents.manage", "create, edit, suspend, reset password or device, transfer manager",
        (AA, AM), sensitive=True),
    _cap("agents.import", "bulk CSV import", (AA,), sensitive=True),
    _cap("cases.read", "cases in scope", (AA, AM)),
    _cap("cases.assign", "reassign cases", (AA, AM)),
    _cap("escalations.manage", "acknowledge or resolve escalations", (AA, AM)),
    _cap("allocation.plan", "plan, re-plan, simulate", (AA, AM)),
    _cap("allocation.settings", "allocation policy", (AA, AM)),
    _cap("allocation.rollback", "roll back a run", (AA, AM), sensitive=True),

    _cap("leave.approve", "approve, reject, revoke or mark leave", (AA, AM)),
    _cap("payments.verify", "verify or reject payments", (AA, AM), sensitive=True),
    _cap("fraud.review", "confirm or dismiss anomaly findings", (AA, AM)),
    _cap("disputes.manage", "work disputes and complaints", (AA, AM)),
    _cap("settlements.propose", "propose a settlement", (AA, AM)),
    # settlements.approve: bank-side approval of an agency-proposed settlement
    # — "S, and CHECK proposer != approver (§4.3)" in §5.1. The distinctness
    # check is a service-layer concern (mirrors ml.promote's), not this gate.
    _cap("settlements.approve", "approve a settlement (bank side)", (BA,), sensitive=True),

    _cap("agency.audit.read", "the agency-scoped audit trail", (AA,)),
    _cap("analytics.agency.read", "the manager analytics pages", (AA, AM)),

    # field.* (8) — "S" in §5.2 for FIELD_AGENT only: granted, self-scoped
    # (an agent's own cases/visits/etc — scope.py enforces which rows).
    _cap("field.cases.read", "own worklist", (FA,)),
    _cap("field.visit.record", "record visits and evidence", (FA,)),
    _cap("field.payment.collect", "collect payments (OTP)", (FA,)),
    _cap("field.ptp.manage", "set or reschedule PTPs", (FA,)),
    _cap("field.call.log", "log calls", (FA,)),
    _cap("field.location.report", "location pings and check-in", (FA,)),
    _cap("field.leave.request", "request leave", (FA,)),
    _cap("field.sos", "raise SOS", (FA,)),

    _cap("service.field_ops.read", "the /api/field-ops/* contract (embedded mode)", (SV,)),
    _cap("service.manager_api.read",
        "read-only /api/v1/manager/* for Command Center (replaces TIQCOLLECT_AGENCY_ACCOUNTS "
        "manager passwords, plan §2.1)", (SV,)),
)

CAPABILITIES: dict[str, Capability] = {c.code: c for c in _CATALOG}

# One definition, checked at import time rather than trusted: a duplicated
# code in _CATALOG above would silently shadow its first definition in the
# dict comprehension, which is exactly the kind of drift this file exists to
# prevent in the surrounding tables.
if len(CAPABILITIES) != len(_CATALOG):
    _seen: set[str] = set()
    _dupes = [c.code for c in _CATALOG if c.code in _seen or _seen.add(c.code)]
    raise RuntimeError(f"duplicate capability code(s) in _CATALOG: {_dupes}")


def role_capabilities(role: UserRole) -> frozenset[str]:
    """Every capability code `role` holds, at any scope (Y, T or S)."""
    return frozenset(c.code for c in _CATALOG if role in c.roles)


ROLE_CAPABILITIES: dict[UserRole, frozenset[str]] = {r: role_capabilities(r) for r in ALL_ROLES}


def has_capability(role: UserRole, code: str) -> bool:
    if code not in CAPABILITIES:
        # A typo'd or retired code must fail closed, not silently grant
        # nothing while looking like an ordinary denial.
        raise KeyError(f"{code!r} is not a declared capability — check core/permissions.CAPABILITIES")
    return role in CAPABILITIES[code].roles


# ── The dependency ───────────────────────────────────────────────────────────
def require_perm(code: str):
    """A FastAPI dependency: 403 (and a ROLE_VIOLATION_ATTEMPT audit row,
    exactly like `require_roles`) unless `current_user.role` holds `code`.

    Raises KeyError at IMPORT time (route-registration time, not
    request time) if `code` is not in the registry — a route wired to a
    capability that does not exist must fail the moment the app starts, not
    the first time someone with the "right" role happens to hit it.
    """
    if code not in CAPABILITIES:
        raise KeyError(f"{code!r} is not a declared capability — check core/permissions.CAPABILITIES")

    def _checker(request: Request, db, current_user) -> object:
        if not has_capability(current_user.role, code):
            from app.core.audit import write_audit
            from app.models.audit_log import AuditAction
            write_audit(
                db, action=AuditAction.ROLE_VIOLATION_ATTEMPT,
                user_id=current_user.id, entity_type="Route",
                entity_id=f"{request.method} {request.url.path}",
                details={"required_capability": code, "actual_role": current_user.role.value,
                         "method": request.method, "path": request.url.path},
                ip_address=request.client.host if request.client else None,
                user_agent=request.headers.get("user-agent"),
                success=False, failure_reason="capability not permitted",
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied. Required capability: {code}",
            )
        return current_user

    # `db`/`current_user` are resolved via the same dependencies require_roles
    # uses, imported lazily to avoid a circular import (dependencies.py does
    # not import this module, so there is nothing to break either way — kept
    # lazy anyway so this module can be imported before the app is built,
    # e.g. from a script that only wants CAPABILITIES/ROLE_CAPABILITIES).
    from app.core.dependencies import CurrentUser, DbSession

    def _dependency(request: Request, current_user: CurrentUser, db: DbSession):
        return _checker(request, db, current_user)

    return Depends(_dependency)


# ── Seeding, for tests (no migration exists yet — see the header) ───────────
def seed_permission_tables(session) -> None:
    """Insert every row of CAPABILITIES/ROLE_CAPABILITIES into
    tenancy.permissions / tenancy.role_permissions. Idempotent: skips rows
    that already exist, so a suite may call this from more than one fixture.
    """
    from app.models.tenancy import Permission, RolePermission

    existing_perms = {p.code for p in session.query(Permission.code).all()}
    for cap in _CATALOG:
        if cap.code in existing_perms:
            continue
        session.add(Permission(
            code=cap.code, category=cap.category, description=cap.description,
            requires_second_person=cap.requires_second_person, is_sensitive=cap.is_sensitive,
        ))
    existing_grants = {(rp.role, rp.permission_code) for rp in
                       session.query(RolePermission.role, RolePermission.permission_code).all()}
    for cap in _CATALOG:
        for role in sorted(cap.roles, key=lambda r: r.value):
            if (role, cap.code) in existing_grants:
                continue
            session.add(RolePermission(role=role, permission_code=cap.code))
    session.commit()
