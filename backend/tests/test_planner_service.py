"""
Comprehensive Test Suite for Smart Nightly Case Allocation Engine & PlannerService.
===================================================================================
Tests:
- Next-day date calculations (weekdays, Saturday->Monday, Sunday->Monday).
- Historical agent competency extraction.
- Hard compliance gates (DNC, female-agent matching, safety flags, capacity limits).
- Soft scoring (loan product affinity, proximity, team workload balance, continuity).
- Beat generation and route sequencing.
- Idempotency & Rollback safety.
- HTTP Manager API endpoints.
"""
from datetime import date, datetime, timedelta, timezone
import uuid
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.core.security import create_access_token
from app.main import app
from app.models.agent import Agent, AgentSpecialization, AgentStatus, AgentTier
from app.models.allocation_decision import AllocationDecision, AllocationOutcome
from app.models.allocation_run import AllocationRun, AllocationRunStatus, AllocationStrategy
from app.models.beat import Beat, BeatStatus
from app.models.case import Case, CasePriority, CaseStatus
from app.models.customer import Customer, RiskCategory
from app.models.loan import DPDBucket, Loan, LoanStatus, LoanType
from app.models.payment import Payment, PaymentMode, PaymentStatus
from app.models.ptp import PTP, PTPStatus
from app.models.tenancy import Agency
from app.models.user import User, UserRole
from app.models.visit import PersonMet, Visit, VisitOutcome
from app.services.planner_service import PlannerService, get_target_plan_date
from tests._db import TEST_BANK_ID, create_schema, drop_schema, make_engine, make_session_factory, test_id  # noqa: F401

test_engine = make_engine()
TestingSessionLocal = make_session_factory(autocommit=False, autoflush=False, bind=test_engine)


@pytest.fixture(autouse=True)
def setup_db():
    create_schema(bind=test_engine)
    yield
    drop_schema(bind=test_engine)


@pytest.fixture
def db_session():
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client(db_session):
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def test_data(db_session):
    """Seed a manager, 2 agents (1 male, 1 female), customers, loans, and cases."""
    # Manager
    mgr = User(
        id=str(uuid.uuid4()),
        email="manager_test@tiqcollect.in",
        phone="9800000001",
        full_name="Manager Vikram",
        hashed_password="hash",
        role=UserRole.AGENCY_MANAGER,
        is_active=True,
        is_verified=True,
    )
    db_session.add(mgr)

    # Agent 1 (Male, Auto Specialist, Delhi)
    u1 = User(
        id=str(uuid.uuid4()), email="agent1@tiqcollect.in", phone="9800000002",
        full_name="Agent Rajesh", hashed_password="hash", role=UserRole.FIELD_AGENT,
        is_active=True, is_verified=True,
    )
    db_session.add(u1)
    ag1 = Agent(
        id=str(uuid.uuid4()), user_id=u1.id, employee_code="EMP001",
        id_card_number="TIQ001", manager_user_id=mgr.id,
        gender="M", base_latitude=28.6139, base_longitude=77.2090, territory="Delhi",
        languages_spoken=["HINDI", "ENGLISH"], specialization=AgentSpecialization.SECURED,
        max_cases_per_day=3, status=AgentStatus.ON_DUTY, tier=AgentTier.TIER_1,
        ranking_score=90.0, lifetime_collection_rate=0.60,
    )
    db_session.add(ag1)

    # Agent 2 (Female, Personal Loan Specialist, Gurugram)
    u2 = User(
        id=str(uuid.uuid4()), email="agent2@tiqcollect.in", phone="9800000003",
        full_name="Agent Priya", hashed_password="hash", role=UserRole.FIELD_AGENT,
        is_active=True, is_verified=True,
    )
    db_session.add(u2)
    ag2 = Agent(
        id=str(uuid.uuid4()), user_id=u2.id, employee_code="EMP002",
        id_card_number="TIQ002", manager_user_id=mgr.id,
        gender="F", base_latitude=28.4595, base_longitude=77.0266, territory="Gurugram",
        languages_spoken=["HINDI", "PUNJABI"], specialization=AgentSpecialization.UNSECURED,
        max_cases_per_day=3, status=AgentStatus.ON_DUTY, tier=AgentTier.TIER_2,
        ranking_score=75.0, lifetime_collection_rate=0.45,
    )
    db_session.add(ag2)

    # Customer 1: Normal Auto Loan (near Delhi)
    c1 = Customer(
        id=str(uuid.uuid4()), customer_ref="CUST01", full_name="Aarav Sharma",
        date_of_birth=date(1990, 1, 1), gender="M", pan_masked="ABCDE1234F", aadhaar_masked="123456789012",
        phone_primary="9900000001", address_line1="Connaught Place, Delhi", city="Delhi", state="Delhi",
        pincode="110001", latitude=28.6315, longitude=77.2167, language_preference="HINDI",
    )
    db_session.add(c1)
    l1 = Loan(
        id=str(uuid.uuid4()), customer_id=c1.id, loan_account_number="LN001",
        loan_type=LoanType.AUTO, branch_code="DL01",
        sanctioned_amount=500000.0, disbursed_amount=500000.0, outstanding_principal=250000.0,
        total_outstanding=250000.0, overdue_amount=50000.0, emi_amount=15000.0, interest_rate=12.5,
        disbursement_date=date(2022, 1, 1), maturity_date=date(2027, 1, 1),
        dpd=45, dpd_bucket=DPDBucket.BUCKET_2, status=LoanStatus.ACTIVE,
    )
    db_session.add(l1)
    case1 = Case(
        id=str(uuid.uuid4()), case_number="CASE001", customer_id=c1.id, loan_id=l1.id,
        agent_id=None, status=CaseStatus.UNASSIGNED, priority=CasePriority.HIGH,
        target_amount=50000, collected_amount=0,
    )
    db_session.add(case1)

    # Customer 2: Requires Female Agent (Gurugram)
    c2 = Customer(
        id=str(uuid.uuid4()), customer_ref="CUST02", full_name="Sunita Devi",
        date_of_birth=date(1992, 5, 15), gender="F", pan_masked="ABCDE5678G", aadhaar_masked="987654321098",
        phone_primary="9900000002", address_line1="Sector 44, Gurugram", city="Gurugram", state="Haryana",
        pincode="122003", latitude=28.4551, longitude=77.0716, language_preference="HINDI",
        requires_female_agent=True,
    )
    db_session.add(c2)
    l2 = Loan(
        id=str(uuid.uuid4()), customer_id=c2.id, loan_account_number="LN002",
        loan_type=LoanType.PERSONAL, branch_code="GG01",
        sanctioned_amount=200000.0, disbursed_amount=200000.0, outstanding_principal=120000.0,
        total_outstanding=120000.0, overdue_amount=35000.0, emi_amount=8000.0, interest_rate=14.0,
        disbursement_date=date(2023, 1, 1), maturity_date=date(2026, 1, 1),
        dpd=80, dpd_bucket=DPDBucket.BUCKET_3, status=LoanStatus.ACTIVE,
    )
    db_session.add(l2)
    case2 = Case(
        id=str(uuid.uuid4()), case_number="CASE002", customer_id=c2.id, loan_id=l2.id,
        agent_id=None, status=CaseStatus.UNASSIGNED, priority=CasePriority.CRITICAL,
        target_amount=35000, collected_amount=0,
    )
    db_session.add(case2)

    # Customer 3: Do-Not-Contact
    c3 = Customer(
        id=str(uuid.uuid4()), customer_ref="CUST03", full_name="Blocked Borrower",
        date_of_birth=date(1985, 11, 20), gender="M", pan_masked="ABCDE9999Z", aadhaar_masked="112233445566",
        phone_primary="9900000003", address_line1="Noida Sector 18", city="Noida", state="Uttar Pradesh",
        pincode="201301", latitude=28.5677, longitude=77.3285, do_not_contact=True, language_preference="HINDI",
    )
    db_session.add(c3)
    l3 = Loan(
        id=str(uuid.uuid4()), customer_id=c3.id, loan_account_number="LN003",
        loan_type=LoanType.PERSONAL, branch_code="NO01",
        sanctioned_amount=100000.0, disbursed_amount=100000.0, outstanding_principal=80000.0,
        total_outstanding=80000.0, overdue_amount=25000.0, emi_amount=5000.0, interest_rate=15.0,
        disbursement_date=date(2023, 6, 1), maturity_date=date(2025, 6, 1),
        dpd=95, dpd_bucket=DPDBucket.NPA, status=LoanStatus.ACTIVE,
    )
    db_session.add(l3)
    case3 = Case(
        id=str(uuid.uuid4()), case_number="CASE003", customer_id=c3.id, loan_id=l3.id,
        agent_id=None, status=CaseStatus.UNASSIGNED, priority=CasePriority.CRITICAL,
        target_amount=25000, collected_amount=0,
    )
    db_session.add(case3)

    # Historical payments for Agent 1 on AUTO loans
    p1 = Payment(
        id=str(uuid.uuid4()), case_id=case1.id, agent_id=ag1.id, amount=40000,
        mode=PaymentMode.UPI, payment_date=date.today() - timedelta(days=10),
        status=PaymentStatus.VERIFIED, receipt_number="RCP001",
    )
    db_session.add(p1)

    db_session.commit()
    return {
        "manager": mgr,
        "agent1": ag1,
        "agent2": ag2,
        "case1": case1,
        "case2": case2,
        "case3": case3,
    }


def test_target_plan_date():
    """Verify next-day planning rules across weekdays, Saturday, and Sunday."""
    # Wednesday -> Thursday
    wed = date(2026, 9, 2)
    assert get_target_plan_date(wed) == date(2026, 9, 3)

    # Friday -> Saturday
    fri = date(2026, 9, 4)
    assert get_target_plan_date(fri) == date(2026, 9, 5)

    # Saturday -> Monday (Skip Sunday)
    sat = date(2026, 9, 5)
    assert get_target_plan_date(sat) == date(2026, 9, 7)

    # Sunday -> Monday
    sun = date(2026, 9, 6)
    assert get_target_plan_date(sun) == date(2026, 9, 7)


def test_planner_historical_matrix(db_session, test_data):
    """Verify historical competency matrix calculates recovery rate correctly."""
    planner = PlannerService(db_session, manager_user_id=test_data["manager"].id)
    matrix = planner.get_historical_competency_matrix([test_data["agent1"].id])

    assert test_data["agent1"].id in matrix
    ag1_data = matrix[test_data["agent1"].id]
    assert ag1_data["total_collected"] == 40000.0
    assert "AUTO" in ag1_data["loan_type_recovery"]


def test_planner_hard_gates_and_allocation(db_session, test_data):
    """Verify DNC block, female-agent matching, and smart allocation."""
    planner = PlannerService(db_session, manager_user_id=test_data["manager"].id)
    tomorrow = date.today() + timedelta(days=1)

    run = planner.plan_next_day(plan_date=tomorrow, strategy="SMART")

    assert run.status == AllocationRunStatus.PLANNED.value
    assert run.total_cases_allocated == 2  # case1 and case2
    assert run.total_cases_blocked == 1    # case3 (DNC)

    # Check decisions
    dnc_decision = next(d for d in run.decisions if d.case_id == test_data["case3"].id)
    assert dnc_decision.outcome == AllocationOutcome.BLOCKED.value
    assert "Do-Not-Contact" in dnc_decision.reason

    # Female agent requirement check: case2 must be assigned to Priya (agent2)
    female_decision = next(d for d in run.decisions if d.case_id == test_data["case2"].id)
    assert female_decision.outcome == AllocationOutcome.ALLOCATED.value
    assert female_decision.allocated_agent_id == test_data["agent2"].id

    # Check generated beats
    beats = db_session.query(Beat).filter(Beat.allocation_run_id == run.id).all()
    assert len(beats) == 2
    for b in beats:
        assert b.status == BeatStatus.PLANNED
        assert b.beat_date == tomorrow
        assert b.total_cases >= 1


def test_planner_pool_is_scoped_to_the_managers_own_agency(db_session, test_data):
    """A04 (standalone plan, coordinator audit 2026-09-28): the unassigned
    half of the candidate-case query used to carry no tenant filter at all,
    so an unassigned case belonging to a DIFFERENT agency's book would enter
    every manager's nightly plan and could be handed to their agent. Reuses
    test_planner_hard_gates_and_allocation's exact fixture and its exact
    expected counts (2 allocated, 1 blocked) — a case belonging to another
    agency, added here, must change NEITHER."""
    other_agency_id = test_id("agency:planner-scope-other")
    db_session.add(Agency(id=other_agency_id, bank_id=TEST_BANK_ID, code="AGENCY-OTHER-A04",
                          legal_name="Nilgiri Field Recovery LLP", trade_name="Nilgiri Field Recovery",
                          status="ACTIVE", contacts=[], is_demo=True))
    db_session.flush()
    other_case = Case(
        id=str(uuid.uuid4()), case_number="CASE-OTHER-AGENCY", customer_id=test_data["case1"].customer_id,
        loan_id=test_data["case1"].loan_id, agent_id=None, agency_id=other_agency_id,
        status=CaseStatus.UNASSIGNED, priority=CasePriority.HIGH, target_amount=60000, collected_amount=0,
    )
    db_session.add(other_case)
    db_session.commit()

    planner = PlannerService(db_session, manager_user_id=test_data["manager"].id)
    tomorrow = date.today() + timedelta(days=1)
    run = planner.plan_next_day(plan_date=tomorrow, strategy="SMART")

    assert run.total_cases_allocated == 2   # unchanged: case1, case2 — NOT case_other
    assert run.total_cases_blocked == 1     # unchanged: case3 (DNC)
    decided_case_ids = {d.case_id for d in run.decisions}
    assert other_case.id not in decided_case_ids
    db_session.refresh(other_case)
    assert other_case.agent_id is None      # never claimed by an out-of-agency agent


def test_a_manager_whose_agency_is_suspended_is_not_planned(db_session, test_data, monkeypatch):
    """MED, coordinator audit on c041835: the nightly task selected managers
    by role + is_active only, with no Agency join — a manager whose agency
    had been SUSPENDED (offboarded, contract lapsed, under review) was still
    planned every night regardless. test_data's own manager sits under the
    default ACTIVE test agency and must still be planned; a second manager
    under a SUSPENDED agency must not be attempted at all — not planned, not
    recorded as a failure either, simply excluded from the query."""
    from app.workers.tasks import allocation as mod

    suspended_agency_id = test_id("agency:suspended-a04")
    db_session.add(Agency(id=suspended_agency_id, bank_id=TEST_BANK_ID, code="AGENCY-SUSPENDED",
                          legal_name="Kumaon Debt Solutions Pvt. Ltd.", trade_name="Kumaon Debt Solutions",
                          status="SUSPENDED", contacts=[], is_demo=True))
    db_session.flush()
    suspended_mgr = User(
        id=str(uuid.uuid4()), email="suspended_mgr@tiqcollect.in", phone="9800009999",
        full_name="Manager Of A Suspended Agency", hashed_password="hash", role=UserRole.AGENCY_MANAGER,
        agency_id=suspended_agency_id, is_active=True, is_verified=True,
    )
    db_session.add(suspended_mgr)
    db_session.commit()

    monkeypatch.setattr("app.core.database.SessionLocal", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)   # the fixture owns closing it

    out = mod.run_nightly_allocation.__wrapped__(
        strategy="SMART", plan_date_str=str(date.today() + timedelta(days=1)))

    assert test_data["manager"].email in out["planned"]
    assert suspended_mgr.email not in out["planned"]
    assert suspended_mgr.email not in out["failed"]


def test_a_manager_with_no_resolvable_agency_raises_without_touching_a_beat(db_session, test_data):
    """LOW, coordinator audit on c041835: manager_agency_id is resolved by
    looking up the manager's own User row; a manager_user_id that names
    nobody (a stale reference — the realistic shape this branch actually
    takes, since a live AGENCY_MANAGER/AGENCY_ADMIN row can't carry a NULL
    agency_id past ck_users_role_scope) makes that lookup return None the
    same way an explicit NULL would. This must raise immediately — before
    the plan lock, before any Beat query — not fall through to the
    "no active agents" branch's zero-run AllocationRun row, which describes
    a different, legitimate state (a real manager with nobody to plan for
    today), not a manager who could not be resolved at all."""
    ghost_manager_id = str(uuid.uuid4())
    planner = PlannerService(db_session, manager_user_id=ghost_manager_id)
    tomorrow = date.today() + timedelta(days=1)

    with pytest.raises(ValueError, match="no agency_id"):
        planner.plan_next_day(plan_date=tomorrow, strategy="SMART")

    assert db_session.query(AllocationRun).filter(
        AllocationRun.manager_user_id == ghost_manager_id).count() == 0
    assert db_session.query(Beat).count() == 0


def test_planner_rollback(db_session, test_data):
    """Verify rollback removes draft PLANNED beats and marks run as ROLLED_BACK."""
    planner = PlannerService(db_session, manager_user_id=test_data["manager"].id)
    tomorrow = date.today() + timedelta(days=1)

    run = planner.plan_next_day(plan_date=tomorrow, strategy="SMART")
    run_id = run.id

    # Verify beats exist
    beats_before = db_session.query(Beat).filter(Beat.allocation_run_id == run_id).all()
    assert len(beats_before) == 2

    # Rollback
    success = planner.rollback_plan(run_id=run_id)
    assert success is True

    # Verify beats removed
    beats_after = db_session.query(Beat).filter(Beat.allocation_run_id == run_id).all()
    assert len(beats_after) == 0

    run_after = db_session.query(AllocationRun).filter(AllocationRun.id == run_id).first()
    assert run_after.status == AllocationRunStatus.ROLLED_BACK.value


def test_manager_allocation_endpoints(client, db_session, test_data):
    """Test HTTP endpoints for manager allocation."""
    mgr = test_data["manager"]
    token = create_access_token(user_id=mgr.id, role=mgr.role.value, device_id="test_device_01")
    headers = {"Authorization": f"Bearer {token}"}

    # 1. Trigger Plan via POST /manager/allocation/plan
    resp = client.post("/api/v1/manager/allocation/plan", json={"strategy": "SMART"}, headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_cases_allocated"] == 2
    run_id = data["run_id"]

    # 2. Fetch Latest via GET /manager/allocation/latest
    resp_latest = client.get("/api/v1/manager/allocation/latest", headers=headers)
    assert resp_latest.status_code == 200
    latest_data = resp_latest.json()
    assert latest_data["has_plan"] is True
    assert latest_data["run_id"] == run_id
    assert len(latest_data["beats"]) == 2
    assert len(latest_data["decisions"]) >= 2

    # 3. Export CSV via GET /manager/allocation/export-decisions
    resp_csv = client.get(f"/api/v1/manager/allocation/export-decisions?run_id={run_id}", headers=headers)
    assert resp_csv.status_code == 200
    assert "text/csv" in resp_csv.headers["content-type"]
    assert "decision_id,case_number,outcome" in resp_csv.text

    # 4. Rollback via POST /manager/allocation/rollback
    resp_rb = client.post("/api/v1/manager/allocation/rollback", json={"run_id": run_id}, headers=headers)
    assert resp_rb.status_code == 200
    assert resp_rb.json()["success"] is True


def test_the_plan_reports_what_the_forecast_is_a_fraction_of(client, db_session, test_data):
    """`expected_recovery_total` alone is not interpretable, and was misread.

    A manager comparing the plan card's Rs 10.3L with the dashboard's "Today's
    Collections" target of Rs 71.2L asked why the plan had written off most of
    the book. It had not — the forecast is the collectable balance weighted by
    each borrower's modelled chance of paying — but neither card carried the
    base, so there was no way to see that from the screen.

    THE TWO BASES ARE NOT INTERCHANGEABLE, which is the whole reason both are
    returned:

      allocated_target_total       sum(target_amount), the LIFETIME figure the
                                   dashboard uses. Includes money banked in
                                   earlier months.
      allocated_collectable_total  sum(target_amount - collected_amount), the
                                   exact base the allocator multiplies by
                                   prob_recovery_ml.

    Only the second divides into the expected figure to give the model's own
    rate. Dividing by the target yields a number the model never computed — the
    mistake the decision panel made on 2026-09-09, when it explained a rupee
    figure with a rate that had not produced it.
    """
    mgr = test_data["manager"]
    token = create_access_token(user_id=mgr.id, role=mgr.role.value, device_id="test_device_01")
    headers = {"Authorization": f"Bearer {token}"}

    # PART-PAY ONE CASE FIRST, or this test cannot see the subtraction it exists
    # to check. Every fixture case carries collected_amount=0, so target and
    # collectable are identical and `collectable <= target` holds even if the
    # endpoint never subtracts anything — verified by mutation: dropping the
    # subtraction left the first version of this test green.
    PART_PAID = 12_000.0
    test_data["case1"].collected_amount = PART_PAID
    db_session.commit()

    client.post("/api/v1/manager/allocation/plan", json={"strategy": "SMART"}, headers=headers)
    body = client.get("/api/v1/manager/allocation/latest", headers=headers).json()

    target = body["allocated_target_total"]
    collectable = body["allocated_collectable_total"]
    expected = body["expected_recovery_total"]

    # Present at all — the card renders nothing without them.
    assert target > 0 and collectable > 0

    # THE SUBTRACTION ITSELF, to the rupee. case1 is in the plan and has been
    # part-paid, so collectable must be exactly that much below target.
    assert collectable == pytest.approx(target - PART_PAID, rel=1e-6)
    assert collectable < target

    # The forecast is a FRACTION of the collectable balance, never more than it.
    # `prob_recovery_ml` is clamped to [0.02, 0.85], so the expected figure
    # cannot reach the base it is drawn from.
    assert 0 < expected < collectable

    # ALLOCATED ONLY. Deferred and blocked cases are not on tomorrow's plan, so
    # their balances must not inflate what the plan claims to be worth.
    allocated = [d for d in body["decisions"] if d["outcome"] == "ALLOCATED"]
    assert len(allocated) == body["total_cases_allocated"]
    assert target == pytest.approx(sum(d["target_amount"] for d in allocated), rel=1e-6)

    # And the base really is narrower than "every case we looked at" whenever
    # anything was held back — otherwise the ALLOCATED filter above is vacuous.
    every_case = sum(d["target_amount"] for d in body["decisions"])
    if len(allocated) < len(body["decisions"]):
        assert target < every_case


def test_planning_does_not_stamp_a_future_date_on_a_case(client, db_session, test_data):
    """`allocation_date` is when the assignment was MADE, never when it is for.

    The planner used to write `target_date` — tomorrow — so a case a manager was
    looking at today claimed it belonged to a beat that had not happened yet.
    Measured 2026-09-10 on one manager's book: 229 of 877 cases carried
    tomorrow's date, and 44 of the 45 an agent had already visited that morning
    were among them.

    It also gave one column two meanings: the stale-clearing sweep resets DROPPED
    cases to the day they were last worked, while the assignment branch was
    writing the day they are next planned for. `ml/allocator.py` has always
    written `today` and documents it; this is that rule in one more place.

    THE SCHEDULE IS NOT LOST — it lives on the Beat, asserted here too, because
    "stop writing tomorrow" would be a bad fix if it left nothing saying when the
    work is due.
    """
    from datetime import date as _date
    mgr = test_data["manager"]
    token = create_access_token(user_id=mgr.id, role=mgr.role.value, device_id="test_device_01")
    headers = {"Authorization": f"Bearer {token}"}

    resp = client.post("/api/v1/manager/allocation/plan", json={"strategy": "SMART"},
                       headers=headers)
    assert resp.status_code == 200
    plan_date = resp.json()["plan_date"]
    today_date = _date.today()
    today = today_date.isoformat()
    assert plan_date > today, "the plan is for a future day, or this test proves nothing"

    body = client.get("/api/v1/manager/allocation/latest", headers=headers).json()
    allocated = [d for d in body["decisions"] if d["outcome"] == "ALLOCATED"]
    assert allocated, "nothing was allocated — the assertions below would be vacuous"

    from app.models.case import Case as _Case
    ids = [d["case_id"] for d in allocated]
    rows = db_session.query(_Case).filter(_Case.id.in_(ids)).all()
    assert rows
    for c in rows:
        # allocation_date is a real Date column (v2); compare date objects, not
        # a date against its own isoformat() string — those are never equal.
        assert c.allocation_date == today_date, (
            f"case {c.case_number} stamped {c.allocation_date}, but the assignment "
            f"was made {today} — a manager would see a date that has not arrived")
        assert c.allocation_date <= today_date

    # The beat still carries the schedule, so nothing about WHEN the work is due
    # was lost by taking it off the case. Asserted against the Beat rows rather
    # than the response, which does not serialise beat_date at all.
    from app.models.beat import Beat as _Beat
    beats = db_session.query(_Beat).filter(
        _Beat.allocation_run_id == body["run_id"]).all()
    assert beats, "no beats — the schedule has nowhere to live"
    assert all(b.beat_date.isoformat() == plan_date for b in beats)
    scheduled = {cid for b in beats for cid in (b.ordered_case_ids or [])}
    assert scheduled, "beats carry no cases — the schedule is empty"


# ── the plan-date correction ────────────────────────────────────────────────
# The planner scored priorities against `date.today()` while building a beat for
# `target_date`. The PTP hold rule in the same method already used `target_date`,
# so one function answered "which day is this plan for?" two ways.
#
# Measured on the real book, 2026-09-10, simulate-only so nothing was written:
# 12 of 931 cases changed priority (8 of them PTP-due); allocation outcome,
# allocated agents, BLOCKED set and expected recovery were all identical.


def _ptp_evidence(score_dict):
    """The evidence dict of whichever component carried the PTP branch."""
    for comp in score_dict.get("components", []):
        ev = comp.get("evidence") or {}
        if ev.get("ptp_due_in_days") is not None:
            return ev
    return None


def test_priority_is_scored_for_the_plan_date_not_the_wall_clock(
        db_session, test_data, monkeypatch):
    """The whole fix, asserted at the seam rather than through its effects."""
    from app.services import planner_service as ps

    seen = {}
    real = ps.score_cases_priority

    def _spy(db, cases, *, today=None, loans=None):
        seen["today"] = today
        return real(db, cases, today=today, loans=loans)

    monkeypatch.setattr(ps, "score_cases_priority", _spy)

    svc = ps.PlannerService(db_session, manager_user_id=test_data["manager"].id)
    run = svc.plan_next_day(strategy="SMART", simulate=True)

    assert seen.get("today") is not None, "priority scoring was never called"
    assert seen["today"] == run.plan_date, (
        f"priorities scored for {seen['today']} but the beat is for "
        f"{run.plan_date} — every component that reads the reference day, not "
        f"only the PTP one, is a day out")


def test_ptp_timing_is_measured_from_the_plan_date(db_session, test_data):
    """`ptp_due_in_days` must be relative to the day the agent visits.

    Asserted against visit_priority_service directly, because that is where the
    subtraction lives; the planner only chooses which day to hand it.
    """
    from app.services.visit_priority_service import score_cases
    from app.models.case import Case as _Case

    case = db_session.query(_Case).filter(_Case.case_number == "CASE001").one()
    plan_day = date.today() + timedelta(days=1)
    db_session.add(PTP(id=str(uuid.uuid4()), case_id=case.id,
                       agent_id=test_data['agent1'].id, committed_amount=1000.0,
                       committed_date=plan_day, status=PTPStatus.ACTIVE))
    db_session.commit()

    # Scored FOR the plan day: the promise falls due that day, so 0 days out.
    on_plan_day = score_cases(db_session, [case], today=plan_day)
    ev = _ptp_evidence(on_plan_day[case.id])
    assert ev is not None, "no PTP evidence — the protection branch never ran"
    assert ev["ptp_due_in_days"] == 0.0

    # Scored for the day the plan is BUILT — the old behaviour — one day out.
    on_build_day = score_cases(db_session, [case], today=date.today())
    ev2 = _ptp_evidence(on_build_day[case.id])
    assert ev2 is not None and ev2["ptp_due_in_days"] == 1.0

    # And the plan-day reading earns the higher timing lift. That inversion —
    # paying more for a promise due the day BEFORE the visit than one due on it
    # — is what the fix removes.
    assert ev["ptp_follow_up_points"] > ev2["ptp_follow_up_points"]


def test_the_hold_boundary_is_unchanged(db_session, test_data):
    """`committed_date > target_date` is held; `== target_date` is workable.

    The promise's own due day is the day to arrive, so it must not be deferred.
    Pinned because the fix moves the OTHER date in the same method, and moving
    this one too would silently push every promise a day past its due date.
    """
    from app.models.case import Case as _Case

    case = db_session.query(_Case).filter(_Case.case_number == "CASE001").one()
    plan_day = get_target_plan_date()

    ptp = PTP(id=str(uuid.uuid4()), case_id=case.id, agent_id=test_data['agent1'].id,
              committed_amount=1000.0, committed_date=plan_day + timedelta(days=1),
              status=PTPStatus.ACTIVE)
    db_session.add(ptp)
    db_session.commit()

    run = PlannerService(db_session, manager_user_id=test_data["manager"].id) \
        .plan_next_day(strategy="SMART", simulate=True)
    held = {d.case_id for d in run.decisions
            if str(d.outcome) == AllocationOutcome.DEFERRED_PTP.value}
    assert case.id in held, "a promise due after the plan date was not held"

    ptp.committed_date = plan_day
    db_session.commit()

    run2 = PlannerService(db_session, manager_user_id=test_data["manager"].id) \
        .plan_next_day(strategy="SMART", simulate=True)
    held2 = {d.case_id for d in run2.decisions
             if str(d.outcome) == AllocationOutcome.DEFERRED_PTP.value}
    assert case.id not in held2, (
        "a promise due ON the plan date was held — the agent would arrive the "
        "day after the borrower said they would pay")


def test_ptp_ownership_and_accountability_are_untouched(db_session, test_data):
    """Planning must not move `PTP.agent_id`.

    That column names the agent who TOOK the promise and feeds ptp_kept_ratio ->
    eb_multiplier -> the allocator's own prob_recovery. Re-pointing it would
    corrupt the agent-effect estimate, so it is asserted rather than assumed.
    """
    from app.models.case import Case as _Case

    case = db_session.query(_Case).filter(_Case.case_number == "CASE001").one()
    taker = test_data["agent1"].id
    ptp = PTP(id=str(uuid.uuid4()), case_id=case.id, agent_id=taker,
              committed_amount=1000.0, committed_date=date.today(),
              status=PTPStatus.ACTIVE)
    db_session.add(ptp)
    db_session.commit()

    PlannerService(db_session, manager_user_id=test_data["manager"].id) \
        .plan_next_day(strategy="SMART", simulate=True)

    db_session.refresh(ptp)
    assert ptp.agent_id == taker
    assert ptp.status == PTPStatus.ACTIVE
    assert ptp.committed_date == date.today()


def test_nothing_forces_a_due_ptp_case_back_to_its_taker(db_session, test_data):
    """The documented rule, pinned against a well-meaning "fix".

    planner_service states it: on the promised day the case "goes to whichever
    agent is the best match then — which may be the agent who took the promise,
    but is not forced to be". The allocator's continuity bonus is keyed on the
    CURRENT holder (`case.agent_id == ag.id`), never on the taker, and this fix
    did not change that.
    """
    import inspect
    from app.services import global_allocator as ga
    from app.models.case import Case as _Case

    src = inspect.getsource(ga)
    stripped = src.replace("PTP fatigue", "").replace("ptp_fatigue", "")
    assert "PTP." not in stripped, (
        "the allocator now reads PTP rows beyond fatigue — if a taker "
        "preference was added, the documented rule needs updating with it")

    # Behaviourally: a promise taken by a DIFFERENT agent does not pin the case
    # to that agent.
    case = db_session.query(_Case).filter(_Case.case_number == "CASE001").one()
    other = test_data["agent2"]
    db_session.add(PTP(id=str(uuid.uuid4()), case_id=case.id, agent_id=other.id,
                       committed_amount=1000.0, committed_date=date.today(),
                       status=PTPStatus.ACTIVE))
    db_session.commit()

    run = PlannerService(db_session, manager_user_id=test_data["manager"].id) \
        .plan_next_day(strategy="SMART", simulate=True)
    d = next((dd for dd in run.decisions if dd.case_id == case.id), None)
    assert d is not None
    # No assertion that it went to `other` — only that nothing forced it there.
    if str(d.outcome) == AllocationOutcome.ALLOCATED.value:
        assert d.allocated_agent_id is not None


def test_export_decisions_refuses_another_managers_run(client, db_session, test_data):
    """A manager must not export another agency's allocation audit.

    Behavioural, because the structural sweep in test_manager_endpoints.py
    cannot catch this class: that sweep greps the ENDPOINT body for a scoping
    token, and this endpoint does mention current_user.id — it passes it to
    PlannerService. The scope was then dropped inside the service, so the sweep
    read as a pass while any manager could export any run by id.

    404 rather than 403 is asserted deliberately: a 403 would confirm the run
    exists and turn the endpoint into an enumeration oracle.
    """
    owner = test_data["manager"]
    owner_headers = {"Authorization": f"Bearer {create_access_token(
        user_id=owner.id, role=owner.role.value, device_id='d1')}"}

    run_id = client.post("/api/v1/manager/allocation/plan",
                         json={"strategy": "SMART"},
                         headers=owner_headers).json()["run_id"]

    intruder = User(
        id=str(uuid.uuid4()), email="manager_other@tiqcollect.in",
        phone="9800000099", full_name="Manager Other", hashed_password="hash",
        role=UserRole.AGENCY_MANAGER, is_active=True, is_verified=True,
    )
    db_session.add(intruder)
    db_session.commit()
    intruder_headers = {"Authorization": f"Bearer {create_access_token(
        user_id=intruder.id, role=intruder.role.value, device_id='d2')}"}

    denied = client.get(
        f"/api/v1/manager/allocation/export-decisions?run_id={run_id}",
        headers=intruder_headers)
    assert denied.status_code == 404
    assert "decision_id" not in denied.text

    # The check must not have closed the endpoint to its legitimate caller.
    allowed = client.get(
        f"/api/v1/manager/allocation/export-decisions?run_id={run_id}",
        headers=owner_headers)
    assert allowed.status_code == 200
    assert "decision_id,case_number,outcome" in allowed.text


# ─── Failure visibility (2026-09-03) ─────────────────────────────────────────
# The 20:00 run on 2026-09-02 died 131ms after dispatch and wrote nothing, so
# the only evidence was a stack trace in the worker's container logs. These pin
# the two halves of the fix: a failed run becomes a row, and that row is never
# mistaken for a plan.

@pytest.fixture
def planner_env(db_session, test_data):
    """A manager, a planner bound to them, and the date the nightly targets."""
    from app.services.planner_service import PlannerService, get_target_plan_date
    mgr = test_data["manager"] if isinstance(test_data, dict) else test_data
    return (db_session, PlannerService(db_session, manager_user_id=mgr.id),
            mgr, get_target_plan_date())


def _mk_run(db, manager_id, plan_date, status, created_at, **over):
    from app.models.allocation_run import AllocationRun
    import uuid as _uuid
    row = AllocationRun(
        id=str(_uuid.uuid4()), manager_user_id=manager_id, plan_date=plan_date,
        strategy="SMART", status=status,
        total_cases_evaluated=over.get("evaluated", 0),
        total_cases_allocated=over.get("allocated", 0),
        total_cases_deferred=0, total_cases_blocked=0,
        total_agents_planned=over.get("agents", 0),
        expected_recovery_total=0.0,
        summary_metadata=over.get("meta", {}),
    )
    db.add(row)
    db.flush()
    row.created_at = created_at
    db.flush()
    return row


def test_failed_run_is_not_returned_as_a_plan(planner_env):
    """A FAILED row carries zeroes for every count. Surfaced as the latest plan
    it would read as a night that legitimately found no work — the opposite of
    what happened."""
    from datetime import datetime, timedelta, timezone
    db, planner, mgr, target = planner_env
    _mk_run(db, mgr.id, target, "FAILED",
            datetime.now(timezone.utc) - timedelta(minutes=5),
            meta={"error_type": "AttributeError", "error": "boom", "trigger": "nightly"})

    assert planner.get_latest_plan(plan_date=target) is None


def test_the_failure_is_still_retrievable(planner_env):
    """Excluded from the plan, but not hidden — otherwise the fix would just be
    a quieter version of the same silence."""
    from datetime import datetime, timedelta, timezone
    db, planner, mgr, target = planner_env
    _mk_run(db, mgr.id, target, "FAILED",
            datetime.now(timezone.utc) - timedelta(minutes=5),
            meta={"error_type": "AttributeError", "error": "boom", "trigger": "nightly"})

    failure = planner.get_last_failure(plan_date=target)
    assert failure is not None
    assert failure.summary_metadata["error_type"] == "AttributeError"


def test_a_later_successful_run_clears_the_failure(planner_env):
    """A manager who re-planned by hand has already resolved it. Continuing to
    report the error would send them chasing something fixed."""
    from datetime import datetime, timedelta, timezone
    db, planner, mgr, target = planner_env
    now = datetime.now(timezone.utc)
    _mk_run(db, mgr.id, target, "FAILED", now - timedelta(minutes=30), meta={"error": "boom"})
    _mk_run(db, mgr.id, target, "PLANNED", now - timedelta(minutes=5), allocated=12, agents=3)

    assert planner.get_last_failure(plan_date=target) is None
    plan = planner.get_latest_plan(plan_date=target)
    assert plan is not None and plan.total_cases_allocated == 12


def test_a_failure_after_a_success_still_reports(planner_env):
    """Order matters, not mere presence. Tonight's run breaking after
    yesterday's succeeded is a live problem."""
    from datetime import datetime, timedelta, timezone
    db, planner, mgr, target = planner_env
    now = datetime.now(timezone.utc)
    _mk_run(db, mgr.id, target, "PLANNED", now - timedelta(minutes=30), allocated=12, agents=3)
    _mk_run(db, mgr.id, target, "FAILED", now - timedelta(minutes=5), meta={"error": "boom"})

    assert planner.get_last_failure(plan_date=target) is not None
    # The good plan is still the plan — a later failure does not erase it.
    assert planner.get_latest_plan(plan_date=target).total_cases_allocated == 12


def test_one_managers_failure_does_not_abort_the_others(monkeypatch):
    """The original loop planned every manager inside one try/except, so the
    first ValueError ended the run for everybody behind it — and
    plan_next_day() raises ValueError by design whenever a beat is already
    IN_PROGRESS, which is normal for one team and irrelevant to the rest."""
    from app.workers.tasks import allocation as mod

    planned, failed = [], []

    class _Svc:
        def __init__(self, db, manager_user_id):
            self.mid = manager_user_id

        def plan_next_day(self, **kw):
            if self.mid == "m2":
                raise ValueError("Cannot replan: 3 beat(s) are already IN_PROGRESS.")
            planned.append(self.mid)
            return type("R", (), {
                "id": f"run-{self.mid}", "total_cases_allocated": 5,
                "total_cases_deferred": 1, "total_cases_blocked": 0,
                "total_agents_planned": 2, "expected_recovery_total": 100.0,
            })()

    monkeypatch.setattr("app.services.planner_service.PlannerService", _Svc)
    monkeypatch.setattr(mod, "_record_failure",
                        lambda *a, **k: failed.append(a[3].id))

    class _Q:
        def join(self, *a, **k): return self
        def filter(self, *a, **k): return self
        def all(self):
            # A04: two agencies (m1/m2 in one, m3 in another), so this also
            # exercises the agency-then-manager grouping — m2's failure must
            # not touch m3's agency any more than it touched m1's.
            agency_of = {"m1": "agA", "m2": "agA", "m3": "agB"}
            return [type("U", (), {"id": i, "email": f"{i}@t.io", "agency_id": agency_of[i]})()
                   for i in ("m1", "m2", "m3")]

    class _DB:
        def query(self, *a, **k): return _Q()
        def close(self): pass

    monkeypatch.setattr("app.core.database.SessionLocal", lambda: _DB())

    # Celery's bind=True already supplies `self` on __wrapped__, so the task
    # body is called with keywords only.
    out = mod.run_nightly_allocation.__wrapped__(
        strategy="SMART", plan_date_str="2026-09-04")

    # m2 failed; m1 and m3 were planned anyway.
    assert planned == ["m1", "m3"]
    assert failed == ["m2"]
    assert set(out["planned"]) == {"m1@t.io", "m3@t.io"}
    assert "m2@t.io" in out["failed"]
    assert "IN_PROGRESS" in out["failed"]["m2@t.io"]


# ─── Concurrent planning ────────────────────────────────────────────────────
# Two planning runs for the same manager and date both delete the PLANNED beats
# and then both insert, colliding on the UNIQUE (agent_id, beat_date) index.
# Observed live as an opaque 500 and a "Failed to generate plan" toast, with the
# run rolled back and nothing to show for it. A double-click on
# "Re-Plan & Sequence" is enough; so is the 20:00 task overlapping a manual
# re-plan.

def test_plan_in_progress_returns_409_not_500(client, db_session, test_data, monkeypatch):
    """The request was valid; the timing was not. That is 409, not 500 — and a
    500 here previously told the user nothing they could act on."""
    from app.services.planner_service import PlannerService

    mgr = test_data["manager"]
    token = create_access_token(user_id=mgr.id, role=mgr.role.value,
                                device_id="test_device_lock")
    headers = {"Authorization": f"Bearer {token}"}

    # Simulate losing the race: the lock is already held by someone else.
    monkeypatch.setattr(PlannerService, "_acquire_plan_lock",
                        lambda self, target_date: False)
    resp = client.post("/api/v1/manager/allocation/plan",
                       json={"strategy": "SMART"}, headers=headers)
    assert resp.status_code == 409, f"expected 409, got {resp.status_code}"
    assert "already being generated" in resp.json()["detail"]


def test_the_exception_is_importable_where_it_is_caught():
    """THE BUG THIS TEST EXISTS FOR was not the lock — it was the import.

    `PlanInProgressError` was added to the imports of `get_latest_allocation_plan`
    while the `except` clause sat in `create_or_simulate_allocation_plan`, so the
    handler raised `NameError: name 'PlanInProgressError' is not defined` and the
    500 came back unchanged. The patch's own `assert "..." in source` passed
    because the string existed — in the wrong function. A name check on the
    module cannot catch that; only executing the handler can, which is what the
    test above does. This one guards the cheaper half: the symbol must exist.
    """
    from app.services.planner_service import PlanInProgressError

    assert issubclass(PlanInProgressError, Exception)
    # And it must NOT be a ValueError, or the endpoint's `except ValueError`
    # would swallow it first and answer 400 instead of 409.
    assert not issubclass(PlanInProgressError, ValueError)


def test_simulate_does_not_take_the_lock(db_session, test_data, monkeypatch):
    """A simulation writes no beats, so it must never block a real plan."""
    from app.services.planner_service import PlannerService

    calls = []
    monkeypatch.setattr(PlannerService, "_acquire_plan_lock",
                        lambda self, d: calls.append(d) or True)
    svc = PlannerService(db_session, test_data["manager"].id)
    svc.plan_next_day(simulate=True)
    assert calls == [], "a simulated plan should not acquire the write lock"


def test_lock_is_a_noop_on_non_postgres(db_session, test_data):
    """SQLite has no advisory locks and no concurrent writers; the guard must
    degrade to True rather than raising or blocking the test suite."""
    from datetime import date as _date

    from app.services.planner_service import PlannerService

    svc = PlannerService(db_session, test_data["manager"].id)
    assert svc._acquire_plan_lock(_date(2026, 9, 9)) is True


def test_transform_is_paired_to_the_model_probabilities(db_session, test_data, monkeypatch):
    """EXECUTABLE proof of the pairing, replacing a source grep.

    The measured allocator change is `calibrated probability + log_rescaled`.
    The other two corners were never evaluated and one is actively bad:
    `calibrated probability + log_current` measured -10.1% realised recovery
    across 8 seeds. So when scoring yields nothing, the transform MUST fall back
    with it. This asserts what the allocator is actually constructed with, by
    capturing the kwargs, rather than asserting a string appears in a file.
    """
    from app.core.config import settings
    from app.services import global_allocator as ga
    from app.services.planner_service import PlannerService

    captured = {}
    real_init = ga.GlobalAllocator.__init__

    def spy(self, *a, **kw):
        captured.update(kw)
        return real_init(self, *a, **kw)

    monkeypatch.setattr(ga.GlobalAllocator, "__init__", spy)

    # (a) scoring OFF -> no probabilities -> the ORIGINAL transform
    monkeypatch.setattr(settings, "ML_SCORING_ENABLED", False)
    PlannerService(db_session, test_data["manager"].id).plan_next_day(
        simulate=True, force_replan=True)
    assert captured.get("value_transform") == "log_current"
    assert captured.get("ml_recovery_probability") in (None, {})
    assert captured.get("use_ml_affinity") is False

    # (b) scoring ON but the model returns nothing -> STILL the original
    captured.clear()
    monkeypatch.setattr(settings, "ML_SCORING_ENABLED", True)
    monkeypatch.setattr(PlannerService, "_ml_recovery_probabilities",
                        lambda self, cases: {})
    PlannerService(db_session, test_data["manager"].id).plan_next_day(
        simulate=True, force_replan=True)
    assert captured.get("value_transform") == "log_current", (
        "an empty model result must not leave the allocator on log_rescaled — "
        "that combination was never measured")

    # (c) probabilities present -> the configured (promoted) transform
    captured.clear()
    cases = test_data.get("cases") or []
    fake = {c.id: 0.3 for c in cases} or {"any-case": 0.3}
    monkeypatch.setattr(PlannerService, "_ml_recovery_probabilities",
                        lambda self, c: fake)
    PlannerService(db_session, test_data["manager"].id).plan_next_day(
        simulate=True, force_replan=True)
    assert captured.get("value_transform") == settings.ALLOCATOR_VALUE_TRANSFORM
    assert captured.get("use_ml_affinity") is True


def test_an_agent_on_approved_leave_for_the_plan_date_is_not_planned(db_session, test_data):
    """2026-09-22. Agent.status only says ON_LEAVE while a leave covers TODAY;
    the plan is for tomorrow, so the planner must ask the leave table. Before,
    an agent approved for leave tomorrow was planned tonight and the new beat
    collided with the leave beat approval had written for that day."""
    from app.models.leave_request import LeaveRequest, LeaveStatus, LeaveType

    mgr = test_data["manager"]
    tomorrow = date.today() + timedelta(days=1)
    db_session.add(LeaveRequest(agent_id=test_data["agent1"].id, manager_user_id=mgr.id, from_date=tomorrow,
                                to_date=tomorrow, leave_type=LeaveType.CASUAL_LEAVE, status=LeaveStatus.APPROVED,
                                requested_by_id=test_data["agent1"].user_id, decided_by_id=mgr.id, beat_ids=[]))
    db_session.commit()
    assert db_session.get(Agent, test_data["agent1"].id).status is AgentStatus.ON_DUTY  # not yet synced

    run = PlannerService(db_session, manager_user_id=mgr.id).plan_next_day(plan_date=tomorrow, strategy="SMART")

    assert run.status == AllocationRunStatus.PLANNED.value
    planned_agents = {b.agent_id for b in db_session.query(Beat).filter(Beat.allocation_run_id == run.id).all()}
    assert test_data["agent1"].id not in planned_agents
    assert all(d.allocated_agent_id != test_data["agent1"].id for d in run.decisions)


def test_an_agent_on_leave_today_is_planned_for_tomorrow(db_session, test_data):
    """2026-09-23 — the mirror of the test above, and the one that bit.

    Agent.status is a statement about TODAY: housekeeping sets ON_LEAVE while
    a leave covers the current date. The 20:00 run plans TOMORROW, so filtering
    on that column dropped an agent whose leave ends tonight. Piyush Sharma
    took 2026-09-22 off and the plan for the 23rd was built for 14 agents
    instead of 15; he came back to an empty route.
    """
    from app.models.leave_request import LeaveRequest, LeaveStatus, LeaveType

    mgr = test_data["manager"]
    today = date.today()
    tomorrow = today + timedelta(days=1)

    # Leave covers TODAY only, and the status column says so.
    db_session.add(LeaveRequest(agent_id=test_data["agent1"].id, manager_user_id=mgr.id,
                                from_date=today, to_date=today, leave_type=LeaveType.CASUAL_LEAVE,
                                status=LeaveStatus.APPROVED, requested_by_id=test_data["agent1"].user_id,
                                decided_by_id=mgr.id, beat_ids=[]))
    db_session.get(Agent, test_data["agent1"].id).status = AgentStatus.ON_LEAVE
    db_session.commit()

    run = PlannerService(db_session, manager_user_id=mgr.id).plan_next_day(
        plan_date=tomorrow, strategy="SMART")

    planned_agents = {b.agent_id for b in db_session.query(Beat).filter(Beat.allocation_run_id == run.id).all()}
    assert test_data["agent1"].id in planned_agents, "back from leave, but left off tomorrow's plan"


def test_a_suspended_agent_is_never_planned(db_session, test_data):
    """SUSPENDED is indefinite, not calendar — it is the one status that still
    filters here after the 2026-09-23 change."""
    db_session.get(Agent, test_data["agent1"].id).status = AgentStatus.SUSPENDED
    db_session.commit()

    run = PlannerService(db_session, manager_user_id=test_data["manager"].id).plan_next_day(
        plan_date=date.today() + timedelta(days=1), strategy="SMART")

    planned_agents = {b.agent_id for b in db_session.query(Beat).filter(Beat.allocation_run_id == run.id).all()}
    assert test_data["agent1"].id not in planned_agents
    assert all(d.allocated_agent_id != test_data["agent1"].id for d in run.decisions)
