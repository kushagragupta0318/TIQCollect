# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-07-15 — quick_login() now enforces single-use on top of the (now much
#   shorter) expiry: rejects a token whose jti is already in
#   UsedQuickLoginToken, logs the rejection as a LOGIN_FAILED audit entry, and
#   records the jti as used before minting a session. Full detail: /changelog.md.
# 2026-09-24 (standalone plan A05 / A09 / A02) — sessions and device binding.
#   - A SESSION PER DEVICE. users.hashed_refresh_token was one slot per user:
#     a login on a second device silently invalidated the first device's
#     refresh token, and "token reuse detected" nulled the slot and logged the
#     user out EVERYWHERE. Each login now opens a tenancy.user_sessions row;
#     its id travels in both tokens as `sid`; refresh rotates that row only;
#     reuse revokes that row only (REUSE_DETECTED); logout revokes that row
#     only. Stored as sha256 (indexable), not bcrypt.
#   - DEVICE BINDING, finally written. users.registered_device_fingerprint was
#     read in one place and written nowhere, so binding never happened. An
#     agent's first login now binds that device (workforce.agent_devices); a
#     different device is refused with DEVICE_MISMATCH unless DEMO_MODE, where
#     the demo is driven from the simulator AND a laptop browser and a
#     re-bind is recorded instead of refused. The manager's "reset device
#     binding" action (G01) unbinds.
#   - Tokens carry bank_id / agency_id (the request context, A02) and sid.
# 2026-09-28 (A02) — every access token now also carries `perms`: the issuing
#   role's capabilities at mint time (core/permissions.role_capabilities). See
#   create_access_token's own docstring for why this is computed HERE, not
#   inside security.py, and why require_perm never trusts it back.
# 2026-09-28 (P1 A07 / A08 / A16, d4 — a shared-file edit announced to 43).
#   login() takes an optional totp_code and gains three branches after the
#   password and active checks, before any session: an enrolled user's code
#   (MFA_REQUIRED / MFA_INVALID, the latter counted toward the lockout), then
#   must_change_password (a FIRST_LOGIN ticket instead of a session), then the
#   BANK_MFA_REQUIRED enrollment ticket. revoke_user_sessions writes one
#   SESSION_REVOKED row per call. Frozen signatures are unchanged.
# ───────────────────────────────────────────────────────────────────────────
from datetime import datetime, timedelta, timezone
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from fastapi import HTTPException, status, Request

from app.core.audit import stage_audit
from app.core.config import settings
from app.core.permissions import role_capabilities
from app.core.errors import AppException, ErrorCode
from app.models.agent import Agent, AgentDevice, AgentStatus
from app.models.identity import UserSession
from app.models.user import User, UserRole
from app.models.audit_log import AuditLog, AuditAction
from app.models.quick_login_token import UsedQuickLoginToken
from app.core.security import (
    verify_password, hash_password, create_access_token, create_refresh_token, decode_token,
    device_fingerprint_for, fingerprint_device, is_disabled_password_hash, token_sha256,
)
import hashlib
import hmac
import secrets
import uuid

MAX_FAILED_ATTEMPTS = 5
LOCKOUT_MINUTES = 15
# 2026-09-28 (P1, d4; coordinator LOW): one refusal for every route that will
# not hand out a session because the account owes a step only /auth/login
# can take (a second factor, a forced change) — quick-login and refresh alike,
# so neither says which step.
SIGN_IN_REQUIRED_MESSAGE = "Sign in with your password to continue."


def _utc(dt: datetime | None) -> datetime | None:
    """SQLite returns TIMESTAMPTZ columns naive; Postgres aware. Compare aware."""
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _log(db: Session, action: AuditAction, user_id: str | None, request: Request, entity_id: str | None = None, details: dict | None = None, success: bool = True, failure_reason: str | None = None) -> None:
    log = AuditLog(
        id=str(uuid.uuid4()),
        created_at=datetime.now(timezone.utc),
        user_id=user_id,
        action=action,
        entity_type="User" if user_id else None,
        entity_id=entity_id or user_id,
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
        device_fingerprint=fingerprint_device(request.headers.get("user-agent", ""), _client_ip(request) or ""),
        details=details,
        success=success,
        failure_reason=failure_reason,
    )
    db.add(log)
    db.commit()


def _open_session(db: Session, user: User, device_id: str, request: Request) -> dict:
    """Mint a token pair under a new user_sessions row. Caller commits."""
    session = UserSession(
        id=str(uuid.uuid4()),
        user_id=user.id,
        bank_id=user.bank_id,
        agency_id=user.agency_id,
        device_id=device_id,
        user_agent=(request.headers.get("user-agent") or "")[:500] or None,
        ip_created=_client_ip(request),
        ip_last=_client_ip(request),
        refresh_token_sha256="",
        refresh_jti="",
        expires_at=datetime.now(timezone.utc) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
    )
    access = create_access_token(user.id, user.role.value, device_id, sid=session.id,
                                 bank_id=user.bank_id, agency_id=user.agency_id,
                                 perms=sorted(role_capabilities(user.role)))
    refresh = create_refresh_token(user.id, device_id, sid=session.id)
    session.refresh_token_sha256 = token_sha256(refresh)
    session.refresh_jti = decode_token(refresh)["jti"]
    db.add(session)
    return {"access_token": access, "refresh_token": refresh}


def open_session(db: Session, user: User, device_id: str, request: Request) -> dict:
    """FROZEN INTERFACE (P1 split, 2026-09-24): mint a token pair under a new
    user_sessions row, the one way a login of any kind (password, invite
    acceptance, quick-login, a future SSO) starts a session. Caller commits."""
    return _open_session(db, user, device_id, request)


def revoke_user_sessions(db: Session, user_id: str, reason: str, *, by: str | None = None,
                         except_sid: str | None = None) -> int:
    """FROZEN INTERFACE (P1 split): end every live session of `user_id` with
    `reason` (one of identity.SESSION_REVOKE_REASONS — e.g. PASSWORD_CHANGED,
    ADMIN_REVOKED, USER_DEACTIVATED, DEVICE_RESET), optionally keeping the
    caller's own session. Returns how many were revoked. Their access tokens
    stop working at once (dependencies.get_current_user checks the sid).
    Caller commits."""
    from app.models.identity import SESSION_REVOKE_REASONS
    if reason not in SESSION_REVOKE_REASONS:
        raise ValueError(f"unknown revoke reason {reason!r}")
    q = db.query(UserSession).filter(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
    if except_sid:
        q = q.filter(UserSession.id != except_sid)
    count = q.update({UserSession.revoked_at: datetime.now(timezone.utc), UserSession.revoked_reason: reason,
                      UserSession.revoked_by: by}, synchronize_session=False)
    if count:
        # 2026-09-28 (A16, d4) — ONE SESSION_REVOKED row per call, however
        # many sessions it ended (43's ask: a mass revoke is one row). Added to
        # the caller's transaction rather than through write_audit, which
        # commits on its own: this function's contract is "caller commits",
        # and the row must land exactly when the revocation does.
        stage_audit(db, action=AuditAction.SESSION_REVOKED, user_id=by or user_id, entity_type="User",
                    entity_id=user_id, details={"reason": reason, "count": count, "kept_sid": except_sid})
    return count


def _new_device_secret() -> tuple[str, str]:
    """(secret, its sha256). The secret goes to the client once; only the hash is stored."""
    secret = secrets.token_urlsafe(32)
    return secret, hashlib.sha256(secret.encode()).hexdigest()


def _secret_matches(presented: str | None, stored_sha256: str | None) -> bool:
    if not presented or not stored_sha256:
        return False
    return hmac.compare_digest(hashlib.sha256(presented.encode()).hexdigest(), stored_sha256)


def _enforce_device_binding(db: Session, user: User, device_id: str, request: Request,
                            device_secret: str | None = None) -> tuple[str, str] | None:
    """A09. First login binds; a different device is refused (or, with
    DEMO_DEVICE_REBIND on, re-bound with a record of it).

    A09b: the device_id is client-chosen, so binding ISSUES a secret and a
    later login must present it. Returns (secret, agent_device id) when this
    call bound a device, else None. A binding with no secret hash never
    matches (v2_0010 released the pre-A09b ones): the secret is never handed
    to whoever presents the device_id first. The client's IP is never part of
    the identity (owner's decision). The app keeps the secret in localStorage,
    accepted as the refresh token's exposure (coordinator, A09b audit)."""
    if user.role != UserRole.FIELD_AGENT:
        return None
    agent = db.query(Agent).filter(Agent.user_id == user.id).first()
    if agent is None:
        return None
    fp = device_fingerprint_for(device_id)
    now = datetime.now(timezone.utc)
    bound = (db.query(AgentDevice)
             .filter(AgentDevice.agent_id == agent.id, AgentDevice.is_bound.is_(True)).first())
    if bound is not None and bound.device_fingerprint == fp:
        if _secret_matches(device_secret, bound.device_secret_sha256):
            bound.last_seen_at = now
            return None
        reason = ("Device bound without a secret" if bound.device_secret_sha256 is None
                  else "Device secret missing or wrong")
    else:
        reason = "Device mismatch"
    if bound is not None:
        if not settings.DEMO_DEVICE_REBIND:
            _log(db, AuditAction.DEVICE_MISMATCH, user.id, request, success=False, failure_reason=reason)
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                                detail="Device not authorized. Contact your manager.")
        bound.is_bound = False
        bound.unbound_at = now
        bound.unbind_reason = "DEMO_DEVICE_REBIND re-bind on login from a new device"
        db.flush()
        # Staged, not committed: it lands with the new binding and the session, or not at all.
        stage_audit(db, action=AuditAction.DEVICE_MISMATCH, user_id=user.id, entity_type="User",
                    entity_id=user.id, ip_address=_client_ip(request),
                    user_agent=request.headers.get("user-agent"),
                    details={"rebound": True, "demo_device_rebind": True, "previous_device_id": bound.id,
                             "reason": reason})
    device = (db.query(AgentDevice)
              .filter(AgentDevice.agent_id == agent.id, AgentDevice.device_fingerprint == fp).first())
    if device is None:
        device = AgentDevice(id=str(uuid.uuid4()), agent_id=agent.id, device_fingerprint=fp, first_seen_at=now,
                             user_agent=(request.headers.get("user-agent") or "")[:500] or None)
        db.add(device)
    secret, device.device_secret_sha256 = _new_device_secret()
    device.is_bound = True
    device.bound_at = now
    device.unbound_at = None
    device.last_seen_at = now
    return secret, device.id


def _agent_is_suspended(db: Session, user: User) -> bool:
    """G02 (coordinator audit HIGH): suspend_agent sets both Agent.status and
    User.is_active, so this is belt-and-braces, not the primary gate — a
    FIELD_AGENT whose Agent.status is SUSPENDED must never sign in even if
    is_active somehow disagrees (a future code path that touches one column
    without the other, a manual data fix, a race)."""
    if user.role != UserRole.FIELD_AGENT:
        return False
    agent = db.query(Agent).filter(Agent.user_id == user.id).first()
    return agent is not None and agent.status == AgentStatus.SUSPENDED


def _login_response(user: User, tokens: dict) -> dict:
    return {
        **tokens,
        "token_type": "bearer",
        "role": user.role.value,
        "user_id": user.id,
        "full_name": user.full_name,
    }


def login(db: Session, email: str, password: str, device_id: str, request: Request, *,
          totp_code: str | None = None, device_secret: str | None = None) -> dict:
    user: User | None = db.query(User).filter(User.email == email).first()

    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    # Lockout check. locked_until is a TIMESTAMPTZ since 2026-09-24 (it was a
    # String parsed with fromisoformat here).
    locked_dt = _utc(user.locked_until)
    if locked_dt and datetime.now(timezone.utc) < locked_dt:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Account temporarily locked. Try after 15 minutes.")

    if not verify_password(password, user.hashed_password):
        user.failed_login_attempts += 1
        if user.failed_login_attempts >= MAX_FAILED_ATTEMPTS:
            user.locked_until = datetime.now(timezone.utc) + timedelta(minutes=LOCKOUT_MINUTES)
        db.commit()
        _log(db, AuditAction.LOGIN_FAILED, user.id, request, success=False, failure_reason="Wrong password")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    if not user.is_active or _agent_is_suspended(db, user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account deactivated")

    # 2026-09-28 (A08, A07; d4) — after the password, before any session.
    # The second factor comes FIRST: a stolen password alone must not reach
    # the forced-change ticket below, or it could take over an MFA account.
    # MFA_REQUIRED is only reachable with the right password, so it does
    # confirm the password to whoever holds it — standard for TOTP, and this
    # is the same route, under the same AUTH_LIMIT, as every other attempt.
    from app.services import mfa_service, password_service
    verdict = mfa_service.check_login_code(db, user, totp_code)
    if verdict != "ok":
        if verdict == "invalid":
            user.failed_login_attempts += 1
            if user.failed_login_attempts >= MAX_FAILED_ATTEMPTS:
                user.locked_until = datetime.now(timezone.utc) + timedelta(minutes=LOCKOUT_MINUTES)
            db.commit()
            _log(db, AuditAction.MFA_FAILED, user.id, request, success=False, failure_reason="Wrong TOTP code")
            raise AppException(401, ErrorCode.MFA_INVALID, "That code is not right.")
        raise AppException(401, ErrorCode.MFA_REQUIRED, "Enter the 6-digit code from your authenticator app.")
    if user.must_change_password:
        # No session: a single-use ticket to set a new password (FIRST_LOGIN).
        return password_service.first_login_ticket(db, user, request)
    gate = mfa_service.enrollment_gate(db, user)
    if gate is not None:
        return gate
    return complete_login(db, user, device_id, request, device_secret=device_secret)


def complete_login(db: Session, user: User, device_id: str, request: Request, *,
                   method: str | None = None, device_secret: str | None = None) -> dict:
    """Everything after the gates, for EVERY route that ends in a session:
    device binding, the lockout reset, the session, the LOGIN row, one commit.

    2026-09-28 (P1, d4; coordinator's audit MED) — invite acceptance and the
    MFA enrollment confirm called open_session directly, so they skipped
    device binding and wrote no LOGIN row. They come through here now, as
    /auth/login does; `method` names the route in the LOGIN row. Commits.
    Any staged rows of the caller (an invite accepted, a user created) land
    in this same commit. A09b: a new binding's secret is returned once, as
    `device_secret`, and its DEVICE_BOUND row commits with the LOGIN row."""
    bound_now = _enforce_device_binding(db, user, device_id, request, device_secret)

    # Reset failed attempts
    user.failed_login_attempts = 0
    user.locked_until = None
    user.last_login_at = datetime.now(timezone.utc)

    tokens = _open_session(db, user, device_id, request)
    stage_audit(db, action=AuditAction.LOGIN, user_id=user.id, entity_type="User", entity_id=user.id,
                ip_address=_client_ip(request), user_agent=request.headers.get("user-agent"),
                details={"method": method} if method else None)
    if bound_now:
        # Same commit as the session: a bind that loses the race below is never recorded as made.
        stage_audit(db, action=AuditAction.DEVICE_BOUND, user_id=user.id, entity_type="AgentDevice",
                    entity_id=bound_now[1], ip_address=_client_ip(request),
                    user_agent=request.headers.get("user-agent"), details={"agent_device_id": bound_now[1]})
    try:
        db.commit()
    except IntegrityError:
        # 2026-09-24 (audit gate 8) — two FIRST logins racing: both saw no
        # bound device and both bound one, and the one-bound-device-per-agent
        # index refused the second as an unaudited 500. It is a device
        # mismatch, and is answered and recorded as one.
        db.rollback()
        _log(db, AuditAction.DEVICE_MISMATCH, user.id, request, success=False,
             failure_reason="Concurrent first login from another device")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Device not authorized. Contact your manager.")
    response = _login_response(user, tokens)
    if bound_now:
        response["device_secret"] = bound_now[0]   # once; the app stores it
    return response


# collection_dashboard: exchanges a quick-login link token for a real session, skips password check
def quick_login(db: Session, token: str, request: Request) -> dict:
    try:
        payload = decode_token(token)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired link") from exc

    if payload.get("type") != "quick_login":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token type")

    # Single-use enforcement: expiry alone (decode_token above) isn't enough —
    # the token was previously valid for 90 days and reusable without limit.
    # One row per redeemed jti; a second redemption of the same token is
    # rejected even though it hasn't expired yet.
    jti = payload.get("jti")
    if not jti or db.get(UsedQuickLoginToken, jti):
        _log(db, AuditAction.LOGIN_FAILED, payload.get("sub"), request, success=False, failure_reason="Quick-login link already used")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="This link has already been used. Ask for a new one.")

    user: User | None = db.query(User).filter(User.id == payload["sub"]).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired link")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account deactivated")
    # 2026-09-24 (hotfix DEMO-LOGIN) — a password retired by
    # scripts/apply_demo_logins.py retires the account's quick-login links too.
    # They are stateless JWTs valid for 15 minutes and skip the password, so
    # without this an outstanding link outlived the retirement. The account
    # stays is_active (the nightly allocation reads that flag for managers).
    if is_disabled_password_hash(user.hashed_password):
        _log(db, AuditAction.LOGIN_FAILED, user.id, request, success=False,
             failure_reason="Quick-login refused: password retired by the demo master login")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired link")
    if user.role == UserRole.FIELD_AGENT:
        # 2026-09-24 (audit LOW) — a quick-login link skips device binding, so
        # an agent must never be signed in by one.
        _log(db, AuditAction.LOGIN_FAILED, user.id, request, success=False,
             failure_reason="Quick-login refused for a field agent")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Field agents sign in on their own device")
    # 2026-09-28 (A07/A08, d4; 43's review HIGH) — a link proves nothing but
    # possession of the link. An account that owes a second factor (enrolled,
    # or BANK_MFA_REQUIRED) or a forced password change signs in through
    # /auth/login only. Refused BEFORE the jti is recorded, so the link is not
    # burned; one message for all three, so it says nothing about which.
    from app.services import mfa_service
    if user.must_change_password or mfa_service.second_step_owed(user):
        _log(db, AuditAction.LOGIN_FAILED, user.id, request, success=False,
             failure_reason="Quick-login refused: account needs password sign-in")
        raise AppException(403, ErrorCode.SIGN_IN_REQUIRED, SIGN_IN_REQUIRED_MESSAGE)

    exp = payload.get("exp")
    db.add(UsedQuickLoginToken(
        jti=jti, used_at=datetime.now(timezone.utc), user_id=user.id,
        expires_at=datetime.fromtimestamp(exp, tz=timezone.utc) if exp else None,
    ))

    device_id = f"quicklogin-{payload.get('agency_code', '')}"
    tokens = _open_session(db, user, device_id, request)
    user.last_login_at = datetime.now(timezone.utc)
    db.commit()

    _log(
        db, AuditAction.LOGIN, user.id, request,
        details={"method": "quick_login", "agency_code": payload.get("agency_code")},
    )
    return _login_response(user, tokens)


def refresh_tokens(db: Session, refresh_token: str, request: Request) -> dict:
    try:
        payload = decode_token(refresh_token)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token") from exc

    if payload.get("type") != "refresh":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token type")

    user = db.get(User, payload["sub"])
    if not user or not user.is_active or _agent_is_suspended(db, user):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired")

    session = (db.query(UserSession)
               .filter(UserSession.refresh_token_sha256 == token_sha256(refresh_token)).first())
    if session is None:
        # A validly-signed refresh token that no live session holds: it was
        # rotated away already, so someone is replaying it. Revoke THAT session
        # (named by sid) — every other device stays signed in.
        sid = payload.get("sid")
        victim = db.get(UserSession, sid) if sid else None
        if victim is not None and victim.user_id == user.id and victim.revoked_at is None:
            victim.revoked_at = datetime.now(timezone.utc)
            victim.revoked_reason = "REUSE_DETECTED"
            db.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token reuse detected. Login again.")

    now = datetime.now(timezone.utc)
    if session.user_id != user.id or session.revoked_at is not None or _utc(session.expires_at) <= now:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired")
    # 2026-09-28 (A07/A08, d4; coordinator) — a refresh must not outlive a
    # step the account now owes: a forced password change set after this
    # session opened, or a second factor BANK_MFA_REQUIRED now demands. The
    # session is not revoked (the person may be at the keyboard); it simply
    # cannot be extended, so the next token needs /auth/login.
    from app.services import mfa_service
    if user.must_change_password or (mfa_service.required_for(user) and not user.totp_enabled):
        _log(db, AuditAction.TOKEN_REFRESH, user.id, request, entity_id=session.id, success=False,
             failure_reason="Refresh refused: account needs password sign-in")
        raise AppException(401, ErrorCode.SIGN_IN_REQUIRED, SIGN_IN_REQUIRED_MESSAGE)

    device_id = session.device_id
    # perms must ride refresh too (MED 2, coordinator audit 2026-09-28) — only
    # _open_session/login set it before this; a refreshed token silently lost
    # the claim after 15 min, which the frontend reads without a round trip.
    new_access = create_access_token(user.id, user.role.value, device_id, sid=session.id,
                                     bank_id=user.bank_id, agency_id=user.agency_id,
                                     perms=sorted(role_capabilities(user.role)))
    new_refresh = create_refresh_token(user.id, device_id, sid=session.id)
    # 2026-09-24 (coordinator audit gate 6) — rotate with a COMPARE-AND-SWAP.
    # Read-then-write let two concurrent refreshes with the same token both
    # succeed, so a thief racing the real device was never detected. The
    # UPDATE matches only while the row still holds the token presented; on
    # Postgres the loser blocks on the row lock, re-reads, matches 0 rows and
    # is treated exactly as a replay.
    rotated = (db.query(UserSession)
               .filter(UserSession.id == session.id,
                       UserSession.refresh_token_sha256 == token_sha256(refresh_token),
                       UserSession.revoked_at.is_(None))
               .update({UserSession.refresh_token_sha256: token_sha256(new_refresh),
                        UserSession.refresh_jti: decode_token(new_refresh)["jti"],
                        UserSession.last_used_at: now,
                        UserSession.ip_last: _client_ip(request)},
                       synchronize_session=False))
    if rotated != 1:
        db.rollback()
        (db.query(UserSession)
         .filter(UserSession.id == session.id, UserSession.revoked_at.is_(None))
         .update({UserSession.revoked_at: now, UserSession.revoked_reason: "REUSE_DETECTED"},
                 synchronize_session=False))
        db.commit()
        _log(db, AuditAction.TOKEN_REFRESH, user.id, request, entity_id=session.id, success=False,
             failure_reason="REUSE_DETECTED (concurrent refresh)")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token reuse detected. Login again.")
    db.commit()

    _log(db, AuditAction.TOKEN_REFRESH, user.id, request)

    return {
        "access_token": new_access,
        "refresh_token": new_refresh,
        "token_type": "bearer",
    }


def logout(db: Session, user: User, request: Request, sid: str | None = None) -> None:
    """Revoke the session this request's token belongs to. A token without a
    sid (minted directly, e.g. by a service account) has no session to end."""
    if sid:
        session = db.get(UserSession, sid)
        if session is not None and session.user_id == user.id and session.revoked_at is None:
            session.revoked_at = datetime.now(timezone.utc)
            session.revoked_reason = "LOGOUT"
            db.commit()
    _log(db, AuditAction.LOGOUT, user.id, request)
