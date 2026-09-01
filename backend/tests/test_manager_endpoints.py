# ─── CHANGELOG (prototype → product) ───
# New file, 2026-08-26. First endpoint-level tests for the manager router
# (backend/app/api/v1/endpoints/manager.py). Until now the /manager surface —
# dashboard, agents, agents/performance, compliance, fraud-alerts/review — had
# no coverage at all; every service beneath it was tested but the HTTP layer,
# where several real bugs shipped (fabricated AgentPerformance figures,
# unscoped compliance queries), was not.
#
# Style: FastAPI TestClient over an in-memory SQLite database seeded with a
# minimal fixture (one manager, two agents, one customer/loan/case, one visit).
# get_db is overridden so nothing touches Postgres or Redis.
#
# NOTE: _live_monthly_metrics uses func.to_char (Postgres-only), so the
# agents/performance test asserts on response SHAPE and scoping rather than
# exact monthly numbers; running it against SQLite would exercise the same
# routing/auth/serialization path.
# ────────────────
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.core.security import create_access_token
from app.main import app
from app.models.agent import Agent, AgentSpecialization, AgentStatus, AgentTier
from app.models.beat import Beat, BeatStatus
from app.models.case import Case, CaseStatus
from app.models.customer import Customer, RiskCategory
from app.models.loan import DPDBucket, Loan, LoanStatus, LoanType
from app.models.payment import Payment, PaymentMode, PaymentStatus
from app.models.ptp import PTP, PTPStatus
from app.models.user import User, UserRole
from app.models.visit import PersonMet, Visit, VisitOutcome

engine = create_engine(
    "sqlite://",  # in-memory
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)

# _live_monthly_metrics uses Postgres' func.to_char(col, 'YYYY-MM'). Give SQLite
# a compatible implementation so the endpoint code paths run unchanged.
from sqlalchemy import event  # noqa: E402
@event.listens_for(engine, "connect")
def _add_to_char(dbapi_conn, _):
    def to_char(value, fmt):
        if value is None:
            return None
        if isinstance(value, str):
            try:
                dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return None
        elif isinstance(value, date):
            dt = value
        else:
            return None
        if "YYYY-MM" in str(fmt):
            return f"{dt.year:04d}-{dt.month:02d}"
        return str(dt)
    dbapi_conn.create_function("to_char", 2, to_char)

TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)

TODAY = date.today()
NOW = datetime.now(timezone.utc)


def _user(db, email: str, role: UserRole, name: str) -> User:
    u = User(email=email, phone=email.split("@")[0][:15].ljust(10, "0"),
             full_name=name, hashed_password="x", role=role)
    db.add(u)
    return u


@pytest.fixture(scope="module")
def seeded():
    Base.metadata.create_all(engine)
    db = TestingSession()
    # Manager + another manager (for tenant-scoping assertions)
    mgr = _user(db, "mgr@t.io", UserRole.AGENCY_MANAGER, "Manager One")
    other = _user(db, "other@t.io", UserRole.AGENCY_MANAGER, "Manager Two")
    agent_user = _user(db, "agent1@t.io", UserRole.FIELD_AGENT, "Agent One")
    agent_user2 = _user(db, "agent2@t.io", UserRole.FIELD_AGENT, "Agent Two")
    db.add_all([mgr, other, agent_user, agent_user2])
    db.flush()

    def agent(code, user_id):
        return Agent(
            user_id=user_id, employee_code=code, id_card_number=code + "-ID",
            agency_id="AG1", base_latitude=28.63, base_longitude=77.21,
            territory="Delhi", languages_spoken=["HINDI"],
            status=AgentStatus.ON_DUTY, tier=AgentTier.TIER_1,
            specialization=AgentSpecialization.BOTH, ranking_score=80.0,
        )

    ag1 = agent("EMP001", agent_user.id)
    ag2 = agent("EMP002", agent_user2.id)  # belongs to the OTHER manager below
    ag1.manager_user_id = mgr.id
    ag2.manager_user_id = other.id
    db.add_all([ag1, ag2])
    db.flush()

    cust = Customer(
        customer_ref="CUST1", full_name="Borrower B", date_of_birth="1990-01-01",
        gender="M", pan_masked="XXXXX1234X", aadhaar_masked="XXXXXXXX5678",
        phone_primary="9999900001", address_line1="12 Road", city="Delhi",
        state="DL", pincode="110001", latitude=28.6315, longitude=77.2167,
        risk_category=RiskCategory.MEDIUM,
    )
    db.add(cust)
    db.flush()

    loan = Loan(
        loan_account_number="LN1", customer_id=cust.id, loan_type=LoanType.PERSONAL,
        bank_name="Test Bank", branch_code="BR1", sanctioned_amount=100000.0,
        disbursed_amount=100000.0, outstanding_principal=80000.0,
        total_outstanding=90000.0, overdue_amount=5000.0, emi_amount=4000.0,
        disbursement_date="2025-01-01", maturity_date="2027-01-01",
        dpd=45, dpd_bucket=DPDBucket.BUCKET_2, status=LoanStatus.ACTIVE,
        interest_rate=14.0, penal_charges=200.0,
    )
    db.add(loan)
    db.flush()

    c1 = Case(case_number="CASE1", customer_id=cust.id, loan_id=loan.id,
              agent_id=ag1.id, status=CaseStatus.IN_PROGRESS,
              target_amount=10000.0, collected_amount=3000.0,
              allocation_date=TODAY.isoformat())
    c2 = Case(case_number="CASE2", customer_id=cust.id, loan_id=loan.id,
              agent_id=ag1.id, status=CaseStatus.ASSIGNED,
              target_amount=5000.0, collected_amount=0.0,
              allocation_date=TODAY.isoformat())
    db.add_all([c1, c2])
    db.flush()

    v = Visit(case_id=c1.id, agent_id=ag1.id, check_in_latitude=28.6315,
              check_in_longitude=77.2167, check_in_time=NOW - timedelta(hours=2),
              check_out_time=NOW - timedelta(hours=1, minutes=40),
              distance_from_customer_metres=20.0, geo_verified=True,
              within_contact_hours=True, customer_met=True,
              outcome=VisitOutcome.PTP, person_met=PersonMet.BORROWER,
              visit_number=1)
    p = Payment(case_id=c1.id, agent_id=ag1.id, amount=3000.0,
                mode=PaymentMode.UPI, receipt_number="RCPT1",
                payment_date=NOW - timedelta(hours=1))
    t = PTP(case_id=c1.id, agent_id=ag1.id, committed_amount=5000.0,
            committed_date=TODAY - timedelta(days=3), status=PTPStatus.BROKEN)
    b = Beat(agent_id=ag1.id, beat_date=TODAY, beat_number="BEAT1",
             ordered_case_ids=[c1.id], status=BeatStatus.PLANNED)
    db.add_all([v, p, t, b])
    db.commit()

    yield {"db": db, "manager": mgr, "other": other, "agents": [ag1, ag2],
           "cases": [c1, c2], "visit": v}


@pytest.fixture(scope="module")
def client(seeded):
    def override_get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


def auth_headers(user) -> dict:
    tok = create_access_token(user.id, user.role.value, "test-device")
    return {"Authorization": f"Bearer {tok}"}


# ─ GET /manager/dashboard ──────────────
def test_dashboard_counts_are_scoped_and_correct(client, seeded):
    r = client.get("/api/v1/manager/dashboard", headers=auth_headers(seeded["manager"]))
    assert r.status_code == 200
    body = r.json()
    assert body["total_agents"] == 1          # only this manager's agent
    assert body["total_cases"] == 2
    assert body["visits_today"] >= 1
    assert body["effective_date"] == TODAY.isoformat()


def test_dashboard_requires_auth(client):
    assert client.get("/api/v1/manager/dashboard").status_code == 401


def test_dashboard_rejects_non_manager(client, seeded):
    # A field-agent token must not reach manager endpoints.
    agent_user = seeded["db"].query(User).filter(User.role == UserRole.FIELD_AGENT).first()
    r = client.get("/api/v1/manager/dashboard", headers=auth_headers(agent_user))
    assert r.status_code == 403


# ─ GET /manager/agents ─────────
def test_list_agents_returns_only_own_team(client, seeded):
    r = client.get("/api/v1/manager/agents", headers=auth_headers(seeded["manager"]))
    assert r.status_code == 200
    codes = [a["employee_code"] for a in r.json()]
    assert "EMP001" in codes
    assert len(codes) == 1                    # EMP002 belongs to the other manager


def test_agent_rows_carry_today_figures(client, seeded):
    r = client.get("/api/v1/manager/agents", headers=auth_headers(seeded["manager"]))
    row = r.json()[0]
    for key in ("cases_today", "today_collected", "today_target", "sos_active"):
        assert key in row


def test_agent_ptp_rate_counts_verified_payment_evidence(client, seeded):
    db = seeded["db"]
    ag1 = seeded["agents"][0]
    c2 = seeded["cases"][1]
    due_ptp = PTP(
        case_id=c2.id,
        agent_id=ag1.id,
        committed_amount=1000.0,
        committed_date=TODAY,
        status=PTPStatus.ACTIVE,
    )
    paid_on_time = Payment(
        case_id=c2.id,
        agent_id=ag1.id,
        amount=1000.0,
        mode=PaymentMode.UPI,
        receipt_number="RCPT-PTP-EVIDENCE",
        payment_date=NOW,
        status=PaymentStatus.VERIFIED,
    )
    db.add_all([due_ptp, paid_on_time])
    db.commit()

    r = client.get("/api/v1/manager/agents", headers=auth_headers(seeded["manager"]))
    assert r.status_code == 200
    row = r.json()[0]
    assert row["current_month_ptps_honored"] >= 1
    assert row["ptp_rate_pct"] > 0


# ─ GET /manager/agents/performance ─────────────
def test_agents_performance_shape_and_months(client, seeded):
    r = client.get("/api/v1/manager/agents/performance?months=6",
                   headers=auth_headers(seeded["manager"]))
    assert r.status_code == 200
    body = r.json()
    # Six DISTINCT months requested — six distinct months returned, oldest first.
    assert len(body["months"]) == 6
    assert len(set(body["months"])) == 6
    assert body["months"] == sorted(body["months"])
    assert len(body["agents"]) == 1
    monthly = body["agents"][0]["monthly"]
    assert len(monthly) == 6
    for m in monthly:
        assert set(m) >= {"month", "collected", "target", "visits",
                          "ptps_set", "ptps_honored", "collection_rate_pct"}


# ─ GET /manager/compliance ──────────────
def test_compliance_scopes_to_manager_team(client, seeded):
    """Regression: compliance figures were computed across the WHOLE database —
    every manager saw company-wide out-of-hours / geo-violation / SOS counts."""
    db = seeded["db"]
    # A second manager's agent with an out-of-hours visit must NOT be counted
    # for Manager One.
    other_agent = seeded["agents"][1]
    stray = Visit(case_id=seeded["cases"][0].id, agent_id=other_agent.id,
                  check_in_latitude=28.6315, check_in_longitude=77.2167,
                  check_in_time=NOW - timedelta(hours=1),
                  distance_from_customer_metres=20.0, geo_verified=False,
                  within_contact_hours=False, customer_met=True,
                  outcome=VisitOutcome.REVISIT, visit_number=1)
    db.add(stray)
    db.commit()

    r = client.get("/api/v1/manager/compliance", headers=auth_headers(seeded["manager"]))
    assert r.status_code == 200
    body = r.json()
    assert body["total_visits"] == 1          # the one visit by EMP001 only
    assert body["out_of_hours_visits"] == 0   # EMP001's visit was within hours
    assert body["geo_violations"] == 0


def test_compliance_counts_violations(client, seeded):
    db = seeded["db"]
    ag1 = seeded["agents"][0]
    bad = Visit(case_id=seeded["cases"][0].id, agent_id=ag1.id,
                check_in_latitude=28.6315, check_in_longitude=77.2167,
                check_in_time=NOW - timedelta(minutes=30),
                distance_from_customer_metres=900.0, geo_verified=False,
                within_contact_hours=False, customer_met=False,
                outcome=VisitOutcome.NOT_AVAILABLE, visit_number=2)
    db.add(bad)
    db.commit()

    r = client.get("/api/v1/manager/compliance", headers=auth_headers(seeded["manager"]))
    body = r.json()
    assert body["total_visits"] == 2
    assert body["out_of_hours_visits"] == 1
    assert body["geo_violations"] == 1
    assert 0 <= body["compliance_rate"] < 1.0


# ─ POST /manager/fraud-alerts/review ───────────
def test_fraud_review_rejects_bad_verdict(client, seeded):
    r = client.post(
        "/api/v1/manager/fraud-alerts/review",
        headers=auth_headers(seeded["manager"]),
        json={"visit_id": seeded["visit"].id, "finding_type": "VISIT_TOO_SHORT",
              "verdict": "MAYBE"},
    )
    assert r.status_code == 422


def test_fraud_review_rejects_unknown_visit(client, seeded):
    r = client.post(
        "/api/v1/manager/fraud-alerts/review",
        headers=auth_headers(seeded["manager"]),
        json={"visit_id": "nonexistent", "finding_type": "VISIT_TOO_SHORT",
              "verdict": "CONFIRMED"},
    )
    assert r.status_code == 404


def test_fraud_review_create_then_update(client, seeded):
    vid = seeded["visit"].id
    payload = {"visit_id": vid, "finding_type": "VISIT_TOO_SHORT",
               "verdict": "CONFIRMED", "note": "clearly too short"}
    r = client.post("/api/v1/manager/fraud-alerts/review",
                    headers=auth_headers(seeded["manager"]), json=payload)
    assert r.status_code == 200
    body = r.json()
    assert body["verdict"] == "CONFIRMED"
    assert body["previous_verdict"] is None

    # Second review of the SAME finding updates the row (unique per pair),
    # and reports the previous verdict.
    r2 = client.post("/api/v1/manager/fraud-alerts/review",
                     headers=auth_headers(seeded["manager"]),
                     json={"visit_id": vid, "finding_type": "VISIT_TOO_SHORT",
                           "verdict": "DISMISSED", "note": "false alarm"})
    assert r2.status_code == 200
    assert r2.json()["previous_verdict"] == "CONFIRMED"

    from app.models.fraud_review import FraudReview
    rows = seeded["db"].query(FraudReview).filter(FraudReview.visit_id == vid).all()
    assert len(rows) == 1
    assert rows[0].verdict.value == "DISMISSED"


def test_fraud_review_cannot_touch_other_managers_visit(client, seeded):
    """Ownership is proved through the visit's agent: a verdict about another
    manager's team must 404 even though the visit exists."""
    db = seeded["db"]
    stray = Visit(case_id=seeded["cases"][0].id, agent_id=seeded["agents"][1].id,
                  check_in_latitude=28.6315, check_in_longitude=77.2167,
                  check_in_time=NOW - timedelta(hours=3), check_out_time=NOW - timedelta(hours=2),
                  distance_from_customer_metres=20.0, geo_verified=True,
                  within_contact_hours=True, customer_met=True,
                  outcome=VisitOutcome.PTP, visit_number=1)
    db.add(stray)
    db.commit()
    r = client.post(
        "/api/v1/manager/fraud-alerts/review",
        headers=auth_headers(seeded["manager"]),   # NOT this agent's manager
        json={"visit_id": stray.id, "finding_type": "VISIT_TOO_SHORT",
              "verdict": "CONFIRMED"},
    )
    assert r.status_code in (403, 404)


# ─ _recent_months unit tests ────────────
# Regression guards for the month-window helper behind agents/performance and
# analytics. Its predecessor stepped back 30-day timedelta chunks, which from
# the 1st of a month could land two iterations in one month and skip another.
from app.api.v1.endpoints.manager import _recent_months  # noqa: E402


def test_recent_months_within_one_year():
    assert _recent_months(date(2026, 8, 26), 6) == [
        "2026-03", "2026-04", "2026-05", "2026-06", "2026-07", "2026-08",
    ]


def test_recent_months_crosses_december_january():
    """The year must roll over: stepping back from January lands in December
    of the PREVIOUS year — this is exactly what the timedelta version got wrong."""
    assert _recent_months(date(2027, 1, 15), 4) == [
        "2026-10", "2026-11", "2026-12", "2027-01",
    ]


def test_recent_months_count_zero_or_negative():
    assert _recent_months(date(2026, 8, 1), 0) == []
    assert _recent_months(date(2026, 8, 1), -3) == []


def test_recent_months_returns_exactly_count_distinct_values():
    out = _recent_months(date(2025, 3, 31), 12)   # spans a full year boundary
    assert len(out) == 12
    assert len(set(out)) == 12                    # no duplicates, no skipped months
    assert all(len(m) == 7 and m[4] == "-" for m in out)   # strict YYYY-MM shape
    assert out == sorted(out)                     # oldest first
    assert out[-1] == "2025-03"                   # ends at the anchor's month


def test_recent_months_single_month():
    assert _recent_months(date(2026, 8, 26), 1) == ["2026-08"]
