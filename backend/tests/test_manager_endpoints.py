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
from app.models.audit_log import AuditAction, AuditLog
from app.models.beat import Beat, BeatStatus
from app.models.case import Case, CaseStatus
from app.models.customer import Customer, RiskCategory
from app.models.loan import DPDBucket, Loan, LoanStatus, LoanType
from app.models.payment import Payment, PaymentMode, PaymentStatus
from app.models.ptp import PTP, PTPStatus
from app.models.user import User, UserRole
from app.models.visit import PersonMet, Visit, VisitOutcome

from tests._db import create_schema, drop_schema, make_engine, make_session_factory, test_id  # noqa: F401

engine = make_engine()

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

TestingSession = make_session_factory(autocommit=False, autoflush=False, bind=engine)

TODAY = date.today()
NOW = datetime.now(timezone.utc)


def _user(db, email: str, role: UserRole, name: str) -> User:
    u = User(email=email, phone=email.split("@")[0][:15].ljust(10, "0"),
             full_name=name, hashed_password="x", role=role)
    db.add(u)
    return u


@pytest.fixture(scope="module")
def seeded():
    create_schema(engine)
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
            base_latitude=28.63, base_longitude=77.21,
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
        customer_ref="CUST1", full_name="Borrower B", date_of_birth=date(1990, 1, 1),
        gender="M", pan_masked="XXXXX1234X", aadhaar_masked="XXXXXXXX5678",
        phone_primary="9999900001", address_line1="12 Road", city="Delhi",
        state="DL", pincode="110001", latitude=28.6315, longitude=77.2167,
        risk_category=RiskCategory.MEDIUM,
    )
    db.add(cust)
    db.flush()

    loan = Loan(
        loan_account_number="LN1", customer_id=cust.id, loan_type=LoanType.PERSONAL,
        branch_code="BR1", sanctioned_amount=100000.0,
        disbursed_amount=100000.0, outstanding_principal=80000.0,
        total_outstanding=90000.0, overdue_amount=5000.0, emi_amount=4000.0,
        disbursement_date=date(2025, 1, 1), maturity_date=date(2027, 1, 1),
        dpd=45, dpd_bucket=DPDBucket.BUCKET_2, status=LoanStatus.ACTIVE,
        interest_rate=14.0, penal_charges=200.0,
    )
    db.add(loan)
    db.flush()

    c1 = Case(case_number="CASE1", customer_id=cust.id, loan_id=loan.id,
              agent_id=ag1.id, status=CaseStatus.IN_PROGRESS,
              target_amount=10000.0, collected_amount=3000.0,
              allocation_date=TODAY)
    c2 = Case(case_number="CASE2", customer_id=cust.id, loan_id=loan.id,
              agent_id=ag1.id, status=CaseStatus.ASSIGNED,
              target_amount=5000.0, collected_amount=0.0,
              allocation_date=TODAY)
    db.add_all([c1, c2])
    db.flush()

    v = Visit(case_id=c1.id, agent_id=ag1.id, check_in_latitude=28.6315,
              check_in_longitude=77.2167, check_in_time=NOW - timedelta(hours=2),
              check_out_time=NOW - timedelta(hours=1, minutes=40),
              distance_from_customer_metres=20.0, geo_verified=True,
              within_contact_hours=True, customer_met=True,
              outcome=VisitOutcome.PTP, person_met=PersonMet.BORROWER,
              visit_number=1,
              borrower_photo_key='mine/borrower.jpg',
              agent_recording_key='mine/agent.webm')
    # A visit belonging to the OTHER manager's agent, carrying media. This is
    # the row the media-urls scoping test tries to reach. Deliberately NOT
    # ag1's: adding an ag1 visit here would change the compliance counts the
    # tests above assert on.
    v_other = Visit(case_id=c1.id, agent_id=ag2.id, check_in_latitude=28.6315,
                    check_in_longitude=77.2167, check_in_time=NOW - timedelta(hours=3),
                    distance_from_customer_metres=25.0, geo_verified=True,
                    within_contact_hours=True, customer_met=True,
                    outcome=VisitOutcome.PTP, person_met=PersonMet.BORROWER,
                    visit_number=1,
                    borrower_photo_key='theirs/borrower.jpg',
                    agent_recording_key='theirs/agent.webm')
    p = Payment(case_id=c1.id, agent_id=ag1.id, amount=3000.0,
                mode=PaymentMode.UPI, receipt_number="RCPT1",
                payment_date=NOW - timedelta(hours=1))
    t = PTP(case_id=c1.id, agent_id=ag1.id, committed_amount=5000.0,
            committed_date=TODAY - timedelta(days=3), status=PTPStatus.BROKEN)
    b = Beat(agent_id=ag1.id, beat_date=TODAY, beat_number="BEAT1",
             ordered_case_ids=[c1.id], status=BeatStatus.PLANNED)
    db.add_all([v, v_other, p, t, b])
    db.commit()

    yield {"db": db, "manager": mgr, "other": other, "agents": [ag1, ag2],
           "cases": [c1, c2], "visit": v, "visit_other": v_other}


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


def test_agent_rows_carry_email_phone_and_base_location(client, seeded):
    """G02 (Manage Agents): the table and edit drawer need these — already
    on the row through the existing joinedload(Agent.user), not a new
    query."""
    r = client.get("/api/v1/manager/agents", headers=auth_headers(seeded["manager"]))
    row = r.json()[0]
    for key in ("email", "phone", "gender", "vehicle_type", "territory_region_id",
               "base_latitude", "base_longitude", "suspended_at", "suspended_reason"):
        assert key in row
    assert row["email"] and "@" in row["email"]
    assert row["base_latitude"] is not None and row["base_longitude"] is not None


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
    """A well-formed id nobody owns is a 404. (2026-09-24: this sent the
    literal "nonexistent", which since body ids are validated (UUIDStr) is a
    422 before any query — asserted separately below.)"""
    r = client.post(
        "/api/v1/manager/fraud-alerts/review",
        headers=auth_headers(seeded["manager"]),
        json={"visit_id": test_id("visit:nobody"), "finding_type": "VISIT_TOO_SHORT",
              "verdict": "CONFIRMED"},
    )
    assert r.status_code == 404


def test_fraud_review_rejects_a_malformed_visit_id_before_any_query(client, seeded):
    r = client.post(
        "/api/v1/manager/fraud-alerts/review",
        headers=auth_headers(seeded["manager"]),
        json={"visit_id": "nonexistent", "finding_type": "VISIT_TOO_SHORT",
              "verdict": "CONFIRMED"},
    )
    assert r.status_code == 422


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


# ─ Tenant scoping on the two endpoints that shipped without it ─────────────
# Both were found by auditing every route in the router rather than by any
# test failing, which is the point: an unscoped endpoint returns 200 and
# correct-looking data. Nothing is wrong until it is someone else's data.

@pytest.fixture()
def _no_minio(monkeypatch):
    """presigned_download_url talks to MinIO. The scoping decision happens
    before it is ever called, so stubbing it keeps these tests about
    ownership rather than about object storage."""
    from app.api.v1.endpoints import manager as mod
    monkeypatch.setattr(mod.storage, 'presigned_download_url',
                        lambda key, expires_minutes=60: f'https://signed.invalid/{key}')


def test_media_urls_returns_media_for_own_agents_visit(client, seeded, _no_minio):
    r = client.get(f"/api/v1/manager/visits/{seeded['visit'].id}/media-urls",
                   headers=auth_headers(seeded['manager']))
    assert r.status_code == 200
    body = r.json()
    assert 'mine/borrower.jpg' in body['photos']['borrower']
    assert 'mine/agent.webm' in body['recordings']['agent']


def test_media_urls_refuses_another_managers_visit(client, seeded, _no_minio):
    """The leak this closes. A presigned URL is a bearer token: once minted it
    grants an hour of unauthenticated access to a borrower's photograph and
    the call recording. Before the fix any manager could mint one for any
    visit id in the system."""
    r = client.get(f"/api/v1/manager/visits/{seeded['visit_other'].id}/media-urls",
                   headers=auth_headers(seeded['manager']))
    assert r.status_code == 404
    assert 'theirs' not in r.text


def test_media_urls_hides_existence_rather_than_forbidding(client, seeded, _no_minio):
    """404, never 403. A 403 confirms the id is real, which turns the endpoint
    into an enumeration oracle for another agency's visit history — the same
    reason the response body must not echo the key."""
    real = client.get(f"/api/v1/manager/visits/{seeded['visit_other'].id}/media-urls",
                      headers=auth_headers(seeded['manager']))
    fake = client.get('/api/v1/manager/visits/does-not-exist/media-urls',
                      headers=auth_headers(seeded['manager']))
    assert real.status_code == fake.status_code == 404


def test_dpd_breakdown_refuses_another_managers_agent(client, seeded):
    r = client.get(f"/api/v1/manager/agents/{seeded['agents'][1].id}/dpd-breakdown",
                   headers=auth_headers(seeded['manager']))
    assert r.status_code == 404


def test_dpd_breakdown_allows_own_agent(client, seeded):
    """The check must not have closed the endpoint to its legitimate caller."""
    r = client.get(f"/api/v1/manager/agents/{seeded['agents'][0].id}/dpd-breakdown",
                   headers=auth_headers(seeded['manager']))
    assert r.status_code == 200


def test_every_manager_route_that_reads_tenant_data_is_scoped():
    """A structural sweep, because both leaks were found this way and neither
    would have failed a behavioural test. An unscoped endpoint returns 200
    and correct-looking data; nothing is wrong until it is someone else's.

    Any new route in this router must either scope by manager_user_id /
    current_user.id / _require_own_agent, or be listed in tenant_free with a
    reason it holds no tenant data."""
    import re
    from pathlib import Path
    src = Path('app/api/v1/endpoints/manager.py').read_text(
        encoding='utf-8').splitlines()
    # /ai/health reports LLM reachability only: it takes no db session and
    # reads no row belonging to anyone.
    #
    # /ml/health (2026-09-08) is the same shape for the trained models: every
    # field it returns describes a model rather than a borrower, an agent or a
    # case. Added to the allowlist rather than given a token scope, because a
    # fake `current_user.id` reference purely to satisfy a textual sweep is
    # worse than an explicit exemption — it would make the sweep report a scope
    # that does not exist.
    #
    # *(This entry used to say /ml/health "takes no db session". That stopped
    # being true on 2026-09-09, when the live monitoring block was added and the
    # route began reading `model_predictions` and `model_candidates`. Corrected
    # rather than deleted, because the OLD reason would have quietly stopped
    # applying while the exemption stayed — which is the exact failure mode this
    # sweep exists to catch.)*
    #
    # THE EXEMPTION STILL HOLDS, for a different and narrower reason: those two
    # tables are global ML state. `model_candidates` has no tenant column at all
    # — there is ONE champion for the whole deployment — and the monitoring
    # block returns aggregate model metrics (n, Gini, KS, PSI, a verdict), never
    # a borrower, agent or case identifier. Scoping a model's health to one
    # manager would make two managers disagree about which model is live.
    #
    # Pinned behaviourally by test_the_ml_routes_return_no_tenant_identifiers,
    # because "it returns aggregates" is a claim about content and this sweep
    # can only read text.
    tenant_free = {'/ai/health', '/ml/health',
                   '/ml/candidates', '/ml/candidates/{candidate_id}',
                   '/ml/candidates/{candidate_id}/approve',
                   '/ml/candidates/{candidate_id}/reject',
                   '/ml/candidates/{candidate_id}/promote'}
    pattern = re.compile(r'@router\.(get|post|put|patch|delete)\("([^"]+)"')
    marks = []
    for i, ln in enumerate(src):
        m = pattern.match(ln.strip())
        if m:
            marks.append((i, m.group(2)))
    assert marks, 'no routes found — did the router move?'
    marks.append((len(src), ''))
    unscoped = []
    for (start, path), (end, _) in zip(marks, marks[1:]):
        if path in tenant_free:
            continue
        chunk = ' '.join(src[start:end])
        # `_audit_visible_user_ids` / `_audit_log_query` added 2026-09-06 for the
        # audit-log routes, which delegate their scoping to a shared helper.
        #
        # BE HONEST ABOUT WHAT THIS BUYS. Recognising a helper NAME is not proof
        # that the helper scopes — that is exactly how
        # /allocation/export-decisions passed this sweep for weeks while the
        # service it called had dropped current_user.id entirely. The sweep is
        # textual and cannot follow a call. It catches the route that forgot
        # scoping altogether; it cannot catch the helper that loses it.
        #
        # So every route relying on a helper here MUST also carry a behavioural
        # tenancy test. For these two that is
        # test_audit_log_does_not_leak_another_managers_team below.
        if not ('manager_user_id' in chunk or 'current_user.id' in chunk
                or '_require_own_agent' in chunk
                or '_audit_visible_user_ids' in chunk or '_audit_log_query' in chunk):
            unscoped.append(path)
    assert unscoped == [], f'unscoped manager routes: {unscoped}'


# ─ GET /manager/audit-log ──────────────────────────────────────────────────
# 2026-09-06. The Compliance page rendered six HARDCODED audit rows — agent
# names absent from the roster, a PTP dated "Jan 25, 2025", and an Export button
# wired to nothing — under the heading "Today's Audit Log". These cover the real
# endpoint that replaced it.
#
# The tenancy test here is not optional. The structural sweep above recognises
# `_audit_visible_user_ids` by NAME, and a name is not proof: that is exactly how
# /allocation/export-decisions passed the sweep while the service it delegated to
# had dropped the scope entirely. Only a behavioural test can catch that.

@pytest.fixture(scope="module")
def audit_rows(seeded):
    """One row per tenant, plus one with no actor at all."""
    db = seeded["db"]
    mine = seeded["agents"][0]        # belongs to seeded["manager"]
    theirs = seeded["agents"][1]      # belongs to seeded["other"]
    rows = [
        AuditLog(created_at=NOW - timedelta(hours=2), user_id=mine.user_id,
                 action=AuditAction.PAYMENT_VERIFIED, entity_type="Payment",
                 entity_id=test_id("pay-mine"), success=True),
        AuditLog(created_at=NOW - timedelta(hours=3), user_id=seeded["manager"].id,
                 action=AuditAction.LOGIN, entity_type="User",
                 entity_id=test_id("mgr-login"), success=True),
        AuditLog(created_at=NOW - timedelta(hours=4), user_id=theirs.user_id,
                 action=AuditAction.PAYMENT_VERIFIED, entity_type="Payment",
                 entity_id=test_id("pay-theirs"), success=True),
        # Written by the system, not a person — PTP_UPDATED when a verified
        # payment honours a promise names no user, deliberately.
        AuditLog(created_at=NOW - timedelta(hours=5), user_id=None,
                 action=AuditAction.PTP_UPDATED, entity_type="PTP",
                 entity_id=test_id("ptp-system"), success=True),
        # Outside the 7-day window.
        AuditLog(created_at=NOW - timedelta(days=30), user_id=mine.user_id,
                 action=AuditAction.LOGIN, entity_type="User",
                 entity_id=test_id("too-old"), success=True),
    ]
    db.add_all(rows)
    db.commit()
    yield rows


def test_audit_log_returns_this_managers_own_team(client, seeded, audit_rows):
    r = client.get("/api/v1/manager/audit-log", headers=auth_headers(seeded["manager"]))
    assert r.status_code == 200
    ids = {e["entity_id"] for e in r.json()["entries"]}
    assert test_id("pay-mine") in ids, "the manager cannot see their own agent's action"
    assert test_id("mgr-login") in ids, "the manager cannot see their own action"


def test_audit_log_does_not_leak_another_managers_team(client, seeded, audit_rows):
    """The whole point. A row belonging to another agency must never appear."""
    r = client.get("/api/v1/manager/audit-log", headers=auth_headers(seeded["manager"]))
    ids = {e["entity_id"] for e in r.json()["entries"]}
    assert test_id("pay-theirs") not in ids

    # And symmetrically, so the test cannot pass by returning nothing at all.
    r2 = client.get("/api/v1/manager/audit-log", headers=auth_headers(seeded["other"]))
    ids2 = {e["entity_id"] for e in r2.json()["entries"]}
    assert test_id("pay-theirs") in ids2
    assert test_id("pay-mine") not in ids2


def test_audit_log_excludes_rows_outside_the_window(client, seeded, audit_rows):
    r = client.get("/api/v1/manager/audit-log", headers=auth_headers(seeded["manager"]))
    assert test_id("too-old") not in {e["entity_id"] for e in r.json()["entries"]}


def test_audit_log_omits_actor_less_system_rows_and_says_so(client, seeded, audit_rows):
    """`user_id IN (...)` drops NULLs by definition, so system-written rows are
    invisible here. That is the right default — an unattributed row cannot be
    proven to belong to this tenant — but it is a real gap, so the response
    must declare it rather than leave it to be discovered."""
    r = client.get("/api/v1/manager/audit-log", headers=auth_headers(seeded["manager"]))
    body = r.json()
    assert test_id("ptp-system") not in {e["entity_id"] for e in body["entries"]}
    assert body["coverage"]["excludes_system_rows"] is True


def test_audit_log_declares_which_actions_are_not_instrumented(client, seeded, audit_rows):
    """A short log must be distinguishable from an uninstrumented one."""
    body = client.get("/api/v1/manager/audit-log",
                      headers=auth_headers(seeded["manager"])).json()
    coverage = body["coverage"]
    assert coverage["declared_action_types"] == len(AuditAction)
    assert "VISIT_RECORDED" in coverage["not_instrumented"]
    assert "PTP_SET" in coverage["not_instrumented"]
    # Anything actually emitted must NOT be listed as missing.
    assert "PAYMENT_VERIFIED" not in coverage["not_instrumented"]
    assert "LOGIN" not in coverage["not_instrumented"]


def test_audit_log_paginates(client, seeded, audit_rows):
    r = client.get("/api/v1/manager/audit-log?limit=1&offset=0",
                   headers=auth_headers(seeded["manager"]))
    body = r.json()
    assert len(body["entries"]) == 1
    assert body["total"] >= 2 and body["limit"] == 1


def test_audit_log_export_is_csv_and_scoped_the_same_way(client, seeded, audit_rows):
    """The export shares `_audit_log_query` with the list, so the two cannot
    disagree about who may see what."""
    r = client.get("/api/v1/manager/audit-log/export",
                   headers=auth_headers(seeded["manager"]))
    assert r.status_code == 200
    assert "text/csv" in r.headers["content-type"]
    assert "timestamp,actor,action" in r.text
    assert test_id("pay-mine") in r.text
    assert test_id("pay-theirs") not in r.text, "CSV export leaked another manager's row"


# ── exporting borrower data is itself an auditable act ──────────────────────
# `DATA_EXPORT` was declared in AuditAction from the beginning and written
# nowhere — one of 14 such actions the 2026-09-10 repo audit counted. These two
# endpoints are the ones that ship data off the platform, so they are the ones
# that got it. The other 12 are still unwritten and are still a known issue.

def test_exporting_the_audit_log_writes_a_data_export_row(client, seeded, audit_rows):
    from app.models.audit_log import AuditLog, AuditAction

    # Counted as a DELTA, not an absolute. `seeded` is module-scoped, so these
    # rows outlive the test and an absolute count makes the assertion depend on
    # what ran before it.
    db = TestingSession()
    try:
        before = db.query(AuditLog).filter(
            AuditLog.action == AuditAction.DATA_EXPORT).count()
    finally:
        db.close()

    r = client.get("/api/v1/manager/audit-log/export",
                   headers=auth_headers(seeded["manager"]))
    assert r.status_code == 200

    db = TestingSession()
    try:
        rows = (db.query(AuditLog)
                  .filter(AuditLog.action == AuditAction.DATA_EXPORT)
                  .order_by(AuditLog.created_at.desc()).all())
        assert len(rows) == before + 1, (
            "the export of an audit trail was itself unaudited")
        row = rows[0]
        assert row.user_id == seeded["manager"].id
        assert row.success is True
        assert row.details["endpoint"] == "/manager/audit-log/export"
        assert row.details["rows"] >= 1
        # The row records the SHAPE of the export, never its content — copying
        # the payload here would duplicate the data being logged.
        assert test_id("pay-mine") not in str(row.details)
    finally:
        db.close()


def test_a_refused_export_writes_no_data_export_row(client, seeded):
    """404 means nothing left the platform. A DATA_EXPORT row here would make the
    trail read as though another agency's run had been handed over."""
    from app.models.audit_log import AuditLog, AuditAction

    r = client.get("/api/v1/manager/allocation/export-decisions?run_id=does-not-exist",
                   headers=auth_headers(seeded["manager"]))
    assert r.status_code == 404

    db = TestingSession()
    try:
        # Scoped to THIS run id, so the assertion says what it means regardless
        # of which other tests exported something first.
        assert db.query(AuditLog).filter(
            AuditLog.action == AuditAction.DATA_EXPORT,
            AuditLog.entity_id == "does-not-exist").count() == 0
    finally:
        db.close()
