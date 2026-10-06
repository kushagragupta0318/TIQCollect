"""Hotfix DEMO-LOGIN, 2026-09-24 — scripts/apply_demo_logins.py.

The owner's decision: one master password, three accounts (one AGENCY_ADMIN,
one AGENCY_MANAGER, one FIELD_AGENT), set from a private server setting; every
other account's password made unusable, which retires the seed's published
passwords. The logins are checked through the real /auth/login route.

Round 3 (the coordinator's audit): accounts on the keep-list (Command
Center's service logins) are never touched; retiring the others needs a second
opt-in, DEMO_MASTER_DISABLE_OTHERS, and refuses on a box with any non-demo
account or more users than the demo fixture — DEMO_MODE alone cannot tell a
demo box from a real one.

2026-09-30 (lane L6, owner-approved): a fourth master account, a second
BANK_ADMIN, for the placement engine's four-eyes apply. Role slots are
positional now; first-match grouping refused two bank users.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.core.database import get_db
from tests._db import TEST_AGENCY_ID, TEST_BANK_ID, create_schema, drop_schema, make_engine, make_session_factory
from app.core.security import hash_password, verify_password
from app.main import app
from app.models.agent import Agent, AgentSpecialization, AgentStatus, AgentTier
from app.models.user import User, UserRole
from scripts import apply_demo_logins as demo

# 2026-09-28: converted to the v2 harness (tests/_db) at the merge of v1 main
# into standalone-p1. On v2 the three master accounts are a BANK user, an
# agency manager and a field agent (the owner's decision; v1's were
# AGENCY_ADMIN / AGENCY_MANAGER / FIELD_AGENT), so the admin slot below is a
# BANK_ADMIN.
engine = make_engine()
Session = make_session_factory(bind=engine, autoflush=False)

MASTER = "correct-horse-battery-staple-2026"          # a test value, 33 chars
# Slots, in order: bank user, AGENCY_MANAGER, FIELD_AGENT, 2nd BANK_ADMIN, AGENCY_ADMIN.
ACCOUNTS = ("admin@tiqcollect.in, manager1@tiqcollect.in ,AGENT002@tiqcollect.in, Admin2@tiqcollect.in, "
            "meera.khanna@aravallifs.test")
# The seed's published passwords — the ones this retires.
SEED = {UserRole.BANK_ADMIN: "Admin@123", UserRole.BANK_ANALYST: "Analyst@123",
        UserRole.AGENCY_MANAGER: "Manager@123", UserRole.FIELD_AGENT: "Agent@123",
        UserRole.AGENCY_ADMIN: "AgencyAdmin@123"}
PEOPLE = [
    ("admin@tiqcollect.in", UserRole.BANK_ADMIN),
    ("admin2@tiqcollect.in", UserRole.BANK_ADMIN),
    ("analyst@tiqcollect.in", UserRole.BANK_ANALYST),
    ("manager1@tiqcollect.in", UserRole.AGENCY_MANAGER),
    ("manager2@tiqcollect.in", UserRole.AGENCY_MANAGER),
    ("agent001@tiqcollect.in", UserRole.FIELD_AGENT),
    ("agent002@tiqcollect.in", UserRole.FIELD_AGENT),
    ("agent003@tiqcollect.in", UserRole.FIELD_AGENT),
    ("meera.khanna@aravallifs.test", UserRole.AGENCY_ADMIN),
]
MASTER_EMAILS = {"admin@tiqcollect.in", "manager1@tiqcollect.in", "agent002@tiqcollect.in",
                 "admin2@tiqcollect.in", "meera.khanna@aravallifs.test"}
N_OTHERS = len(PEOPLE) - len(MASTER_EMAILS)
CC_ACCOUNT = "manager2@tiqcollect.in"                   # stands in for a Command Center service login


def _live_session(user, tag):
    """A live UserSession for `user` (v2 keeps sessions in tenancy.user_sessions)."""
    from datetime import datetime, timedelta, timezone
    from app.models.identity import UserSession
    return UserSession(id=str(uuid.uuid4()), user_id=user.id, device_id=f"dev-{tag}",
                       refresh_token_sha256=uuid.uuid4().hex + uuid.uuid4().hex, refresh_jti=uuid.uuid4().hex,
                       expires_at=datetime.now(timezone.utc) + timedelta(days=7))


def _live(db, user) -> int:
    from app.models.identity import UserSession
    return db.query(UserSession).filter(UserSession.user_id == user.id, UserSession.revoked_at.is_(None)).count()


@pytest.fixture
def db():
    create_schema(engine)
    s = Session()
    mgr_id = None
    for i, (email, role) in enumerate(PEOPLE):
        u = User(id=str(uuid.uuid4()), email=email, phone=f"90000002{i:02d}", full_name=f"Person {i}",
                 hashed_password=hash_password(SEED[role]),
                 role=role, is_active=True, is_verified=True, bank_id=TEST_BANK_ID,
                 agency_id=None if role.value.startswith("BANK_") else TEST_AGENCY_ID)
        s.add(u)
        s.flush()
        s.add(_live_session(u, "old-refresh"))    # v2: a live session in place of v1's hashed_refresh_token
        if role == UserRole.AGENCY_MANAGER and mgr_id is None:
            mgr_id = u.id
        if role == UserRole.FIELD_AGENT:
            s.add(Agent(id=str(uuid.uuid4()), user_id=u.id, employee_code=f"EMP2{i:02d}", id_card_number=f"ID2{i:02d}",
                        agency_id=TEST_AGENCY_ID, manager_user_id=mgr_id, gender="F", base_latitude=28.45, base_longitude=77.07,
                        territory="Gurugram", languages_spoken=["HINDI"], status=AgentStatus.ON_DUTY,
                        tier=AgentTier.TIER_1, specialization=AgentSpecialization.BOTH, ranking_score=70.0,
                        max_cases_per_day=5))
    s.commit()
    yield s
    s.close()
    drop_schema(engine)


def _hashes(db):
    return {u.email: (u.hashed_password, _live(db, u)) for u in db.query(User).all()}


def _apply(db, password=MASTER, accounts=ACCOUNTS, demo_mode=True, **kw):
    kw.setdefault("disable_others", True)
    # This test book is @tiqcollect.in; the v2 default (settings) is the
    # girivanfinance.test / aravallifs.test book, so name the domain here.
    kw.setdefault("demo_domains", "tiqcollect.in")
    return demo.apply(db, password=password, accounts_raw=accounts, demo_mode=demo_mode, **kw)


# ── the rule ─────────────────────────────────────────────────────────────────
def test_the_five_accounts_take_the_master_password_and_nobody_else_can_log_in(db):
    out = _apply(db)
    assert out.applied and sorted(out.master_set) == sorted(MASTER_EMAILS)
    assert out.disabled == N_OTHERS
    for u in db.query(User).all():
        if u.email in MASTER_EMAILS:
            assert verify_password(MASTER, u.hashed_password)
        else:
            assert demo.is_disabled_hash(u.hashed_password)
            assert not verify_password(MASTER, u.hashed_password)
        for published in SEED.values():                 # the published passwords are gone, for everyone
            assert not verify_password(published, u.hashed_password)
        assert _live(db, u) == 0                        # every changed password revokes the session


def test_a_second_boot_changes_nothing_and_does_not_rehash(db):
    _apply(db)
    before = _hashes(db)
    out = _apply(db)
    assert out.applied and out.master_set == [] and sorted(out.master_unchanged) == sorted(MASTER_EMAILS)
    assert out.disabled == 0 and out.already_disabled == N_OTHERS
    assert _hashes(db) == before


def test_a_new_master_password_rehashes_and_revokes(db):
    _apply(db)
    for u in db.query(User).filter(User.email.in_(MASTER_EMAILS)):
        db.add(_live_session(u, "a-live-session"))
    db.commit()
    out = _apply(db, password=MASTER + "-rotated")
    assert sorted(out.master_set) == sorted(MASTER_EMAILS)
    for u in db.query(User).filter(User.email.in_(MASTER_EMAILS)):
        assert verify_password(MASTER + "-rotated", u.hashed_password)
        assert not verify_password(MASTER, u.hashed_password)
        assert _live(db, u) == 0


# ── refusals change nothing ──────────────────────────────────────────────────
@pytest.mark.parametrize("kwargs,reason", [
    ({"password": "short-but-15-ch"}, "shorter than 16"),
    ({"demo_mode": False}, "DEMO_MODE is off"),
    # too few: the v2 three, without the fourth and fifth slots
    ({"accounts": "admin@tiqcollect.in,manager1@tiqcollect.in,agent002@tiqcollect.in"}, "exactly 5"),
    ({"accounts": ACCOUNTS + ",agent001@tiqcollect.in"}, "exactly 5"),
    # five entries, but one is a case-dup of another: not five distinct
    ({"accounts": "admin@tiqcollect.in,manager1@tiqcollect.in,agent002@tiqcollect.in,admin2@tiqcollect.in,"
                  "ADMIN@tiqcollect.in"}, "exactly 5"),
    ({"accounts": "admin@tiqcollect.in,manager1@tiqcollect.in,agent002@tiqcollect.in,admin2@tiqcollect.in,"
                  "nobody@tiqcollect.in"}, "unknown accounts"),
    ({"accounts": ACCOUNTS + ",${DEMO_EXTRA}"}, "unresolved"),     # five real emails and a broken sixth
    ({"accounts": "${DEMO_MASTER_ACCOUNTS}"}, "unresolved"),
    ({"keep_raw": "nobody@tiqcollect.in"}, "unknown accounts"),
    ({"keep_raw": "ADMIN2@tiqcollect.in"}, "both master and keep"),
    # the wrong set of roles
    ({"accounts": "admin@tiqcollect.in,manager1@tiqcollect.in,manager2@tiqcollect.in,admin2@tiqcollect.in,"
                  "meera.khanna@aravallifs.test"},
     "#3 manager2@tiqcollect.in is AGENCY_MANAGER"),
    # the right roles in the wrong slots: positional, not first-match
    ({"accounts": "manager1@tiqcollect.in,admin@tiqcollect.in,agent002@tiqcollect.in,admin2@tiqcollect.in,"
                  "meera.khanna@aravallifs.test"},
     "in this order"),
    # slot 4 must be able to apply a placement run: a BANK_ANALYST cannot
    ({"accounts": "admin@tiqcollect.in,manager1@tiqcollect.in,agent002@tiqcollect.in,analyst@tiqcollect.in,"
                  "meera.khanna@aravallifs.test"},
     "#4 analyst@tiqcollect.in is BANK_ANALYST"),
    # slot 5 must be an AGENCY_ADMIN: an AGENCY_MANAGER there is refused
    ({"accounts": "admin@tiqcollect.in,manager1@tiqcollect.in,agent002@tiqcollect.in,admin2@tiqcollect.in,"
                  "manager2@tiqcollect.in"},
     "#5 manager2@tiqcollect.in is AGENCY_MANAGER"),
])
def test_a_refusal_changes_nothing(db, kwargs, reason):
    before = _hashes(db)
    out = _apply(db, **kwargs)
    assert not out.applied and reason in out.reason
    assert _hashes(db) == before


def test_two_bank_admins_are_accepted_and_slot_one_still_takes_any_bank_role(db):
    """The four-eyes pair: first-match grouping put both bank users in slot 1
    and refused them. Slot 1 keeps the v2 rule (any BANK_* role)."""
    assert _apply(db).applied
    out = _apply(db, accounts=("analyst@tiqcollect.in,manager1@tiqcollect.in,agent002@tiqcollect.in,"
                               "admin2@tiqcollect.in,meera.khanna@aravallifs.test"))
    assert out.applied and "analyst@tiqcollect.in" in out.master_set


@pytest.mark.parametrize("password", [None, "", "   ", "${DEMO_MASTER_PASSWORD}"])
def test_unset_means_no_change(db, password):
    before = _hashes(db)
    out = _apply(db, password=password)
    assert not out.applied and "is not set" in out.reason
    assert _hashes(db) == before


def test_the_disabled_marker_only_matches_what_the_script_wrote():
    assert demo.is_disabled_hash(demo._disabled_hash())
    assert not demo.is_disabled_hash(hash_password("anything-at-all"))
    assert not demo.is_disabled_hash(None) and not demo.is_disabled_hash("")


# ── through the real login route ─────────────────────────────────────────────
@pytest.fixture
def client(db, monkeypatch):
    from app.core.ratelimit import limiter
    monkeypatch.setattr(limiter, "enabled", False)

    def override():
        s = Session()
        try:
            yield s
        finally:
            s.close()
    app.dependency_overrides[get_db] = override
    yield TestClient(app)
    app.dependency_overrides.pop(get_db, None)


def _login(client, email, password):
    return client.post("/api/v1/auth/login", json={"email": email, "password": password, "device_id": "test-device-0001"})


def test_the_login_route_admits_the_five_and_refuses_the_published_passwords(db, client):
    _apply(db)
    for email, role in PEOPLE:
        r = _login(client, email, MASTER)
        if email in MASTER_EMAILS:
            assert r.status_code == 200, (email, r.text)
            assert r.json()["role"] == role.value
        else:
            assert r.status_code in (401, 403), (email, r.status_code)
        assert _login(client, email, SEED[role]).status_code in (401, 403), email


# ── the password never leaves the environment ────────────────────────────────
@pytest.fixture
def settings_for_main(db, monkeypatch):
    from app.core import config, database
    for k, v in {"DEMO_MASTER_PASSWORD": MASTER, "DEMO_MASTER_ACCOUNTS": ACCOUNTS, "DEMO_MODE": True,
                 "DEMO_MASTER_KEEP_ACCOUNTS": "", "DEMO_MASTER_DISABLE_OTHERS": "",
                 "DEMO_EMAIL_DOMAINS": "tiqcollect.in"}.items():
        monkeypatch.setattr(config.settings, k, v)
    monkeypatch.setattr(database, "SessionLocal", Session)
    return config.settings


def test_main_never_prints_the_password(settings_for_main, monkeypatch, capsys):
    assert demo.main() == demo.EXIT_APPLIED
    monkeypatch.setattr(settings_for_main, "DEMO_MASTER_PASSWORD", "tiny-secret-9")    # refused: 13 chars
    assert demo.main() == demo.EXIT_REFUSED
    out = capsys.readouterr()
    for secret in (MASTER, "tiny-secret-9"):
        assert secret not in out.out and secret not in out.err


@pytest.mark.parametrize("password", ["", "${DEMO_MASTER_PASSWORD}"])
def test_main_says_not_configured_with_its_own_exit_code(settings_for_main, monkeypatch, capsys, password):
    """Exit 3, not 0: the entrypoint must not print "applied" for a run that
    changed nothing (audit of 4dcd9dc)."""
    monkeypatch.setattr(settings_for_main, "DEMO_MASTER_PASSWORD", password)
    assert demo.main() == demo.EXIT_NOT_CONFIGURED
    assert "not set" in capsys.readouterr().out


def test_main_reads_the_keep_list_and_the_second_opt_in(db, settings_for_main, monkeypatch):
    monkeypatch.setattr(settings_for_main, "DEMO_MASTER_KEEP_ACCOUNTS", CC_ACCOUNT)
    monkeypatch.setattr(settings_for_main, "DEMO_MASTER_DISABLE_OTHERS", "true")
    before = _hashes(db)
    assert demo.main() == demo.EXIT_APPLIED
    db.expire_all()
    after = _hashes(db)
    assert after[CC_ACCOUNT] == before[CC_ACCOUNT]
    assert demo.is_disabled_hash(after["agent001@tiqcollect.in"][0])


# ── round 3: the keep-list, and the second opt-in ─────────────────────────────
def test_a_kept_account_is_never_touched(db):
    """Command Center logs in here as agency managers with its own passwords;
    changing one 502s every /field/* page it serves."""
    before = _hashes(db)
    out = _apply(db, keep_raw=f" {CC_ACCOUNT.upper()} ")
    assert out.applied and out.kept == 1
    assert _hashes(db)[CC_ACCOUNT] == before[CC_ACCOUNT]           # password AND session untouched
    assert verify_password(SEED[UserRole.AGENCY_MANAGER], db.query(User).filter(User.email == CC_ACCOUNT).one().hashed_password)
    assert out.disabled == N_OTHERS - 1


def test_without_the_second_opt_in_other_accounts_are_left_alone(db):
    """DEMO_MODE cannot tell demo from real, so the master password alone sets
    the master accounts and retires nobody."""
    before = _hashes(db)
    out = _apply(db, disable_others=False)
    assert out.applied and out.disabled == 0 and out.left_alone == N_OTHERS
    after = _hashes(db)
    for email, _role in PEOPLE:
        if email not in MASTER_EMAILS:
            assert after[email] == before[email]


def test_retiring_others_refuses_on_a_box_with_a_non_demo_account(db):
    db.add(User(id=str(uuid.uuid4()), email="ops.lead@meridianfinance.in", phone="9000000298",
                full_name="Farhan Qureshi", hashed_password=hash_password("Real-Password-01"),
                role=UserRole.AGENCY_MANAGER, is_active=True, is_verified=True))
    db.commit()
    before = _hashes(db)
    out = _apply(db)
    assert not out.applied and "outside the demo domains" in out.reason
    assert _hashes(db) == before
    # The same box, the foreign account on the keep-list: allowed, and it stays usable.
    out = _apply(db, keep_raw="ops.lead@meridianfinance.in")
    assert out.applied
    assert _hashes(db)["ops.lead@meridianfinance.in"] == before["ops.lead@meridianfinance.in"]


def test_retiring_others_refuses_on_a_box_with_more_users_than_the_fixture(db):
    before = _hashes(db)
    out = _apply(db, max_users=len(PEOPLE) - 1)
    assert not out.applied and "more than the demo fixture" in out.reason
    assert _hashes(db) == before
    assert _apply(db, max_users=len(PEOPLE)).applied          # the boundary itself is allowed


def test_a_foreign_domain_is_fine_when_nothing_is_retired(db):
    db.add(User(id=str(uuid.uuid4()), email="ops.lead@meridianfinance.in", phone="9000000297",
                full_name="Farhan Qureshi", hashed_password=hash_password("Real-Password-01"),
                role=UserRole.AGENCY_MANAGER, is_active=True, is_verified=True))
    db.commit()
    before = _hashes(db)
    out = _apply(db, disable_others=False)
    assert out.applied
    assert _hashes(db)["ops.lead@meridianfinance.in"] == before["ops.lead@meridianfinance.in"]


def test_accounts_that_differ_only_by_case_are_refused(db):
    """Review of 4dcd9dc: keyed by lower-cased email, one of two such rows
    dropped out and kept its published password while the run said success."""
    db.add(User(id=str(uuid.uuid4()), email="Agent001@tiqcollect.in", phone="9000000299", full_name="Dup",
                hashed_password=hash_password("Agent@123"), role=UserRole.FIELD_AGENT, is_active=True,
                is_verified=True))
    db.commit()
    before = _hashes(db)
    out = _apply(db)
    assert not out.applied and "letter case" in out.reason
    assert _hashes(db) == before


# ── coordinator re-audit of bb4371a ───────────────────────────────────────────
@pytest.mark.parametrize("keep", ["${DEMO_MASTER_KEEP_ACCOUNTS}", " , ", "manager2@tiqcollect.in,${CC_ACCOUNT}"])
def test_a_keep_list_that_names_nobody_or_is_unresolved_is_refused(db, keep):
    """parse_accounts drops a ${VAR} entry; with the keep-list silently empty
    the run would retire the very accounts it was meant to protect."""
    before = _hashes(db)
    out = _apply(db, keep_raw=keep)
    assert not out.applied and "KEEP_ACCOUNTS" in out.reason
    assert _hashes(db) == before


@pytest.mark.parametrize("value", ["${DEMO_MASTER_DISABLE_OTHERS}", "false", "1", "yes", ""])
def test_main_retires_nobody_unless_the_second_opt_in_says_true(db, settings_for_main, monkeypatch, value):
    monkeypatch.setattr(settings_for_main, "DEMO_MASTER_DISABLE_OTHERS", value)
    before = _hashes(db)
    assert demo.main() == demo.EXIT_APPLIED
    db.expire_all()
    after = _hashes(db)
    for email, _role in PEOPLE:
        if email not in MASTER_EMAILS:
            assert after[email] == before[email], (value, email)
