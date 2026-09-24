"""The v2 data model's structural guarantees (docs/DATA-MODEL-V2.md §2).

2026-09-24 (B02–B10). Pinned:
- every table lives in one of the ten domain schemas (or public for infra);
- table AND index/constraint names are globally unique — the property that
  makes the SQLite schema_translate_map and the Postgres search_path safe;
- the whole model creates on SQLite through the shared factory;
- lookup seeds agree with the Python enums they mirror (derived, not restated);
- the tenant listener fills bank_id / agency_id from parents, including a
  graph added in one flush, and REFUSES an explicit value that disagrees
  with any declared parent (it used to let it through; see below);
- the database refuses a cross-agency row on its own (SQLite FKs enforced);
- a 5,000-row flush costs one parent query per class, not one per row;
- mappers configure and tables sort with zero SQLAlchemy warnings.
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
from app.models.tenancy_listener import TenantMismatchError
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


def _visit(case, agent, **kw):
    """`case` / `agent` are objects or ids (ids survive expunge/commit)."""
    return Visit(case_id=getattr(case, "id", case), agent_id=getattr(agent, "id", agent),
                 check_in_latitude=28.46, check_in_longitude=77.08,
                 check_in_time=datetime.now(timezone.utc), distance_from_customer_metres=1.0,
                 customer_met=False, outcome=VisitOutcome.NOT_AVAILABLE, **kw)


def test_an_explicit_tenant_that_agrees_is_kept(session):
    mgr, au, agent, cust, loan, case = _graph(session)
    v = _visit(case, agent, agency_id=TEST_AGENCY_ID, bank_id=TEST_BANK_ID)
    session.add(v)
    session.flush()
    assert (v.bank_id, v.agency_id) == (TEST_BANK_ID, TEST_AGENCY_ID)


def test_an_explicit_tenant_that_disagrees_is_refused_not_rewritten(session):
    """2026-09-24 — this test used to be `test_an_explicit_tenant_is_never_
    overridden` and asserted the mismatched agency_id was FLUSHED as given,
    on the reasoning that 'Postgres's composite FK would refuse this row'.
    That left the refusal to a constraint SQLite did not enforce, so the
    suite certified a row production would reject. The listener still never
    rewrites what was said — it now refuses it, at flush, naming both sides."""
    other = test_id("agency:other")
    mgr, au, agent, cust, loan, case = _graph(session)
    session.add(_visit(case, agent, agency_id=other))
    with pytest.raises(TenantMismatchError, match="agency_id"):
        session.flush()


def test_every_declared_parent_is_checked_not_only_the_one_that_filled(session):
    """A visit's case (agency A) fills its agency; its agent is agency B's.
    Filling from the first parent alone would flush that row."""
    mgr, au, agent, cust, loan, case = _graph(session)
    case_id = case.id
    session.commit()
    agent_b = _agency_b_agent(session)
    session.add(_visit(case_id, agent_b))
    with pytest.raises(TenantMismatchError, match="Agent.agency_id"):
        session.flush()


def _agency_b_agent(session) -> str:
    """A second agency of the same bank, with its own manager and agent (the
    agent's manager must be of its own agency: agents carry the composite FK
    (manager_user_id, agency_id) -> users). Returns the agent id."""
    from app.models.tenancy import Agency
    agency_b = test_id("agency:kaveri-resolve")
    session.add(Agency(id=agency_b, bank_id=TEST_BANK_ID, code="AGENCY-TIQ-002",
                       legal_name="Kaveri Resolve Partners LLP", trade_name="Kaveri Resolve",
                       status="ACTIVE", contacts=[], is_demo=True))
    session.flush()
    mgr_b = User(id=test_id("u:mgr-b"), email="lakshmi@kaveriresolve.in", phone="9810000004",
                 full_name="Lakshmi Iyer", hashed_password="x", role=UserRole.AGENCY_MANAGER,
                 bank_id=TEST_BANK_ID, agency_id=agency_b)
    bu = User(id=test_id("u:agent-b"), email="imran@kaveriresolve.in", phone="9810000003",
              full_name="Imran Qureshi", hashed_password="x", role=UserRole.FIELD_AGENT,
              bank_id=TEST_BANK_ID, agency_id=agency_b)
    agent_b = Agent(id=test_id("agent-b"), user_id=bu.id, employee_code="KRS0001", id_card_number="KRS-ID-0001",
                    base_latitude=28.45, base_longitude=77.07, territory="DLF Phase 3, Gurugram",
                    manager_user_id=mgr_b.id, bank_id=TEST_BANK_ID, agency_id=agency_b)
    session.add_all([mgr_b, bu, agent_b])
    session.flush()
    return agent_b.id


def test_the_database_refuses_a_cross_agency_visit_even_without_the_listener(session):
    """The composite FK (case_id, agency_id) -> cases(id, agency_id) is the
    guarantee; the listener is only the early, readable form of it. Inserted
    through Core, so no ORM event runs — only SQLite's enforced FK stands
    between this row and the table (tests/_db.make_engine turns it on)."""
    from sqlalchemy import insert
    from sqlalchemy.exc import IntegrityError

    mgr, au, agent, cust, loan, case = _graph(session)
    case_id, agent_id = case.id, agent.id
    session.commit()
    agency_b = test_id("agency:kaveri-resolve")
    _agency_b_agent(session)
    session.commit()
    row = {"id": test_id("visit:cross"), "case_id": case_id, "agent_id": agent_id,
           "bank_id": TEST_BANK_ID, "agency_id": agency_b,
           "check_in_latitude": 28.46, "check_in_longitude": 77.08,
           "check_in_time": datetime.now(timezone.utc), "distance_from_customer_metres": 1.0,
           "customer_met": False, "outcome": VisitOutcome.NOT_AVAILABLE.value}
    with pytest.raises(IntegrityError, match="FOREIGN KEY"):
        session.execute(insert(Visit.__table__), [row])
    session.rollback()
    # The same row with the case's own agency goes in: the refusal above was
    # the tenant FK, not some other column.
    session.execute(insert(Visit.__table__), [{**row, "agency_id": TEST_AGENCY_ID}])
    session.commit()


def test_listener_resolves_a_large_flush_in_one_query_per_parent_class(session):
    """Coordinator audit item 1. The first listener scanned session.new per
    child and issued a SELECT per parent: 8,000 new visits took 36.9 s against
    1.0 s with the tenant given explicitly. Pinned two ways — by the number of
    parent SELECTs (the property), and by a wall-clock ceiling generous enough
    for a loaded CI box (the symptom)."""
    import time
    from sqlalchemy import event

    mgr, au, agent, cust, loan, case = _graph(session)
    case_id, agent_id = case.id, agent.id
    session.commit()
    session.expunge_all()          # parents must come from the database, not the identity map
    visits = [_visit(case_id, agent_id) for _ in range(5000)]
    session.add_all(visits)

    selects = []

    def _count(conn, cursor, statement, *a):  # noqa: ARG001
        if statement.lstrip().upper().startswith("SELECT"):
            selects.append(statement)

    engine = session.get_bind()
    event.listen(engine, "before_cursor_execute", _count)
    try:
        t0 = time.perf_counter()
        session.flush()
        elapsed = time.perf_counter() - t0
    finally:
        event.remove(engine, "before_cursor_execute", _count)
    assert all(v.agency_id == TEST_AGENCY_ID for v in visits)
    # One IN query for Case, one for Agent — never one per visit.
    assert len(selects) <= 4, selects[:5]
    assert elapsed < 2.0, f"5,000-visit flush took {elapsed:.2f}s"


def test_mappers_configure_without_a_single_sqlalchemy_warning():
    """Coordinator audit items 4 and 5, run in a fresh interpreter because
    mapper configuration happens once per process and the rest of the suite
    has usually done it already. SAWarning is promoted to an error, so an
    overlapping relationship (the composite tenant FKs make SQLAlchemy infer
    joins on BOTH columns) or an unresolvable table cycle fails here.

    Why it matters: a relationship inferred from the composite
    (customer_id, bank_id) FK joined on bank_id too, so a bank mismatch made
    `loan.customer` silently None — and the ML adapter then dropped every
    customer feature without an error, the 2026-09-08 silent-failure shape."""
    import subprocess
    import sys
    code = (
        "import warnings\n"
        "from sqlalchemy.exc import SAWarning\n"
        "warnings.simplefilter('error', SAWarning)\n"
        "import app.models\n"
        "from sqlalchemy.orm import configure_mappers\n"
        "configure_mappers()\n"
        "from app.core.database import Base\n"
        "print(len(Base.metadata.sorted_tables))\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr[-3000:]


def test_sorted_tables_needs_no_cycle_breaking_guess():
    """use_alter on cases.closed_by_bank_action_id and
    model_predictions.case_id (audit item 5). Without it SQLAlchemy warns it
    cannot sort the tables and create_all / drop_all order becomes a guess."""
    import warnings
    from sqlalchemy.exc import SAWarning
    with warnings.catch_warnings():
        warnings.simplefilter("error", SAWarning)
        assert len(Base.metadata.sorted_tables) > 50


def test_loan_customer_joins_on_id_only():
    rel = Loan.__mapper__.relationships["customer"]
    cols = {c.name for c in rel.local_columns}
    assert cols == {"customer_id"}, cols


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
