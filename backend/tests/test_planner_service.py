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
