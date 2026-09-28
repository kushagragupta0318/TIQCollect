"""The frozen P1-split interfaces (2026-09-24): services/scope.agents_in_scope /
cases_in_scope and auth_service.open_session / revoke_user_sessions.

ce (A04 planner pools) and d4 (A06 invites, A07 password flows) build on these
without waiting for the rest of P1; changing their behaviour goes through the
coordinator. These tests are the contract.
"""
from __future__ import annotations

from datetime import date

import pytest
from starlette.requests import Request

from app.models import Agent, Case, Customer, Loan, LoanType, User, UserRole
from app.models.identity import UserSession
from app.models.tenancy import Agency
from app.services import auth_service
from app.services.scope import agents_in_scope, cases_in_scope
from tests._db import TEST_AGENCY_ID, TEST_BANK_ID, create_schema, make_engine, make_session_factory, test_id

OTHER = test_id("agency:other")


def _req():
    return Request({"type": "http", "method": "POST", "path": "/", "headers": [], "client": ("10.0.0.1", 1),
                    "query_string": b""})


@pytest.fixture()
def db():
    engine = make_engine()
    create_schema(engine)
    s = make_session_factory(engine)()
    s.add(Agency(id=OTHER, bank_id=TEST_BANK_ID, code="AGENCY-OTHER", legal_name="Almora Recovery Desk LLP",
                 trade_name="Almora Recovery Desk", status="ACTIVE", contacts=[], is_demo=True))
    s.flush()

    people_seen: list[str] = []

    def user(tag, role, agency=TEST_AGENCY_ID, bank=TEST_BANK_ID):
        people_seen.append(tag)
        u = User(id=test_id(f"u:{tag}"), email=f"{tag}@scope.test", phone=f"97100{len(people_seen):05d}",
                 full_name=tag, hashed_password="x", role=role, bank_id=bank,
                 agency_id=agency if role in (UserRole.FIELD_AGENT, UserRole.AGENCY_MANAGER,
                                              UserRole.AGENCY_ADMIN) else None)
        s.add(u)
        return u

    people = {
        "mgr": user("mgr", UserRole.AGENCY_MANAGER), "mgr2": user("mgr2", UserRole.AGENCY_MANAGER),
        "admin": user("admin", UserRole.AGENCY_ADMIN), "bank": user("bank", UserRole.BANK_ANALYST),
        "a1": user("a1", UserRole.FIELD_AGENT), "a2": user("a2", UserRole.FIELD_AGENT),
        "o_mgr": user("o_mgr", UserRole.AGENCY_MANAGER, OTHER), "o1": user("o1", UserRole.FIELD_AGENT, OTHER),
    }
    s.flush()

    def agent(tag, mgr, agency=TEST_AGENCY_ID):
        a = Agent(id=test_id(f"ag:{tag}"), user_id=people[tag].id, employee_code=tag.upper(), id_card_number=tag,
                  base_latitude=28.4, base_longitude=77.0, territory="Gurugram", manager_user_id=people[mgr].id,
                  bank_id=TEST_BANK_ID, agency_id=agency)
        s.add(a)
        return a

    agents = {"a1": agent("a1", "mgr"), "a2": agent("a2", "mgr2"), "o1": agent("o1", "o_mgr", OTHER)}
    s.flush()
    cust = Customer(id=test_id("c"), bank_id=TEST_BANK_ID, customer_ref="S-1", full_name="Kiran Bisht",
                    date_of_birth=date(1980, 1, 1), gender="F", pan_masked="XXXXX0000X", aadhaar_masked="XXXXXXXX0000",
                    phone_primary="9899001111", address_line1="Mall Road", city="Almora", state="Uttarakhand",
                    pincode="263601", latitude=29.6, longitude=79.6)
    loan = Loan(id=test_id("l"), bank_id=TEST_BANK_ID, loan_account_number="S1", customer_id=cust.id,
                loan_type=LoanType.PERSONAL, branch_code="BR", sanctioned_amount=1.0, disbursed_amount=1.0,
                outstanding_principal=1.0, total_outstanding=1.0, emi_amount=1.0, disbursement_date=date(2024, 1, 1),
                maturity_date=date(2027, 1, 1), interest_rate=12.0)
    s.add_all([cust, loan])
    s.flush()
    for tag, agent_id, agency in (("mine", agents["a1"].id, TEST_AGENCY_ID), ("peer", agents["a2"].id, TEST_AGENCY_ID),
                                  ("pool", None, TEST_AGENCY_ID), ("other", agents["o1"].id, OTHER),
                                  ("other_pool", None, OTHER)):
        s.add(Case(id=test_id(f"case:{tag}"), case_number=f"S-{tag}", customer_id=cust.id, loan_id=loan.id,
                   agent_id=agent_id, target_amount=1.0, bank_id=TEST_BANK_ID, agency_id=agency))
    s.commit()
    yield s, people
    s.close()


def _ids(q):
    return {r.id for r in q.all()}


def test_agents_in_scope_follows_the_role_table(db):
    s, p = db
    assert _ids(agents_in_scope(s, p["a1"])) == {test_id("ag:a1")}
    assert _ids(agents_in_scope(s, p["mgr"])) == {test_id("ag:a1")}
    assert _ids(agents_in_scope(s, p["admin"])) == {test_id("ag:a1"), test_id("ag:a2")}
    assert _ids(agents_in_scope(s, p["bank"])) == {test_id("ag:a1"), test_id("ag:a2"), test_id("ag:o1")}
    assert _ids(agents_in_scope(s, p["o_mgr"])) == {test_id("ag:o1")}


def test_cases_in_scope_never_crosses_an_agency_and_managers_see_their_own_pool(db):
    s, p = db
    c = lambda *tags: {test_id(f"case:{t}") for t in tags}  # noqa: E731
    assert _ids(cases_in_scope(s, p["a1"])) == c("mine")
    assert _ids(cases_in_scope(s, p["mgr"])) == c("mine", "pool")
    assert _ids(cases_in_scope(s, p["admin"])) == c("mine", "peer", "pool")
    assert _ids(cases_in_scope(s, p["o_mgr"])) == c("other", "other_pool")
    assert _ids(cases_in_scope(s, p["bank"])) == c("mine", "peer", "pool", "other", "other_pool")


def test_revoke_user_sessions_ends_every_live_session_but_the_one_kept(db):
    s, p = db
    user = p["mgr"]
    a = auth_service.open_session(s, user, "laptop", _req())
    b = auth_service.open_session(s, user, "tablet", _req())
    s.commit()
    from app.core.security import decode_token
    keep = decode_token(a["access_token"])["sid"]
    assert auth_service.revoke_user_sessions(s, user.id, "PASSWORD_CHANGED", by=user.id, except_sid=keep) == 1
    s.commit()
    s.expire_all()
    rows = {r.id: r for r in s.query(UserSession).filter(UserSession.user_id == user.id)}
    assert rows[keep].revoked_at is None
    other = decode_token(b["access_token"])["sid"]
    assert rows[other].revoked_reason == "PASSWORD_CHANGED" and rows[other].revoked_by == user.id


def test_an_unknown_revoke_reason_is_refused(db):
    s, p = db
    with pytest.raises(ValueError):
        auth_service.revoke_user_sessions(s, p["mgr"].id, "BECAUSE")
