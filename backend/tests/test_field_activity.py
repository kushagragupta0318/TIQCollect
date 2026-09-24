"""
Field Activity — TODAY MEANS TODAY'S VISITS ONLY.

Two layers. `classify` is pure and gets the semantic rules; the API layer gets
the window, the tenant scope and the Cases-list link, against an in-memory
database with visits placed deliberately inside and outside each window.
Every scenario the spec names is here by number.
"""
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
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
from app.models.ptp import PTP, PTPStatus
from app.models.user import User, UserRole
from app.models.visit import PersonMet, Visit, VisitOutcome
from app.services import field_activity_service as fa
from app.services.field_activity_service import VisitRow, classify
from tests._db import create_schema, drop_schema, make_engine, make_session_factory, test_id  # noqa: F401

# ── the pure classifier ──────────────────────────────────────────────────────

T0 = datetime(2026, 9, 17, 10, 0, tzinfo=timezone.utc)


def _v(case, outcome, hours=0):
    return VisitRow(case_id=case, outcome=outcome, check_in_time=T0 + timedelta(hours=hours))


def test_1_no_in_window_visit_means_nothing_past_planned():
    # Scenario 1/2/8: the classifier is handed NO visits for the window; a
    # historical Met / REVISIT / PAID outcome is simply not in its input.
    r = classify({"c1"}, [])
    assert (r.planned, r.visited, r.met, r.paid_or_promised) == (1, 0, 0, 0)
    assert r.not_met_reasons == {} and r.met_no_money_reasons == {}


def test_3_revisit_today_is_visited_and_met_never_not_met():
    r = classify({"c1"}, [_v("c1", "REVISIT")])
    assert (r.visited, r.met, r.paid_or_promised) == (1, 1, 0)
    assert r.not_met == 0 and r.not_met_reasons == {}
    assert r.met_no_money_reasons == {"REVISIT": 1}


@pytest.mark.parametrize("outcome", ["NOT_AVAILABLE", "ADDRESS_ISSUE"])
def test_4_5_not_available_and_address_issue_are_visited_not_met(outcome):
    r = classify({"c1"}, [_v("c1", outcome)])
    assert (r.visited, r.met, r.paid_or_promised) == (1, 0, 0)
    assert r.not_met == 1 and r.not_met_reasons == {outcome: 1}


def test_6_dispute_is_met_no_money():
    r = classify({"c1"}, [_v("c1", "DISPUTE")])
    assert (r.visited, r.met, r.paid_or_promised) == (1, 1, 0)
    assert r.not_met == 0 and r.met_no_money_reasons == {"DISPUTE": 1}


@pytest.mark.parametrize("outcome", ["PTP", "PAID_FULL", "PART_PAID", "PART_PAID_PTP"])
def test_7_paid_or_promised_outcomes(outcome):
    r = classify({"c1"}, [_v("c1", outcome)])
    assert (r.visited, r.met, r.paid_or_promised) == (1, 1, 1)
    assert r.met_no_money == 0


def test_9_multiple_visits_count_the_case_once_at_its_best_stage():
    r = classify({"c1"}, [
        _v("c1", "NOT_AVAILABLE", 0), _v("c1", "NOT_AVAILABLE", 1),
        _v("c1", "REVISIT", 2), _v("c1", "PTP", 3),
    ])
    assert (r.planned, r.visited, r.met, r.paid_or_promised) == (1, 1, 1, 1)
    assert r.not_met_reasons == {} and r.met_no_money_reasons == {}


def test_9b_two_not_met_visits_are_one_not_met_case_with_the_latest_reason():
    r = classify({"c1"}, [_v("c1", "ADDRESS_ISSUE", 0), _v("c1", "NOT_AVAILABLE", 5)])
    assert r.visited == 1 and r.not_met == 1
    assert r.not_met_reasons == {"NOT_AVAILABLE": 1}


def test_13_arithmetic_partitions_and_drop_offs_use_the_preceding_stage():
    r = classify(
        {f"c{i}" for i in range(10)},
        [_v("c0", "PTP"), _v("c1", "PAID_FULL"), _v("c2", "RTP"), _v("c3", "DISPUTE"),
         _v("c4", "REVISIT"), _v("c5", "NOT_AVAILABLE"), _v("c6", "ADDRESS_ISSUE")],
    )
    assert (r.planned, r.visited, r.met, r.paid_or_promised) == (10, 7, 5, 2)
    assert r.visited == r.not_met + r.met
    assert r.met == r.paid_or_promised + r.met_no_money
    assert sum(r.not_met_reasons.values()) == r.not_met
    assert sum(r.met_no_money_reasons.values()) == r.met_no_money
    d = r.drop_offs()
    assert d["planned_to_visited"] == 30.0          # (10-7)/10
    assert d["visited_to_met"] == pytest.approx(28.57, abs=0.01)   # (7-5)/7
    assert d["met_to_paid_or_promised"] == 60.0     # (5-2)/5


def test_13b_zero_denominators_are_none_not_a_crash():
    r = classify(set(), [])
    assert r.drop_offs() == {"planned_to_visited": None, "visited_to_met": None, "met_to_paid_or_promised": None}
    r2 = classify({"c1"}, [])
    assert r2.drop_offs()["planned_to_visited"] == 100.0
    assert r2.drop_offs()["visited_to_met"] is None


def test_visits_on_unplanned_cases_are_ignored():
    r = classify({"c1"}, [_v("c9", "PTP")])
    assert (r.planned, r.visited) == (1, 0)


def test_every_outcome_is_classified_and_only_two_are_not_met():
    for o in VisitOutcome:
        r = classify({"c"}, [_v("c", o.value)])
        assert r.visited == 1
        assert (r.met == 0) == (o.value in {"NOT_AVAILABLE", "ADDRESS_ISSUE"}), o


def test_window_bounds_anchor_on_the_effective_date():
    eff = date(2026, 9, 17)
    eod = datetime(2026, 9, 17, 23, 59, 59, 999999, tzinfo=timezone.utc)
    t = fa.window_bounds("today", eff, eod)
    assert (t.start_date, t.end_date) == (eff, eff)
    assert t.start == datetime(2026, 9, 17, tzinfo=timezone.utc) and t.end == eod
    w7 = fa.window_bounds("7d", eff, eod)
    assert w7.start_date == date(2026, 9, 11) and w7.end_date == eff
    w30 = fa.window_bounds("30d", eff, eod)
    assert w30.start_date == date(2026, 8, 19)
    assert fa.window_bounds("bogus", eff, eod).window == "today"


# ── the API, on a controlled book ────────────────────────────────────────────

engine = make_engine()


@event.listens_for(engine, "connect")
def _sqlite_helpers(dbapi_conn, _):
    def to_char(value, fmt):
        return str(value)[:7] if value else None
    dbapi_conn.create_function("to_char", 2, to_char)


Session = make_session_factory(autocommit=False, autoflush=False, bind=engine)

TODAY = date.today()
NOW = datetime.now(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0)
YESTERDAY = NOW - timedelta(days=1)


def _user(db, email, role, name):
    u = User(email=email, phone="9" + str(abs(hash(email)) % 10**9).zfill(9), full_name=name,
             hashed_password="x", role=role, is_active=True, is_verified=True)
    db.add(u); db.flush(); return u


def _agent(db, code, user, mgr):
    a = Agent(user_id=user.id, employee_code=code, id_card_number=code + "-ID", 
              base_latitude=28.6, base_longitude=77.2, tier=AgentTier.TIER_1,
              specialization=AgentSpecialization.BOTH, status=AgentStatus.ON_DUTY,
              territory="Delhi", languages_spoken=["HINDI"], ranking_score=80.0,
              manager_user_id=mgr.id)
    db.add(a); db.flush(); return a


def _case(db, num, agent, cust, loan):
    c = Case(case_number=num, customer_id=cust.id, loan_id=loan.id, agent_id=agent.id,
             status=CaseStatus.IN_PROGRESS, target_amount=10000.0, collected_amount=0.0,
             allocation_date=TODAY)
    db.add(c); db.flush(); return c


def _visit(db, case, agent, outcome, when, n=1):
    v = Visit(case_id=case.id, agent_id=agent.id, check_in_latitude=28.6, check_in_longitude=77.2,
              check_in_time=when, distance_from_customer_metres=10.0, geo_verified=True,
              within_contact_hours=True, customer_met=outcome not in (VisitOutcome.NOT_AVAILABLE, VisitOutcome.ADDRESS_ISSUE),
              outcome=outcome, person_met=PersonMet.BORROWER, visit_number=n)
    db.add(v); return v


@pytest.fixture(scope="module")
def book():
    create_schema(bind=engine)
    db = Session()
    mgr = _user(db, "fa_mgr@t.in", UserRole.AGENCY_MANAGER, "Mgr")
    other = _user(db, "fa_other@t.in", UserRole.AGENCY_MANAGER, "Other")
    au1 = _user(db, "fa_a1@t.in", UserRole.FIELD_AGENT, "A1")
    au2 = _user(db, "fa_a2@t.in", UserRole.FIELD_AGENT, "A2")
    ag = _agent(db, "FA001", au1, mgr)
    ag_other = _agent(db, "FA002", au2, other)
    cust = Customer(customer_ref="FAC1", full_name="B", date_of_birth=date(1990, 1, 1), gender="M",
                    pan_masked="X", aadhaar_masked="X", phone_primary="9000000001", address_line1="1",
                    city="Delhi", state="DL", pincode="110001", latitude=28.6, longitude=77.2,
                    risk_category=RiskCategory.MEDIUM)
    db.add(cust); db.flush()
    loan = Loan(loan_account_number="FAL1", customer_id=cust.id, loan_type=LoanType.PERSONAL,
                branch_code="BR", sanctioned_amount=1.0, disbursed_amount=1.0,
                outstanding_principal=1.0, total_outstanding=1.0, overdue_amount=1.0, emi_amount=1.0,
                disbursement_date=date(2025, 1, 1), maturity_date=date(2027, 1, 1), dpd=45,
                dpd_bucket=DPDBucket.BUCKET_2, status=LoanStatus.ACTIVE, interest_rate=1.0, penal_charges=0.0)
    db.add(loan); db.flush()

    # A: visited yesterday (PTP), not today          -> today: planned only
    # B: REVISIT yesterday, nothing today           -> today: planned only
    # C: REVISIT today                              -> visited, met, no money
    # D: NOT_AVAILABLE today                        -> visited, not met
    # E: ADDRESS_ISSUE today                        -> visited, not met
    # F: DISPUTE today                              -> visited, met, no money
    # G: PTP today                                  -> paid_or_promised
    # H: PAID_FULL 10 days ago, nothing since        -> today/7d: planned only; 30d: paid
    # I: three visits today: NOT_AVAILABLE x2, PTP  -> ONE paid_or_promised case
    # J: on a beat 5 days ago, visited then (RTP)   -> today: not planned; 7d: met no money
    # K: 40 days ago, PTP                           -> outside 30d entirely
    # X: OTHER manager's case, PTP today            -> never visible
    names = "ABCDEFGHIJK"
    cases = {n: _case(db, f"FA-{n}", ag, cust, loan) for n in names}
    cx = _case(db, "FA-X", ag_other, cust, loan)

    _visit(db, cases["A"], ag, VisitOutcome.PTP, YESTERDAY)
    _visit(db, cases["B"], ag, VisitOutcome.REVISIT, YESTERDAY)
    _visit(db, cases["C"], ag, VisitOutcome.REVISIT, NOW)
    _visit(db, cases["D"], ag, VisitOutcome.NOT_AVAILABLE, NOW)
    _visit(db, cases["E"], ag, VisitOutcome.ADDRESS_ISSUE, NOW)
    _visit(db, cases["F"], ag, VisitOutcome.DISPUTE, NOW)
    _visit(db, cases["G"], ag, VisitOutcome.PTP, NOW)
    _visit(db, cases["H"], ag, VisitOutcome.PAID_FULL, NOW - timedelta(days=10))
    _visit(db, cases["I"], ag, VisitOutcome.NOT_AVAILABLE, NOW - timedelta(hours=3), 1)
    _visit(db, cases["I"], ag, VisitOutcome.NOT_AVAILABLE, NOW - timedelta(hours=2), 2)
    _visit(db, cases["I"], ag, VisitOutcome.PTP, NOW - timedelta(hours=1), 3)
    _visit(db, cases["J"], ag, VisitOutcome.RTP, NOW - timedelta(days=5))
    _visit(db, cases["K"], ag, VisitOutcome.PTP, NOW - timedelta(days=40))
    _visit(db, cx, ag_other, VisitOutcome.PTP, NOW)

    today_ids = [cases[n].id for n in "ABCDEFGHI"]
    db.add(Beat(agent_id=ag.id, beat_date=TODAY, beat_number="FB-T", ordered_case_ids=today_ids, status=BeatStatus.PLANNED))
    db.add(Beat(agent_id=ag.id, beat_date=TODAY - timedelta(days=5), beat_number="FB-5", ordered_case_ids=[cases["J"].id, cases["A"].id], status=BeatStatus.COMPLETED))
    db.add(Beat(agent_id=ag.id, beat_date=TODAY - timedelta(days=40), beat_number="FB-40", ordered_case_ids=[cases["K"].id], status=BeatStatus.COMPLETED))
    db.add(Beat(agent_id=ag_other.id, beat_date=TODAY, beat_number="FB-X", ordered_case_ids=[cx.id], status=BeatStatus.PLANNED))
    # promises for the Promises card
    db.add_all([
        PTP(case_id=cases["G"].id, agent_id=ag.id, committed_amount=1.0, committed_date=TODAY + timedelta(days=2), status=PTPStatus.ACTIVE),
        PTP(case_id=cases["A"].id, agent_id=ag.id, committed_amount=1.0, committed_date=TODAY + timedelta(days=9), status=PTPStatus.ACTIVE),
        PTP(case_id=cases["C"].id, agent_id=ag.id, committed_amount=1.0, committed_date=TODAY - timedelta(days=3), status=PTPStatus.HONORED),
        PTP(case_id=cases["D"].id, agent_id=ag.id, committed_amount=1.0, committed_date=TODAY - timedelta(days=4), status=PTPStatus.BROKEN),
        PTP(case_id=cases["E"].id, agent_id=ag.id, committed_amount=1.0, committed_date=TODAY - timedelta(days=4), status=PTPStatus.BROKEN),
        PTP(case_id=cases["F"].id, agent_id=ag.id, committed_amount=1.0, committed_date=TODAY + timedelta(days=1), status=PTPStatus.RESCHEDULED),
        PTP(case_id=cx.id, agent_id=ag_other.id, committed_amount=1.0, committed_date=TODAY + timedelta(days=1), status=PTPStatus.ACTIVE),
    ])
    db.commit()
    yield {"db": db, "mgr": mgr, "other": other, "cases": cases, "cx": cx}
    db.close()


@pytest.fixture(scope="module")
def client(book):
    def override():
        db = Session()
        try:
            yield db
        finally:
            db.close()
    app.dependency_overrides[get_db] = override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


def _h(user):
    return {"Authorization": f"Bearer {create_access_token(user.id, user.role.value, 'test-device')}"}


def _fa(client, book, window="today"):
    r = client.get(f"/api/v1/manager/dashboard/field-activity?window={window}", headers=_h(book["mgr"]))
    assert r.status_code == 200, r.text
    return r.json()


def test_api_today_counts_todays_visits_only(client, book):
    d = _fa(client, book, "today")
    # planned: A..I on today's beat = 9 (J, K, X not planned today)
    assert d["planned"] == 9
    # visited today: C D E F G I = 6 (A, B, H have only historical visits)
    assert d["visited"] == 6
    # met today: C F G I = 4 (D, E not met)
    assert d["met"] == 4
    # paid/promised today: G, I = 2
    assert d["paid_or_promised"] == 2
    assert d["not_met"] == 2 and d["not_met_reasons"] == {"NOT_AVAILABLE": 1, "ADDRESS_ISSUE": 1}
    assert d["met_no_money"] == 2 and d["met_no_money_reasons"] == {"REVISIT": 1, "DISPUTE": 1}
    assert d["window_start"] == d["window_end"] == TODAY.isoformat()


def test_api_scenarios_1_2_8_historical_outcomes_do_not_leak_into_today(client, book):
    # A (PTP yesterday), B (REVISIT yesterday), H (PAID_FULL 10 days ago) are
    # planned today and must appear in NO stage past planned.
    r = client.get("/api/v1/manager/cases?activity=visited&activity_window=today", headers=_h(book["mgr"]))
    nums = {c["case_number"] for c in r.json()["cases"]}
    assert nums == {"FA-C", "FA-D", "FA-E", "FA-F", "FA-G", "FA-I"}
    assert not ({"FA-A", "FA-B", "FA-H"} & nums)


def test_api_scenario_9_multiple_visits_one_case(client, book):
    r = client.get("/api/v1/manager/cases?activity=paid_or_promised&activity_window=today", headers=_h(book["mgr"]))
    nums = [c["case_number"] for c in r.json()["cases"]]
    assert sorted(nums) == ["FA-G", "FA-I"]      # I once, despite three visits
    assert r.json()["total"] == 2


def test_api_scenario_10_seven_day_window(client, book):
    d = _fa(client, book, "7d")
    # planned in 7d: today's beat (9) + the 5-day-old beat (J, A) -> 10 distinct
    assert d["planned"] == 10
    # visited in 7d: today's 6 + A, B (yesterday) + J (5 days ago) = 9; H (10d) and K (40d) excluded
    assert d["visited"] == 9
    assert d["met"] == 7                           # + A(PTP), B(REVISIT), J(RTP)
    assert d["paid_or_promised"] == 3              # G, I, A
    assert d["met_no_money_reasons"] == {"REVISIT": 2, "DISPUTE": 1, "RTP": 1}


def test_api_scenario_11_thirty_day_window(client, book):
    d = _fa(client, book, "30d")
    assert d["planned"] == 10                      # K's beat is 40 days old
    assert d["visited"] == 10                      # H's 10-day-old visit now counts
    assert d["paid_or_promised"] == 4              # + H
    r = client.get("/api/v1/manager/cases?activity=visited&activity_window=30d", headers=_h(book["mgr"]))
    assert "FA-K" not in {c["case_number"] for c in r.json()["cases"]}


def test_api_scenario_12_tenant_scoping(client, book):
    d = _fa(client, book, "today")
    assert d["planned"] == 9                       # FA-X (other manager, PTP today) absent
    d_other = client.get("/api/v1/manager/dashboard/field-activity", headers=_h(book["other"])).json()
    assert (d_other["planned"], d_other["visited"], d_other["paid_or_promised"]) == (1, 1, 1)
    r = client.get("/api/v1/manager/cases?activity=paid_or_promised&activity_window=today", headers=_h(book["mgr"]))
    assert "FA-X" not in {c["case_number"] for c in r.json()["cases"]}


def test_api_scenario_15_reason_links_land_on_exactly_those_cases(client, book):
    r = client.get("/api/v1/manager/cases?activity=not_met&activity_window=today&visit_outcome=NOT_AVAILABLE", headers=_h(book["mgr"]))
    assert [c["case_number"] for c in r.json()["cases"]] == ["FA-D"]
    r = client.get("/api/v1/manager/cases?activity=met_no_money&activity_window=today&visit_outcome=REVISIT,DISPUTE", headers=_h(book["mgr"]))
    assert sorted(c["case_number"] for c in r.json()["cases"]) == ["FA-C", "FA-F"]
    r = client.get("/api/v1/manager/cases?activity=planned&activity_window=today", headers=_h(book["mgr"]))
    assert r.json()["total"] == 9
    r = client.get("/api/v1/manager/cases?activity=nonsense", headers=_h(book["mgr"]))
    assert r.status_code == 422


def test_api_scenario_14_empty_today_is_explicit_and_leak_free(client, book):
    # The OTHER manager's agent has a beat today; remove its visit -> a
    # planned-but-unvisited day reads 1 / 0 / 0 / 0, never the historical row.
    db = book["db"]
    db.query(Visit).filter(Visit.case_id == book["cx"].id).delete()
    db.commit()
    d = client.get("/api/v1/manager/dashboard/field-activity", headers=_h(book["other"])).json()
    assert (d["planned"], d["visited"], d["met"], d["paid_or_promised"]) == (1, 0, 0, 0)
    assert d["drop_offs"] == {"planned_to_visited": 100.0, "visited_to_met": None, "met_to_paid_or_promised": None}


def test_api_default_window_is_today(client, book):
    d = client.get("/api/v1/manager/dashboard/field-activity", headers=_h(book["mgr"])).json()
    assert d["window"] == "today"


def test_api_ptp_health_on_dashboard(client, book):
    d = client.get("/api/v1/manager/dashboard", headers=_h(book["mgr"])).json()["ptp_health"]
    assert d["honored"] == 1 and d["broken"] == 2
    assert d["kept_rate_pct"] == pytest.approx(33.3, abs=0.05)
    assert d["active"] == 2 and d["rescheduled"] == 1
    assert d["due_next_7_days"] == 1               # G at +2; A at +9 is outside; X is the other tenant
    r = client.get(f"/api/v1/manager/cases?ptp_due_from={d['due_from']}&ptp_due_to={d['due_to']}", headers=_h(book["mgr"]))
    assert [c["case_number"] for c in r.json()["cases"]] == ["FA-G"]


def test_api_requires_manager(client, book):
    assert client.get("/api/v1/manager/dashboard/field-activity").status_code in (401, 403)
