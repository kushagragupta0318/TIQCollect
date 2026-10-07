# ─── CHANGELOG (standalone plan) ─────────────────────────────────────────────
# 2026-10-07 — NEW (P3 K01). Admin > Bank Users' routes, /bank/users. A
#   separate router file (like bank_agencies_admin.py and manager_agents_admin.py),
#   mounted alongside the rest in router.py.
#
#   Every route is gated by require_perm("bank.users.manage") — BANK_ADMIN only
#   (core/permissions.py §5.2) — including the list: who the bank's staff are is
#   itself admin-only. The service re-checks the role and does all tenant
#   scoping; these handlers only shape the request/response.
#
#   reset-login shares the "admin-credential-links" rate-limit bucket with
#   /admin/users/{id}/password-reset and the agents-admin reset — one caller,
#   ten credential-texting actions a minute, across every route that sends one
#   (see manager_agents_admin.py's own note on why a per-path limit is wrong
#   when {user_id} is in the path). invite also texts a link on channel=SMS and
#   joins the same bucket for the same reason. The read and the pure-state
#   mutations (role, deactivate, reactivate) send nothing and are not limited.
#
#   No `from __future__ import annotations` — see manager_agents_admin.py's own
#   note (2026-09-28): it breaks FastAPI/Pydantic 2.10's body-vs-query
#   resolution on a forward-referenced request model, live-server only.
# ────────────────────────────────────────────────────────────────────────────
from fastapi import APIRouter, Request
from typing import Any, Optional

from pydantic import BaseModel, Field

from app.core.dependencies import DbSession
from app.core.emails import AccountEmail
from app.core.ids import UUIDPath
from app.core.permissions import require_perm
from app.core.ratelimit import AUTH_LIMIT, limiter
from app.models.user import User, UserRole
from app.services.bank import bank_user_service

router = APIRouter(prefix="/bank", tags=["bank-users-admin"])


class BankUserOut(BaseModel):
    """One staff row, exactly what bank_user_service._user_dict builds.

    Rule 18: every bank- or agency-reachable route carries a typed response, so
    the contract is in the schema rather than in whatever the service happened
    to return that day.
    """
    user_id: str
    full_name: str
    email: str
    phone: str
    role: str
    is_active: bool
    status: str
    mfa_enabled: bool
    mfa_required: bool
    last_login_at: Optional[str] = None


class RoleChangeOut(BankUserOut):
    #: False when the requested role was the one already held -- a no-op, not
    #: an error, so the caller can tell the two apart.
    changed: bool


class InviteOut(BaseModel):
    """EXACTLY what invite_service.create_invite returns, read from the code
    rather than guessed.

    A response_model is an allowlist: a field the service returns and the
    model omits is DROPPED, silently. My first draft of this invented
    invite_id/email/role/link and would have thrown away `token` and `path` —
    the two fields the LINK channel exists to deliver, since the caller builds
    the set-password URL from them. That would have broken invite-by-link
    while every test still passed.

    `invite` stays a plain dict: it is invite_service.to_dict's shape, that
    service owns it, and restating its fields here is how the two drift apart.
    """
    invite: dict[str, Any]
    #: None when the channel sent nothing (LINK), True/False for an SMS send.
    delivered: Optional[bool] = None
    #: LINK channel only, shown once.
    token: Optional[str] = None
    path: Optional[str] = None


class ResetLoginOut(BaseModel):
    """password_service._issue_and_text's shape: {sent, expires_at}. Also not
    what I first guessed."""
    sent: bool
    expires_at: Optional[str] = None



@router.get("/users", response_model=list[BankUserOut])
def list_bank_users_route(db: DbSession, current_user: User = require_perm("bank.users.manage")):
    return bank_user_service.list_bank_users(db, current_user)


class InviteBankUserRequest(BaseModel):
    full_name: str
    email: AccountEmail    # 43's B15/core.emails — .test demo domains allowed
    phone: str
    role: UserRole
    channel: str = "LINK"


@router.post("/users/invite", status_code=201, response_model=InviteOut)
@limiter.shared_limit(AUTH_LIMIT, scope="admin-credential-links")
async def invite_bank_user_route(
    body: InviteBankUserRequest, request: Request, db: DbSession,
    current_user: User = require_perm("bank.users.manage"),
):
    return bank_user_service.invite_bank_user(
        db, current_user, email=body.email, full_name=body.full_name, phone=body.phone,
        role=body.role, channel=body.channel, request=request,
    )


class ChangeRoleRequest(BaseModel):
    role: UserRole


@router.patch("/users/{user_id}/role", response_model=RoleChangeOut)
def change_bank_user_role_route(
    user_id: UUIDPath, body: ChangeRoleRequest, request: Request, db: DbSession,
    current_user: User = require_perm("bank.users.manage"),
):
    return bank_user_service.change_role(db, current_user, user_id, role=body.role, request=request)


class DeactivateUserRequest(BaseModel):
    reason: str = Field(min_length=1)


@router.post("/users/{user_id}/deactivate", response_model=BankUserOut)
def deactivate_bank_user_route(
    user_id: UUIDPath, body: DeactivateUserRequest, request: Request, db: DbSession,
    current_user: User = require_perm("bank.users.manage"),
):
    return bank_user_service.deactivate_user(db, current_user, user_id, reason=body.reason, request=request)


@router.post("/users/{user_id}/reactivate", response_model=BankUserOut)
def reactivate_bank_user_route(
    user_id: UUIDPath, request: Request, db: DbSession,
    current_user: User = require_perm("bank.users.manage"),
):
    return bank_user_service.reactivate_user(db, current_user, user_id, request=request)


@router.post("/users/{user_id}/reset-login", response_model=ResetLoginOut)
@limiter.shared_limit(AUTH_LIMIT, scope="admin-credential-links")
async def reset_bank_user_login_route(
    user_id: UUIDPath, request: Request, db: DbSession,
    current_user: User = require_perm("bank.users.manage"),
):
    return bank_user_service.reset_login(db, current_user, user_id, request=request)
