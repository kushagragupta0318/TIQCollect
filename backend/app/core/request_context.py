# ─── CHANGELOG (standalone plan) ─────────────────────────────────────────────
# 2026-09-28 (A02) — NEW. "`RequestContext` (bank, agency, team, self) says
#   *where*" (docs/DATA-MODEL-V2.md §5). A capability (core/permissions.py)
#   says WHAT a principal may do; this says WHOSE data they are allowed to be
#   asking about, so `services/scope.py`'s helpers (agents_in_scope,
#   cases_in_scope, ...) have one thing to read instead of each reaching into
#   `current_user.bank_id` / `.agency_id` by hand at every call site — which
#   is how the tenancy leaks this plan closes (§1) happened in the first
#   place: no single place defined "the agents this manager owns", so it got
#   retyped (known issue 6).
#
#   DELIBERATELY built ON TOP of CurrentUser, not as an alternative to it:
#   `role` / `bank_id` / `agency_id` are read from the DB-loaded `current_user`
#   row (fresh every request, revocation already checked by get_current_user)
#   rather than from the token's own claims, which can be stale the moment an
#   admin changes someone's role or tenant mid-session. `sid` has nowhere else
#   to come from (it is not a User column) and is read from the token.
#
#   `perms` is likewise NOT read from the token's `perms` claim (see
#   core/security.create_access_token's docstring for what that claim is
#   actually for — the frontend, reading its own JWT without a round trip).
#   It is recomputed here, fresh, from `current_user.role` against the same
#   registry `require_perm` uses, so RequestContext.perms can never disagree
#   with what a `require_perm` check on the same request would decide, and
#   can never go stale within a token's life the way a baked-in claim could.
# ────────────────────────────────────────────────────────────────────────────
"""Who is asking, and from where — derived, never a second source of truth.

    from app.core.request_context import CurrentContext, RequestContext

    @router.get("/team/agents")
    def list_agents(ctx: CurrentContext, db: DbSession):
        return agents_in_scope(db, ctx)   # scope.py reads ctx.role/bank_id/agency_id/user_id
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends

from app.core.dependencies import CurrentUser, TokenPayload
from app.core.permissions import role_capabilities
from app.models.user import UserRole, tenant_scope


@dataclass(frozen=True)
class RequestContext:
    user_id: str
    role: UserRole
    bank_id: str | None
    agency_id: str | None
    sid: str | None
    perms: frozenset[str]

    def has(self, capability: str) -> bool:
        """Same question `require_perm` asks, for code that wants to branch on
        a capability rather than be gated by one (e.g. "show this field only
        if the caller could also edit it")."""
        return capability in self.perms

    @property
    def scope(self) -> str:
        """PLATFORM / BANK / AGENCY / AGENT: the RLS scope this request's transactions carry."""
        return tenant_scope(self.role)


def get_request_context(current_user: CurrentUser, payload: TokenPayload) -> RequestContext:
    return RequestContext(
        user_id=current_user.id,
        role=current_user.role,
        bank_id=current_user.bank_id,
        agency_id=current_user.agency_id,
        sid=payload.get("sid"),
        perms=role_capabilities(current_user.role),
    )


CurrentContext = Annotated[RequestContext, Depends(get_request_context)]
