# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-07-15 — create_quick_login_token: days_valid=90 (90-day, unlimited-
#   reuse) -> minutes_valid=15. A real 90-day token from this function was
#   found hardcoded in frontend/public/collection_dashboard/script.js, a
#   publicly-servable file — anyone with the URL got a live manager login,
#   no password, for up to 90 days. Single-use enforcement lives in
#   auth_service.quick_login()/models/quick_login_token.py (this function has
#   no DB access). Full detail: /changelog.md.
# ───────────────────────────────────────────────────────────────────────────
from datetime import datetime, timedelta, timezone
from typing import Any
import hashlib
import secrets

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto", bcrypt__truncate_error=False)


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def _make_token(subject: str, token_type: str, expires_delta: timedelta, extra: dict[str, Any] | None = None) -> str:
    payload: dict[str, Any] = {
        "sub": subject,
        "type": token_type,
        "iat": datetime.now(timezone.utc),
        "exp": datetime.now(timezone.utc) + expires_delta,
        "jti": secrets.token_hex(16),
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def create_access_token(user_id: str, role: str, device_id: str) -> str:
    return _make_token(
        subject=user_id,
        token_type="access",
        expires_delta=timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
        extra={"role": role, "device_id": device_id},
    )


def create_refresh_token(user_id: str, device_id: str) -> str:
    return _make_token(
        subject=user_id,
        token_type="refresh",
        expires_delta=timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
        extra={"device_id": device_id},
    )


# collection_dashboard: mints the link token used by generate_quick_login_link.py.
# 2026-07-15 — was days_valid=90 (a 90-day, unlimited-reuse credential — see
# changelog.md). Now minutes-scale and single-use: expiry is enforced here via
# a short exp claim, single-use is enforced separately in
# auth_service.quick_login() via UsedQuickLoginToken (this function has no DB
# access, so it can't check reuse itself). Generate a fresh link right before
# handing it to someone — it is not meant to be embedded anywhere persistent.
def create_quick_login_token(user_id: str, agency_code: str, minutes_valid: int = 15) -> str:
    return _make_token(
        subject=user_id,
        token_type="quick_login",
        expires_delta=timedelta(minutes=minutes_valid),
        extra={"agency_code": agency_code},
    )


def decode_token(token: str) -> dict[str, Any]:
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        return payload
    except JWTError as exc:
        raise ValueError("Invalid or expired token") from exc


def fingerprint_device(user_agent: str, ip: str) -> str:
    raw = f"{user_agent}|{ip}"
    return hashlib.sha256(raw.encode()).hexdigest()


def generate_api_key() -> str:
    return secrets.token_urlsafe(48)
