# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-07-15 — create_quick_login_token: days_valid=90 (90-day, unlimited-
#   reuse) -> minutes_valid=15. A real 90-day token from this function was
#   found hardcoded in frontend/public/collection_dashboard/script.js, a
#   publicly-servable file — anyone with the URL got a live manager login,
#   no password, for up to 90 days. Single-use enforcement lives in
#   auth_service.quick_login()/models/quick_login_token.py (this function has
#   no DB access). Full detail: /changelog.md.
# 2026-09-24 (hotfix DEMO-LOGIN) — disabled_password_hash() /
#   is_disabled_password_hash(): the one definition of "this account's
#   password was retired by scripts/apply_demo_logins.py", read by that
#   script and by quick-login (which must refuse such an account: its
#   quick-login links are stateless JWTs and outlive the password). And
#   demo_master_login_active(), read by the ML approve/promote gate.
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


# Every retired password shares this bcrypt salt, so the marker is a string
# check, not a bcrypt verify per user per boot. The SECRET behind each hash is
# 32 fresh random bytes, discarded at once: a known salt does not make an
# unknown secret guessable. 22 chars of bcrypt's alphabet, ending in one of
# ".Oeu" (the last salt character carries two bits; others are rewritten).
DISABLED_PASSWORD_SALT = "DisabledDemoLoginsTIQe"


def disabled_password_hash() -> str:
    from passlib.hash import bcrypt
    return bcrypt.using(salt=DISABLED_PASSWORD_SALT).hash(secrets.token_urlsafe(32))


def is_disabled_password_hash(hashed: str | None) -> bool:
    """"$2b$<rounds>$<22-char salt><31-char digest>": compare the salt's first
    21 characters, which no bcrypt implementation rewrites."""
    parts = (hashed or "").split("$")
    return len(parts) == 4 and parts[3][:21] == DISABLED_PASSWORD_SALT[:21]


def demo_master_login_active() -> bool:
    """True while this box runs the shared demo master login. One password
    for an admin AND a manager lets one person be both halves of a four-eyes
    check, so the gates that rely on two people refuse while it is on."""
    v = (settings.DEMO_MASTER_PASSWORD or "").strip()
    return bool(v) and not v.startswith("${")


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


def create_agent_verify_token(agent_id: str, days_valid: int = 365) -> str:
    """Signed token embedded in the agent's QR ID. A borrower scans it and the
    public /verify-agent endpoint validates the signature — so a fraudster can't
    forge a genuine-looking card without SECRET_KEY. Long-lived (card lifetime)."""
    return _make_token(
        subject=agent_id,
        token_type="agent_verify",
        expires_delta=timedelta(days=days_valid),
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
