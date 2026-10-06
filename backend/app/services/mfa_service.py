# ─── CHANGELOG (standalone plan) ─────────────────────────────────────────────
# 2026-09-28 — NEW (P1 A08, d4). TOTP for bank roles.
#
#   users.totp_secret / totp_enabled existed and nothing used them. Bank users
#   see every agency's book, so they get a second factor:
#   - ENROLL: a random base32 secret, shown ONCE as the secret and an
#     otpauth:// URI (the app renders it as a QR), pending until a valid code
#     confirms it. Bank roles only (MFA_NOT_ALLOWED otherwise).
#   - THE SECRET IS NEVER STORED IN PLAINTEXT. Fernet under TOTP_ENC_KEY;
#     with the key unset, an unresolved ${VAR} or malformed, enrollment is
#     refused (MFA_NOT_CONFIGURED) — fail closed, per the coordinator's A08
#     ruling. A secret that no longer decrypts (key rotated) reads as not
#     enrolled for verification: the code is refused, never waved through.
#   - REPLAY IS REFUSED. A code is accepted only for a 30-second step strictly
#     later than users.totp_last_step, and the step is claimed with a
#     compare-and-swap UPDATE, so the same code cannot sign in twice — not
#     even from two requests racing.
#   - LOGIN: an enrolled user must send totp_code (MFA_REQUIRED without one,
#     MFA_INVALID for a wrong one — auth_service counts that toward lockout).
#     With BANK_MFA_REQUIRED="true" a bank user who is not enrolled gets no
#     session, only an enrollment ticket (10 minutes, single-use, ephemeral
#     store) that opens a session once a code confirms the new secret.
#   - Disabling needs a current code; with BANK_MFA_REQUIRED on, a bank user
#     cannot switch it off at all. A bank admin can reset another bank user's
#     MFA (a lost phone), audited.
# ────────────────────────────────────────────────────────────────────────────
"""TOTP multi-factor authentication for bank roles."""
from __future__ import annotations

import hmac
import time

import pyotp
import structlog
from fastapi import Request
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.audit import stage_audit
from app.core.config import settings
from app.core.errors import AppException, ErrorCode
from app.core.security import token_sha256
from app.models.audit_log import AuditAction
from app.models.user import BANK_ROLES, User, UserRole
from app.services.credentials import flag_on, new_token

logger = structlog.get_logger()

ISSUER = "TIQCollect"
STEP_SECONDS = 30
VALID_WINDOW = 1                 # the step before and after, for clock drift
TICKET_TTL_SECONDS = 600
_TICKET_PREFIX = "mfa_enroll:"


def _client_ip(request: Request | None) -> str | None:
    return request.client.host if request is not None and request.client else None


def _fernet():
    """The cipher, or None when MFA is not configured. Never raises."""
    key = (settings.TOTP_ENC_KEY or "").strip()
    if not key or key.startswith("${"):
        return None
    try:
        from cryptography.fernet import Fernet
        return Fernet(key.encode())
    except Exception as exc:             # malformed key: configured wrongly, so not configured
        logger.error("mfa.bad_totp_enc_key", error_type=type(exc).__name__)
        return None


def configured() -> bool:
    return _fernet() is not None


def required_for(user: User) -> bool:
    return user.role in BANK_ROLES and flag_on(settings.BANK_MFA_REQUIRED)


def _secret_of(user: User) -> str | None:
    f = _fernet()
    if f is None or not user.totp_secret:
        return None
    try:
        return f.decrypt(user.totp_secret.encode()).decode()
    except Exception:
        return None


def _store():
    from app.services import otp_service
    return otp_service._default_store()


# ── enrollment ──────────────────────────────────────────────────────────────
def start_enrollment(db: Session, user: User) -> dict:
    """A new pending secret; returned once. Replaces any unconfirmed one."""
    if user.role not in BANK_ROLES:
        raise AppException(403, ErrorCode.MFA_NOT_ALLOWED, "Two-factor sign-in is for bank users.")
    f = _fernet()
    if f is None:
        raise AppException(503, ErrorCode.MFA_NOT_CONFIGURED,
                           "Two-factor sign-in is not configured on this server.")
    if user.totp_enabled:
        raise AppException(409, ErrorCode.CONFLICT, "Two-factor sign-in is already on.")
    secret = pyotp.random_base32()
    user.totp_secret = f.encrypt(secret.encode()).decode()
    user.totp_last_step = None
    db.commit()
    return {"secret": secret,
            "otpauth_uri": pyotp.TOTP(secret).provisioning_uri(name=user.email, issuer_name=ISSUER)}


def _claim_step(db: Session, user: User, code: str) -> bool:
    """True when `code` is valid for a step later than the last one used,
    and that step is now claimed. The claim is a compare-and-swap."""
    secret = _secret_of(user)
    code = (code or "").strip()
    if secret is None or len(code) != 6 or not code.isdigit():
        return False
    totp = pyotp.TOTP(secret)
    current = int(time.time()) // STEP_SECONDS
    for step in range(current - VALID_WINDOW, current + VALID_WINDOW + 1):
        if hmac.compare_digest(totp.at(step * STEP_SECONDS), code):
            won = (db.query(User)
                   .filter(User.id == user.id,
                           or_(User.totp_last_step.is_(None), User.totp_last_step < step))
                   .update({User.totp_last_step: step}, synchronize_session=False))
            db.commit()
            db.refresh(user)
            return won == 1
    return False


def confirm_enrollment(db: Session, user: User, code: str, *, request: Request | None = None,
                       keep_sid: str | None = None) -> None:
    """Turn the factor on. Every OTHER session was opened without it, so it
    ends (MFA_CHANGED); the one enrolling (keep_sid) stays."""
    if user.totp_enabled:
        raise AppException(409, ErrorCode.CONFLICT, "Two-factor sign-in is already on.")
    if not user.totp_secret or not _claim_step(db, user, code):
        raise AppException(400, ErrorCode.MFA_INVALID, "That code is not right. Check the time on your phone.")
    user.totp_enabled = True
    from app.services import auth_service
    ended = auth_service.revoke_user_sessions(db, user.id, "MFA_CHANGED", except_sid=keep_sid)
    stage_audit(db, action=AuditAction.MFA_ENABLED, user_id=user.id, entity_type="User", entity_id=user.id,
                ip_address=_client_ip(request), details={"other_sessions_ended": ended})
    db.commit()


def disable(db: Session, user: User, code: str, *, request: Request | None = None) -> None:
    if not user.totp_enabled:
        raise AppException(409, ErrorCode.CONFLICT, "Two-factor sign-in is not on.")
    if required_for(user):
        raise AppException(403, ErrorCode.MFA_NOT_ALLOWED, "Two-factor sign-in is required for bank users.")
    if not _claim_step(db, user, code):
        raise AppException(400, ErrorCode.MFA_INVALID, "That code is not right.")
    _clear(user)
    stage_audit(db, action=AuditAction.MFA_DISABLED, user_id=user.id, entity_type="User", entity_id=user.id,
                ip_address=_client_ip(request), details={"by": "self"})
    db.commit()


def admin_reset(db: Session, admin: User, target: User, *, request: Request | None = None) -> None:
    """An admin clears another bank user's MFA (lost phone). They must
    enroll again; with BANK_MFA_REQUIRED on, before their next session.
    Who may: password_service.can_manage — the ONE rule for acting on another
    person's credentials — so a bank admin never clears a fellow bank admin's
    factor (coordinator's audit HIGH); only the platform admin does."""
    from app.services.password_service import can_manage, claim_cooldown
    if target.role not in BANK_ROLES or not can_manage(db, admin, target):
        raise AppException(404, ErrorCode.NOT_FOUND, "User not found")
    # One per person per 10 minutes (coordinator, from bb's limiter check):
    # clearing a second factor is as sensitive as sending a password link.
    if not claim_cooldown(target.id, "mfa"):
        raise AppException(429, ErrorCode.RATE_LIMITED,
                           "This person's two-factor sign-in was reset in the last 10 minutes.")
    _clear(target)
    from app.services import auth_service
    auth_service.revoke_user_sessions(db, target.id, "ADMIN_REVOKED", by=admin.id)
    stage_audit(db, action=AuditAction.MFA_DISABLED, user_id=admin.id, entity_type="User", entity_id=target.id,
                bank_id=target.bank_id, agency_id=target.agency_id,
                ip_address=_client_ip(request), details={"by": "admin"})
    db.commit()


def _clear(user: User) -> None:
    user.totp_enabled = False
    user.totp_secret = None
    user.totp_last_step = None


# ── login ───────────────────────────────────────────────────────────────────
def check_login_code(db: Session, user: User, code: str | None) -> str:
    """"ok" (not enrolled, or a valid fresh code), "missing" or "invalid".
    auth_service.login turns the last two into 401s and counts "invalid"
    toward the lockout."""
    if not user.totp_enabled:
        return "ok"
    if not code:
        return "missing"
    return "ok" if _claim_step(db, user, code) else "invalid"


def second_step_owed(user: User) -> bool:
    """True when a sign-in must go through /auth/login to prove more than a
    link: an enrolled second factor, or one BANK_MFA_REQUIRED demands. No side
    effects (enrollment_gate mints a ticket; this only answers)."""
    return user.totp_enabled or required_for(user)


def enrollment_gate(db: Session, user: User) -> dict | None:
    """None when the user may have a session; otherwise the enrollment
    ticket response (BANK_MFA_REQUIRED on, bank role, not enrolled)."""
    if user.totp_enabled or not required_for(user):
        return None
    if not configured():
        # Required but impossible: refuse rather than let a bank user in
        # without the factor the deployment demands.
        raise AppException(503, ErrorCode.MFA_NOT_CONFIGURED,
                           "Two-factor sign-in is required but not configured. Contact your administrator.")
    ticket, sha = new_token()
    store = _store()
    store.hset(_TICKET_PREFIX + sha, mapping={"user_id": user.id})
    store.expire(_TICKET_PREFIX + sha, TICKET_TTL_SECONDS)
    return {"next": "ENROLL_MFA", "enrollment_token": ticket,
            "message": "Set up two-factor sign-in to continue."}


def _ticket_user(db: Session, ticket: str) -> tuple[User, str]:
    key = _TICKET_PREFIX + token_sha256(ticket or "")
    data = _store().hgetall(key) or {}
    from app.core import preauth
    principal = preauth.by_user_id(db, data.get("user_id"))    # the tenant first (A13b S1b)
    if principal is not None:
        preauth.bind(db, principal)
    user = db.get(User, principal.user_id) if principal is not None else None
    if user is None or not user.is_active:
        raise AppException(400, ErrorCode.MFA_INVALID, "This setup link has expired. Sign in again.")
    return user, key


def ticket_start(db: Session, ticket: str) -> dict:
    user, _ = _ticket_user(db, ticket)
    return start_enrollment(db, user)


def ticket_confirm(db: Session, ticket: str, code: str, device_id: str, request: Request) -> dict:
    """Confirm the new secret and open the session the login withheld."""
    from app.services import auth_service
    user, key = _ticket_user(db, ticket)
    confirm_enrollment(db, user, code, request=request)
    _store().delete(key)                          # single-use
    # Through the same post-gate path as /auth/login: device binding and the
    # LOGIN row (coordinator's audit MED).
    # No device secret on this route: a field agent with a bound device gets the uniform 403.
    return auth_service.complete_login(db, user, device_id, request, method="mfa_enrollment",
                                       device_secret=None)
