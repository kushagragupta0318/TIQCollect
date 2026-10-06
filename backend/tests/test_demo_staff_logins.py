"""apply_demo_logins.apply_agency_staff (lane L6, 2026-09-30): a second,
separate opt-in that gives the SAME shared password to a believable org
chart — the bank's own users plus every ACTIVE agency's admin, managers and
first N field agents — so the demo isn't four isolated accounts. Off by
default; independent of apply()/DEMO_MASTER_ACCOUNTS, which this file does
not touch.
"""
from __future__ import annotations

import uuid

import pytest

from app.core.security import hash_password, verify_password
from app.models.agent import Agent, AgentSpecialization, AgentStatus, AgentTier
from app.models.tenancy import Agency
from app.models.user import User, UserRole
from scripts import apply_demo_logins as demo
from tests._db import TEST_AGENCY_ID, TEST_BANK_ID, create_schema, drop_schema, make_engine, make_session_factory, test_id

engine = make_engine()
Session = make_session_factory(bind=engine, autoflush=False)

MASTER = "correct-horse-battery-staple-2026"
BANK_CODE = "MTB"          # tests/_db.create_schema's default test bank
DOMAIN = "aravallifs.test"  # a domain DEMO_EMAIL_DOMAINS below carries

# A second agency, PENDING, so the "active agencies only" rule has something
# to exclude, and a manager account that stands in for a Command Center login.
PENDING_AGENCY_ID = test_id("agency:pending-0001")
KEEP_ACCOUNT = "keep.manager@aravallifs.test"


def _live(db, user) -> int:
    from app.models.identity import UserSession
    return db.query(UserSession).filter(UserSession.user_id == user.id, UserSession.revoked_at.is_(None)).count()


def _mk_user(s, email, role, *, agency_id=None, phone_seed=0):
    u = User(id=str(uuid.uuid4()), email=email, phone=f"900000{phone_seed:04d}", full_name=email.split("@")[0],
             hashed_password=hash_password("Seed@123"), role=role, is_active=True, is_verified=True,
             bank_id=TEST_BANK_ID, agency_id=agency_id)
    s.add(u)
    s.flush()
    if role == UserRole.FIELD_AGENT:
        s.add(Agent(id=str(uuid.uuid4()), user_id=u.id, employee_code=f"EMP{phone_seed:04d}", id_card_number=f"ID{phone_seed:04d}",
                    agency_id=agency_id, gender="F", base_latitude=28.45, base_longitude=77.07,
                    territory="Gurugram", languages_spoken=["HINDI"], status=AgentStatus.ON_DUTY,
                    tier=AgentTier.TIER_1, specialization=AgentSpecialization.BOTH, ranking_score=0.0,
                    max_cases_per_day=5))
    return u


@pytest.fixture
def db():
    create_schema(engine)
    s = Session()
    s.add(Agency(id=PENDING_AGENCY_ID, bank_id=TEST_BANK_ID, code="AGENCY-PENDING-1", legal_name="Not Yet Onboarded LLP",
                trade_name="Not Yet Onboarded", status="PENDING", contacts=[], is_demo=True))
    s.flush()
    _mk_user(s, "bank.admin@girivanfinance.test", UserRole.BANK_ADMIN, phone_seed=1)
    _mk_user(s, "bank.analyst@girivanfinance.test", UserRole.BANK_ANALYST, phone_seed=2)
    _mk_user(s, "bank.admin2@girivanfinance.test", UserRole.BANK_ADMIN, phone_seed=6)
    _mk_user(s, "agency.admin@aravallifs.test", UserRole.AGENCY_ADMIN, agency_id=TEST_AGENCY_ID, phone_seed=3)
    _mk_user(s, KEEP_ACCOUNT, UserRole.AGENCY_MANAGER, agency_id=TEST_AGENCY_ID, phone_seed=4)
    _mk_user(s, "manager.b@aravallifs.test", UserRole.AGENCY_MANAGER, agency_id=TEST_AGENCY_ID, phone_seed=5)
    for i, name in enumerate(["agent.a", "agent.b", "agent.c", "agent.d", "agent.e", "agent.f"]):
        _mk_user(s, f"{name}@aravallifs.test", UserRole.FIELD_AGENT, agency_id=TEST_AGENCY_ID, phone_seed=10 + i)
    _mk_user(s, "pending.admin@aravallifs.test", UserRole.AGENCY_ADMIN, agency_id=PENDING_AGENCY_ID, phone_seed=20)
    s.commit()
    yield s
    s.close()
    drop_schema(engine)


def _apply(db, password=MASTER, demo_mode=True, enabled=True, **kw):
    kw.setdefault("bank_code", BANK_CODE)
    kw.setdefault("agents_per_agency", 4)
    kw.setdefault("demo_domains", "girivanfinance.test,aravallifs.test")
    return demo.apply_agency_staff(db, password=password, demo_mode=demo_mode, enabled=enabled, **kw)


# ── the rule ─────────────────────────────────────────────────────────────────
def test_the_bank_users_and_the_active_agency_s_org_chart_are_covered(db):
    out = _apply(db)
    assert out.applied and out.agencies == 1        # the pending agency is excluded
    got = set(out.accounts_set)
    assert {"bank.admin@girivanfinance.test", "bank.analyst@girivanfinance.test",
            "agency.admin@aravallifs.test", "manager.b@aravallifs.test"} <= got
    # cap of 4: agent.a..agent.d win, deterministically (by email)
    assert {"agent.a@aravallifs.test", "agent.b@aravallifs.test",
            "agent.c@aravallifs.test", "agent.d@aravallifs.test"} <= got
    assert "agent.e@aravallifs.test" not in got and "agent.f@aravallifs.test" not in got
    for email in got:
        u = db.query(User).filter(User.email == email).one()
        assert verify_password(MASTER, u.hashed_password)
        assert _live(db, u) == 0


def test_the_pending_agency_is_never_touched(db):
    out = _apply(db)
    assert out.applied
    assert "pending.admin@aravallifs.test" not in out.accounts_set
    u = db.query(User).filter(User.email == "pending.admin@aravallifs.test").one()
    assert verify_password("Seed@123", u.hashed_password)


def test_a_kept_account_is_skipped_and_does_not_count_against_the_agent_cap(db):
    out = _apply(db, keep_raw=KEEP_ACCOUNT)
    assert out.applied
    assert KEEP_ACCOUNT not in out.accounts_set
    u = db.query(User).filter(User.email == KEEP_ACCOUNT).one()
    assert verify_password("Seed@123", u.hashed_password)


def test_a_smaller_cap_takes_fewer_agents_in_the_same_deterministic_order(db):
    out = _apply(db, agents_per_agency=2)
    agents = sorted(e for e in out.accounts_set if e.startswith("agent."))
    assert agents == ["agent.a@aravallifs.test", "agent.b@aravallifs.test"]


def test_a_zero_cap_still_covers_admins_and_managers(db):
    out = _apply(db, agents_per_agency=0)
    assert out.applied
    assert not any(e.startswith("agent.") for e in out.accounts_set)
    assert "agency.admin@aravallifs.test" in out.accounts_set


def test_a_second_boot_changes_nothing_and_does_not_rehash(db):
    _apply(db)
    before = {u.hashed_password for u in db.query(User).all()}
    out = _apply(db)
    assert out.applied and out.accounts_set == []
    assert len(out.accounts_unchanged) > 0
    assert {u.hashed_password for u in db.query(User).all()} == before


def test_overlapping_with_the_master_accounts_pass_is_harmless(db):
    """apply() and apply_agency_staff() may both touch the same account (a
    bank admin can be both); applying the same password twice is a no-op."""
    a = demo.apply(db, password=MASTER, demo_mode=True,
                   accounts_raw="bank.admin@girivanfinance.test,manager.b@aravallifs.test,"
                                "agent.a@aravallifs.test,bank.admin2@girivanfinance.test",
                   demo_domains="girivanfinance.test,aravallifs.test", max_users=100)
    assert a.applied, a.reason
    out = _apply(db)
    assert out.applied
    u = db.query(User).filter(User.email == "bank.admin@girivanfinance.test").one()
    assert verify_password(MASTER, u.hashed_password)


# ── refusals change nothing ──────────────────────────────────────────────────
@pytest.mark.parametrize("kwargs,reason", [
    ({"enabled": False}, "DEMO_STAFF_LOGIN_ENABLED is off"),
    ({"demo_mode": False}, "DEMO_MODE is off"),
    ({"password": "short-but-15-ch"}, "shorter than 16"),
    ({"agents_per_agency": -1}, "must not be negative"),
    ({"keep_raw": "${DEMO_MASTER_KEEP_ACCOUNTS}"}, "KEEP_ACCOUNTS"),
    ({"bank_code": "NOT-A-BANK"}, "no bank with code"),
    ({"demo_domains": "girivanfinance.test"}, "outside the demo domains"),   # aravallifs.test excluded
    ({"max_users": 3}, "more than the demo fixture's 3"),
])
def test_a_refusal_changes_nothing(db, kwargs, reason):
    before = {u.hashed_password for u in db.query(User).all()}
    out = _apply(db, **kwargs)
    assert not out.applied and reason in out.reason
    assert {u.hashed_password for u in db.query(User).all()} == before


@pytest.mark.parametrize("password", [None, "", "   ", "${DEMO_MASTER_PASSWORD}"])
def test_unset_password_means_no_change(db, password):
    out = _apply(db, password=password)
    assert not out.applied and not out.configured and "is not set" in out.reason


def test_accounts_that_differ_only_by_case_are_refused(db):
    db.add(User(id=str(uuid.uuid4()), email="Agent.A@aravallifs.test", phone="9000009999", full_name="Dup",
                hashed_password=hash_password("Seed@123"), role=UserRole.FIELD_AGENT, is_active=True,
                is_verified=True, bank_id=TEST_BANK_ID, agency_id=TEST_AGENCY_ID))
    db.commit()
    before = {u.hashed_password for u in db.query(User).all()}
    out = _apply(db)
    assert not out.applied and "letter case" in out.reason
    assert {u.hashed_password for u in db.query(User).all()} == before


def test_main_calls_both_passes_and_the_second_is_off_by_default(db, monkeypatch):
    """main() reads settings; DEMO_STAFF_LOGIN_ENABLED unset must not expand
    who gets the password beyond DEMO_MASTER_ACCOUNTS' four."""
    from app.core import config, database
    for k, v in {"DEMO_MASTER_PASSWORD": MASTER, "DEMO_MODE": True, "DEMO_MASTER_KEEP_ACCOUNTS": "",
                 "DEMO_MASTER_DISABLE_OTHERS": "", "DEMO_STAFF_LOGIN_ENABLED": "", "DEMO_STAFF_BANK_CODE": BANK_CODE,
                 "DEMO_EMAIL_DOMAINS": "girivanfinance.test,aravallifs.test",
                 "DEMO_MASTER_ACCOUNTS": ("bank.admin@girivanfinance.test,manager.b@aravallifs.test,"
                                         "agent.a@aravallifs.test,bank.admin2@girivanfinance.test")}.items():
        monkeypatch.setattr(config.settings, k, v)
    monkeypatch.setattr(database, "SessionLocal", Session)
    assert demo.main() == demo.EXIT_APPLIED
    assert not verify_password(MASTER, db.query(User).filter(User.email == "agency.admin@aravallifs.test").one().hashed_password)

    monkeypatch.setattr(config.settings, "DEMO_STAFF_LOGIN_ENABLED", "true")
    assert demo.main() == demo.EXIT_APPLIED
    db.expire_all()
    assert verify_password(MASTER, db.query(User).filter(User.email == "agency.admin@aravallifs.test").one().hashed_password)
