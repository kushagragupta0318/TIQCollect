# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-07-15 — quick_login() now enforces single-use on top of the (now much
#   shorter) expiry: rejects a token whose jti is already in
#   UsedQuickLoginToken, logs the rejection as a LOGIN_FAILED audit entry, and
#   records the jti as used before minting a session. Full detail: /changelog.md.
# ───────────────────────────────────────────────────────────────────────────
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from fastapi import HTTPException, status, Request

from app.models.user import User, UserRole
from app.models.audit_log import AuditLog, AuditAction
from app.models.quick_login_token import UsedQuickLoginToken
from app.core.security import (
    verify_password, hash_password, create_access_token,
    create_refresh_token, decode_token, fingerprint_device,
)
import uuid

MAX_FAILED_ATTEMPTS = 5
LOCKOUT_MINUTES = 15


def _log(db: Session, action: AuditAction, user_id: str | None, request: Request, entity_id: str | None = None, details: dict | None = None, success: bool = True, failure_reason: str | None = None) -> None:
    log = AuditLog(
        id=str(uuid.uuid4()),
        created_at=datetime.now(timezone.utc),
        user_id=user_id,
        action=action,
        entity_type="User" if user_id else None,
        entity_id=entity_id or user_id,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        device_fingerprint=fingerprint_device(request.headers.get("user-agent", ""), request.client.host if request.client else ""),
        details=details,
        success=success,
        failure_reason=failure_reason,
    )
    db.add(log)
    db.commit()


def login(db: Session, email: str, password: str, device_id: str, request: Request) -> dict:
    user: User | None = db.query(User).filter(User.email == email).first()

    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    # Lockout check
    if user.locked_until:
        locked_dt = datetime.fromisoformat(user.locked_until)
        if datetime.now(timezone.utc) < locked_dt:
            raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Account temporarily locked. Try after 15 minutes.")

    if not verify_password(password, user.hashed_password):
        user.failed_login_attempts += 1
        if user.failed_login_attempts >= MAX_FAILED_ATTEMPTS:
            from datetime import timedelta
            user.locked_until = (datetime.now(timezone.utc) + timedelta(minutes=LOCKOUT_MINUTES)).isoformat()
        db.commit()
        _log(db, AuditAction.LOGIN_FAILED, user.id, request, success=False, failure_reason="Wrong password")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account deactivated")

    # Device binding — agents are locked to their registered device
    if user.role == UserRole.FIELD_AGENT and user.registered_device_fingerprint:
        incoming_fp = fingerprint_device(request.headers.get("user-agent", ""), request.client.host if request.client else "")
        if user.registered_device_fingerprint != incoming_fp and device_id != user.registered_device_fingerprint:
            _log(db, AuditAction.DEVICE_MISMATCH, user.id, request, success=False, failure_reason="Device mismatch")
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Device not authorized. Contact your manager.")

    # Reset failed attempts
    user.failed_login_attempts = 0
    user.locked_until = None
    user.last_login_at = datetime.now(timezone.utc).isoformat()

    access_token = create_access_token(user.id, user.role.value, device_id)
    refresh_token = create_refresh_token(user.id, device_id)

    # Store hashed refresh token for rotation
    user.hashed_refresh_token = hash_password(refresh_token)
    db.commit()

    _log(db, AuditAction.LOGIN, user.id, request)

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "role": user.role.value,
        "user_id": user.id,
        "full_name": user.full_name,
    }


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
    db.add(UsedQuickLoginToken(jti=jti, used_at=datetime.now(timezone.utc)))

    user: User | None = db.query(User).filter(User.id == payload["sub"]).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired link")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account deactivated")

    device_id = f"quicklogin-{payload.get('agency_code', '')}"
    access_token = create_access_token(user.id, user.role.value, device_id)
    refresh_token = create_refresh_token(user.id, device_id)

    user.hashed_refresh_token = hash_password(refresh_token)
    user.last_login_at = datetime.now(timezone.utc).isoformat()
    db.commit()

    _log(
        db, AuditAction.LOGIN, user.id, request,
        details={"method": "quick_login", "agency_code": payload.get("agency_code")},
    )

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "role": user.role.value,
        "user_id": user.id,
        "full_name": user.full_name,
    }


def refresh_tokens(db: Session, refresh_token: str, request: Request) -> dict:
    try:
        payload = decode_token(refresh_token)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token") from exc

    if payload.get("type") != "refresh":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token type")

    user_id: str = payload["sub"]
    device_id: str = payload.get("device_id", "")
    user = db.get(User, user_id)

    if not user or not user.is_active or not user.hashed_refresh_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired")

    if not verify_password(refresh_token, user.hashed_refresh_token):
        # Token reuse — invalidate all sessions
        user.hashed_refresh_token = None
        db.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token reuse detected. Login again.")

    new_access = create_access_token(user.id, user.role.value, device_id)
    new_refresh = create_refresh_token(user.id, device_id)
    user.hashed_refresh_token = hash_password(new_refresh)
    db.commit()

    _log(db, AuditAction.TOKEN_REFRESH, user.id, request)

    return {
        "access_token": new_access,
        "refresh_token": new_refresh,
        "token_type": "bearer",
    }


def logout(db: Session, user: User, request: Request) -> None:
    user.hashed_refresh_token = None
    db.commit()
    _log(db, AuditAction.LOGOUT, user.id, request)
