# ─── CHANGELOG (standalone plan) ─────────────────────────────────────────────
# 2026-09-28 — NEW (P1 A07, d4). The password lifecycle. Until now a password
#   could be set by the seed and by nothing else: "Forgot password" was a toast.
#
#   - CHANGE (signed in): the current password, then a new one that passes
#     credentials.password_problem. Every OTHER session ends
#     (PASSWORD_CHANGED); the device that changed it stays signed in.
#   - ADMIN RESET: an admin never sees or sets a password. They get a
#     single-use link (ADMIN_RESET, 24 h), shown once, to hand over; issuing
#     it ends all the user's sessions. Who may reset whom is one function,
#     can_manage(), mirroring invite_service.can_invite.
#   - FORCED CHANGE ON FIRST LOGIN: a correct password on an account flagged
#     must_change_password opens NO session; login returns a FIRST_LOGIN
#     ticket (15 min, single-use) that only sets a new password.
#   - SELF-SERVICE RESET BY SMS OTP: forgot -> verify -> reset.
#     * No account enumeration: forgot answers the same body, with a fresh
#       request id, whether or not the account exists, has a phone, is
#       throttled or the SMS failed. An unknown request id fails verification
#       exactly as a wrong code does.
#     * The code is 6 digits (not the 4-digit payment OTP), lives 10 minutes,
#       allows 5 tries, and an account gets at most 3 requests an hour. It is
#       stored hashed in OtpService's store (Redis, or the in-process
#       fallback), never in the database and never logged.
#     * A verified code yields a SELF_SERVICE token with otp_verified_at set;
#       only that token resets the password.
#   - Every reset ends all sessions and clears the lockout. MFA is NOT reset:
#     the next login still asks for the code.
# ────────────────────────────────────────────────────────────────────────────
"""Change, reset (admin link, first-login ticket, SMS OTP) a password."""
from __future__ import annotations

import hmac
import secrets
import threading
from datetime import timedelta

import structlog
from fastapi import Request
from sqlalchemy.orm import Session, sessionmaker

from app.core.audit import stage_audit, write_audit
from app.core.config import settings
from app.core.errors import AppException, ErrorCode
from app.core.security import hash_password, token_sha256, verify_password
from app.models.audit_log import AuditAction
from app.models.identity import PasswordResetToken
from app.models.user import BANK_ROLES, User, UserRole
from app.services.credentials import new_token, now, require_good_password, utc

ADMIN_RESET_TTL = timedelta(hours=24)
FIRST_PASSWORD_TTL = timedelta(hours=72)          # a new account's first password, like an invitation
RESET_COOLDOWN_SECONDS = 600                      # one admin-sent link per person per 10 minutes
FIRST_LOGIN_TTL = timedelta(minutes=15)
SELF_SERVICE_TTL = timedelta(minutes=15)       # after the code is verified
RESET_OTP_LENGTH = 6
RESET_OTP_TTL_SECONDS = 600
RESET_OTP_MAX_ATTEMPTS = 5
RESET_REQUESTS_PER_HOUR = 3
_OTP_PREFIX = "pwreset:"
_COUNT_PREFIX = "pwreset_count:"
_COOLDOWN_PREFIX = "pwreset_admin_cooldown:"

_INVALID_LINK = "This reset link is not valid. It may have expired or already been used."
_BAD_CODE = "That code is not right, or it has expired. Request a new one."
logger = structlog.get_logger()
FORGOT_MESSAGE = ("If an account matches, a 6-digit code has been sent to its registered phone. "
                  "It expires in 10 minutes.")


def _client_ip(request: Request | None) -> str | None:
    return request.client.host if request is not None and request.client else None


def _store():
    from app.services import otp_service
    return otp_service._default_store()


def reset_password_path(token: str) -> str:
    """The frontend route that takes a reset token (pages/auth/ResetPasswordPage)."""
    return f"/reset-password?token={token}"


# ── who may reset whom ──────────────────────────────────────────────────────
_BANK_ADMIN_MANAGES = frozenset({UserRole.BANK_ANALYST, UserRole.BANK_TECHOPS, UserRole.AGENCY_ADMIN})

def can_manage(db: Session, admin: User, target: User) -> bool:
    """The one rule for an admin acting on another person's credentials.
    Never yourself; never across tenants.

    2026-09-28 (ce's G02 ask): an AGENCY_MANAGER may act on a FIELD_AGENT they
    manage — the everyday case, an agent who forgot their password and asks
    their own manager. "The agents a manager manages" is scope.agents_in_scope
    (43's frozen interface: same agency AND Agent.manager_user_id), not
    restated here — which is why this takes `db`. Only agents: never another
    manager, the agency admin, or anyone's MFA (mfa_service.admin_reset stays
    bank-roles-only)."""
    if admin.id == target.id:
        return False
    if admin.role == UserRole.AGENCY_MANAGER:
        if target.role != UserRole.FIELD_AGENT:
            return False
        from app.models.agent import Agent, AgentStatus
        from app.services.scope import agents_in_scope
        # Not a SUSPENDED agent (coordinator's audit LOW of 12c3232): their
        # User can still be active, and a reset would hand them a way back in.
        return (agents_in_scope(db, admin)
                .filter(Agent.user_id == target.id, Agent.status != AgentStatus.SUSPENDED)
                .first() is not None)
    if admin.role == UserRole.PLATFORM_ADMIN:
        return target.role == UserRole.BANK_ADMIN
    if admin.role == UserRole.BANK_ADMIN:
        # Never a fellow BANK_ADMIN (coordinator's audit HIGH, 2026-09-28): one
        # admin resetting another's password or factor is a takeover with
        # nobody else involved. A bank admin's credentials are reset by the
        # platform admin only — the simplest rule that needs a second party.
        return target.bank_id == admin.bank_id and target.role in _BANK_ADMIN_MANAGES
    if admin.role == UserRole.AGENCY_ADMIN:
        return (target.agency_id is not None and target.agency_id == admin.agency_id
                and target.role in (UserRole.AGENCY_MANAGER, UserRole.FIELD_AGENT))
    return False


# ── tokens ──────────────────────────────────────────────────────────────────
def _issue(db: Session, user: User, kind: str, ttl: timedelta, *, issued_by: str | None = None,
           otp_verified: bool = False, request: Request | None = None) -> str:
    """A new single-use token; any earlier open token of the user is spent,
    so only the newest link works. Caller commits."""
    stamp = now()
    (db.query(PasswordResetToken)
     .filter(PasswordResetToken.user_id == user.id, PasswordResetToken.used_at.is_(None))
     .update({PasswordResetToken.used_at: stamp}, synchronize_session=False))
    plain, sha = new_token()
    db.add(PasswordResetToken(user_id=user.id, kind=kind, token_sha256=sha, issued_by=issued_by,
                              otp_verified_at=stamp if otp_verified else None, expires_at=stamp + ttl,
                              requested_ip=_client_ip(request)))
    return plain


def first_login_ticket(db: Session, user: User, request: Request | None = None) -> dict:
    """Called by auth_service.login after a CORRECT password on an account
    flagged must_change_password: a ticket, not a session."""
    user.failed_login_attempts = 0
    user.locked_until = None
    token = _issue(db, user, "FIRST_LOGIN", FIRST_LOGIN_TTL, request=request)
    db.commit()
    return {"next": "CHANGE_PASSWORD", "reset_token": token,
            "message": "Choose a new password to finish signing in."}


def admin_reset(db: Session, admin: User, target_id: str, *, request: Request | None = None) -> dict:
    """The link goes to the TARGET's phone, never to the admin (coordinator's
    audit MED): an admin holding a working link could take over any account
    without a second factor. Returns only whether it was sent and when it
    expires."""
    target = db.get(User, target_id)
    if target is None or not can_manage(db, admin, target):
        raise AppException(404, ErrorCode.NOT_FOUND, "User not found")
    return _issue_and_text(
        db, admin, target, kind="ADMIN_RESET", ttl=ADMIN_RESET_TTL, audit_kind="ADMIN_RESET", revoke=True,
        text="Your TIQCollect administrator started a password reset. Set a new password within 24 hours: ",
        request=request)


def issue_first_password(db: Session, admin: User, target: User, *, request: Request | None = None) -> dict:
    """An account created WITHOUT a password — G02's new field agent — gets a
    single-use link texted to its own phone to choose one (ce's ask,
    2026-09-28). Same gate as admin_reset (can_manage: an agent's own manager,
    or their agency admin), same delivery, same {sent, expires_at}. The kind is
    FIRST_LOGIN, the audit row PASSWORD_RESET_ISSUED with kind FIRST_PASSWORD,
    and it lives 72 hours like an invitation: a new agent may not open it for
    a day or two, where a reset should be acted on the same day.
    The CALLER creates the user with an unusable password
    (security.disabled_password_hash-style) and commits it first."""
    if not can_manage(db, admin, target):
        raise AppException(404, ErrorCode.NOT_FOUND, "User not found")
    if not never_had_a_password(db, target):
        # An ESTABLISHED account (coordinator's audit MED of 12c3232): a
        # "first" password would spend its open tokens and leave its sessions
        # running. That is a reset, and admin_reset is the call for it.
        raise AppException(409, ErrorCode.CONFLICT,
                           "This account already has a password. Use \"reset login\" instead.")
    return _issue_and_text(
        db, admin, target, kind="FIRST_LOGIN", ttl=FIRST_PASSWORD_TTL, audit_kind="FIRST_PASSWORD", revoke=False,
        text="Welcome to TIQCollect. Choose your password within 72 hours to sign in: ",
        request=request)


def never_had_a_password(db: Session, user: User) -> bool:
    """True for an account created without one: never signed in, never had a
    session, never set a password. (The hotfix's unusable-hash marker reaches
    p1 at the next rebase; until then these three facts are the evidence, and
    any one of them missing means the account is established.)"""
    from app.models.identity import UserSession
    if user.last_login_at is not None or user.password_changed_at is not None:
        return False
    return db.query(UserSession.id).filter(UserSession.user_id == user.id).first() is None


def _claim_cooldown(target_id: str) -> bool:
    """One credential link per target per RESET_COOLDOWN_SECONDS (coordinator's
    audit MED of 12c3232): every manager can reach the route now, and each
    call costs an SMS and ends the target's sessions. SET NX in the OTP store
    (Redis, or the in-process fallback), so it holds across requests."""
    return bool(_store().set(_COOLDOWN_PREFIX + target_id, "1", nx=True, ex=RESET_COOLDOWN_SECONDS))


def _issue_and_text(db: Session, admin: User, target: User, *, kind: str, ttl: timedelta, audit_kind: str,
                    revoke: bool, text: str, request: Request | None) -> dict:
    """Mint a single-use link for `target`, text it to THEIR phone, return
    only {sent, expires_at}. Refuses (and changes nothing) without
    PUBLIC_BASE_URL. The audit row lands in the same commit as the token."""
    if not target.is_active:
        raise AppException(409, ErrorCode.CONFLICT, "The account is deactivated.")
    base = (settings.PUBLIC_BASE_URL or "").strip().rstrip("/")
    if not base or base.startswith("${"):
        raise AppException(503, ErrorCode.CHANNEL_UNAVAILABLE,
                           "Password links are sent by SMS and need PUBLIC_BASE_URL. Nothing was changed.")
    if not _claim_cooldown(target.id):
        raise AppException(429, ErrorCode.RATE_LIMITED,
                           "A link was sent to this person in the last 10 minutes. Ask them to check their phone.")
    token = _issue(db, target, kind, ttl, issued_by=admin.id, request=request)
    if revoke:
        from app.services import auth_service
        auth_service.revoke_user_sessions(db, target.id, "ADMIN_REVOKED", by=admin.id)
    stage_audit(db, action=AuditAction.PASSWORD_RESET_ISSUED, user_id=admin.id, entity_type="User",
                entity_id=target.id, ip_address=_client_ip(request),
                details={"kind": audit_kind, "channel": "SMS"})
    db.commit()
    from app.services.notification_service import NotificationService
    sent = NotificationService.send_sms(
        "+" + NotificationService.normalize_phone(target.phone), f"{text}{base}{reset_password_path(token)}",
        db=db, user_id=target.id)
    return {"sent": bool(sent), "expires_at": (now() + ttl).isoformat()}


def reset_with_token(db: Session, token: str, new_password: str, *, request: Request | None = None) -> None:
    row = (db.query(PasswordResetToken)
           .filter(PasswordResetToken.token_sha256 == token_sha256(token or "")).first())
    if (row is None or row.used_at is not None or utc(row.expires_at) <= now()
            or (row.kind == "SELF_SERVICE" and row.otp_verified_at is None)):
        raise AppException(400, ErrorCode.RESET_INVALID, _INVALID_LINK)
    user = db.get(User, row.user_id)
    if user is None or not user.is_active:
        raise AppException(400, ErrorCode.RESET_INVALID, _INVALID_LINK)
    require_good_password(new_password, email=user.email)
    if verify_password(new_password, user.hashed_password):
        raise AppException(422, ErrorCode.PASSWORD_POLICY, "Choose a password you have not used here before.")
    stamp = now()
    won = (db.query(PasswordResetToken)
           .filter(PasswordResetToken.id == row.id, PasswordResetToken.used_at.is_(None))
           .update({PasswordResetToken.used_at: stamp}, synchronize_session=False))
    if won != 1:
        db.rollback()
        raise AppException(400, ErrorCode.RESET_INVALID, _INVALID_LINK)
    _set_password(user, new_password, stamp)
    from app.services import auth_service
    auth_service.revoke_user_sessions(db, user.id, "PASSWORD_CHANGED")
    stage_audit(db, action=AuditAction.PASSWORD_RESET, user_id=user.id, entity_type="User", entity_id=user.id,
                ip_address=_client_ip(request), details={"kind": row.kind, "issued_by": row.issued_by})
    db.commit()


def _set_password(user: User, password: str, stamp) -> None:
    user.hashed_password = hash_password(password)
    user.password_changed_at = stamp
    user.must_change_password = False
    user.failed_login_attempts = 0
    user.locked_until = None


# ── change (signed in) ──────────────────────────────────────────────────────
def change_password(db: Session, user: User, current_password: str, new_password: str, *,
                    sid: str | None = None, request: Request | None = None) -> None:
    from app.services import auth_service
    locked = utc(user.locked_until)
    if locked and now() < locked:
        raise AppException(429, ErrorCode.RATE_LIMITED, "Too many wrong attempts. Try again in 15 minutes.")
    if not verify_password(current_password or "", user.hashed_password):
        # Counts toward the same lockout as a wrong password at sign-in
        # (coordinator LOW): a stolen session must not be a free guessing oracle.
        user.failed_login_attempts += 1
        if user.failed_login_attempts >= auth_service.MAX_FAILED_ATTEMPTS:
            user.locked_until = now() + timedelta(minutes=auth_service.LOCKOUT_MINUTES)
        db.commit()
        write_audit(db, action=AuditAction.PASSWORD_CHANGED, user_id=user.id, entity_type="User",
                    entity_id=user.id, success=False, failure_reason="Wrong current password",
                    ip_address=_client_ip(request))
        raise AppException(400, ErrorCode.PASSWORD_INCORRECT, "Your current password is not right.")
    require_good_password(new_password, email=user.email)
    if hmac.compare_digest(current_password, new_password):
        raise AppException(422, ErrorCode.PASSWORD_POLICY, "The new password must be different.")
    _set_password(user, new_password, now())
    ended = auth_service.revoke_user_sessions(db, user.id, "PASSWORD_CHANGED", except_sid=sid)
    stage_audit(db, action=AuditAction.PASSWORD_CHANGED, user_id=user.id, entity_type="User", entity_id=user.id,
                ip_address=_client_ip(request), details={"other_sessions_ended": ended})
    db.commit()


# ── self-service by SMS OTP ─────────────────────────────────────────────────
def _find_user(db: Session, identifier: str) -> User | None:
    ident = (identifier or "").strip()
    if not ident:
        return None
    if "@" in ident:
        return db.query(User).filter(User.email == ident.lower()).first()
    digits = "".join(c for c in ident if c.isdigit())
    if len(digits) < 10:
        return None
    return db.query(User).filter(User.phone.in_(sorted({digits, digits[-10:]}))).first()


def _hash_code(request_sha: str, code: str) -> str:
    # Bound to the request, so a code cannot be moved to another request.
    return token_sha256(f"{request_sha}:{code}")


def forgot(db: Session, identifier: str, *, request: Request | None = None) -> dict:
    """Always the same answer. Sends a code only to an active, eligible
    account under its hourly cap."""
    request_id, request_sha = new_token()
    out = {"request_id": request_id, "message": FORGOT_MESSAGE}
    user = _find_user(db, identifier)
    if user is None or not user.is_active or not user.phone or user.role == UserRole.SERVICE:
        return out
    store = _store()
    count_key = _COUNT_PREFIX + user.id
    requests_this_hour = int(store.incr(count_key))
    if requests_this_hour == 1:
        store.expire(count_key, 3600)
    if requests_this_hour > RESET_REQUESTS_PER_HOUR:
        return out
    code = str(secrets.randbelow(10 ** RESET_OTP_LENGTH)).zfill(RESET_OTP_LENGTH)
    key = _OTP_PREFIX + request_sha
    store.hset(key, mapping={"user_id": user.id, "code": _hash_code(request_sha, code), "attempts": "0"})
    store.expire(key, RESET_OTP_TTL_SECONDS)
    # Off the request path (coordinator LOW): sending an SMS takes hundreds of
    # milliseconds that an unknown account never spends, and that difference
    # would answer the question the identical body refuses to. The send and
    # its audit row run on their own session.
    factory = sessionmaker(bind=db.get_bind())
    _dispatch(lambda: _send_reset_code(factory, user.id, user.phone, code, _client_ip(request)))
    return out


def _dispatch(fn) -> None:
    """Run `fn` after the response. A daemon thread: a lost SMS is recoverable
    (the person asks again), a request held open by a slow SMS gateway is an
    enumeration oracle. Tests replace this with a synchronous call."""
    threading.Thread(target=fn, name="pwreset-sms", daemon=True).start()


def _send_reset_code(factory, user_id: str, phone: str, code: str, ip: str | None) -> None:
    from app.services.notification_service import NotificationService
    db = factory()
    try:
        NotificationService.send_sms(
            "+" + NotificationService.normalize_phone(phone),
            f"Your TIQCollect password reset code is {code}. It expires in 10 minutes. Never share it.",
            db=db, user_id=user_id)
        write_audit(db, action=AuditAction.PASSWORD_RESET_ISSUED, user_id=user_id, entity_type="User",
                    entity_id=user_id, ip_address=ip, details={"kind": "SELF_SERVICE"})
    except Exception as exc:  # noqa: BLE001 — never raised into a thread nobody joins
        logger.error("password_reset.sms_dispatch_failed", error_type=type(exc).__name__, exc_info=True)
    finally:
        db.close()


def verify_code(db: Session, request_id: str, code: str, *, request: Request | None = None) -> dict:
    """A right code within its window and tries yields the reset token."""
    request_sha = token_sha256(request_id or "")
    key = _OTP_PREFIX + request_sha
    store = _store()
    data = store.hgetall(key) or {}
    if not data.get("user_id"):
        raise AppException(400, ErrorCode.RESET_INVALID, _BAD_CODE)
    attempts = int(store.hincrby(key, "attempts", 1))
    if attempts > RESET_OTP_MAX_ATTEMPTS:
        store.delete(key)
        raise AppException(400, ErrorCode.RESET_INVALID, _BAD_CODE)
    if not hmac.compare_digest(data.get("code", ""), _hash_code(request_sha, (code or "").strip())):
        raise AppException(400, ErrorCode.RESET_INVALID, _BAD_CODE)
    store.delete(key)                               # single-use
    user = db.get(User, data["user_id"])
    if user is None or not user.is_active:
        raise AppException(400, ErrorCode.RESET_INVALID, _BAD_CODE)
    token = _issue(db, user, "SELF_SERVICE", SELF_SERVICE_TTL, otp_verified=True, request=request)
    db.commit()
    return {"reset_token": token}
