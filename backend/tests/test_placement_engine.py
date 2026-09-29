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


# ── The plan: options, solve, exploration, recording (D09 step 3) ────────────

from app.models.planning import PlacementDecision, PlacementRun  # noqa: E402
from app.models.tenancy import Agency, AgencyContractTerm, Bank  # noqa: E402
from app.models.loan import DPDBucket, LoanType  # noqa: E402
from app.services.bank.placement_engine import (  # noqa: E402
    DEMO_BOOK_NOTE, EngineTooSlow, Option, build_plan, explore, record_run,
)
from tests._db import TEST_AGENCY_ID, TEST_BANK_ID, create_schema, make_engine, make_session_factory, test_id  # noqa: E402
from tests._placement import cover, make_loan  # noqa: E402

DAY = date(2026, 10, 15)
AG_B = test_id("agency:kaveri")


def _world(*, cap_a=None, cap_b=None, terms=None):
    engine = make_engine()
    create_schema(engine)
    db = make_session_factory(engine, info={})()
    db.add(Agency(id=AG_B, bank_id=TEST_BANK_ID, code="AGY-KAV", legal_name="Kaveri Resolve Partners LLP",
                  status="ACTIVE", contacts=[], is_demo=True))
    db.flush()
    contracts = {}
    for aid, cap in ((TEST_AGENCY_ID, cap_a), (AG_B, cap_b)):
        c = AgencyContract(bank_id=TEST_BANK_ID, agency_id=aid, contract_no=f"C/{aid[:6]}", status="ACTIVE",
                           start_date=date(2026, 4, 1), end_date=date(2027, 3, 31), max_placed_cases=cap,
                           sla_first_visit_days=5, recall_on_sla_breach=True)
        db.add(c)
        db.flush()
        cover(db, c)
        contracts[aid] = c
    for aid, pct in (terms or {}).items():
        db.add(AgencyContractTerm(bank_id=TEST_BANK_ID, agency_id=aid, contract_id=contracts[aid].id,
                                  loan_type=LoanType.PERSONAL, dpd_bucket=DPDBucket.BUCKET_2, commission_pct=pct))
    db.commit()
    return db, contracts


def _region(db):
    from app.models.tenancy import Branch
    return db.query(Branch).filter_by(branch_code="GGN044").one().region_id


def _effects(db, a=1.0, b=1.0):
    r = _region(db)
    return {(TEST_AGENCY_ID, r): (a, 40), (AG_B, r): (b, 40)}


def test_loans_go_to_the_better_agency_until_it_is_full_then_the_next_then_wait():
    db, _ = _world(cap_a=2, cap_b=1)
    loans = [make_loan(db, n, overdue=10_000.0 * n) for n in (1, 2, 3, 4)]
    db.commit()
    plan = build_plan(db, TEST_BANK_ID, DAY, effects=_effects(db, a=1.2, b=0.9))
    got = {l.loan_account_number: plan.assignment[l.id] for l in loans}
    # The two biggest go to A (m 1.2), the next to B, the smallest waits.
    assert got == {"LN00000004": TEST_AGENCY_ID, "LN00000003": TEST_AGENCY_ID, "LN00000002": AG_B,
                   "LN00000001": None}


def test_commission_can_outweigh_a_better_effect():
    db, _ = _world(terms={TEST_AGENCY_ID: 30.0, AG_B: 5.0})
    loan = make_loan(db)
    db.commit()
    plan = build_plan(db, TEST_BANK_ID, DAY, effects=_effects(db, a=1.1, b=1.0))
    # A: 1.1 x 0.70 = 0.77 < B: 1.0 x 0.95 = 0.95
    assert plan.assignment[loan.id] == AG_B
    opt = plan.options[loan.id][0]
    assert (opt.agency_id, opt.commission_pct, opt.commission_known) == (AG_B, 5.0, True)


def test_a_loan_no_agency_may_take_is_blocked_with_the_reasons():
    from app.models.tenancy import AgencyRegion
    db, _ = _world()
    db.query(AgencyRegion).delete()                       # nobody covers anything
    loan = make_loan(db)
    db.commit()
    plan = build_plan(db, TEST_BANK_ID, DAY, effects={})
    assert plan.options[loan.id] == [] and plan.assignment[loan.id] is None
    assert set(plan.refusals[loan.id].values()) == {"NOT_COVERED"}


def test_unmodelled_loans_are_valued_at_their_overdue_and_say_so():
    from app.ml.pipeline.config import RECOVERY_RISK
    from app.models.model_prediction import ModelPrediction
    from app.services.bank.expected_recovery import expected_recovery_inr
    db, _ = _world()
    scored, unscored = make_loan(db, 1, overdue=20_000.0), make_loan(db, 2, overdue=20_000.0)
    db.add(ModelPrediction(bank_id=TEST_BANK_ID, model_name=RECOVERY_RISK.name, model_version="2.2.0",
                           entity_type="loan", entity_id=scored.id, loan_id=scored.id, as_of_date=DAY,
                           probability=0.75, is_modelled=True))
    db.commit()
    plan = build_plan(db, TEST_BANK_ID, DAY, effects={})
    c = {x.loan.id: x for x in plan.candidates}
    assert c[scored.id].is_modelled and c[scored.id].p_pay == pytest.approx(0.25)
    assert c[scored.id].expected == pytest.approx(expected_recovery_inr(
        prob=0.25, overdue_at_placement=20_000.0, exposure_at_placement=281_000.0,
        dpd_bucket=DPDBucket.BUCKET_2, loan_type=LoanType.PERSONAL))
    assert (c[unscored.id].is_modelled, c[unscored.id].expected, c[unscored.id].p_pay) == (False, 20_000.0, None)


def test_a_recalled_loan_is_re_placed_with_another_agency_in_the_same_run():
    from app.services.placement_service import PlacementService
    db, _ = _world()
    loan = make_loan(db)
    p = PlacementService(db).place_new_loan(loan, agency_id=TEST_AGENCY_ID, on=date(2026, 10, 1), source="FEED")
    db.commit()                                             # never visited: SLA (due 10-06) breached on the 15th
    plan = build_plan(db, TEST_BANK_ID, DAY, effects=_effects(db, a=1.25, b=0.8))
    cand = next(x for x in plan.candidates if x.loan.id == loan.id)
    assert cand.recall is not None and cand.previous_agency_id == TEST_AGENCY_ID
    assert plan.refusals[loan.id][TEST_AGENCY_ID] == "RECALLED_FROM"
    assert plan.assignment[loan.id] == AG_B                 # despite A's better effect
    run = record_run(db, plan, bank_id=TEST_BANK_ID, plan_date=DAY, simulate=True, exploration_rate=0.0,
                     created_by=None)
    d = db.query(PlacementDecision).filter_by(run_id=run.id).one()
    assert (d.outcome, d.previous_agency_id, d.chosen_agency_id) == ("RECALLED", TEST_AGENCY_ID, AG_B)
    assert d.score_breakdown["replacement"] == "PLACED" and d.score_breakdown["recall_rules"] == ["SLA_BREACH"]
    assert db.get(Placement, p.id).status == "ACTIVE"      # a plan changes nothing


def test_a_recall_frees_its_slot_for_the_same_run():
    from app.services.placement_service import PlacementService
    db, _ = _world(cap_a=1, cap_b=0)
    old, new = make_loan(db, 1), make_loan(db, 2)
    PlacementService(db).place_new_loan(old, agency_id=TEST_AGENCY_ID, on=date(2026, 10, 1), source="FEED")
    db.commit()
    plan = build_plan(db, TEST_BANK_ID, DAY, effects={})
    assert plan.capacity[TEST_AGENCY_ID] == 1                # full, but the recall gives its slot back
    assert plan.assignment[new.id] == TEST_AGENCY_ID and plan.assignment[old.id] is None


def test_the_run_records_one_decision_per_loan_and_counts_kept_placements():
    from app.services.placement_service import PlacementService
    db, contracts = _world(cap_a=1, cap_b=1)
    kept = make_loan(db, 9)
    PlacementService(db).place_new_loan(kept, agency_id=AG_B, on=DAY, source="FEED")   # fills B; SLA not yet due
    db.get(Bank, TEST_BANK_ID).is_demo = True
    loans = [make_loan(db, n) for n in (1, 2)]
    db.commit()
    plan = build_plan(db, TEST_BANK_ID, DAY, effects={})
    run = record_run(db, plan, bank_id=TEST_BANK_ID, plan_date=DAY, simulate=False, exploration_rate=0.0,
                     created_by=None)
    db.commit()
    rows = db.query(PlacementDecision).filter_by(run_id=run.id).all()
    assert {r.loan_id for r in rows} == {l.id for l in loans}                   # the kept one has no row
    assert sorted(r.outcome for r in rows) == ["DEFERRED", "PLACED"]
    assert (run.status, run.strategy, run.total_kept, run.total_placed, run.total_deferred) == (
        "PLANNED", "MIN_COST_FLOW", 1, 1, 1)
    assert DEMO_BOOK_NOTE in run.summary["synthetic_warning"] and "limitations" in run.summary
    assert rows[0].score_breakdown["versions"]["engine"] == "placement-engine-1.0.0"


def test_the_time_limit_stops_a_run_before_anything_is_written():
    db, _ = _world()
    make_loan(db)
    db.commit()
    ticks = iter([0.0, 31.0, 62.0, 93.0, 124.0])
    with pytest.raises(EngineTooSlow):
        build_plan(db, TEST_BANK_ID, DAY, effects={}, clock=lambda: next(ticks))
    assert db.query(PlacementRun).count() == 0


# ── exploration, pure ────────────────────────────────────────────────────────

def _opts(*agencies):
    return [Option(a, 100.0, 1.0, 0, 0.0, False) for a in agencies]


def test_exploration_swaps_only_eligible_pairs_keeps_capacity_and_repeats_from_its_seed():
    options = {f"L{i}": _opts("A", "B", "C") for i in range(40)}
    options.update({f"S{i}": _opts("A") for i in range(10)})                  # single-option loans never move
    base = {f"L{i}": "ABC"[i % 3] for i in range(40)} | {f"S{i}": "A" for i in range(10)}
    counts = lambda a: {x: sum(1 for v in a.values() if v == x) for x in "ABC"}   # noqa: E731
    first, second = dict(base), dict(base)
    log1 = explore(first, options, 0.2, 20261015)
    log2 = explore(second, options, 0.2, 20261015)
    assert first == second and log1 == log2 and log1                          # reproducible, and it did explore
    assert counts(first) == counts(base)                                      # swaps keep capacity exact
    assert all(first[s] == "A" for s in (f"S{i}" for i in range(10)))
    for lid, keys in log1.items():
        assert first[lid] != keys["exploration_from_agency"] and first[lid] in {o.agency_id for o in options[lid]}
        assert keys["exploration_propensity"] == pytest.approx(1 / 3) and keys["exploration_seed"] == 20261015
    assert len(log1) <= round(40 * 0.2 / 2) * 2                              # initiators halved


def test_exploration_is_off_at_rate_zero():
    a = {"L1": "A", "L2": "B"}
    assert explore(a, {"L1": _opts("A", "B"), "L2": _opts("A", "B")}, 0.0, 1) == {} and a == {"L1": "A", "L2": "B"}
