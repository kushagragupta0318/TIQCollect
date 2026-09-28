# ─── CHANGELOG (standalone plan) ─────────────────────────────────────────────
# 2026-09-28 — NEW (P1 A06/A07/A08, d4). The pieces every account flow shares,
#   in one place so invites, password changes, resets and MFA cannot drift:
#   - the password rule (password_problem). The frontend mirrors it in
#     pages/auth/passwordRule.ts, and a vitest holds the two lists together;
#   - single-use tokens: minted with `secrets`, shown once, stored as sha256
#     (security.token_sha256, the same form sessions use, so it is indexable);
#   - aware-UTC time, because SQLite hands TIMESTAMPTZ back naive.
# ────────────────────────────────────────────────────────────────────────────
"""Shared rules for account flows: passwords, one-time tokens, time."""
from __future__ import annotations

import re
import secrets
from datetime import datetime, timezone

from app.core.errors import AppException, ErrorCode
from app.core.security import token_sha256

PASSWORD_MIN_LENGTH = 10
PASSWORD_MAX_LENGTH = 128
# The obvious choices, compared after lower-casing and stripping digits and
# symbols ("Password@123" is "password"). Not a breach list, a floor.
_COMMON = frozenset({
    "password", "passw", "welcome", "qwerty", "qwertyuiop", "admin", "letmein", "iloveyou",
    "tiqcollect", "collection", "collections", "manager", "agent", "abcdef", "abcdefgh",
})


def password_problem(password: str | None, *, email: str | None = None) -> str | None:
    """None when the password is acceptable; otherwise the reason, in words
    the person choosing it can act on."""
    p = password or ""
    if len(p) < PASSWORD_MIN_LENGTH:
        return f"Use at least {PASSWORD_MIN_LENGTH} characters."
    if len(p) > PASSWORD_MAX_LENGTH:
        return f"Use at most {PASSWORD_MAX_LENGTH} characters."
    if not re.search(r"[A-Za-z]", p) or not re.search(r"\d", p):
        return "Use at least one letter and one number."
    if re.sub(r"[^a-z]", "", p.lower()) in _COMMON:
        return "That password is too common."
    local = (email or "").split("@", 1)[0].lower()
    if len(local) >= 4 and local in p.lower():
        return "Don't use your email address in your password."
    return None


def require_good_password(password: str | None, *, email: str | None = None) -> None:
    problem = password_problem(password, email=email)
    if problem:
        raise AppException(422, ErrorCode.PASSWORD_POLICY, problem)


def new_token() -> tuple[str, str]:
    """(plaintext, sha256). The plaintext is returned to exactly one caller
    and never stored."""
    plain = secrets.token_urlsafe(32)
    return plain, token_sha256(plain)


def now() -> datetime:
    return datetime.now(timezone.utc)


def utc(dt: datetime | None) -> datetime | None:
    """SQLite returns TIMESTAMPTZ columns naive; Postgres aware. Compare aware."""
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def flag_on(value) -> bool:
    """A switch is on only when it says "true" (any case); a literal ${VAR}
    reads as off. The same rule as the hotfix's security.explicit_true, which
    replaces this at the P1 rebase (one definition)."""
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() == "true"
