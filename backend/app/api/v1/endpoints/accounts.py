# ─── CHANGELOG (standalone plan) ─────────────────────────────────────────────
# 2026-09-28 — NEW (P1 A06/A07/A08, d4). Account routes, kept out of auth.py
#   (43's) and manager.py (known issue 6) on purpose.
#   - PUBLIC (rate-limited like /auth/login): accept an invitation, reset a
#     forgotten password by SMS code, finish a withheld login by enrolling
#     TOTP. Tokens travel in bodies, never in URLs.
#   - SIGNED IN: change password, TOTP setup / confirm / disable, MFA status.
#   - ADMIN (/admin): invitations and credential resets. require_roles lets
#     the three admin roles in and records anyone else; WHICH user or role an
#     admin may act on is decided in the services (can_invite / can_manage),
#     which become require_perm() checks with A01.
# ────────────────────────────────────────────────────────────────────────────
from fastapi import APIRouter, Depends, Request
from typing import Annotated

from app.core.dependencies import CurrentUser, DbSession, TokenPayload, require_roles
from app.core.ids import UUIDPath
from app.core.ratelimit import AUTH_LIMIT, limiter
from app.models.user import BANK_ROLES, User, UserRole
from app.schemas.accounts import (
    ForgotPassword, ForgotVerify, InviteAccept, InviteCreate, MfaCode, MfaTicketConfirm, MfaTicketStart,
    PasswordChange, PasswordReset, TokenBody,
)
from app.services import invite_service, mfa_service, password_service

router = APIRouter(prefix="/auth", tags=["Accounts"])
admin_router = APIRouter(prefix="/admin", tags=["Account administration"])

AccountAdmin = Annotated[User, Depends(require_roles(UserRole.PLATFORM_ADMIN, UserRole.BANK_ADMIN,
                                                     UserRole.AGENCY_ADMIN))]
_OK = {"message": "Done"}


# ── public ──────────────────────────────────────────────────────────────────
@router.post("/invites/preview", summary="What an invitation is for, before accepting it")
@limiter.limit(AUTH_LIMIT)
async def preview_invite(body: TokenBody, request: Request, db: DbSession):
    return invite_service.preview_invite(db, body.token)


@router.post("/invites/accept", summary="Accept an invitation: set a password, get signed in")
@limiter.limit(AUTH_LIMIT)
async def accept_invite(body: InviteAccept, request: Request, db: DbSession):
    return invite_service.accept_invite(db, body.token, body.password, body.device_id, request)


@router.post("/password/forgot", status_code=202, summary="Send a reset code to the account's phone")
@limiter.limit(AUTH_LIMIT)
async def forgot_password(body: ForgotPassword, request: Request, db: DbSession):
    return password_service.forgot(db, body.identifier, request=request)


@router.post("/password/forgot/verify", summary="Exchange the SMS code for a reset token")
@limiter.limit(AUTH_LIMIT)
async def verify_reset_code(body: ForgotVerify, request: Request, db: DbSession):
    return password_service.verify_code(db, body.request_id, body.code, request=request)


@router.post("/password/reset", summary="Set a new password with a reset token")
@limiter.limit(AUTH_LIMIT)
async def reset_password(body: PasswordReset, request: Request, db: DbSession):
    password_service.reset_with_token(db, body.token, body.new_password, request=request)
    return {"message": "Password updated. Sign in with your new password."}


@router.post("/mfa/enroll/start", summary="Start TOTP enrollment with the ticket login returned")
@limiter.limit(AUTH_LIMIT)
async def mfa_ticket_start(body: MfaTicketStart, request: Request, db: DbSession):
    return mfa_service.ticket_start(db, body.enrollment_token)


@router.post("/mfa/enroll/confirm", summary="Confirm TOTP enrollment and finish signing in")
@limiter.limit(AUTH_LIMIT)
async def mfa_ticket_confirm(body: MfaTicketConfirm, request: Request, db: DbSession):
    return mfa_service.ticket_confirm(db, body.enrollment_token, body.code, body.device_id, request)


# ── signed in ───────────────────────────────────────────────────────────────
@router.post("/password/change", summary="Change your password; other devices are signed out")
@limiter.limit(AUTH_LIMIT)
async def change_password(body: PasswordChange, current_user: CurrentUser, payload: TokenPayload,
                          request: Request, db: DbSession):
    password_service.change_password(db, current_user, body.current_password, body.new_password,
                                     sid=payload.get("sid"), request=request)
    return {"message": "Password changed. Your other devices have been signed out."}


@router.get("/mfa", summary="Two-factor sign-in status for the current user")
async def mfa_status(current_user: CurrentUser):
    return {"enabled": current_user.totp_enabled, "allowed": current_user.role in BANK_ROLES,
            "required": mfa_service.required_for(current_user), "configured": mfa_service.configured()}


@router.post("/mfa/setup", summary="Start TOTP enrollment (bank users)")
@limiter.limit(AUTH_LIMIT)
async def mfa_setup(current_user: CurrentUser, request: Request, db: DbSession):
    return mfa_service.start_enrollment(db, current_user)


@router.post("/mfa/confirm", summary="Confirm TOTP enrollment with a code")
@limiter.limit(AUTH_LIMIT)
async def mfa_confirm(body: MfaCode, current_user: CurrentUser, payload: TokenPayload, request: Request,
                      db: DbSession):
    mfa_service.confirm_enrollment(db, current_user, body.code, request=request, keep_sid=payload.get("sid"))
    return {"message": "Two-factor sign-in is on. Your other devices have been signed out."}


@router.post("/mfa/disable", summary="Turn TOTP off (needs a current code)")
@limiter.limit(AUTH_LIMIT)
async def mfa_disable(body: MfaCode, current_user: CurrentUser, request: Request, db: DbSession):
    mfa_service.disable(db, current_user, body.code, request=request)
    return {"message": "Two-factor sign-in is off."}


# ── admin ───────────────────────────────────────────────────────────────────
@admin_router.post("/invites", status_code=201, summary="Invite a person to a role")
async def create_invite(body: InviteCreate, admin: AccountAdmin, request: Request, db: DbSession):
    return invite_service.create_invite(
        db, admin, email=body.email, role=body.role, full_name=body.full_name, phone=body.phone,
        agency_id=body.agency_id, bank_id=body.bank_id, channel=body.channel, request=request)


@admin_router.get("/invites", summary="Invitations in your bank or agency (newest 200)")
async def list_invites(admin: AccountAdmin, db: DbSession):
    return invite_service.list_invites(db, admin)


@admin_router.delete("/invites/{invite_id}", summary="Withdraw an open invitation")
async def revoke_invite(invite_id: UUIDPath, admin: AccountAdmin, request: Request, db: DbSession):
    return invite_service.revoke_invite(db, admin, invite_id, request=request)


@admin_router.post("/users/{user_id}/password-reset",
                   summary="Text a single-use reset link to the user (the admin never sees it)")
async def admin_password_reset(user_id: UUIDPath, admin: AccountAdmin, request: Request, db: DbSession):
    return password_service.admin_reset(db, admin, user_id, request=request)


@admin_router.post("/users/{user_id}/mfa-reset", summary="Clear a bank user's TOTP (lost phone)")
async def admin_mfa_reset(user_id: UUIDPath, admin: AccountAdmin, request: Request, db: DbSession):
    target = db.get(User, user_id)
    if target is None:
        from app.core.errors import AppException, ErrorCode
        raise AppException(404, ErrorCode.NOT_FOUND, "User not found")
    mfa_service.admin_reset(db, admin, target, request=request)
    return _OK
