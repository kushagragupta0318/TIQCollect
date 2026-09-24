# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-09-24 — NEW (live-site hotfix, board DEMO-LOGIN; the owner's decision:
#   "one password, three accounts").
#
#   The demo book's passwords were published: printed by seed_data.py, written
#   in the compose file and README, typed into the login page by its demo
#   buttons (A10 removed those). Every box restored from the fixture or seeded
#   by the script accepted them — the live site included.
#
#   This runs on EVERY API boot (docker-entrypoint.sh) when DEMO_MASTER_PASSWORD
#   is set, and makes the demo's logins exactly:
#     - the three accounts named in DEMO_MASTER_ACCOUNTS — one AGENCY_ADMIN,
#       one AGENCY_MANAGER, one FIELD_AGENT (the roles v1 has) — log in with
#       DEMO_MASTER_PASSWORD, and nothing else;
#     - every OTHER user gets an unusable password: a bcrypt hash of 32 random
#       bytes that are then discarded. Nobody, including us, knows a password
#       that verifies against it.
#   A password change revokes that user's refresh token. Access tokens already
#   issued run out on their own (ACCESS_TOKEN_EXPIRE_MINUTES).
#
#   It REFUSES — logs an error, changes nothing, exits 1 — when DEMO_MODE is
#   off, the password is shorter than 16 characters or an uninterpolated
#   "${VAR}", or the accounts are not exactly one of each role. The entrypoint
#   treats that as non-fatal: the API still starts.
#
#   The password is read from the environment only. It is never printed,
#   logged, passed on a command line, or written anywhere but as a bcrypt hash.
# ────────────────────────────────────────────────────────────────────────────
"""Apply the demo's master login. `python -m scripts.apply_demo_logins`."""
from __future__ import annotations

import secrets
import sys
from dataclasses import dataclass, field

import structlog
from sqlalchemy.orm import Session

from app.core.security import hash_password, verify_password
from app.models.user import User, UserRole

logger = structlog.get_logger()

MIN_PASSWORD_LENGTH = 16
REQUIRED_ROLES = (UserRole.AGENCY_ADMIN, UserRole.AGENCY_MANAGER, UserRole.FIELD_AGENT)

# Every disabled account's hash shares this bcrypt salt, so a later boot can
# recognise it with a string check instead of a bcrypt verify per user per
# boot. The SECRET behind each is 32 fresh random bytes, discarded at once: a
# known salt does not make an unknown secret guessable.
# 22 chars from bcrypt's alphabet, ending in one of ".Oeu" (the last salt
# character carries only two bits; any other ending is "corrected" by passlib).
_DISABLED_SALT = "DisabledDemoLoginsTIQe"
assert len(_DISABLED_SALT) == 22 and _DISABLED_SALT[-1] in ".Oeu"


@dataclass
class Outcome:
    applied: bool
    reason: str = ""
    master_set: list[str] = field(default_factory=list)       # emails whose hash was (re)set
    master_unchanged: list[str] = field(default_factory=list)
    disabled: int = 0
    already_disabled: int = 0


def _real(value: str | None) -> bool:
    v = (value or "").strip()
    return bool(v) and not v.startswith("${")


def _disabled_hash() -> str:
    from passlib.hash import bcrypt
    return bcrypt.using(salt=_DISABLED_SALT).hash(secrets.token_urlsafe(32))


def is_disabled_hash(hashed: str | None) -> bool:
    """"$2b$<rounds>$<22-char salt><31-char digest>" — compare the salt's first
    21 characters, which no bcrypt implementation rewrites."""
    parts = (hashed or "").split("$")
    return len(parts) == 4 and parts[3][:21] == _DISABLED_SALT[:21]


def parse_accounts(raw: str | None) -> list[str]:
    return [e.strip().lower() for e in (raw or "").split(",") if e.strip()]


def apply(db: Session, *, password: str | None, accounts_raw: str | None, demo_mode: bool) -> Outcome:
    """Make the three named accounts the only ones that can log in, with the
    master password. Changes nothing unless every precondition holds."""
    if not _real(password):
        return Outcome(applied=False, reason="DEMO_MASTER_PASSWORD is not set; nothing changed")
    if not demo_mode:
        return Outcome(applied=False, reason="DEMO_MODE is off; refusing to set a shared password")
    if len(password) < MIN_PASSWORD_LENGTH:
        return Outcome(applied=False, reason=f"DEMO_MASTER_PASSWORD is shorter than {MIN_PASSWORD_LENGTH} characters")

    emails = parse_accounts(accounts_raw)
    if len(emails) != len(REQUIRED_ROLES) or len(set(emails)) != len(emails):
        return Outcome(applied=False, reason=f"DEMO_MASTER_ACCOUNTS must name exactly {len(REQUIRED_ROLES)} distinct emails")
    everyone = db.query(User).all()
    # User.email is case-sensitive in the schema and nothing normalises it, so
    # "A@x.in" and "a@x.in" can both exist. Keyed by lower case, one of them
    # would drop out of the dict and keep whatever password it had — possibly
    # a published one — while the run reported success. Refuse instead.
    lowered = [(u.email or "").strip().lower() for u in everyone]
    clashes = sorted({e for e in lowered if e and lowered.count(e) > 1})
    if clashes:
        return Outcome(applied=False, reason=f"accounts differ only by letter case: {', '.join(clashes)}")
    users = {key or f"<no email:{u.id}>": u for key, u in zip(lowered, everyone)}
    missing = [e for e in emails if e not in users]
    if missing:
        return Outcome(applied=False, reason=f"DEMO_MASTER_ACCOUNTS names unknown users: {', '.join(missing)}")
    roles = sorted(users[e].role.value for e in emails)
    if roles != sorted(r.value for r in REQUIRED_ROLES):
        return Outcome(applied=False, reason=f"DEMO_MASTER_ACCOUNTS must be one {', one '.join(r.value for r in REQUIRED_ROLES)}; got {roles}")

    out = Outcome(applied=True)
    master = set(emails)
    for email, user in users.items():
        if email in master:
            if user.hashed_password and not is_disabled_hash(user.hashed_password) \
                    and verify_password(password, user.hashed_password):
                out.master_unchanged.append(email)
                continue
            user.hashed_password = hash_password(password)
            user.hashed_refresh_token = None
            out.master_set.append(email)
        elif is_disabled_hash(user.hashed_password):
            out.already_disabled += 1
        else:
            user.hashed_password = _disabled_hash()
            user.hashed_refresh_token = None
            out.disabled += 1
    db.commit()
    return out


def main() -> int:
    from app.core.config import settings
    from app.core.database import SessionLocal

    db = SessionLocal()
    try:
        out = apply(db, password=settings.DEMO_MASTER_PASSWORD, accounts_raw=settings.DEMO_MASTER_ACCOUNTS,
                    demo_mode=settings.DEMO_MODE)
    finally:
        db.close()
    if not out.applied:
        # Not-configured is normal (exit 0); every other refusal is an error.
        if "is not set" in out.reason:
            print(f"[demo-logins] {out.reason}")
            return 0
        logger.error("demo_logins.refused", reason=out.reason)
        print(f"[demo-logins] REFUSED: {out.reason}", file=sys.stderr)
        return 1
    print(f"[demo-logins] master login: {len(out.master_set)} set, {len(out.master_unchanged)} unchanged "
          f"({', '.join(sorted(out.master_set + out.master_unchanged))}); "
          f"{out.disabled} other accounts disabled, {out.already_disabled} already disabled")
    return 0


if __name__ == "__main__":
    sys.exit(main())
