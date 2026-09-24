"""The agent's "PTPs due today" count must never exceed what it can show.

WHAT WENT WRONG. The home screen said "1 PTPs Due Today"; tapping it listed
nothing. Three different scopes were answering one question:

    get_beat          PTP.agent_id == agent  OR  PTP.case_id IN beat
    get_home_summary  PTP.agent_id == agent
    the case list     Case.agent_id == agent

A PTP keeps the agent who TOOK it, and the nightly allocator reassigns cases
freely — so a promise this agent made on a case that now belongs to somebody
else was counted, and could never be rendered, because the flag is only ever
attached to cases the agent actually holds. Found live on 2026-09-10: PTP
71820ca4 on CASE0000567, Piyush Sharma's promise, another agent's case.

The property held here is not "the count is 1". It is that the COUNT AND THE
LIST AGREE — a to-do item an agent cannot open is not a to-do item.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models.agent import Agent, AgentSpecialization, AgentStatus, AgentTier
from app.models.beat import Beat, BeatStatus
from app.models.case import Case, CaseStatus
from app.models.customer import Customer
from app.models.loan import DPDBucket, Loan, LoanStatus, LoanType
from app.models.ptp import PTP, PTPStatus
from app.models.user import User, UserRole
from app.models.visit import PersonMet, Visit, VisitOutcome
from app.services.agent_service import AgentService
from tests._db import create_schema, drop_schema, make_engine, make_session_factory, test_id  # noqa: F401

TODAY = date.today()
NOW = datetime.now(timezone.utc)


@pytest.fixture()
def db():
    engine = make_engine()
    create_schema(engine)
    s = make_session_factory(bind=engine, autoflush=False)()
    yield s
    s.close()


def _uid() -> str:
    return str(uuid.uuid4())


@pytest.fixture()
def world(db):
    """Two agents, and a case that has moved from the first to the second."""
    mgr = User(id=_uid(), email="m@t.io", phone="9000000001", full_name="M",
               hashed_password="x", role=UserRole.AGENCY_MANAGER)
    u1 = User(id=_uid(), email="a1@t.io", phone="9000000002", full_name="A One",
              hashed_password="x", role=UserRole.FIELD_AGENT)
    u2 = User(id=_uid(), email="a2@t.io", phone="9000000003", full_name="A Two",
              hashed_password="x", role=UserRole.FIELD_AGENT)
    db.add_all([mgr, u1, u2])
    db.flush()

    def _agent(u, code):
        return Agent(id=_uid(), user_id=u.id, employee_code=code,
                     id_card_number=code + "-ID", agency_id="AG1",
                     manager_user_id=mgr.id, base_latitude=28.63, base_longitude=77.21,
                     territory="Delhi", languages_spoken=["HINDI"],
                     status=AgentStatus.ON_DUTY, tier=AgentTier.TIER_1,
                     specialization=AgentSpecialization.BOTH, ranking_score=80.0)
    a1, a2 = _agent(u1, "EMP001"), _agent(u2, "EMP002")
    db.add_all([a1, a2])
    db.flush()

    # Field list copied from tests/test_planner_service.py rather than guessed —
    # several of these columns are NOT NULL and discovering that one failure at a
    # time is slower than reusing a construction that already works.
    cust = Customer(
        id=_uid(), customer_ref="C1", full_name="Borrower",
        date_of_birth="1990-01-01", gender="M", pan_masked="ABCDE1234F",
        aadhaar_masked="123456789012", phone_primary="9800000000",
        address_line1="Connaught Place, Delhi", city="Delhi", state="Delhi",
        pincode="110001", latitude=28.6315, longitude=77.2167,
        language_preference="HINDI",
    )
    db.add(cust)
    db.flush()
    loan = Loan(
        id=_uid(), customer_id=cust.id, loan_account_number="L1",
        loan_type=LoanType.PERSONAL, bank_name="HDFC Bank", branch_code="DL01",
        sanctioned_amount=500000.0, disbursed_amount=500000.0,
        outstanding_principal=250000.0, total_outstanding=250000.0,
        overdue_amount=50000.0, emi_amount=15000.0, interest_rate=12.5,
        disbursement_date="2022-01-01", maturity_date="2027-01-01",
        dpd=45, dpd_bucket=DPDBucket.BUCKET_2, status=LoanStatus.ACTIVE,
    )
    db.add(loan)
    db.flush()

    # The case is a1's today and on a1's beat.
    on_beat = Case(id=_uid(), case_number="ONBEAT", customer_id=cust.id, loan_id=loan.id,
                   agent_id=a1.id, status=CaseStatus.IN_PROGRESS,
                   target_amount=10000.0, collected_amount=0.0,
                   allocation_date=TODAY.isoformat())
    # This one a1 visited last week and took a promise on; it has since been
    # reassigned to a2 by the nightly plan, and is NOT on a1's beat today.
    moved_on = Case(id=_uid(), case_number="MOVEDON", customer_id=cust.id, loan_id=loan.id,
                    agent_id=a2.id, status=CaseStatus.IN_PROGRESS,
                    target_amount=8590.0, collected_amount=0.0,
                    allocation_date=TODAY.isoformat())
    db.add_all([on_beat, moved_on])
    db.flush()

    beat = Beat(id=_uid(), agent_id=a1.id, beat_date=TODAY,
                beat_number="B1", status=BeatStatus.PLANNED,
                ordered_case_ids=[on_beat.id], total_cases=1,
                total_target_amount=10000.0)
    db.add(beat)

    # Both promises are due today and both were taken BY a1.
    db.add(PTP(id=_uid(), case_id=on_beat.id, agent_id=a1.id,
               committed_amount=2000.0, committed_date=TODAY, status=PTPStatus.ACTIVE))
    db.add(PTP(id=_uid(), case_id=moved_on.id, agent_id=a1.id,
               committed_amount=1500.0, committed_date=TODAY, status=PTPStatus.ACTIVE))
    db.commit()
    return {"a1": a1, "a2": a2, "on_beat": on_beat, "moved_on": moved_on}


def test_the_beat_count_matches_the_cases_it_flags(db, world):
    """The exact defect: a count of 1 with an empty list behind it.

    a1 took BOTH promises, so the old `or_(PTP.agent_id == agent, ...)` counted
    two while only one case on the beat could carry the flag.
    """
    beat = AgentService(db).get_beat(world["a1"])
    assert beat is not None

    flagged = [c for c in beat["cases"] if c.get("ptp_due_today")]
    assert beat["ptps_due_today"] == len(flagged), (
        f"card says {beat['ptps_due_today']} PTPs due but only {len(flagged)} "
        f"case(s) can show one — tapping it would list nothing")
    assert beat["ptps_due_today"] == 1
    assert flagged[0]["case_number"] == "ONBEAT"


def test_a_promise_on_a_case_that_left_the_beat_is_not_a_to_do(db, world):
    """Both counts are scoped to the BEAT, because that is what the list shows.

    `case_service.ranked_cases` builds the list behind the card from
    `beat.ordered_case_ids`, so a promise on a case that is not on today's beat
    cannot be displayed no matter who owns it. Scoping the count by case
    OWNERSHIP instead would reproduce the same mismatch one step removed: the
    agent still holds MOVEDON, but it is not on their beat, so it would be
    counted and still not listed.
    """
    summary = AgentService(db).home_summary(world["a1"])
    assert summary["ptps_due_today"] == 1, (
        "a promise on a case that is not on today's beat is being counted as "
        "work this agent can open today")

    # a2 holds MOVEDON but has no beat, so it is nobody's card item today. The
    # promise is not lost — it is still ACTIVE and still due.
    moved_ptp = db.query(PTP).filter(PTP.case_id == world["moved_on"].id).one()
    assert moved_ptp.status == PTPStatus.ACTIVE
    assert moved_ptp.committed_date == TODAY

    # And it still records who made it. Agent accountability reads that column,
    # so this fix must not have quietly reassigned credit.
    assert moved_ptp.agent_id == world["a1"].id


def test_the_two_surfaces_do_not_disagree(db, world):
    """`get_beat` and `home_summary` both feed the same card — the frontend
    reads `beat?.ptps_due_today ?? summary?.ptps_due_today` — so they must not
    answer differently depending on which one happens to be present."""
    svc = AgentService(db)
    beat = svc.get_beat(world["a1"])
    summary = svc.home_summary(world["a1"])
    assert beat["ptps_due_today"] == summary["ptps_due_today"]


def test_a_settled_case_stops_counting(db, world):
    """Both scopes already excluded resolved and fully-paid cases. Pinned so the
    ownership change above did not drop those guards on the way past."""
    svc = AgentService(db)
    assert svc.home_summary(world["a1"])["ptps_due_today"] == 1

    c = db.query(Case).filter(Case.case_number == "ONBEAT").one()
    c.collected_amount = c.target_amount
    c.status = CaseStatus.PAID
    db.commit()

    assert svc.home_summary(world["a1"])["ptps_due_today"] == 0
    beat = svc.get_beat(world["a1"])
    assert beat["ptps_due_today"] == 0


# ── Pending: the same defect, one screen over (2026-09-22) ──────────────────
# Home read "1 pending" from total_cases - cases_visited_today while My Cases
# showed every card done, because the Cases page also treats PAID / CLOSED /
# WRITTEN_OFF / fully-collected as finished. A case paid off on an EARLIER day
# and still on today's route is exactly the gap between those two rules.
# `cases_pending` is now computed once, in the service, and both read it.

def _paid_earlier(db, world, *, status=CaseStatus.PAID, collected=10000.0, target=10000.0):
    """Put a second case on a1's beat that was settled before today."""
    a1 = world["a1"]
    src = world["on_beat"]
    settled = Case(id=_uid(), case_number="SETTLED", customer_id=src.customer_id,
                   loan_id=src.loan_id, agent_id=a1.id, status=status,
                   target_amount=target, collected_amount=collected,
                   allocation_date=TODAY.isoformat())
    db.add(settled)
    db.flush()
    beat = db.query(Beat).filter(Beat.agent_id == a1.id, Beat.beat_date == TODAY).one()
    beat.ordered_case_ids = list(beat.ordered_case_ids) + [settled.id]
    beat.total_cases = len(beat.ordered_case_ids)
    db.commit()
    return settled


def test_a_case_settled_on_an_earlier_day_leaves_the_route(db, world):
    """2026-09-22 — it is not merely "not pending", it is not a stop at all.

    The beat is written at 20:00 the night before and nothing revises it, so a
    borrower who settles overnight stayed on the route: 2 stops, 1 of them
    real, and the home screen had to explain away the second."""
    settled = _paid_earlier(db, world)
    out = AgentService(db).get_beat(world["a1"])

    assert out["total_cases"] == 1                  # the route, not the plan
    assert [c["id"] for c in out["cases"]] == [world["on_beat"].id]
    assert out["ordered_case_ids"] == [world["on_beat"].id]
    assert out["cases_visited_today"] == 0          # nobody visited today
    assert settled.id in out["no_visit_needed_ids"]  # reported, so a client can say why
    assert out["cases_pending"] == 1                # only the live case is work
    # Its target went with it — otherwise "collected today" is measured against
    # money that was already in the bank.
    assert out["total_target_amount"] == 10000.0


def test_the_stored_beat_is_not_rewritten(db, world):
    """The plan is a record. Dropping a stop from today's view must not edit it."""
    settled = _paid_earlier(db, world)
    AgentService(db).get_beat(world["a1"])
    beat = db.query(Beat).filter(Beat.agent_id == world["a1"].id, Beat.beat_date == TODAY).one()
    assert settled.id in beat.ordered_case_ids and beat.total_cases == 2


def test_home_summary_counts_the_same_route_as_the_beat(db, world):
    """Two endpoints back one screen; they must not disagree."""
    _paid_earlier(db, world)
    svc = AgentService(db)
    assert svc.home_summary(world["a1"])["cases_today"] == svc.get_beat(world["a1"])["total_cases"]


def test_pending_equals_the_cases_the_list_still_shows_as_to_do(db, world):
    """The property, not the number: what Home counts is what My Cases offers."""
    _paid_earlier(db, world)
    on_beat = world["on_beat"]
    db.add(Visit(id=_uid(), case_id=on_beat.id, agent_id=world["a1"].id,
                 check_in_latitude=28.63, check_in_longitude=77.21,
                 check_in_time=datetime.now(timezone.utc),
                 distance_from_customer_metres=12.0, geo_verified=True,
                 within_contact_hours=True, customer_met=True,
                 person_met=PersonMet.BORROWER, outcome=VisitOutcome.PART_PAID,
                 visit_number=1))
    db.commit()

    out = AgentService(db).get_beat(world["a1"])
    done = set(out["visited_today_ids"]) | set(out["no_visit_needed_ids"])
    still_to_do = [c for c in out["cases"] if c["id"] not in done]

    assert out["cases_pending"] == len(still_to_do) == 0
    assert out["cases_visited_today"] == 1          # the visit is still a visit
    assert out["total_cases"] == 1                  # the settled stop is not on the route


def test_an_escalated_case_is_still_pending_work(db, world):
    """ESCALATED is open and visitable — it must never fall into no-visit-needed."""
    esc = _paid_earlier(db, world, status=CaseStatus.ESCALATED, collected=0.0)
    out = AgentService(db).get_beat(world["a1"])
    assert esc.id not in out["no_visit_needed_ids"]
    assert esc.id in [c["id"] for c in out["cases"]]   # still a stop
    assert out["total_cases"] == 2
    assert out["cases_pending"] == 2


def test_a_case_collected_to_target_needs_no_visit_even_if_its_status_lags(db, world):
    """collected >= target is the Cases page's other done rule; keep it."""
    full = _paid_earlier(db, world, status=CaseStatus.PARTIALLY_PAID, collected=10000.0)
    out = AgentService(db).get_beat(world["a1"])
    assert full.id in out["no_visit_needed_ids"]
    assert full.id not in [c["id"] for c in out["cases"]]
    assert out["cases_pending"] == 1
