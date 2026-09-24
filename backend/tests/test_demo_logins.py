"""Hotfix DEMO-LOGIN, 2026-09-24 — scripts/apply_demo_logins.py.

The owner's decision: one master password, three accounts (one AGENCY_ADMIN,
one AGENCY_MANAGER, one FIELD_AGENT), set from a private server setting; every
other account's password made unusable, which retires the seed's published
passwords. The logins are checked through the real /auth/login route.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.core.security import hash_password, verify_password
from app.main import app
from app.models.agent import Agent, AgentSpecialization, AgentStatus, AgentTier
from app.models.user import User, UserRole
from scripts import apply_demo_logins as demo

engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)

MASTER = "correct-horse-battery-staple-2026"          # a test value, 33 chars
ACCOUNTS = "admin@tiqcollect.in, manager1@tiqcollect.in ,AGENT002@tiqcollect.in"
# The seed's published passwords — the ones this retires.
SEED = {UserRole.AGENCY_ADMIN: "Admin@123", UserRole.AGENCY_MANAGER: "Manager@123", UserRole.FIELD_AGENT: "Agent@123"}
PEOPLE = [
    ("admin@tiqcollect.in", UserRole.AGENCY_ADMIN),
    ("manager1@tiqcollect.in", UserRole.AGENCY_MANAGER),
    ("manager2@tiqcollect.in", UserRole.AGENCY_MANAGER),
    ("agent001@tiqcollect.in", UserRole.FIELD_AGENT),
    ("agent002@tiqcollect.in", UserRole.FIELD_AGENT),
    ("agent003@tiqcollect.in", UserRole.FIELD_AGENT),
]
MASTER_EMAILS = {"admin@tiqcollect.in", "manager1@tiqcollect.in", "agent002@tiqcollect.in"}


@pytest.fixture
def db():
    Base.metadata.create_all(engine)
    s = Session()
    mgr_id = None
    for i, (email, role) in enumerate(PEOPLE):
        u = User(id=str(uuid.uuid4()), email=email, phone=f"90000002{i:02d}", full_name=f"Person {i}",
                 hashed_password=hash_password(SEED[role]), hashed_refresh_token="old-refresh-hash",
                 role=role, is_active=True, is_verified=True)
        s.add(u)
        s.flush()
        if role == UserRole.AGENCY_MANAGER and mgr_id is None:
            mgr_id = u.id
        if role == UserRole.FIELD_AGENT:
            s.add(Agent(id=str(uuid.uuid4()), user_id=u.id, employee_code=f"EMP2{i:02d}", id_card_number=f"ID2{i:02d}",
                        agency_id="AG1", manager_user_id=mgr_id, gender="F", base_latitude=28.45, base_longitude=77.07,
                        territory="Gurugram", languages_spoken=["HINDI"], status=AgentStatus.ON_DUTY,
                        tier=AgentTier.TIER_1, specialization=AgentSpecialization.BOTH, ranking_score=70.0,
                        max_cases_per_day=5))
    s.commit()
    yield s
    s.close()
    Base.metadata.drop_all(engine)


def _hashes(db):
    return {u.email: (u.hashed_password, u.hashed_refresh_token) for u in db.query(User).all()}


def _apply(db, password=MASTER, accounts=ACCOUNTS, demo_mode=True):
    return demo.apply(db, password=password, accounts_raw=accounts, demo_mode=demo_mode)


# ── the rule ─────────────────────────────────────────────────────────────────
def test_the_three_accounts_take_the_master_password_and_nobody_else_can_log_in(db):
    out = _apply(db)
    assert out.applied and sorted(out.master_set) == sorted(MASTER_EMAILS)
    assert out.disabled == len(PEOPLE) - 3
    for u in db.query(User).all():
        if u.email in MASTER_EMAILS:
            assert verify_password(MASTER, u.hashed_password)
        else:
            assert demo.is_disabled_hash(u.hashed_password)
            assert not verify_password(MASTER, u.hashed_password)
        for published in SEED.values():                 # the published passwords are gone, for everyone
            assert not verify_password(published, u.hashed_password)
        assert u.hashed_refresh_token is None           # every changed password revokes the session


def test_a_second_boot_changes_nothing_and_does_not_rehash(db):
    _apply(db)
    before = _hashes(db)
    out = _apply(db)
    assert out.applied and out.master_set == [] and sorted(out.master_unchanged) == sorted(MASTER_EMAILS)
    assert out.disabled == 0 and out.already_disabled == len(PEOPLE) - 3
    assert _hashes(db) == before


def test_a_new_master_password_rehashes_and_revokes(db):
    _apply(db)
    for u in db.query(User).filter(User.email.in_(MASTER_EMAILS)):
        u.hashed_refresh_token = "a-live-session"
    db.commit()
    out = _apply(db, password=MASTER + "-rotated")
    assert sorted(out.master_set) == sorted(MASTER_EMAILS)
    for u in db.query(User).filter(User.email.in_(MASTER_EMAILS)):
        assert verify_password(MASTER + "-rotated", u.hashed_password)
        assert not verify_password(MASTER, u.hashed_password)
        assert u.hashed_refresh_token is None


# ── refusals change nothing ──────────────────────────────────────────────────
@pytest.mark.parametrize("kwargs,reason", [
    ({"password": "short-but-15-ch"}, "shorter than 16"),
    ({"demo_mode": False}, "DEMO_MODE is off"),
    ({"accounts": "admin@tiqcollect.in,manager1@tiqcollect.in"}, "exactly 3"),
    ({"accounts": "admin@tiqcollect.in,manager1@tiqcollect.in,manager1@tiqcollect.in"}, "exactly 3"),
    ({"accounts": "admin@tiqcollect.in,manager1@tiqcollect.in,nobody@tiqcollect.in"}, "unknown users"),
    ({"accounts": "admin@tiqcollect.in,manager1@tiqcollect.in,manager2@tiqcollect.in"}, "must be one"),
])
def test_a_refusal_changes_nothing(db, kwargs, reason):
    before = _hashes(db)
    out = _apply(db, **kwargs)
    assert not out.applied and reason in out.reason
    assert _hashes(db) == before


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


def test_the_login_route_admits_the_three_and_refuses_the_published_passwords(db, client):
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
def test_main_never_prints_the_password(db, monkeypatch, capsys):
    from app.core import config, database
    monkeypatch.setattr(config.settings, "DEMO_MASTER_PASSWORD", MASTER)
    monkeypatch.setattr(config.settings, "DEMO_MASTER_ACCOUNTS", ACCOUNTS)
    monkeypatch.setattr(config.settings, "DEMO_MODE", True)
    monkeypatch.setattr(database, "SessionLocal", Session)
    assert demo.main() == 0
    monkeypatch.setattr(config.settings, "DEMO_MASTER_PASSWORD", "tiny-secret-9")    # refused: 13 chars
    assert demo.main() == 1
    out = capsys.readouterr()
    for secret in (MASTER, "tiny-secret-9"):
        assert secret not in out.out and secret not in out.err
