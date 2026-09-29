"""The placement engine (P3 D09, ADR 0010)."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_placement_exploration_is_off_by_default_and_capped_at_twenty_percent():
    assert Settings().PLACEMENT_EXPLORATION_RATE == 0.0
    assert Settings(PLACEMENT_EXPLORATION_RATE=0.2).PLACEMENT_EXPLORATION_RATE == 0.2
    for bad in (0.21, -0.01):
        with pytest.raises(ValidationError):
            Settings(PLACEMENT_EXPLORATION_RATE=bad)


# ── Recall rules (D09 step 2) ────────────────────────────────────────────────

from datetime import date, datetime, timedelta, timezone  # noqa: E402

from app.models.placement import Placement  # noqa: E402
from app.models.tenancy import AgencyContract  # noqa: E402
from app.services.bank.placement_engine import (  # noqa: E402
    CONTRACT_END, NO_ACTIVITY, SLA_BREACH, PlacementActivity, ist_day, judge_recall,
)

PLAN = date(2026, 10, 15)


def _pa(*, end_on=None, sla_due=date(2026, 10, 6), placed=date(2026, 10, 1), last=None, first=None,
        at_end=True, on_sla=True, idle_days=None, status="ACTIVE", c_end=date(2027, 3, 31)):
    c = AgencyContract(contract_no="C/1", status=status, start_date=date(2026, 4, 1), end_date=c_end,
                       recall_at_contract_end=at_end, recall_on_sla_breach=on_sla,
                       recall_no_activity_days=idle_days)
    p = Placement(placed_on=placed, expected_end_on=end_on, sla_first_visit_due=sla_due)
    return PlacementActivity(placement=p, contract=c, last_activity=last, first_visit=first)


def test_a_placement_with_nothing_wrong_is_kept():
    v = judge_recall(_pa(first=date(2026, 10, 3), last=date(2026, 10, 14), idle_days=10), PLAN)
    assert not v.recalled and v.end_reason is None


@pytest.mark.parametrize("kw,rule", [
    ({"end_on": date(2026, 10, 14), "first": date(2026, 10, 2)}, CONTRACT_END),   # ran past its end
    ({"status": "TERMINATED", "first": date(2026, 10, 2)}, CONTRACT_END),  # contract no longer in force
    ({"c_end": date(2026, 10, 14), "first": date(2026, 10, 2)}, CONTRACT_END),
    ({"first": None}, SLA_BREACH),                                         # never visited
    ({"first": date(2026, 10, 7)}, SLA_BREACH),                            # first visit a day late
    ({"first": date(2026, 10, 3), "last": date(2026, 10, 4), "idle_days": 11}, NO_ACTIVITY),
    ({"first": date(2026, 10, 3), "idle_days": 14}, NO_ACTIVITY),         # none since placed_on
])
def test_each_rule_fires_on_its_own_condition(kw, rule):
    v = judge_recall(_pa(**kw), PLAN)
    assert v.rules == [rule] and v.end_reason == rule, v.details


@pytest.mark.parametrize("kw", [
    {"end_on": date(2026, 10, 15), "first": date(2026, 10, 2)},   # the last day of the placement is still in it
    {"sla_due": date(2026, 10, 15)},                               # the SLA day itself is not yet a breach
    {"first": date(2026, 10, 6)},                                  # a visit ON the due day meets the SLA
    {"first": date(2026, 10, 3), "last": date(2026, 10, 5), "idle_days": 11},   # 10 idle days < 11
    {"end_on": date(2026, 10, 1), "at_end": False, "on_sla": False},            # flags off: nothing fires
])
def test_the_rules_hold_at_their_boundaries(kw):
    assert not judge_recall(_pa(**kw), PLAN).recalled


def test_several_rules_can_fire_and_the_end_reason_is_the_first_in_order():
    v = judge_recall(_pa(end_on=date(2026, 10, 10), first=None, idle_days=5), PLAN)
    assert v.rules == [CONTRACT_END, SLA_BREACH, NO_ACTIVITY] and v.end_reason == CONTRACT_END


def test_a_days_boundary_is_the_ist_calendar():
    assert ist_day(datetime(2026, 10, 14, 19, 0, tzinfo=timezone.utc)) == date(2026, 10, 15)   # 00:30 IST
    assert ist_day(datetime(2026, 10, 14, 18, 0)) == date(2026, 10, 14)                         # naive = UTC
    assert ist_day(None) is None


def test_activity_is_read_per_placement_from_visits_calls_and_payments():
    """placement_activity: newest visit/call/payment day and earliest visit day
    per ACTIVE placement, IST days, this bank only."""
    from app.models.agent import Agent, AgentSpecialization, AgentStatus, AgentTier
    from app.models.call_log import CallLog, CallOutcome
    from app.models.payment import Payment, PaymentMode
    from app.models.user import User, UserRole
    from app.models.visit import PersonMet, Visit, VisitOutcome
    from app.services.bank.placement_engine import placement_activity, recall_verdicts
    from app.services.placement_service import PlacementService
    from tests._db import TEST_AGENCY_ID, TEST_BANK_ID, create_schema, make_engine, make_session_factory
    from tests._placement import cover, make_loan

    engine = make_engine()
    create_schema(engine)
    db = make_session_factory(engine)()
    c = AgencyContract(bank_id=TEST_BANK_ID, agency_id=TEST_AGENCY_ID, contract_no="C/1", status="ACTIVE",
                       start_date=date(2026, 4, 1), end_date=date(2027, 3, 31), sla_first_visit_days=5,
                       recall_no_activity_days=10, recall_on_sla_breach=True)
    db.add(c)
    db.flush()
    cover(db, c)
    u = User(email="agent@example.test", phone="9811100001", full_name="Rohit Bhatt", hashed_password="x",
             role=UserRole.FIELD_AGENT, bank_id=TEST_BANK_ID, agency_id=TEST_AGENCY_ID)
    db.add(u)
    db.flush()
    ag = Agent(user_id=u.id, employee_code="AR001", id_card_number="AR001-ID", base_latitude=28.4,
               base_longitude=77.0, tier=AgentTier.TIER_1, specialization=AgentSpecialization.BOTH,
               status=AgentStatus.ON_DUTY, territory="Gurugram", languages_spoken=["HINDI"], ranking_score=80.0)
    db.add(ag)
    db.flush()
    svc = PlacementService(db)
    busy, quiet = make_loan(db, 1), make_loan(db, 2)
    p_busy = svc.place_new_loan(busy, agency_id=TEST_AGENCY_ID, on=date(2026, 10, 1), source="FEED")
    p_quiet = svc.place_new_loan(quiet, agency_id=TEST_AGENCY_ID, on=date(2026, 10, 1), source="FEED")
    case = svc.open_case(p_busy, busy, case_number="PLT-1", target_amount=1.0)
    svc.open_case(p_quiet, quiet, case_number="PLT-2", target_amount=1.0)
    at = lambda d, h=6: datetime(d.year, d.month, d.day, h, 0, tzinfo=timezone.utc)   # noqa: E731
    for n, d in enumerate((date(2026, 10, 3), date(2026, 10, 8)), start=1):
        db.add(Visit(case_id=case.id, agent_id=ag.id, check_in_latitude=28.4, check_in_longitude=77.0,
                     check_in_time=at(d), distance_from_customer_metres=10.0, geo_verified=True,
                     within_contact_hours=True, customer_met=True, outcome=VisitOutcome.PTP,
                     person_met=PersonMet.BORROWER, visit_number=n))
    db.add(CallLog(case_id=case.id, agent_id=ag.id, customer_id=busy.customer_id, called_at=at(date(2026, 10, 9)),
                   outcome=CallOutcome.ANSWERED))
    # 19:00 UTC on the 10th is the 11th in IST: the day the rules read.
    db.add(Payment(case_id=case.id, loan_id=busy.id, amount=500.0, mode=PaymentMode.CASH, receipt_number="R-1",
                   payment_date=at(date(2026, 10, 10), 19)))
    db.commit()

    acts = {a.placement.id: a for a in placement_activity(db, TEST_BANK_ID)}
    assert (acts[p_busy.id].first_visit, acts[p_busy.id].last_activity) == (date(2026, 10, 3), date(2026, 10, 11))
    assert (acts[p_quiet.id].first_visit, acts[p_quiet.id].last_activity) == (None, None)
    assert placement_activity(db, _other_bank()) == []

    verdicts = {v.placement.id: v for v in recall_verdicts(db, TEST_BANK_ID, date(2026, 10, 15))}
    assert not verdicts[p_busy.id].recalled                                 # visited by the 6th, active on the 11th
    assert verdicts[p_quiet.id].rules == [SLA_BREACH, NO_ACTIVITY]          # never visited; 14 idle days


def _other_bank():
    from tests._db import test_id
    return test_id("bank:elsewhere")
