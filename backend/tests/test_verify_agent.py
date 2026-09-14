"""GET /verify-agent — the public check behind the QR on an agent's ID card.

The token has been minted since the first release; the endpoint that reads it
did not exist until 2026-09-11. What is pinned here: a genuine card returns
exactly four fields and nothing else; every way a card can be wrong returns
the same 404 with the same body; and no login is needed, because the person
scanning it is a borrower at their own front door.
"""
from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from jose import jwt
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.database import Base, get_db
from app.core.ratelimit import limiter
from app.core.security import _make_token, create_access_token, create_agent_verify_token
from app.main import app
from app.models.agent import Agent, AgentSpecialization, AgentStatus, AgentTier
from app.models.user import User, UserRole

engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                       poolclass=StaticPool)
TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
URL = "/api/v1/verify-agent"
ALLOWED = {"agent_name", "employee_code", "agency", "active"}


def _uid():
    return str(uuid.uuid4())


@pytest.fixture(autouse=True)
def _fresh_rate_limit_window():
    """The route is limited to AUTH_RATE_LIMIT_PER_MINUTE per client address
    (tests/test_rate_limits.py proves it), and TestClient is one address. Each
    test here gets its own window so the limit is exercised where it is the
    subject and invisible where it is not."""
    limiter.reset()
    yield
    limiter.reset()


@pytest.fixture(scope="module")
def world():
    Base.metadata.create_all(engine)
    db = TestingSession()
    mgr = User(id=_uid(), email="m@t.io", phone="9000000001", full_name="Manager",
               hashed_password="x", role=UserRole.AGENCY_MANAGER, is_active=True, is_verified=True)
    u_ok = User(id=_uid(), email="ok@t.io", phone="9000000002", full_name="Arjun Mehta",
                hashed_password="x", role=UserRole.FIELD_AGENT, is_active=True, is_verified=True)
    u_susp = User(id=_uid(), email="s@t.io", phone="9000000003", full_name="Suspended Person",
                  hashed_password="x", role=UserRole.FIELD_AGENT, is_active=True, is_verified=True)
    u_off = User(id=_uid(), email="off@t.io", phone="9000000004", full_name="Off Duty",
                 hashed_password="x", role=UserRole.FIELD_AGENT, is_active=True, is_verified=True)
    u_dis = User(id=_uid(), email="dis@t.io", phone="9000000005", full_name="Disabled Login",
                 hashed_password="x", role=UserRole.FIELD_AGENT, is_active=False, is_verified=True)
    db.add_all([mgr, u_ok, u_susp, u_off, u_dis]); db.flush()

    def agent(u, code, status):
        a = Agent(id=_uid(), user_id=u.id, employee_code=code, id_card_number=code + "-ID",
                  agency_id="TIQ-DELHI-01", manager_user_id=mgr.id, gender="M",
                  base_latitude=28.63, base_longitude=77.21, territory="Delhi",
                  languages_spoken=["HINDI"], status=status, tier=AgentTier.TIER_1,
                  specialization=AgentSpecialization.BOTH, ranking_score=80.0)
        db.add(a); return a
    ok = agent(u_ok, "EMP0101", AgentStatus.ON_DUTY)
    susp = agent(u_susp, "EMP0102", AgentStatus.SUSPENDED)
    off = agent(u_off, "EMP0103", AgentStatus.OFF_DUTY)
    dis = agent(u_dis, "EMP0104", AgentStatus.ON_DUTY)
    db.commit()
    yield {"db": db, "ok": ok, "susp": susp, "off": off, "dis": dis, "u_ok": u_ok}
    db.close()
    Base.metadata.drop_all(engine)


@pytest.fixture(scope="module")
def client(world):
    def override():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()
    app.dependency_overrides[get_db] = override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


def test_a_genuine_card_verifies_with_exactly_the_four_allowed_fields(client, world):
    r = client.get(URL, params={"token": create_agent_verify_token(world["ok"].id)})
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == ALLOWED
    assert body == {"agent_name": "Arjun Mehta", "employee_code": "EMP0101",
                    "agency": "TIQ-DELHI-01", "active": True}


def test_no_authentication_is_needed_and_a_token_header_changes_nothing(client, world):
    # No Authorization header at all — the borrower has no account.
    r = client.get(URL, params={"token": create_agent_verify_token(world["ok"].id)})
    assert r.status_code == 200
    # A stray bearer token (even a valid one) must neither help nor hurt.
    hdr = {"Authorization": f"Bearer {create_access_token(world['u_ok'].id, 'FIELD_AGENT', 'dev')}"}
    r2 = client.get(URL, params={"token": create_agent_verify_token(world["ok"].id)}, headers=hdr)
    assert r2.status_code == 200 and r2.json() == r.json()


def test_the_response_never_carries_anything_sensitive(client, world):
    r = client.get(URL, params={"token": create_agent_verify_token(world["ok"].id)})
    text = r.text
    for secret in (world["ok"].id, world["u_ok"].id, "ok@t.io", "9000000002",
                   "manager_user_id", "user_id", "phone", "email", "token", "sub", "jti"):
        assert secret not in text, secret


def _refused(client, token):
    r = client.get(URL, params={"token": token})
    assert r.status_code == 404, r.text
    return r.json()


def test_every_bad_card_gets_the_same_404_so_a_forger_learns_nothing(client, world):
    good = create_agent_verify_token(world["ok"].id)
    # 1. forged signature
    forged = jwt.encode({"sub": world["ok"].id, "type": "agent_verify", "exp": 9999999999},
                        "not-the-server-secret", algorithm=settings.ALGORITHM)
    # 2. expired
    expired = _make_token(subject=world["ok"].id, token_type="agent_verify",
                          expires_delta=timedelta(seconds=-60))
    # 3. wrong token type — a real access token, correctly signed
    wrong_type = create_access_token(world["u_ok"].id, "FIELD_AGENT", "dev")
    # 4. agent that does not exist
    ghost = create_agent_verify_token(_uid())
    # 5. malformed
    garbage = "not.a.jwt"
    bodies = {_refused(client, t)["detail"] for t in (forged, expired, wrong_type, ghost, garbage)}
    assert bodies == {"Agent not found"}, "the refusal must be indistinguishable"
    # and the good one still works afterwards
    assert client.get(URL, params={"token": good}).status_code == 200


def test_active_answers_the_borrowers_question_not_the_rosters(client, world):
    """May this person be at my door? Suspended: no. Login disabled: no.
    Off duty: still a genuine, employed agent — the card is real."""
    def active(agent):
        return client.get(URL, params={"token": create_agent_verify_token(agent.id)}).json()["active"]
    assert active(world["ok"]) is True
    assert active(world["off"]) is True
    assert active(world["susp"]) is False
    assert active(world["dis"]) is False


def test_missing_or_empty_token_is_refused_too(client):
    assert client.get(URL).status_code == 422          # FastAPI's own "required"
    assert client.get(URL, params={"token": ""}).status_code == 422
