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
from app.models.user import User, UserRole
from app.models.visit import PersonMet, Visit, VisitOutcome
from app.services.planner_service import PlannerService, get_target_plan_date

test_engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.create_all(bind=test_engine)
    yield
    Base.metadata.drop_all(bind=test_engine)


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
        id_card_number="TIQ001", agency_id="AG01", manager_user_id=mgr.id,
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
        id_card_number="TIQ002", agency_id="AG01", manager_user_id=mgr.id,
        gender="F", base_latitude=28.4595, base_longitude=77.0266, territory="Gurugram",
        languages_spoken=["HINDI", "PUNJABI"], specialization=AgentSpecialization.UNSECURED,
        max_cases_per_day=3, status=AgentStatus.ON_DUTY, tier=AgentTier.TIER_2,
        ranking_score=75.0, lifetime_collection_rate=0.45,
    )
    db_session.add(ag2)

    # Customer 1: Normal Auto Loan (near Delhi)
    c1 = Customer(
        id=str(uuid.uuid4()), customer_ref="CUST01", full_name="Aarav Sharma",
        date_of_birth="1990-01-01", gender="M", pan_masked="ABCDE1234F", aadhaar_masked="123456789012",
        phone_primary="9900000001", address_line1="Connaught Place, Delhi", city="Delhi", state="Delhi",
        pincode="110001", latitude=28.6315, longitude=77.2167, language_preference="HINDI",
    )
    db_session.add(c1)
    l1 = Loan(
        id=str(uuid.uuid4()), customer_id=c1.id, loan_account_number="LN001",
        loan_type=LoanType.AUTO, bank_name="HDFC Bank", branch_code="DL01",
        sanctioned_amount=500000.0, disbursed_amount=500000.0, outstanding_principal=250000.0,
        total_outstanding=250000.0, overdue_amount=50000.0, emi_amount=15000.0, interest_rate=12.5,
        disbursement_date="2022-01-01", maturity_date="2027-01-01",
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
        date_of_birth="1992-05-15", gender="F", pan_masked="ABCDE5678G", aadhaar_masked="987654321098",
        phone_primary="9900000002", address_line1="Sector 44, Gurugram", city="Gurugram", state="Haryana",
        pincode="122003", latitude=28.4551, longitude=77.0716, language_preference="HINDI",
        requires_female_agent=True,
    )
    db_session.add(c2)
    l2 = Loan(
        id=str(uuid.uuid4()), customer_id=c2.id, loan_account_number="LN002",
        loan_type=LoanType.PERSONAL, bank_name="ICICI Bank", branch_code="GG01",
        sanctioned_amount=200000.0, disbursed_amount=200000.0, outstanding_principal=120000.0,
        total_outstanding=120000.0, overdue_amount=35000.0, emi_amount=8000.0, interest_rate=14.0,
        disbursement_date="2023-01-01", maturity_date="2026-01-01",
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
        date_of_birth="1985-11-20", gender="M", pan_masked="ABCDE9999Z", aadhaar_masked="112233445566",
        phone_primary="9900000003", address_line1="Noida Sector 18", city="Noida", state="Uttar Pradesh",
        pincode="201301", latitude=28.5677, longitude=77.3285, do_not_contact=True, language_preference="HINDI",
    )
    db_session.add(c3)
    l3 = Loan(
        id=str(uuid.uuid4()), customer_id=c3.id, loan_account_number="LN003",
        loan_type=LoanType.PERSONAL, bank_name="Axis Bank", branch_code="NO01",
        sanctioned_amount=100000.0, disbursed_amount=100000.0, outstanding_principal=80000.0,
        total_outstanding=80000.0, overdue_amount=25000.0, emi_amount=5000.0, interest_rate=15.0,
        disbursement_date="2023-06-01", maturity_date="2025-06-01",
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
    today = _date.today().isoformat()
    assert plan_date > today, "the plan is for a future day, or this test proves nothing"

    body = client.get("/api/v1/manager/allocation/latest", headers=headers).json()
    allocated = [d for d in body["decisions"] if d["outcome"] == "ALLOCATED"]
    assert allocated, "nothing was allocated — the assertions below would be vacuous"

    from app.models.case import Case as _Case
    ids = [d["case_id"] for d in allocated]
    rows = db_session.query(_Case).filter(_Case.id.in_(ids)).all()
    assert rows
    for c in rows:
        assert c.allocation_date == today, (
            f"case {c.case_number} stamped {c.allocation_date}, but the assignment "
            f"was made {today} — a manager would see a date that has not arrived")
        assert c.allocation_date <= today

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
        def filter(self, *a, **k): return self
        def all(self): return [type("U", (), {"id": i, "email": f"{i}@t.io"})()
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
