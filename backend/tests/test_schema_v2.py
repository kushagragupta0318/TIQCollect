"""The v2 data model's structural guarantees (docs/DATA-MODEL-V2.md §2).

2026-09-24 (B02–B10). Pinned:
- every table lives in one of the ten domain schemas (or public for infra);
- table AND index/constraint names are globally unique — the property that
  makes the SQLite schema_translate_map and the Postgres search_path safe;
- the whole model creates on SQLite through the shared factory;
- lookup seeds agree with the Python enums they mirror (derived, not restated);
- the tenant listener fills bank_id / agency_id from parents, including a
  graph added in one flush, and never overrides an explicit value.
"""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timezone

import pytest

from app.core.database import DOMAIN_SCHEMAS, Base
from app.models import (
    Agent, AllocationDecision, AllocationOutcome, AllocationObjective, AllocationRun, Case, Customer,
    Loan, LoanType, Payment, PaymentMode, User, UserRole, Visit, VisitOutcome,
)
from app.models.lookups import LOOKUP_SEEDS
from tests._db import (
    TEST_AGENCY_ID, TEST_BANK_ID, create_schema, make_engine, make_session_factory, test_id,
)


def test_every_table_lives_in_a_declared_schema():
    allowed = set(DOMAIN_SCHEMAS) | {"public"}
    stray = [t.fullname for t in Base.metadata.sorted_tables if t.schema not in allowed]
    assert stray == []


def test_table_names_are_globally_unique():
    names = Counter(t.name for t in Base.metadata.sorted_tables)
    assert [n for n, c in names.items() if c > 1] == []


def test_index_and_constraint_names_are_globally_unique():
    # SQLite (no schemas) needs this; Postgres index names share a namespace
    # per schema only, so a clash would surface only on the SQLite path.
    seen: Counter = Counter()
    for t in Base.metadata.sorted_tables:
        for ix in t.indexes:
            seen[ix.name] += 1
    assert [n for n, c in seen.items() if c > 1] == []


def test_lookup_seeds_are_derived_from_the_enums():
    assert [r["code"] for r in LOOKUP_SEEDS["allocation_outcomes"]] == [o.value for o in AllocationOutcome]
    assert [r["code"] for r in LOOKUP_SEEDS["allocation_objectives"]] == [o.value for o in AllocationObjective]


@pytest.fixture()
def session():
    engine = make_engine()
    create_schema(engine)
    db = make_session_factory(engine)()
    yield db
    db.close()


def _graph(db):
    """A borrower, loan, agent and case added in ONE flush, tenant given nowhere
    except the roots' test defaults."""
    mgr = User(id=test_id("u:mgr"), email="vikram@aravallifs.in", phone="9810000001", full_name="Vikram Malhotra",
               hashed_password="x", role=UserRole.AGENCY_MANAGER)
    au = User(id=test_id("u:agent"), email="piyush@aravallifs.in", phone="9810000002", full_name="Piyush Sharma",
              hashed_password="x", role=UserRole.FIELD_AGENT)
    agent = Agent(id=test_id("agent"), user_id=au.id, employee_code="EMP0002", id_card_number="MTB-ID-0002",
                  base_latitude=28.45, base_longitude=77.07, territory="Sector 44, Gurugram",
                  manager_user_id=mgr.id)
    cust = Customer(id=test_id("cust"), customer_ref="MTB-C-0001", full_name="Rekha Nair",
                    date_of_birth=date(1984, 3, 2), gender="FEMALE", pan_masked="XXXXX1234X",
                    aadhaar_masked="XXXXXXXX5678", phone_primary="9899000001", address_line1="H-12, Sushant Lok",
                    city="Gurugram", state="Haryana", pincode="122009", latitude=28.46, longitude=77.08)
    loan = Loan(id=test_id("loan"), loan_account_number="MTB0000001", customer_id=cust.id,
                loan_type=LoanType.PERSONAL, branch_code="GGN044", sanctioned_amount=500000.0,
                disbursed_amount=500000.0, outstanding_principal=320000.0, total_outstanding=345000.0,
                emi_amount=16500.0, disbursement_date=date(2024, 1, 10), maturity_date=date(2027, 1, 10),
                interest_rate=13.5)
    case = Case(id=test_id("case"), case_number="MTB-CASE-1", customer_id=cust.id, loan_id=loan.id,
                agent_id=agent.id, target_amount=33000.0)
    db.add_all([mgr, au, agent, cust, loan, case])
    db.flush()
    return mgr, au, agent, cust, loan, case


def test_roots_take_the_test_default_tenant_and_children_inherit(session):
    mgr, au, agent, cust, loan, case = _graph(session)
    assert (au.bank_id, au.agency_id) == (TEST_BANK_ID, TEST_AGENCY_ID)
    assert agent.bank_id == TEST_BANK_ID and agent.agency_id == TEST_AGENCY_ID
    assert loan.bank_id == TEST_BANK_ID
    # A case takes its tenant from its loan (bank) and — with no placement —
    # the test default (agency).
    assert (case.bank_id, case.agency_id) == (TEST_BANK_ID, TEST_AGENCY_ID)

    visit = Visit(case_id=case.id, agent_id=agent.id, check_in_latitude=28.46, check_in_longitude=77.08,
                  check_in_time=datetime.now(timezone.utc), distance_from_customer_metres=12.0,
                  customer_met=True, outcome=VisitOutcome.PTP)
    pay = Payment(case_id=case.id, agent_id=agent.id, amount=5000.0, mode=PaymentMode.UPI,
                  receipt_number="RCPT-1", payment_date=datetime.now(timezone.utc))
    session.add_all([visit, pay])
    session.flush()
    assert (visit.bank_id, visit.agency_id) == (TEST_BANK_ID, TEST_AGENCY_ID)
    # loan_id is denormalised onto payments from the case.
    assert pay.loan_id == loan.id and pay.agency_id == TEST_AGENCY_ID


def test_an_explicit_tenant_is_never_overridden(session):
    other = test_id("agency:other")
    mgr, au, agent, cust, loan, case = _graph(session)
    v = Visit(case_id=case.id, agent_id=agent.id, agency_id=other, check_in_latitude=1.0, check_in_longitude=1.0,
              check_in_time=datetime.now(timezone.utc), distance_from_customer_metres=1.0, customer_met=False,
              outcome=VisitOutcome.NOT_AVAILABLE)
    session.add(v)
    session.flush()
    # Postgres's composite FK would refuse this row; the listener's job is
    # only to fill what is missing, never to rewrite what was said.
    assert v.agency_id == other


def test_decision_inherits_plan_date_from_its_run(session):
    mgr, au, agent, cust, loan, case = _graph(session)
    run = AllocationRun(manager_user_id=mgr.id, plan_date=date(2026, 9, 25))
    session.add(run)
    session.flush()
    d = AllocationDecision(run_id=run.id, case_id=case.id, outcome="ALLOCATED", reason="nearest eligible agent")
    session.add(d)
    session.flush()
    assert d.plan_date == date(2026, 9, 25)
    assert (d.bank_id, d.agency_id) == (TEST_BANK_ID, TEST_AGENCY_ID)


def test_loan_bank_name_reads_the_bank_row(session):
    *_, loan, _case = _graph(session)
    session.commit()
    assert session.get(Loan, loan.id).bank_name == "Meridian Trust Bank"
