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
from app.services.agent_service import AgentService

TODAY = date.today()
NOW = datetime.now(timezone.utc)


@pytest.fixture()
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine, autoflush=False)()
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
