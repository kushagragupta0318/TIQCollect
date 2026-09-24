# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-09-24 — NEW (live-site hotfix, board DEMO-LOGIN; the owner's decision:
#   "one password, three accounts").
#
#   The demo book's passwords were published: printed by seed_data.py, written
#   in the compose file and README, typed into the login page by its demo
#   buttons (A10 removed those). Every box restored from the fixture or seeded
#   by the script accepted them — the live site included.
#
#   Run on every API boot (docker-entrypoint.sh) when DEMO_MASTER_PASSWORD is
#   set:
#     - the three accounts in DEMO_MASTER_ACCOUNTS — one AGENCY_ADMIN, one
#       AGENCY_MANAGER, one FIELD_AGENT (the roles v1 has) — log in with
#       DEMO_MASTER_PASSWORD;
#     - accounts in DEMO_MASTER_KEEP_ACCOUNTS are NEVER touched: the Collections
#       Command Center logs in here as agency managers with its own passwords
#       (its TIQCOLLECT_AGENCY_ACCOUNTS), and changing theirs 502s every
#       /field/* page it serves;
#     - every OTHER account's password is retired (a bcrypt hash of 32 random
#       bytes, discarded) ONLY when DEMO_MASTER_DISABLE_OTHERS=true is also set,
#       and only if every one of them is a demo account: an email in
#       DEMO_EMAIL_DOMAINS, and no more users than the demo fixture has.
#       DEMO_MODE alone cannot tell demo from real — .env.example ships it true
#       and the compose file defaults it on — so a real deployment that set the
#       master password would otherwise lock out every real user, every boot.
#   A password change clears that user's refresh token; quick-login refuses a
#   retired account (auth_service.quick_login).
#
#   REFUSES — logs an error, changes nothing, exit 1 — on DEMO_MODE off, a
#   password under 16 characters, the wrong accounts, a keep account that is
#   also a master one, accounts that differ only by letter case, or (with
#   DISABLE_OTHERS) a non-demo account or too many users. Not configured is
#   exit 3, so the entrypoint can say "not applied" rather than "applied".
#
#   The password is read from the environment only. It is never printed,
#   logged, passed on a command line, or written anywhere but as a bcrypt hash.
# ────────────────────────────────────────────────────────────────────────────
"""Apply the demo's master login. `python -m scripts.apply_demo_logins`."""
from __future__ import annotations

import sys
from dataclasses import dataclass, field

import structlog
from sqlalchemy.orm import Session

from app.core.security import (
    disabled_password_hash, explicit_true, hash_password, is_disabled_password_hash, verify_password,
)
from app.models.user import User, UserRole

logger = structlog.get_logger()

MIN_PASSWORD_LENGTH = 16
REQUIRED_ROLES = (UserRole.AGENCY_ADMIN, UserRole.AGENCY_MANAGER, UserRole.FIELD_AGENT)
# The committed demo book (backend/fixtures/tables/users.csv, 2026-09-24): 21
# users, every one @tiqcollect.in. A box with more users than that has users
# the demo did not create, and is not one whose passwords this may retire.
FIXTURE_USER_COUNT = 21

EXIT_APPLIED, EXIT_REFUSED, EXIT_NOT_CONFIGURED = 0, 1, 3

# Kept as names for the tests and older callers; the definitions live in
# app/core/security.py, which quick-login also reads.
is_disabled_hash = is_disabled_password_hash
_disabled_hash = disabled_password_hash


@dataclass
class Outcome:
    applied: bool
    reason: str = ""
    configured: bool = True
    master_set: list[str] = field(default_factory=list)       # emails whose hash was (re)set
    master_unchanged: list[str] = field(default_factory=list)
    kept: int = 0
    disabled: int = 0
    already_disabled: int = 0
    left_alone: int = 0                                         # others, when DISABLE_OTHERS is off


def _real(value: str | None) -> bool:
    v = (value or "").strip()
    return bool(v) and not v.startswith("${")


def parse_accounts(raw: str | None) -> list[str]:
    return [e.strip().lower() for e in (raw or "").split(",") if e.strip() and not e.strip().startswith("${")]


def _domain(email: str) -> str:
    return email.rsplit("@", 1)[-1] if "@" in email else ""


def apply(db: Session, *, password: str | None, accounts_raw: str | None, demo_mode: bool,
          keep_raw: str | None = "", disable_others: bool = False,
          demo_domains: str | None = "tiqcollect.in", max_users: int = FIXTURE_USER_COUNT) -> Outcome:
    """Give the three named accounts the master password; with disable_others,
    retire every other demo account's password. Changes nothing unless every
    precondition holds."""
    if not _real(password):
        return Outcome(applied=False, configured=False, reason="DEMO_MASTER_PASSWORD is not set; nothing changed")
    if not demo_mode:
        return Outcome(applied=False, reason="DEMO_MODE is off; refusing to set a shared password")
    if len(password) < MIN_PASSWORD_LENGTH:
        return Outcome(applied=False, reason=f"DEMO_MASTER_PASSWORD is shorter than {MIN_PASSWORD_LENGTH} characters")

    emails = parse_accounts(accounts_raw)
    if len(emails) != len(REQUIRED_ROLES) or len(set(emails)) != len(emails):
        return Outcome(applied=False, reason=f"DEMO_MASTER_ACCOUNTS must name exactly {len(REQUIRED_ROLES)} distinct emails")
    keep = set(parse_accounts(keep_raw))
    # A keep-list that is set but names nobody (or carries an unresolved
    # ${VAR}) is a mistake that would retire the very accounts it meant to
    # protect: refuse rather than drop the entry (coordinator re-audit).
    if (keep_raw or "").strip() and ("${" in keep_raw or not keep):
        return Outcome(applied=False, reason="DEMO_MASTER_KEEP_ACCOUNTS is set but names no account, "
                                             "or holds an unresolved ${...}")
    if keep & set(emails):
        return Outcome(applied=False, reason=f"an account is both master and keep: {', '.join(sorted(keep & set(emails)))}")

    everyone = db.query(User).all()
    # User.email is case-sensitive in the schema and nothing normalises it, so
    # "A@x.in" and "a@x.in" can both exist. Keyed by lower case, one of them
    # would drop out and keep whatever password it had while the run reported
    # success. Refuse instead.
    lowered = [(u.email or "").strip().lower() for u in everyone]
    clashes = sorted({e for e in lowered if e and lowered.count(e) > 1})
    if clashes:
        return Outcome(applied=False, reason=f"accounts differ only by letter case: {', '.join(clashes)}")
    users = {key or f"<no email:{u.id}>": u for key, u in zip(lowered, everyone)}
    missing = [e for e in [*emails, *sorted(keep)] if e not in users]
    if missing:
        return Outcome(applied=False, reason=f"unknown accounts: {', '.join(missing)}")
    roles = sorted(users[e].role.value for e in emails)
    if roles != sorted(r.value for r in REQUIRED_ROLES):
        return Outcome(applied=False, reason=f"DEMO_MASTER_ACCOUNTS must be one {', one '.join(r.value for r in REQUIRED_ROLES)}; got {roles}")

    master = set(emails)
    others = [e for e in users if e not in master and e not in keep]
    if disable_others:
        domains = {d.strip().lower() for d in (demo_domains or "").split(",") if d.strip()}
        foreign = sorted(e for e in others if _domain(e) not in domains)
        if foreign:
            return Outcome(applied=False, reason=(f"{len(foreign)} account(s) outside the demo domains "
                                                  f"{sorted(domains)}; this is not a demo box"))
        if len(users) > max_users:
            return Outcome(applied=False, reason=(f"{len(users)} users, more than the demo fixture's {max_users}; "
                                                  "this is not a demo box"))

    out = Outcome(applied=True, kept=len(keep))
    for email in sorted(master):
        user = users[email]
        if user.hashed_password and not is_disabled_password_hash(user.hashed_password) \
                and verify_password(password, user.hashed_password):
            out.master_unchanged.append(email)
            continue
        user.hashed_password = hash_password(password)
        user.hashed_refresh_token = None
        out.master_set.append(email)
    for email in others:
        user = users[email]
        if not disable_others:
            out.left_alone += 1
        elif is_disabled_password_hash(user.hashed_password):
            out.already_disabled += 1
        else:
            user.hashed_password = disabled_password_hash()
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
                    demo_mode=settings.DEMO_MODE, keep_raw=settings.DEMO_MASTER_KEEP_ACCOUNTS,
                    disable_others=explicit_true(settings.DEMO_MASTER_DISABLE_OTHERS),
                    demo_domains=settings.DEMO_EMAIL_DOMAINS)
    finally:
        db.close()
    if not out.applied:
        if not out.configured:
            print(f"[demo-logins] {out.reason}")
            return EXIT_NOT_CONFIGURED
        logger.error("demo_logins.refused", reason=out.reason)
        print(f"[demo-logins] REFUSED: {out.reason}", file=sys.stderr)
        return EXIT_REFUSED
    print(f"[demo-logins] master login: {len(out.master_set)} set, {len(out.master_unchanged)} unchanged "
          f"({', '.join(sorted(out.master_set + out.master_unchanged))}); {out.kept} kept; "
          + (f"{out.disabled} other accounts retired, {out.already_disabled} already retired"
             if explicit_true(settings.DEMO_MASTER_DISABLE_OTHERS) else
             f"{out.left_alone} other accounts left as they are (DEMO_MASTER_DISABLE_OTHERS is off)"))
    return EXIT_APPLIED


if __name__ == "__main__":
    sys.exit(main())
