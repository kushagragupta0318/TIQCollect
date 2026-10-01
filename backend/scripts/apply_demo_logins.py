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
#       *(2026-09-28, merge into standalone-p1 / v2: the owner's decision for
#       v2 is a BANK user, an agency manager and a field agent. The three are
#       now matched against role GROUPS — any BANK_* role, AGENCY_MANAGER,
#       FIELD_AGENT — one account per group. FIXTURE_USER_COUNT and the
#       default demo domain below are still the v1 book's; B16/B18 replace
#       them with the v2 roster's.)*
#       *(2026-09-30, lane L6, owner-approved: a FOURTH account, a second
#       BANK_ADMIN, so the placement engine's four-eyes apply (ADR 0010) can
#       be demoed by two people. Slots are now POSITIONAL: the Nth email must
#       hold a role of the Nth group. First-match grouping put two bank users
#       in group 0 and refused the pair.)*
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
#   *(v2, 2026-09-28: "clears the refresh token" now means REVOKES EVERY LIVE
#   SESSION of the user (auth_service.revoke_user_sessions, PASSWORD_CHANGED).
#   v2 keeps sessions in tenancy.user_sessions. The v1 line
#   `user.hashed_refresh_token = None` only set a plain attribute there and
#   silently ended nothing; found at the merge.)*
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


def _end_sessions(db: Session, user: User) -> None:
    """A changed or retired password ends every live session of the user, in
    the caller's transaction (committed with the password change)."""
    from app.services.auth_service import revoke_user_sessions
    revoke_user_sessions(db, user.id, "PASSWORD_CHANGED")


MIN_PASSWORD_LENGTH = 16
# One account per slot, positionally: DEMO_MASTER_ACCOUNTS' Nth email must
# hold a role of the Nth slot (owner's decisions, v2 and 2026-09-30).
REQUIRED_ROLE_GROUPS: tuple[tuple[str, frozenset[UserRole]], ...] = (
    ("a bank user (BANK_ADMIN / BANK_ANALYST / BANK_TECHOPS)",
     frozenset({UserRole.BANK_ADMIN, UserRole.BANK_ANALYST, UserRole.BANK_TECHOPS})),
    ("an AGENCY_MANAGER", frozenset({UserRole.AGENCY_MANAGER})),
    ("a FIELD_AGENT", frozenset({UserRole.FIELD_AGENT})),
    # placement.run is BANK_ADMIN-only and apply needs a second person.
    ("a second bank user, for the four-eyes demo (BANK_ADMIN)", frozenset({UserRole.BANK_ADMIN})),
)


# The committed demo book: backend/fixtures/fieldops-demo-v2.dump (B16-B18,
# 2026-09-28) holds 199 users, every one on a roster domain
# (settings.DEMO_EMAIL_DOMAINS). A box with more users than that has users the
# demo did not create, and is not one whose passwords this may retire.
# tests/pg/test_pg_demo_fixture.py counts the dump's users against this.
# (Was 198 before Girivan's second BANK_ADMIN, 2026-09-29; 24 for B15's book;
# 21 for v1's users.csv, all @tiqcollect.in.)
FIXTURE_USER_COUNT = 199

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
          demo_domains: str | None = None, max_users: int = FIXTURE_USER_COUNT) -> Outcome:
    """Give the named accounts the master password; with disable_others,
    retire every other demo account's password. Changes nothing unless every
    precondition holds."""
    if not _real(password):
        return Outcome(applied=False, configured=False, reason="DEMO_MASTER_PASSWORD is not set; nothing changed")
    if not demo_mode:
        return Outcome(applied=False, reason="DEMO_MODE is off; refusing to set a shared password")
    if len(password) < MIN_PASSWORD_LENGTH:
        return Outcome(applied=False, reason=f"DEMO_MASTER_PASSWORD is shorter than {MIN_PASSWORD_LENGTH} characters")

    # parse_accounts drops a ${VAR} entry, so "a,b,c,d,${X}" would pass the
    # count; an unresolved variable is a broken setting, refuse it (L6 audit).
    if "${" in (accounts_raw or ""):
        return Outcome(applied=False, reason="DEMO_MASTER_ACCOUNTS holds an unresolved ${...}")
    emails = parse_accounts(accounts_raw)
    if len(emails) != len(REQUIRED_ROLE_GROUPS) or len(set(emails)) != len(emails):
        return Outcome(applied=False, reason=f"DEMO_MASTER_ACCOUNTS must name exactly {len(REQUIRED_ROLE_GROUPS)} distinct emails")
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
    wrong = [f"#{i + 1} {e} is {users[e].role.value}, wanted {name}"
             for i, (e, (name, roles)) in enumerate(zip(emails, REQUIRED_ROLE_GROUPS))
             if users[e].role not in roles]
    if wrong:
        wanted = ", ".join(name for name, _ in REQUIRED_ROLE_GROUPS)
        return Outcome(applied=False, reason=(f"DEMO_MASTER_ACCOUNTS must be, in this order: {wanted}; "
                                              + "; ".join(wrong)))

    master = set(emails)
    others = [e for e in users if e not in master and e not in keep]
    if disable_others:
        if demo_domains is None:                  # the one definition: settings (core/config.py)
            from app.core.config import settings
            demo_domains = settings.DEMO_EMAIL_DOMAINS
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
        _end_sessions(db, user)
        out.master_set.append(email)
    for email in others:
        user = users[email]
        if not disable_others:
            out.left_alone += 1
        elif is_disabled_password_hash(user.hashed_password):
            out.already_disabled += 1
        else:
            user.hashed_password = disabled_password_hash()
            _end_sessions(db, user)
            out.disabled += 1
    db.commit()
    return out


STAFF_ROLES = frozenset({UserRole.AGENCY_ADMIN, UserRole.AGENCY_MANAGER, UserRole.FIELD_AGENT})
DEFAULT_AGENTS_PER_AGENCY = 4


class _NoSuchBank(Exception):
    pass


def staff_targets(db: Session, *, bank_code: str, agents_per_agency: int,
                  everyone: list[User] | None = None) -> tuple[list[User], int]:
    """The ONE definition of "who gets the shared staff login": `bank_code`'s
    own users, plus every ACTIVE agency's admin, every manager, and its first
    `agents_per_agency` field agents (by email, deterministic). Read-only;
    apply_agency_staff() and scripts/generate_demo_logins_doc.py both call
    this, so the doc can never list an account that would not actually get
    the password. Raises _NoSuchBank if `bank_code` names no bank."""
    from app.models.tenancy import Agency, Bank

    everyone = db.query(User).all() if everyone is None else everyone
    bank = db.query(Bank).filter(Bank.code == bank_code).one_or_none()
    if bank is None:
        raise _NoSuchBank(bank_code)

    targets: list[User] = [u for u in everyone if u.bank_id == bank.id and u.agency_id is None
                           and u.role.value.startswith("BANK_")]
    agencies = (db.query(Agency).filter(Agency.bank_id == bank.id, Agency.status == "ACTIVE")
               .order_by(Agency.code).all())
    for a in agencies:
        staff = sorted((u for u in everyone if u.agency_id == a.id and u.role in STAFF_ROLES),
                       key=lambda u: (u.role.value, (u.email or "").lower()))
        admins = [u for u in staff if u.role == UserRole.AGENCY_ADMIN]
        managers = [u for u in staff if u.role == UserRole.AGENCY_MANAGER]
        agents = [u for u in staff if u.role == UserRole.FIELD_AGENT][:agents_per_agency]
        targets += admins + managers + agents
    return targets, len(agencies)


@dataclass
class StaffOutcome:
    applied: bool
    reason: str = ""
    configured: bool = True
    accounts_set: list[str] = field(default_factory=list)
    accounts_unchanged: list[str] = field(default_factory=list)
    agencies: int = 0


def apply_agency_staff(db: Session, *, password: str | None, demo_mode: bool, enabled: bool,
                       bank_code: str = "GIRIVAN", agents_per_agency: int = DEFAULT_AGENTS_PER_AGENCY,
                       keep_raw: str | None = "", demo_domains: str | None = None,
                       max_users: int = FIXTURE_USER_COUNT) -> StaffOutcome:
    """A SECOND, SEPARATE pass (owner's decision, 2026-09-30): the same shared
    password for every bank user of `bank_code`, plus every ACTIVE agency's
    AGENCY_ADMIN, every AGENCY_MANAGER, and its first `agents_per_agency`
    FIELD_AGENTs (by email, deterministic) — so the demo shows a believable
    org chart logging in, not four isolated accounts. DOES NOT touch
    apply()'s DEMO_MASTER_ACCOUNTS mechanism or its four-eyes slots; the two
    are independent and may overlap harmlessly (same password, idempotent).

    Off by default (`enabled`): a deployment that only wants the four master
    accounts must opt in. Same fail-closed shape as apply(): every account
    touched must be case-unique, on a demo domain, and within the fixture's
    user count; a keep-listed account is skipped, never assigned nor counted
    against the agency's agent quota."""
    if not _real(password):
        return StaffOutcome(applied=False, configured=False, reason="DEMO_MASTER_PASSWORD is not set; nothing changed")
    if not demo_mode:
        return StaffOutcome(applied=False, reason="DEMO_MODE is off; refusing to set a shared password")
    if not enabled:
        return StaffOutcome(applied=False, configured=False, reason="DEMO_STAFF_LOGIN_ENABLED is off; nothing changed")
    if len(password) < MIN_PASSWORD_LENGTH:
        return StaffOutcome(applied=False, reason=f"DEMO_MASTER_PASSWORD is shorter than {MIN_PASSWORD_LENGTH} characters")
    if agents_per_agency < 0:
        return StaffOutcome(applied=False, reason="agents_per_agency must not be negative")

    keep = set(parse_accounts(keep_raw))
    if (keep_raw or "").strip() and ("${" in keep_raw or not keep):
        return StaffOutcome(applied=False, reason="DEMO_MASTER_KEEP_ACCOUNTS is set but names no account, "
                                                   "or holds an unresolved ${...}")

    everyone = db.query(User).all()
    lowered = [(u.email or "").strip().lower() for u in everyone]
    clashes = sorted({e for e in lowered if e and lowered.count(e) > 1})
    if clashes:
        return StaffOutcome(applied=False, reason=f"accounts differ only by letter case: {', '.join(clashes)}")
    if len(everyone) > max_users:
        return StaffOutcome(applied=False, reason=(f"{len(everyone)} users, more than the demo fixture's "
                                                    f"{max_users}; this is not a demo box"))

    if demo_domains is None:
        from app.core.config import settings
        demo_domains = settings.DEMO_EMAIL_DOMAINS
    domains = {d.strip().lower() for d in (demo_domains or "").split(",") if d.strip()}

    try:
        targets, n_agencies = staff_targets(db, bank_code=bank_code, agents_per_agency=agents_per_agency,
                                            everyone=everyone)
    except _NoSuchBank:
        return StaffOutcome(applied=False, reason=f"no bank with code {bank_code!r}; nothing changed")

    targets = [u for u in targets if ((u.email or "").strip().lower()) not in keep]
    foreign = sorted({u.email for u in targets if _domain((u.email or "").lower()) not in domains})
    if foreign:
        return StaffOutcome(applied=False, reason=(f"{len(foreign)} account(s) outside the demo domains "
                                                    f"{sorted(domains)}; this is not a demo box"))

    out = StaffOutcome(applied=True, agencies=n_agencies)
    for u in sorted(targets, key=lambda u: (u.email or "").lower()):
        if u.hashed_password and not is_disabled_password_hash(u.hashed_password) \
                and verify_password(password, u.hashed_password):
            out.accounts_unchanged.append(u.email)
            continue
        u.hashed_password = hash_password(password)
        _end_sessions(db, u)
        out.accounts_set.append(u.email)
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

    db2 = SessionLocal()
    try:
        staff_out = apply_agency_staff(
            db2, password=settings.DEMO_MASTER_PASSWORD, demo_mode=settings.DEMO_MODE,
            enabled=explicit_true(settings.DEMO_STAFF_LOGIN_ENABLED),
            bank_code=settings.DEMO_STAFF_BANK_CODE or "GIRIVAN",
            agents_per_agency=int(settings.DEMO_STAFF_AGENTS_PER_AGENCY or DEFAULT_AGENTS_PER_AGENCY),
            keep_raw=settings.DEMO_MASTER_KEEP_ACCOUNTS, demo_domains=settings.DEMO_EMAIL_DOMAINS)
    finally:
        db2.close()
    if not staff_out.applied:
        if not staff_out.configured:
            print(f"[demo-logins] agency staff: {staff_out.reason}")
        else:
            logger.error("demo_logins.staff_refused", reason=staff_out.reason)
            print(f"[demo-logins] agency staff REFUSED: {staff_out.reason}", file=sys.stderr)
            return EXIT_REFUSED
    else:
        print(f"[demo-logins] agency staff: {len(staff_out.accounts_set)} set, "
              f"{len(staff_out.accounts_unchanged)} unchanged, across {staff_out.agencies} active agencies")
    return EXIT_APPLIED


if __name__ == "__main__":
    sys.exit(main())
