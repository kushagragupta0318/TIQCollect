"""Tenant isolation for the UNASSIGNED (unallocated) case pool.

GET /manager/cases/unallocated and POST /manager/cases/{id}/reassign both
reached a case through `agent_id IS NULL` with no tenant bound, so every
unplaced case in the database matched — any bank, any agency. The same defect
was closed in planner_service.plan_next_day (A04); the fix never reached this
router.

Why the existing sweeps did not catch it:
  * test_every_manager_route_that_reads_tenant_data_is_scoped is TEXTUAL and
    found `_scoped_agent_ids` in the handler — used for its AGENT query, not
    for the case query underneath it.
  * test_cross_tenant_behavioral's fixture only ever creates ASSIGNED cases,
    so the `agent_id IS NULL` branch was never exercised by it.

This file closes both gaps behaviourally: two banks, two agencies, and an
UNASSIGNED case in each.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event

from app.core.database import get_db
from app.core.security import create_access_token
from app.main import app
from app.models.agent import Agent
from app.models.case import Case, CaseStatus
from app.models.customer import Customer
from app.models.loan import Loan, LoanType
from app.models.tenancy import Agency, Bank, Branch
from app.models.user import User, UserRole
from tests._db import create_schema, make_engine, make_session_factory, test_id

UNALLOCATED = "/api/v1/manager/cases/unallocated"


def _tenant(db, tag: str, phone_prefix: str):
    """One bank + one agency + a manager, an agent, and TWO cases: one held by
    the agent (ASSIGNED) and one still in the agency's unallocated pool."""
    bank_id = test_id(f"ub:bank:{tag}")
    agency_id = test_id(f"ub:agency:{tag}")
    db.add(Bank(id=bank_id, code=f"U{tag.upper()[:8]}", legal_name=f"{tag.title()} Bank Ltd.",
                display_name=f"{tag.title()} Bank", timezone="Asia/Kolkata", brand={},
                status="ACTIVE", is_demo=True))
    db.flush()
    db.add(Agency(id=agency_id, bank_id=bank_id, code=f"UAG-{tag.upper()[:7]}",
                  legal_name=f"{tag.title()} Recovery Pvt. Ltd.", trade_name=f"{tag.title()} Recovery",
                  status="ACTIVE", contacts=[], is_demo=True))
    db.add(Branch(id=test_id(f"ub:branch:{tag}"), bank_id=bank_id, branch_code="BR1",
                  name=f"{tag.title()} HQ", is_active=True))
    db.flush()

    mgr = User(id=test_id(f"ub:u:mgr:{tag}"), email=f"mgr.{tag}@unalloc.test", phone=f"{phone_prefix}001",
               full_name=f"Manager {tag.title()}", hashed_password="x", role=UserRole.AGENCY_MANAGER,
               bank_id=bank_id, agency_id=agency_id)
    admin = User(id=test_id(f"ub:u:adm:{tag}"), email=f"adm.{tag}@unalloc.test", phone=f"{phone_prefix}004",
                 full_name=f"Admin {tag.title()}", hashed_password="x", role=UserRole.AGENCY_ADMIN,
                 bank_id=bank_id, agency_id=agency_id)
    agent_user = User(id=test_id(f"ub:u:agent:{tag}"), email=f"agent.{tag}@unalloc.test",
                      phone=f"{phone_prefix}002", full_name=f"Agent {tag.title()}", hashed_password="x",
                      role=UserRole.FIELD_AGENT, bank_id=bank_id, agency_id=agency_id)
    db.add_all([mgr, admin, agent_user])
    db.flush()

    agent = Agent(id=test_id(f"ub:agent:{tag}"), user_id=agent_user.id, employee_code=f"{tag.upper()[:3]}901",
                  id_card_number=f"{tag.upper()}-UID-901", manager_user_id=mgr.id,
                  base_latitude=28.4, base_longitude=77.0, territory=f"{tag.title()} Territory",
                  bank_id=bank_id, agency_id=agency_id)
    db.add(agent)
    db.flush()

    cust = Customer(id=test_id(f"ub:cust:{tag}"), bank_id=bank_id, customer_ref=f"{tag.upper()}-UC1",
                    full_name=f"Borrower {tag.title()}", date_of_birth=date(1985, 1, 1), gender="M",
                    pan_masked="XXXXX2222X", aadhaar_masked="XXXXXXXX2222",
                    phone_primary=f"{phone_prefix}003", address_line1="9 Pool Road", city="Testville",
                    state="Haryana", pincode="122001", latitude=28.4, longitude=77.0)
    loan = Loan(id=test_id(f"ub:loan:{tag}"), bank_id=bank_id, loan_account_number=f"{tag.upper()}-ULN1",
                customer_id=cust.id, loan_type=LoanType.PERSONAL, branch_code="BR1",
                sanctioned_amount=100000.0, disbursed_amount=100000.0, outstanding_principal=80000.0,
                total_outstanding=80000.0, emi_amount=5000.0, interest_rate=12.0,
                disbursement_date=date(2024, 1, 1), maturity_date=date(2027, 1, 1))
    db.add_all([cust, loan])
    db.flush()

    # The case under test: placed with this agency, not yet allocated.
    unassigned = Case(id=test_id(f"ub:case:unassigned:{tag}"), case_number=f"{tag.upper()}-UNALLOC",
                      customer_id=cust.id, loan_id=loan.id, agent_id=None,
                      status=CaseStatus.UNASSIGNED, target_amount=80000.0, collected_amount=0.0,
                      bank_id=bank_id, agency_id=agency_id)
    # A control: already allocated, so it must NOT appear in the unallocated pool.
    assigned = Case(id=test_id(f"ub:case:assigned:{tag}"), case_number=f"{tag.upper()}-ASSIGNED",
                    customer_id=cust.id, loan_id=loan.id, agent_id=agent.id,
                    status=CaseStatus.ASSIGNED, target_amount=50000.0, collected_amount=0.0,
                    bank_id=bank_id, agency_id=agency_id)
    db.add_all([unassigned, assigned])
    db.commit()

    return {"bank_id": bank_id, "agency_id": agency_id, "manager": mgr, "admin": admin,
            "agent": agent, "customer": cust, "unassigned": unassigned, "assigned": assigned}


@pytest.fixture()
def two_banks():
    engine = make_engine()

    @event.listens_for(engine, "connect")
    def _sqlite_helpers(dbapi_conn, _record):
        dbapi_conn.create_function("to_char", 2, lambda v, f: str(v)[:7] if v else None)

    create_schema(engine, seed_tenant=False)
    Session = make_session_factory(engine)
    db = Session()
    a = _tenant(db, "alpha", "98200010")
    b = _tenant(db, "bravo", "98200020")

    def override():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = override
    try:
        yield {"db": db, "a": a, "b": b, "Session": Session}
    finally:
        app.dependency_overrides.pop(get_db, None)
        db.close()


def _headers(user: User) -> dict:
    return {"Authorization": "Bearer " + create_access_token(user.id, user.role.value, "dev-unalloc")}


def _case_numbers(payload) -> list[str]:
    """Every case_number in the response, whatever envelope it uses."""
    rows = payload if isinstance(payload, list) else payload.get("cases", payload.get("items", []))
    return [r.get("case_number") for r in rows]


# ── 1 / 2 / 3: the isolation itself ─────────────────────────────────────────

def test_a_manager_sees_their_own_agencys_unallocated_pool(two_banks):
    a = two_banks["a"]
    r = TestClient(app).get(UNALLOCATED, headers=_headers(a["manager"]))
    assert r.status_code == 200, r.text
    assert "ALPHA-UNALLOC" in _case_numbers(r.json())


def test_a_manager_never_sees_another_banks_unallocated_pool(two_banks):
    """The defect: BRAVO's case reached ALPHA's manager. Both directions, so a
    one-sided filter cannot pass this."""
    a, b = two_banks["a"], two_banks["b"]
    client = TestClient(app)

    seen_by_a = _case_numbers(client.get(UNALLOCATED, headers=_headers(a["manager"])).json())
    assert "BRAVO-UNALLOC" not in seen_by_a

    seen_by_b = _case_numbers(client.get(UNALLOCATED, headers=_headers(b["manager"])).json())
    assert "ALPHA-UNALLOC" not in seen_by_b


def test_no_identifier_of_the_other_tenant_leaks_in_the_raw_body(two_banks):
    """Not just case_number: the borrower's name, phone and the case id are
    what actually makes this a disclosure. Checked as substrings of the raw
    body, so a field this test does not know about is still covered."""
    a, b = two_banks["a"], two_banks["b"]
    body = TestClient(app).get(UNALLOCATED, headers=_headers(a["manager"])).text
    for marker in (str(b["unassigned"].id), b["customer"].full_name,
                   b["customer"].phone_primary, "BRAVO-UNALLOC"):
        assert marker not in body, f"leaked {marker!r}"


# ── 4: authentication ───────────────────────────────────────────────────────

def test_an_unauthenticated_request_is_refused(two_banks):
    r = TestClient(app).get(UNALLOCATED)
    assert r.status_code in (401, 403), r.status_code
    assert "ALPHA-UNALLOC" not in r.text and "BRAVO-UNALLOC" not in r.text


# ── 5: the scope cannot be widened from the request ─────────────────────────

@pytest.mark.parametrize("query", [
    "?agency_id={other_agency}",
    "?bank_id={other_bank}",
    "?manager_user_id={other_mgr}",
    "?agency_id={other_agency}&bank_id={other_bank}",
])
def test_request_parameters_cannot_widen_the_tenant_scope(two_banks, query):
    """A client-supplied tenant id is never proof of authorization. Whether the
    route declares these params or ignores them, the answer must not change."""
    a, b = two_banks["a"], two_banks["b"]
    url = UNALLOCATED + query.format(other_agency=b["agency_id"], other_bank=b["bank_id"],
                                     other_mgr=b["manager"].id)
    r = TestClient(app).get(url, headers=_headers(a["manager"]))
    assert r.status_code in (200, 422), r.text
    if r.status_code == 200:
        assert "BRAVO-UNALLOC" not in _case_numbers(r.json())
        assert str(b["unassigned"].id) not in r.text


# ── 6: counts agree with the rows ───────────────────────────────────────────

def test_another_tenants_unallocated_cases_do_not_move_this_tenants_count(two_banks):
    """A count computed on a wider query than the rows is the same leak, one
    integer at a time. Tenant B gains three cases; tenant A's answer must not
    move at all."""
    a = two_banks["a"]
    client = TestClient(app)

    before = client.get(UNALLOCATED, headers=_headers(a["manager"])).json()
    before_rows = before if isinstance(before, list) else before.get("cases", [])

    db = two_banks["Session"]()
    b = two_banks["b"]
    for i in range(3):
        db.add(Case(id=test_id(f"ub:case:extra-b-{i}"), case_number=f"BRAVO-EXTRA-{i}",
                    customer_id=b["customer"].id, loan_id=test_id("ub:loan:bravo"),
                    agent_id=None, status=CaseStatus.UNASSIGNED, target_amount=1000.0,
                    collected_amount=0.0, bank_id=b["bank_id"], agency_id=b["agency_id"]))
    db.commit()
    db.close()

    after = client.get(UNALLOCATED, headers=_headers(a["manager"])).json()
    after_rows = after if isinstance(after, list) else after.get("cases", [])

    assert len(after_rows) == len(before_rows)
    assert not any("BRAVO" in (n or "") for n in _case_numbers(after))


# ── 7 / 8: behaviour preserved ──────────────────────────────────────────────

def test_an_already_allocated_case_is_not_in_the_unallocated_pool(two_banks):
    """The status filter still does its job — the fix narrows the tenant, not
    the meaning of 'unallocated'."""
    a = two_banks["a"]
    r = TestClient(app).get(UNALLOCATED, headers=_headers(a["manager"]))
    assert "ALPHA-ASSIGNED" not in _case_numbers(r.json())


def test_each_row_still_carries_its_reason(two_banks):
    """The endpoint's purpose is WHY a case is unallocated; the tenant fix must
    not have emptied the payload."""
    a = two_banks["a"]
    rows = TestClient(app).get(UNALLOCATED, headers=_headers(a["manager"])).json()
    rows = rows if isinstance(rows, list) else rows.get("cases", [])
    mine = [r for r in rows if r.get("case_number") == "ALPHA-UNALLOC"]
    assert mine, "the manager's own unallocated case disappeared"
    assert mine[0].get("reason"), "a row with no reason is the page's whole point missing"


# ── 9: the other legitimate agency role ─────────────────────────────────────

def test_an_agency_admin_sees_their_whole_agencys_pool_and_no_other(two_banks):
    """AGENCY_ADMIN oversees the agency rather than a roster of agents, so the
    fix must not collapse to 'this manager's agents' — scope.cases_in_scope
    gives them the agency, and nothing beyond it."""
    a, b = two_banks["a"], two_banks["b"]
    r = TestClient(app).get(UNALLOCATED, headers=_headers(a["admin"]))
    assert r.status_code == 200, r.text
    numbers = _case_numbers(r.json())
    assert "ALPHA-UNALLOC" in numbers
    assert "BRAVO-UNALLOC" not in numbers


# ── the write route that shared the defect ──────────────────────────────────

def test_reassigning_another_tenants_unallocated_case_is_a_uniform_404(two_banks):
    """POST /cases/{id}/reassign resolved any unplaced case in the database.
    A foreign id must now be indistinguishable from one that does not exist,
    so the id cannot be used to probe another agency."""
    a, b = two_banks["a"], two_banks["b"]
    client = TestClient(app)
    # A VALID body on purpose: a 422 from schema validation would pass a
    # "not 200" assertion without the route's access check ever running.
    body = {"new_agent_id": str(a["agent"].id), "reason": "tenancy regression probe"}

    foreign = client.post(f"/api/v1/manager/cases/{b['unassigned'].id}/reassign",
                          json=body, headers=_headers(a["manager"]))
    missing = client.post(f"/api/v1/manager/cases/{test_id('ub:case:nope')}/reassign",
                          json=body, headers=_headers(a["manager"]))

    assert foreign.status_code == 404, foreign.text
    assert foreign.status_code == missing.status_code
    assert foreign.json() == missing.json(), "a foreign id must not be distinguishable from a missing one"
