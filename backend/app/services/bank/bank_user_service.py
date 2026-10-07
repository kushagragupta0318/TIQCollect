# ─── CHANGELOG (standalone plan) ─────────────────────────────────────────────
# 2026-10-07 — NEW (P3 K01). Admin > Bank Users: a BANK_ADMIN manages its own
#   bank's staff accounts (BANK_ADMIN / BANK_ANALYST / BANK_TECHOPS) — list
#   them, invite a new one, change an analyst/techops role, deactivate or
#   reactivate an account, and send a reset-login link.
#
#   This is the bank-side mirror of G02's Manage Agents
#   (agent_management_service): the same "resolve the target in my own scope or
#   get the uniform 404, re-check who-may-act-on-whom in the service even
#   though require_perm already gated the route, and stage the audit row in the
#   same commit as the change" shape. It reuses, never restates:
#     - invite_service.create_invite for the invite (its can_invite rule
#       already lets a BANK_ADMIN bring in BANK_ROLES; it writes USER_INVITED);
#     - password_service.admin_reset for reset-login (its can_manage rule lets
#       a BANK_ADMIN reset a BANK_ANALYST / BANK_TECHOPS but never a fellow
#       BANK_ADMIN; it writes PASSWORD_RESET_ISSUED and texts the TARGET);
#     - auth_service.revoke_user_sessions on deactivate (USER_DEACTIVATED is
#       itself a session-revoke reason; that call also writes SESSION_REVOKED).
#
#   Tenant binding is never taken from the request body: the bank is always
#   `admin.bank_id`, so a bank admin can neither see nor act on another bank's
#   users (the uniform 404). require_perm("bank.users.manage") gates every
#   route — BANK_ADMIN only (core/permissions.py §5.2) — and each service
#   function re-checks role as defence in depth (a future wiring mistake must
#   fail closed, not leak).
#
#   AUDIT ENUM GAP (flagged to 43/coordinator): AuditAction has USER_DEACTIVATED
#   but no USER_REACTIVATED / USER_ROLE_CHANGED member, and the enum (a native
#   Postgres type, models/** + alembic owned by 43, migration-frozen on
#   TIQCollect-app) cannot gain one from this lane. Reactivation and role change
#   therefore record under USER_DEACTIVATED with an explicit `event` in
#   `details` — exactly as agent_management_service records both suspend AND
#   reactivate under the single AGENT_STATUS_CHANGED action. Deactivation uses
#   USER_DEACTIVATED directly. Proper dedicated actions are a follow-up for 43.
# ────────────────────────────────────────────────────────────────────────────
"""Bank-side user administration (Admin > Bank Users, K01)."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import Request
from sqlalchemy.orm import Session

from app.core.audit import stage_audit
from app.core.errors import AppException, ErrorCode
from app.models.audit_log import AuditAction
from app.models.user import BANK_ROLES, User, UserRole
from app.services import mfa_service

# A bank admin may set an analyst's or techops' role to the other of the two.
# Promotion to BANK_ADMIN is deliberately NOT offered here: minting a second
# admin is a privilege escalation with nobody else in the loop, the same
# concern password_service.can_manage encodes ("never a fellow BANK_ADMIN").
# A first/second BANK_ADMIN is brought in by invite (invite acceptance sets a
# password and proves the person), never by flipping a live account's role.
_ROLE_CHANGE_SET = frozenset({UserRole.BANK_ANALYST, UserRole.BANK_TECHOPS})
# What a bank admin may invite through this page: its own bank's staff roles.
_INVITABLE_BANK_ROLES = frozenset(BANK_ROLES)   # BANK_ADMIN, BANK_ANALYST, BANK_TECHOPS


def _client_ip(request: Request | None) -> str | None:
    return request.client.host if request is not None and request.client else None


def _require_bank_admin(admin: User) -> None:
    """Defence in depth behind require_perm("bank.users.manage"): the route
    already restricts this capability to BANK_ADMIN, but a service that trusts
    its caller's role without re-checking is how a future wiring mistake turns
    into a tenant leak instead of a clean 403 (agent_management_service makes
    the same argument for its own _require_manager)."""
    if admin.role != UserRole.BANK_ADMIN or not admin.bank_id:
        raise AppException(403, ErrorCode.FORBIDDEN, "Only a bank administrator can manage bank users.")


def _bank_user_or_404(db: Session, admin: User, user_id: str) -> User:
    """The target, resolved in the admin's own bank. A user of another bank, or
    an agency/field user (not a bank staff account), reads identically to an id
    that does not exist at all (scope.py's uniform-404 convention)."""
    target = db.get(User, user_id)
    if target is None or target.bank_id != admin.bank_id or target.role not in BANK_ROLES:
        raise AppException(404, ErrorCode.NOT_FOUND, "Not found")
    return target


def _require_manageable(admin: User, target: User) -> None:
    """Who a bank admin may act on through this page. Never yourself (a self
    change here is how an admin locks themselves out by mistake — self-service
    lives in Account Security), and never a fellow BANK_ADMIN (a takeover with
    nobody else involved; a bank admin's own account is the platform admin's to
    manage, mirroring password_service.can_manage's HIGH-finding rule)."""
    if target.id == admin.id:
        raise AppException(409, ErrorCode.CONFLICT, "You cannot change your own account from here.")
    if target.role == UserRole.BANK_ADMIN:
        raise AppException(403, ErrorCode.FORBIDDEN,
                           "A bank administrator's account is managed by the platform administrator.")


def _user_dict(target: User) -> dict:
    return {
        "user_id": target.id,
        "full_name": target.full_name,
        "email": target.email,
        "phone": target.phone,
        "role": target.role.value,
        "is_active": target.is_active,
        "status": "ACTIVE" if target.is_active else "DEACTIVATED",
        "mfa_enabled": bool(target.totp_enabled),
        "mfa_required": mfa_service.required_for(target),
        "last_login_at": target.last_login_at.isoformat() if target.last_login_at else None,
    }


def list_bank_users(db: Session, admin: User) -> list[dict]:
    """Every staff account in the admin's own bank (BANK_ADMIN, BANK_ANALYST,
    BANK_TECHOPS). Agency and field users are not bank staff and belong to the
    agency's own Manage Agents / Manage Users surfaces, not here."""
    _require_bank_admin(admin)
    rows = (db.query(User)
            .filter(User.bank_id == admin.bank_id, User.role.in_(BANK_ROLES))
            .order_by(User.role, User.full_name)
            .all())
    return [_user_dict(r) for r in rows]


def invite_bank_user(db: Session, admin: User, *, email: str, full_name: str, phone: str,
                     role: UserRole, channel: str = "LINK", request: Request | None = None) -> dict:
    """Bring a new staff member into the admin's own bank. Wraps
    invite_service.create_invite, which owns the token minting, the can_invite
    rule and the USER_INVITED audit row — this only refuses a non-bank role
    early with a clear message rather than letting can_invite's generic
    "cannot invite someone to that role" stand for it."""
    _require_bank_admin(admin)
    if role not in _INVITABLE_BANK_ROLES:
        raise AppException(422, ErrorCode.VALIDATION_ERROR,
                           "A bank user is a BANK_ADMIN, BANK_ANALYST or BANK_TECHOPS.")
    from app.services.invite_service import create_invite
    # bank_id is omitted on purpose: create_invite fills it from the inviter for
    # every role but PLATFORM_ADMIN, so the tenant can never come from the body.
    return create_invite(db, admin, email=email, role=role, full_name=full_name, phone=phone,
                         channel=channel, request=request)


def change_role(db: Session, admin: User, user_id: str, *, role: UserRole,
                request: Request | None = None) -> dict:
    """Move a BANK_ANALYST <-> BANK_TECHOPS. get_current_user re-reads the role
    from the row on every request, so the change takes effect on the target's
    next call without ending their session."""
    _require_bank_admin(admin)
    target = _bank_user_or_404(db, admin, user_id)
    _require_manageable(admin, target)
    if role not in _ROLE_CHANGE_SET:
        raise AppException(422, ErrorCode.VALIDATION_ERROR,
                           "A role change here moves a user between BANK_ANALYST and BANK_TECHOPS.")
    if role == target.role:
        return {**_user_dict(target), "changed": False}
    previous = target.role
    target.role = role
    stage_audit(db, action=AuditAction.USER_DEACTIVATED, user_id=admin.id, entity_type="User",
                entity_id=target.id, bank_id=target.bank_id, ip_address=_client_ip(request),
                details={"event": "USER_ROLE_CHANGED", "from_role": previous.value, "to_role": role.value})
    db.commit()
    return {**_user_dict(target), "changed": True}


def deactivate_user(db: Session, admin: User, user_id: str, *, reason: str,
                    request: Request | None = None) -> dict:
    """Turn the account off and end every session it holds at once — a
    deactivated user must not keep working from an already-open tab."""
    _require_bank_admin(admin)
    target = _bank_user_or_404(db, admin, user_id)
    _require_manageable(admin, target)
    reason = (reason or "").strip()
    if not reason:
        raise AppException(422, ErrorCode.VALIDATION_ERROR, "Enter a reason for deactivating this account.")
    if len(reason) > 500:
        raise AppException(422, ErrorCode.VALIDATION_ERROR, "Keep the reason under 500 characters.")
    if not target.is_active:
        raise AppException(409, ErrorCode.CONFLICT, "This account is already deactivated.")

    from app.services.auth_service import revoke_user_sessions
    target.is_active = False
    target.deactivated_at = datetime.now(timezone.utc)
    target.deactivated_by = admin.id
    # USER_DEACTIVATED is a valid session-revoke reason; that call also writes
    # its own SESSION_REVOKED row in this same transaction.
    revoke_user_sessions(db, target.id, "USER_DEACTIVATED", by=admin.id)
    stage_audit(db, action=AuditAction.USER_DEACTIVATED, user_id=admin.id, entity_type="User",
                entity_id=target.id, bank_id=target.bank_id, ip_address=_client_ip(request),
                details={"event": "USER_DEACTIVATED", "reason": reason})
    db.commit()
    return _user_dict(target)


def reactivate_user(db: Session, admin: User, user_id: str, *, request: Request | None = None) -> dict:
    """Turn a deactivated account back on. Credentials are untouched — if the
    person also needs a fresh password, that is the separate reset-login
    action."""
    _require_bank_admin(admin)
    target = _bank_user_or_404(db, admin, user_id)
    _require_manageable(admin, target)
    if target.is_active:
        raise AppException(409, ErrorCode.CONFLICT, "This account is already active.")
    target.is_active = True
    target.deactivated_at = None
    target.deactivated_by = None
    stage_audit(db, action=AuditAction.USER_DEACTIVATED, user_id=admin.id, entity_type="User",
                entity_id=target.id, bank_id=target.bank_id, ip_address=_client_ip(request),
                details={"event": "USER_REACTIVATED", "is_active": True})
    db.commit()
    return _user_dict(target)


def reset_login(db: Session, admin: User, user_id: str, *, request: Request | None = None) -> dict:
    """Send a single-use set-password link to the user's own phone and end
    their current sessions. Delegates to password_service.admin_reset, whose
    can_manage rule already refuses a fellow BANK_ADMIN and the admin's own
    account (so this never has to restate _require_manageable for it)."""
    _require_bank_admin(admin)
    target = _bank_user_or_404(db, admin, user_id)
    from app.services import password_service
    return password_service.admin_reset(db, admin, target.id, request=request)
