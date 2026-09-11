"""
Smart Nightly Case Allocation & Historical Matching Engine
==========================================================
Encapsulates next-day field case allocation, historical agent competency matching,
route sequencing (OSRM + OR-Tools VRPTW), and explainable decision auditing.
"""
from __future__ import annotations

import csv
import io
import math
import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Sequence

import structlog
import zlib

from sqlalchemy import and_, func, or_, text
from sqlalchemy.orm import Session, joinedload

from app.core.config import settings as _settings
from app.core.routing import avg_visit_seconds, plan_route
from app.ml.eligibility import agent_block_reason, is_eligible, is_female
from app.ml.visit_priority import score as score_visit_priority
from app.models.agent import Agent, AgentSpecialization, AgentStatus, AgentTier
from app.models.allocation_decision import AllocationDecision, AllocationOutcome
from app.models.allocation_run import AllocationRun, AllocationRunStatus, AllocationStrategy
from app.models.beat import Beat, BeatStatus
from app.models.case import Case, CasePriority, CaseStatus, RESOLVED_STATUSES
from app.models.customer import Customer
from app.models.loan import Loan, LoanType
from app.models.payment import Payment, PaymentStatus
from app.models.ptp import PTP, PTPStatus
from app.models.visit import Visit, VisitOutcome
from app.services.visit_priority_service import score_cases as score_cases_priority

logger = structlog.get_logger("planner_service")


class PlanInProgressError(RuntimeError):
    """Another planning run holds this (manager, date). Retryable, not a bug.

    Distinct from ValueError so the API can answer 409 Conflict rather than 400
    Bad Request: the caller's request was fine, the timing was not.
    """

# Strategy weights for Smart Allocation (heavier weight on proximity to ensure 60-140 km daily routes)
W_AFFINITY = 0.30
W_PROXIMITY = 0.40
W_WORKLOAD = 0.15
W_SKILLS = 0.15
INCUMBENT_CONTINUITY_BONUS = 0.10


# A borrower may promise the same agent this many times before that pairing is
# treated as not working. Three, per the operating decision on 2026-09-02.
PTP_FATIGUE_THRESHOLD = 3

# Promises that did NOT turn into money. HONORED and PARTIALLY_HONORED are
# excluded deliberately: a promise that was kept is the process working, and
# counting it as fatigue would punish the agent who got the borrower to pay.
_FAILED_PTP_STATUSES = (PTPStatus.BROKEN, PTPStatus.EXPIRED, PTPStatus.RESCHEDULED)

# Every way a case can be held back rather than allocated. Kept in one place
# because the run's deferred_count is read straight onto the manager's dashboard,
# and a new outcome that is not listed here silently stops being counted.
_DEFERRED_OUTCOMES = frozenset({
    AllocationOutcome.DEFERRED.value,
    AllocationOutcome.DEFERRED_ROUTE_INFEASIBLE.value,
    AllocationOutcome.DEFERRED_PTP.value,
    AllocationOutcome.DEFERRED_VISIT_CAP.value,
})


def get_target_plan_date(reference_date: date | None = None) -> date:
    """Calculate the target execution date for next working day.
    
    Monday–Friday: Plans next day (Tuesday–Saturday).
    Saturday: Plans Monday (Sunday is a scheduled rest day).
    Sunday: Plans Monday.
    """
    ref = reference_date or date.today()
    weekday = ref.weekday()  # Monday is 0, Sunday is 6
    if weekday == 5:  # Saturday -> Monday (skip Sunday)
        return ref + timedelta(days=2)
    elif weekday == 6:  # Sunday -> Monday
        return ref + timedelta(days=1)
    else:  # Monday-Friday -> Next day
        return ref + timedelta(days=1)


class PlannerService:
    def __init__(self, db: Session, manager_user_id: str):
        self.db = db
        self.manager_user_id = manager_user_id

    def _ptp_fatigue_map(self, case_ids: list[str]) -> dict[str, set[str]]:
        """case_id -> agents this borrower has promised, and not paid, too often.

        Returned to GlobalAllocator, which drops those agents from that case's row
        in the cost matrix. Barring the AGENT and not the CASE is the whole point:
        the money is still owed and the borrower should still be visited, just by
        somebody else. Because only one column is removed, the bipartite solve
        picks the next best agent on its own — there is no reassignment pass and
        no ordering to get wrong.

        Counted per (case, agent), not per case: a borrower who has broken three
        promises to three different agents is a difficult borrower, not evidence
        that any one agent should be taken off them.
        """
        if not case_ids:
            return {}
        rows = (
            self.db.query(PTP.case_id, PTP.agent_id, func.count(PTP.id))
            .filter(
                PTP.case_id.in_(case_ids),
                PTP.status.in_(_FAILED_PTP_STATUSES),
            )
            .group_by(PTP.case_id, PTP.agent_id)
            .having(func.count(PTP.id) >= PTP_FATIGUE_THRESHOLD)
            .all()
        )
        out: dict[str, set[str]] = {}
        for case_id, agent_id, _n in rows:
            if agent_id:
                out.setdefault(case_id, set()).add(agent_id)
        return out

    def get_historical_competency_matrix(self, agent_ids: list[str], days_lookback: int = 90) -> dict[str, dict[str, Any]]:
        """Compute each agent's historical recovery rate per loan type and DPD band.
        
        Read-only aggregation over existing verified payments and completed visits.
        """
        if not agent_ids:
            return {}

        cutoff_date = datetime.now(timezone.utc) - timedelta(days=days_lookback)

        # 1. Payments by agent and loan_type
        payment_stats = self.db.query(
            Payment.agent_id,
            Loan.loan_type,
            func.sum(Payment.amount).label("total_collected"),
            func.count(Payment.id).label("payment_count"),
        ).join(Case, Case.id == Payment.case_id)\
         .join(Loan, Loan.id == Case.loan_id)\
         .filter(
             Payment.agent_id.in_(agent_ids),
             Payment.status == PaymentStatus.VERIFIED,
             Payment.created_at >= cutoff_date,
         )\
         .group_by(Payment.agent_id, Loan.loan_type).all()

        # 2. Target amounts chased by agent and loan_type
        target_stats = self.db.query(
            Case.agent_id,
            Loan.loan_type,
            func.sum(Case.target_amount).label("total_target"),
            func.count(Case.id).label("case_count"),
        ).join(Loan, Loan.id == Case.loan_id)\
         .filter(
             Case.agent_id.in_(agent_ids),
             Case.created_at >= cutoff_date,
         )\
         .group_by(Case.agent_id, Loan.loan_type).all()

        # Map targets for recovery % calculation
        targets_by_agent_loan = defaultdict(lambda: 1.0)
        for row in target_stats:
            if row.agent_id and row.loan_type:
                targets_by_agent_loan[(row.agent_id, row.loan_type.value)] = float(row.total_target or 1.0)

        # Calculate recovery rate per (agent, loan_type)
        matrix: dict[str, dict[str, Any]] = defaultdict(lambda: {
            "loan_type_recovery": {},
            "total_collected": 0.0,
            "overall_recovery_rate": 0.35,  # default baseline
        })

        for row in payment_stats:
            agent_id = row.agent_id
            lt_val = row.loan_type.value if hasattr(row.loan_type, "value") else str(row.loan_type)
            collected = float(row.total_collected or 0.0)
            target = targets_by_agent_loan[(agent_id, lt_val)]
            rate = min(1.0, collected / max(target, 1000.0))
            matrix[agent_id]["loan_type_recovery"][lt_val] = rate
            matrix[agent_id]["total_collected"] += collected

        return matrix

    def plan_next_day(
        self,
        plan_date: date | None = None,
        strategy: str = AllocationStrategy.SMART.value,
        objective: str = "BALANCED",
        simulate: bool = False,
        force_replan: bool = False,
    ) -> AllocationRun:
        """Main entry point: Generate tomorrow's case assignments and beat routes.
        
        Args:
            plan_date: Target planning date (defaults to next working day).
            strategy: 'SMART' or 'LEGACY'.
            objective: 'BALANCED', 'MAX_RECOVERY', or 'MIN_DISTANCE'.
            simulate: If True, returns the plan without committing to DB.
            force_replan: If True, replaces existing PLANNED beats for plan_date.
        """
        target_date = plan_date or get_target_plan_date()

        # 1. Fetch eligible on-duty agents under this manager
        agents = self.db.query(Agent).filter(
            Agent.manager_user_id == self.manager_user_id,
            Agent.status.in_([AgentStatus.ON_DUTY, AgentStatus.OFF_DUTY]),  # Exclude SUSPENDED / ON_LEAVE
        ).all()

        if not agents:
            # Create a zero-run record if no agents exist
            run = AllocationRun(
                id=str(uuid.uuid4()),
                manager_user_id=self.manager_user_id,
                plan_date=target_date,
                strategy=strategy,
                status=AllocationRunStatus.PLANNED.value if not simulate else "SIMULATED",
                total_cases_evaluated=0,
                total_cases_allocated=0,
                total_cases_deferred=0,
                total_cases_blocked=0,
                total_agents_planned=0,
                expected_recovery_total=0.0,
                summary_metadata={"message": "No active agents available for manager."},
            )
            if not simulate:
                self.db.add(run)
                self.db.commit()
            return run

        agent_ids = [a.id for a in agents]
        agent_by_id = {a.id: a for a in agents}

        # Serialise before touching any beat — see _acquire_plan_lock.
        if not simulate and not self._acquire_plan_lock(target_date):
            raise PlanInProgressError(
                f"A plan for {target_date} is already being generated for this "
                f"team. Wait for it to finish, then try again."
            )

        # 2. Check for existing PLANNED beats on target_date
        existing_beats = self.db.query(Beat).filter(
            Beat.agent_id.in_(agent_ids),
            Beat.beat_date == target_date,
        ).all()

        # If beats exist and not force_replan, do not overwrite in-progress/completed beats
        active_or_done = [b for b in existing_beats if b.status in (BeatStatus.IN_PROGRESS, BeatStatus.COMPLETED)]
        if active_or_done and not simulate:
            raise ValueError(f"Cannot replan: {len(active_or_done)} beat(s) on {target_date} are already IN_PROGRESS or COMPLETED.")

        # 3. Load Candidate Case Pool Scoped to Manager's Team
        # Unassigned cases + cases assigned to this manager's agents
        candidate_cases = self.db.query(Case).options(
            joinedload(Case.customer),
            joinedload(Case.loan),
        ).filter(
            or_(
                Case.agent_id.in_(agent_ids),
                Case.agent_id.is_(None),
            ),
            Case.status.notin_(list(RESOLVED_STATUSES)),
            Case.status != CaseStatus.PAID,
            Case.collected_amount < Case.target_amount,
        ).all()

        # ── Who is workable TOMORROW ─────────────────────────────────────────
        # 2026-09-02 — the filter above used to end with
        #   Case.visit_count < Case.max_visits_allowed
        # which counts visits over the case's ENTIRE LIFE. A borrower repaying
        # in five instalments needs five visits, so on the third one the case
        # left the pool and never came back: 104 open cases were sitting in that
        # state with Rs 26.1L still owed, 79 of them PARTIALLY_PAID — people who
        # were actively paying. Nothing recorded it and no screen listed them.
        #
        # The cap is a CONTACT-FREQUENCY control, not a lifetime budget, so it is
        # now counted within the calendar month and resets on the 1st.
        #
        # Two exclusions are applied here rather than in the allocator, because a
        # case that must not be visited tomorrow should never enter the matrix at
        # all — scoring it and then discarding it would spend a slot and muddy the
        # decision audit. Both are recorded as decisions so the manager sees WHY a
        # case is absent instead of it silently disappearing.
        # target_date, NOT the plan_date argument. They are equal for every
        # production caller — both pass a date — but plan_date is Optional and
        # defaults to None, so `plan_date.year` raised AttributeError on the
        # documented default. scripts/test_run_planner.py calls it exactly that
        # way. Reading the resolved value makes the signature honest and changes
        # nothing for any caller that already passed a date. Fixed 2026-09-06.
        month_start = datetime(target_date.year, target_date.month, 1, tzinfo=timezone.utc)
        case_ids = [c.id for c in candidate_cases]

        visits_this_month: dict[str, int] = {}
        if case_ids:
            visits_this_month = {
                cid: n for cid, n in self.db.query(Visit.case_id, func.count(Visit.id))
                .filter(Visit.case_id.in_(case_ids), Visit.check_in_time >= month_start)
                .group_by(Visit.case_id).all()
            }

        # An ACTIVE promise for a future date. The borrower has committed to pay
        # on a named day; arriving before it collects nothing and harasses someone
        # who is cooperating. On the promised day the case simply becomes eligible
        # again and goes to whichever agent is the best match then — which may be
        # the agent who took the promise, but is not forced to be.
        ptp_hold: dict[str, date] = {}
        if case_ids:
            for cid, due in (
                self.db.query(PTP.case_id, func.min(PTP.committed_date))
                .filter(PTP.case_id.in_(case_ids), PTP.status == PTPStatus.ACTIVE,
                        PTP.committed_date > target_date)
                .group_by(PTP.case_id).all()
            ):
                ptp_hold[cid] = due

        pool_decisions: list[AllocationDecision] = []
        workable: list[Case] = []
        for c in candidate_cases:
            due = ptp_hold.get(c.id)
            if due is not None:
                pool_decisions.append(AllocationDecision(
                    id=str(uuid.uuid4()), run_id="", case_id=c.id,
                    previous_agent_id=c.agent_id, allocated_agent_id=None,
                    outcome=AllocationOutcome.DEFERRED_PTP.value,
                    reason=f"Promise to pay due {due.isoformat()}; held until then.",
                    visit_priority_score=0.0, fit_score=0.0,
                    score_breakdown={"ptp_due": due.isoformat()},
                ))
                continue
            spent = visits_this_month.get(c.id, 0)
            allowed = int(c.max_visits_allowed or 3)
            if spent >= allowed:
                pool_decisions.append(AllocationDecision(
                    id=str(uuid.uuid4()), run_id="", case_id=c.id,
                    previous_agent_id=c.agent_id, allocated_agent_id=None,
                    outcome=AllocationOutcome.DEFERRED_VISIT_CAP.value,
                    reason=(f"{spent} of {allowed} visits already used this month; "
                            f"the budget resets on the 1st."),
                    visit_priority_score=0.0, fit_score=0.0,
                    score_breakdown={"visits_this_month": spent, "max_visits_allowed": allowed},
                ))
                continue
            workable.append(c)

        candidate_cases = workable

        # Scored for THE DAY THE BEAT WILL BE WORKED, not the day it is built.
        #
        # 2026-09-10 — this passed `date.today()` while the plan being built is
        # for `target_date`, normally tomorrow. The PTP hold rule twenty lines
        # above already uses `target_date` (`committed_date > target_date`), so
        # one method was answering "which day is this plan for?" two ways.
        #
        # The visible consequence is in the timing lift. `visit_priority_service`
        # derives `ptp_due_in_days = committed_date - today` and
        # `_ptp_follow_up` pays 8 points when that is < 1 and 5 otherwise, so:
        #
        #   promise due on the day the agent actually visits  ->  5 points
        #   promise due the day BEFORE, already overdue       ->  8 points
        #
        # exactly inverted. Every other component that reads the reference day
        # (urgency around the NPA line, effort, value) was one day early for the
        # same reason; this corrects the input, not any weight.
        #
        # NOT CHANGED HERE, deliberately, and each is its own decision: the
        # protection window (PTP_PROTECTION_DAYS = 1), the handling of a promise
        # already overdue (still filtered out by `today <= committed_date` in
        # visit_priority_service), the PTP weights, and the absence of any rule
        # returning a case to the agent who took the promise.
        scored_priorities = score_cases_priority(self.db, candidate_cases,
                                                 today=target_date)

        # Sort candidate cases by visit priority score (highest first)
        def _get_prio(c: Case) -> float:
            p_dict = scored_priorities.get(c.id, {})
            return float(p_dict.get("score", 0.0))

        sorted_cases = sorted(candidate_cases, key=_get_prio, reverse=True)

        # 4. Load Historical Competency Matrix & Empirical Bayes Adjuster
        history_matrix = self.get_historical_competency_matrix(agent_ids, days_lookback=90)
        from app.ml.empirical_bayes import EmpiricalBayesAgentAdjuster
        from app.ml.shadow_evaluator import ShadowModelEvaluator

        # as_of is mandatory on fit_from_db so that no caller can build a
        # historical feature by accident. Live allocation is the one place
        # where "now" is the right answer, and it says so explicitly.
        eb_adjuster = EmpiricalBayesAgentAdjuster().fit_from_db(
            self.db, as_of=date.today())
        shadow_evaluator = ShadowModelEvaluator()
        ptp_fatigue = self._ptp_fatigue_map([c.id for c in candidate_cases])

        # 5. Initialize Allocation State
        assigned_cases_by_agent: dict[str, list[Case]] = defaultdict(list)
        decisions: list[AllocationDecision] = []
        allocated_count = 0
        deferred_count = 0
        blocked_count = 0
        expected_recovery_sum = 0.0

        # Daily capacity tracking
        capacity_remaining: dict[str, int] = {
            a.id: max(1, a.max_cases_per_day or 12) for a in agents
        }

        # 6. Matching & Assignment
        prio_map = {c.id: _get_prio(c) for c in sorted_cases}
        shadow_telemetry = shadow_evaluator.evaluate_cohort(sorted_cases, prio_map)

        if strategy == AllocationStrategy.SMART.value:
            from app.services.global_allocator import GlobalAllocator
            ml_probs = self._ml_recovery_probabilities(sorted_cases)
            # Paired by construction: no probabilities means the original
            # transform, so a model outage cannot leave the allocator running a
            # configuration nobody measured.
            transform = (_settings.ALLOCATOR_VALUE_TRANSFORM if ml_probs
                         else "log_current")
            global_allocator = GlobalAllocator(
                objective=objective, eb_adjuster=eb_adjuster,
                ml_recovery_probability=ml_probs,
                use_ml_affinity=bool(ml_probs),
                value_transform=transform,
                exploration_rate=_settings.ALLOCATOR_EXPLORATION_RATE,
                # Seeded from the plan DATE, not from the clock: two runs for the
                # same target date explore identically, so a re-plan is
                # reproducible and the audit trail holds. A different date gets a
                # different draw, which is what makes the slice random over time.
                exploration_seed=int(target_date.strftime("%Y%m%d")),
            )
            assigned_cases_by_agent, decisions, expected_recovery_sum = global_allocator.allocate(
                cases=sorted_cases,
                agents=agents,
                history_matrix=history_matrix,
                prio_scores=prio_map,
                ptp_fatigue=ptp_fatigue,
            )
            allocated_count = sum(len(c_list) for c_list in assigned_cases_by_agent.values())
            deferred_count = sum(1 for d in decisions if d.outcome in _DEFERRED_OUTCOMES)
            blocked_count = sum(1 for d in decisions if d.outcome == AllocationOutcome.BLOCKED.value)
        else:
            # Legacy matching loop
            for case in sorted_cases:
                customer: Customer | None = case.customer
                loan: Loan | None = case.loan
                prio_score = _get_prio(case)

                if not customer:
                    deferred_count += 1
                    continue

                if getattr(customer, "do_not_contact", False):
                    blocked_count += 1
                    decisions.append(AllocationDecision(
                        id=str(uuid.uuid4()),
                        run_id="",
                        case_id=case.id,
                        previous_agent_id=case.agent_id,
                        allocated_agent_id=None,
                        outcome=AllocationOutcome.BLOCKED.value,
                        reason="Borrower is on the Do-Not-Contact (DNC) list.",
                        visit_priority_score=prio_score,
                        fit_score=0.0,
                        score_breakdown={"block_type": "DNC"},
                    ))
                    continue

                if getattr(customer, "is_hostile", False):
                    blocked_count += 1
                    decisions.append(AllocationDecision(
                        id=str(uuid.uuid4()),
                        run_id="",
                        case_id=case.id,
                        previous_agent_id=case.agent_id,
                        allocated_agent_id=None,
                        outcome=AllocationOutcome.BLOCKED.value,
                        reason="Withheld due to safety/hostility risk flag.",
                        visit_priority_score=prio_score,
                        fit_score=0.0,
                        score_breakdown={"block_type": "SAFETY_FLAG"},
                    ))
                    continue

                available_agents = [a for a in agents if capacity_remaining[a.id] > 0]
                if not available_agents:
                    deferred_count += 1
                    decisions.append(AllocationDecision(
                        id=str(uuid.uuid4()),
                        run_id="",
                        case_id=case.id,
                        previous_agent_id=case.agent_id,
                        allocated_agent_id=None,
                        outcome=AllocationOutcome.DEFERRED.value,
                        reason="All eligible agents have reached their maximum daily visit capacity.",
                        visit_priority_score=prio_score,
                        fit_score=0.0,
                        score_breakdown={"deferred_reason": "CAPACITY_FULL"},
                    ))
                    continue

                best_agent = available_agents[0]
                assigned_cases_by_agent[best_agent.id].append(case)
                capacity_remaining[best_agent.id] -= 1
                allocated_count += 1
                rec_rate = 0.30
                # Collectable balance, not lifetime target — matching the SMART path
                # in global_allocator, so both strategies report the same quantity and
                # a manager comparing them compares like with like.
                _remaining = max(0.0, float(case.target_amount or 0.0)
                                 - float(case.collected_amount or 0.0))
                expected_recovery_sum += _remaining * rec_rate
                decisions.append(AllocationDecision(
                    id=str(uuid.uuid4()),
                    run_id="",
                    case_id=case.id,
                    previous_agent_id=case.agent_id,
                    allocated_agent_id=best_agent.id,
                    outcome=AllocationOutcome.ALLOCATED.value,
                    reason=f"Legacy round-robin allocation to {best_agent.employee_code}.",
                    visit_priority_score=prio_score,
                    fit_score=0.5,
                    score_breakdown={"strategy": "LEGACY"},
                ))

        # Pool-level holds belong to the run whichever strategy produced it: SMART
        # replaces `decisions` wholesale with the allocator's list, the legacy loop
        # appends to its own. Folding them in once, here, is the only version that
        # cannot double-count or silently drop them — an earlier attempt inferred
        # "already added?" from the outcomes present, which happened to work but
        # would have quietly broken the day a pool hold arrived from somewhere else.
        # Decision linkage: stamp each prediction with the agent the case was
        # actually given. Written here rather than at scoring time because the
        # assignment does not exist until the solve returns, and a prediction
        # with no decision attached cannot be evaluated per agent once the
        # outcome matures.
        _rows = getattr(self, "_ml_prediction_rows", None)
        if _rows:
            # `assigned_cases_by_agent` is what allocate() RETURNED, so it is
            # already post-exploration: a swapped case names the agent that
            # actually got it, not the one the solve first picked.
            _agent_of = {c.id: aid
                         for aid, cs in assigned_cases_by_agent.items()
                         for c in cs}
            # Ids are Python-side defaults, applied at INSERT. Without this
            # flush every `_row.id` below is None and the lineage column would
            # be silently NULL on every decision.
            self.db.flush()
            _pred_of = {_row.case_id: _row.id for _row in _rows if _row.case_id}
            _linked = 0
            for _row in _rows:
                _aid = _agent_of.get(_row.case_id)
                if _aid:
                    _row.agent_id = _aid
                    _linked += 1
            # THE LINEAGE, written on the decision. Stamped for every outcome,
            # not only ALLOCATED: a deferred or blocked case was still scored,
            # and "the model said X and we held the case anyway" is exactly the
            # kind of decision somebody will want to audit later.
            _stamped = 0
            for _d in decisions:
                _pid = _pred_of.get(_d.case_id)
                if _pid:
                    _d.model_prediction_id = _pid
                    _stamped += 1
            logger.info("allocation.ml_predictions_linked",
                        rows=len(_rows), linked=_linked,
                        decisions_stamped=_stamped)

        decisions.extend(pool_decisions)
        deferred_count += len(pool_decisions)

        # 7. Route Optimization & Beat Creation
        run_id = str(uuid.uuid4())
        created_beats: list[Beat] = []

        # If not simulated, clean up existing PLANNED beats on target date
        if not simulate and force_replan:
            for b in existing_beats:
                if b.status == BeatStatus.PLANNED:
                    self.db.delete(b)
            self.db.flush()

        for ag in agents:
            cases_for_agent = assigned_cases_by_agent.get(ag.id, [])
            if not cases_for_agent:
                continue

            # ── Route the day, and KEEP WHAT THE OPTIMISER RETURNED ─────
            #
            # 2026-09-08. This block used to call optimize_route(), which had
            # just paid for an N x N OSRM road matrix, take only the ordering
            # out of it, and then recompute the distance itself as
            # Haversine x 1.15 and the duration as km/25 + 20 minutes a stop.
            # The road matrix was discarded every single night, so THE ETA
            # SHOWN TO EVERY AGENT AND MANAGER WAS CROW-FLIES even when OSRM
            # answered perfectly. There were four different per-visit constants
            # involved — 15 in settings (which is what the route was actually
            # made feasible against), and 30, 20 and 25 hardcoded here.
            #
            # plan_route() returns the legs it used. Nothing is recomputed.
            stops = [
                (float(c.customer.latitude), float(c.customer.longitude))
                for c in cases_for_agent
                if c.customer and c.customer.latitude and c.customer.longitude
            ]

            ordered_case_ids = [c.id for c in cases_for_agent]
            est_distance_km = 0.0
            # Fallback duration is service time alone — honest for a day whose
            # travel could not be computed, rather than a third invented number.
            est_duration_min = int(len(cases_for_agent) * avg_visit_seconds() / 60)
            route_geometry = None
            route_legs = None
            route_source = None

            if stops and len(stops) == len(cases_for_agent):
                try:
                    windows = self._contact_windows(cases_for_agent)
                    route = plan_route(
                        case_coords=stops,
                        start_lat=ag.base_latitude,
                        start_lon=ag.base_longitude,
                        time_windows=windows,
                        with_geometry=True,
                    )
                    ordered_case_ids = [cases_for_agent[i].id for i in route.order]
                    est_distance_km = route.km
                    est_duration_min = route.minutes
                    route_geometry = route.geometry
                    route_source = route.source
                    route_legs = [
                        {"from": leg.from_stop, "to": leg.to_stop,
                         "seconds": leg.seconds, "metres": leg.metres,
                         "case_id": cases_for_agent[leg.to_stop].id}
                        for leg in route.legs
                    ]
                except Exception as e:
                    logger.warning("Routing optimization fallback used",
                                   agent_id=ag.id, error=str(e))

            total_target = sum(float(c.target_amount or 0.0) for c in cases_for_agent)

            beat = Beat(
                id=str(uuid.uuid4()),
                agent_id=ag.id,
                beat_date=target_date,
                beat_number=f"BEAT-{target_date.strftime('%Y%m%d')}-{ag.employee_code}",
                ordered_case_ids=ordered_case_ids,
                total_cases=len(ordered_case_ids),
                estimated_distance_km=round(est_distance_km, 2),
                estimated_duration_minutes=int(est_duration_min),
                route_geometry=route_geometry,
                route_legs=route_legs,
                route_source=route_source,
                total_target_amount=round(total_target, 2),
                status=BeatStatus.PLANNED,
                cases_completed=0,
                amount_collected=0.0,
                is_ml_generated=True,
                ml_model_version=f"planner-{strategy.lower()}-v1.0",
                allocation_run_id=run_id,
            )
            created_beats.append(beat)

            # Update case assignments if not simulation
            if not simulate:
                # THE DAY THE ASSIGNMENT WAS MADE, NOT THE DAY IT IS FOR.
                #
                # 2026-09-10 — this stamped `target_date`, which is TOMORROW,
                # and that put a future date on a case a manager is looking at
                # today. It also gave the column two meanings at once: the
                # stale-clearing block below resets DROPPED cases to "the day the
                # case was last worked" and says so in as many words, while this
                # line was writing "the day it is next planned for". Which of the
                # two a given row meant depended on whether it survived the most
                # recent plan.
                #
                # Measured that day on one manager's book: 229 of 877 cases
                # carried tomorrow's date, and 44 of the 45 visited that morning
                # were among them — a case an agent had already worked claiming
                # it belonged to a beat that had not happened yet.
                #
                # `ml/allocator.py` — the other implementation of this same step
                # — has always written `self.today`, and its docstring says
                # "Assign: set agent_id, status=ASSIGNED, allocation_date=today".
                # This is that rule, applied in one more place rather than
                # invented here.
                #
                # NOTHING IS LOST. The schedule lives on the Beat, which is where
                # it belongs: `beat_date` plus `ordered_case_ids` say exactly
                # which cases are worked when, and the agent's day is built from
                # those, never from this column.
                assigned_on = date.today().strftime("%Y-%m-%d")
                for c in cases_for_agent:
                    c.agent_id = ag.id
                    c.allocation_date = assigned_on
                    c.status = CaseStatus.ASSIGNED if c.status == CaseStatus.UNASSIGNED else c.status
                self.db.add(beat)

        # A case that is no longer in this plan must stop claiming its date.
        #
        # 2026-09-02 — allocation_date was stamped on allocation and never
        # cleared, so every re-plan left the cases it dropped still carrying the
        # plan date. The stamps accumulated across runs: 602 cases claimed
        # 2026-09-03 while only 230 were in a 3-Sep beat, and 50 of the strays
        # were already settled. On screen that read as a case resolved on 2 Sep
        # displaying 3 Sep, and 94 cases whose allocation_date preceded their own
        # resolved_at.
        #
        # Cleared back to the day the case was last actually worked, so the date
        # column keeps meaning "when this was last touched" — which is what sorts
        # Case Management, and what leaves genuinely new, never-visited cases at
        # the top with no visited or resolved tag against them.
        if not simulate:
            allocated_ids = {c.id for lst in assigned_cases_by_agent.values() for c in lst}
            # Must match what the loop above actually wrote, or the sweep looks
            # for a stamp nobody made and silently clears nothing.
            stamp = date.today().strftime("%Y-%m-%d")
            stale = [
                c for c in self.db.query(Case)
                .filter(Case.allocation_date == stamp).all()
                if c.id not in allocated_ids
            ]
            if stale:
                last_visit = dict(
                    self.db.query(Visit.case_id, func.max(Visit.check_in_time))
                    .filter(Visit.case_id.in_([c.id for c in stale]))
                    .group_by(Visit.case_id).all()
                )
                for c in stale:
                    when = last_visit.get(c.id)
                    if when is not None:
                        c.allocation_date = when.date().strftime("%Y-%m-%d")

        # 8. Create and Persist AllocationRun
        run = AllocationRun(
            id=run_id,
            manager_user_id=self.manager_user_id,
            plan_date=target_date,
            strategy=strategy,
            status=AllocationRunStatus.PLANNED.value if not simulate else "SIMULATED",
            total_cases_evaluated=len(sorted_cases),
            total_cases_allocated=allocated_count,
            total_cases_deferred=deferred_count,
            total_cases_blocked=blocked_count,
            total_agents_planned=len(created_beats),
            expected_recovery_total=round(expected_recovery_sum, 2),
            summary_metadata={
                "strategy": strategy,
                "objective": objective,
                "model_version": f"scorecard-allocator-{strategy.lower()}-v1.2",
                "is_simulated": simulate,
                "agents_count": len(agents),
                "allocated_cases_count": allocated_count,
                "shadow_evaluation": shadow_telemetry,
            },
        )

        for d in decisions:
            d.run_id = run_id
            if not simulate:
                self.db.add(d)

        if not simulate:
            self.db.add(run)
            self.db.commit()

        # Attach in-memory relations for caller
        run.decisions = decisions
        run.beats = created_beats
        return run

    def rollback_plan(self, run_id: str) -> bool:
        """Roll back an untouched future PLANNED allocation run.
        
        Guarantees:
        - Never mutates IN_PROGRESS or COMPLETED beats.
        - Marks run as ROLLED_BACK.
        """
        run = self.db.query(AllocationRun).filter(
            AllocationRun.id == run_id,
            AllocationRun.manager_user_id == self.manager_user_id,
        ).first()

        if not run:
            raise ValueError(f"Allocation run {run_id} not found.")

        if run.status == AllocationRunStatus.ROLLED_BACK.value:
            return True

        # Check associated beats
        beats = self.db.query(Beat).filter(Beat.allocation_run_id == run.id).all()
        for b in beats:
            if b.status in (BeatStatus.IN_PROGRESS, BeatStatus.COMPLETED):
                raise ValueError(f"Cannot rollback: Beat {b.beat_number} is already {b.status.value}.")

        # Delete future PLANNED beats
        for b in beats:
            if b.status == BeatStatus.PLANNED:
                self.db.delete(b)

        run.status = AllocationRunStatus.ROLLED_BACK.value
        self.db.commit()
        return True

    def get_latest_plan(self, plan_date: date | None = None) -> AllocationRun | None:
        """The latest SUCCESSFUL allocation run for this manager and date.

        FAILED runs are excluded deliberately. This method feeds the manager's
        plan view, and a failure row carries zeroes for every count — surfaced
        here it would render as a plan that legitimately found no work, which
        is the opposite of what happened. The caller asks for the failure
        separately via get_last_failure(); an absent plan plus a visible
        failure is honest, a zeroed plan is not."""
        query = self.db.query(AllocationRun).options(
            joinedload(AllocationRun.decisions),
            joinedload(AllocationRun.beats),
        ).filter(
            AllocationRun.manager_user_id == self.manager_user_id,
            AllocationRun.status != AllocationRunStatus.FAILED.value,
        )
        if plan_date:
            query = query.filter(AllocationRun.plan_date == plan_date)

        return query.order_by(AllocationRun.created_at.desc()).first()

    def get_last_failure(self, plan_date: date | None = None) -> AllocationRun | None:
        """The most recent FAILED run, if the last attempt did not complete.

        Returns None when a successful run for the same date came AFTER the
        failure — a manager who re-planned by hand has already resolved it, and
        showing a stale error would send them chasing something fixed."""
        query = self.db.query(AllocationRun).filter(
            AllocationRun.manager_user_id == self.manager_user_id,
            AllocationRun.status == AllocationRunStatus.FAILED.value,
        )
        if plan_date:
            query = query.filter(AllocationRun.plan_date == plan_date)
        failure = query.order_by(AllocationRun.created_at.desc()).first()
        if failure is None:
            return None
        latest_ok = self.get_latest_plan(plan_date=plan_date)
        if latest_ok is not None and latest_ok.created_at > failure.created_at:
            return None
        return failure

    def export_decisions_csv(self, run_id: str) -> str:
        """Export allocation decisions for a run to a CSV string.

        Raises ValueError when the run is not this manager's, which the endpoint
        turns into a 404.

        2026-09-06 — this filtered on run_id ALONE. PlannerService is constructed
        with a manager_user_id and every other method uses it; this one accepted
        it and never read it, so any manager could export another agency's entire
        decision audit — case numbers, borrower-facing reasons and agent codes —
        by supplying a run id.

        It survived the structural tenancy sweep in test_manager_endpoints.py
        because that sweep is TEXTUAL over the endpoint body, and the endpoint
        does mention current_user.id: it passes it to this constructor. The sweep
        cannot follow the call, so scoping has to hold HERE. Fixing it in the
        endpoint would have satisfied the test and left the service exportable by
        anyone who calls it directly — including the next endpoint that does.

        404, never 403: a 403 confirms the run exists, which turns this into an
        enumeration oracle for another agency's allocation history. Same rule as
        _require_own_agent in endpoints/manager.py.
        """
        owns_run = (
            self.db.query(AllocationRun.id)
            .filter(AllocationRun.id == run_id,
                    AllocationRun.manager_user_id == self.manager_user_id)
            .first()
        )
        if not owns_run:
            raise ValueError(f"Allocation run {run_id} not found.")

        decisions = self.db.query(AllocationDecision).options(
            joinedload(AllocationDecision.case),
            joinedload(AllocationDecision.allocated_agent),
        ).filter(AllocationDecision.run_id == run_id).all()

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "decision_id", "case_number", "outcome", "allocated_agent_code",
            "visit_priority_score", "fit_score", "reason", "created_at"
        ])

        for d in decisions:
            case_num = d.case.case_number if d.case else ""
            agent_code = d.allocated_agent.employee_code if d.allocated_agent else ""
            writer.writerow([
                d.id, case_num, d.outcome, agent_code,
                d.visit_priority_score, d.fit_score, d.reason,
                d.created_at.isoformat() if d.created_at else ""
            ])

        return output.getvalue()




    # ── Concurrency guard ───────────────────────────────────────────────────
    def _acquire_plan_lock(self, target_date) -> bool:
        """Serialise planning per (manager, date). True if we hold the lock.

        WHY. Two planning runs for the same manager and date both delete the
        PLANNED beats and then both insert, and the second one violates the
        UNIQUE index on (agent_id, beat_date) — surfacing as a 500 and a
        "Failed to generate plan" toast, with the run rolled back and nothing to
        show for it. Observed live: a run at 11:25:15 succeeded and a second at
        11:27:32 died on `duplicate key value violates unique constraint
        "ix_beat_agent_date"`.

        It is not a rare race. A double-click on "Re-Plan & Sequence" fires two
        POSTs, and the 20:00 nightly task can overlap a manual re-plan on the
        same date.

        A TRANSACTION-LEVEL advisory lock, so it is released automatically on
        commit or rollback — a session-level lock leaks on a crashed worker and
        would block every subsequent plan until the connection is reaped.
        Postgres only; other dialects (SQLite, in the tests) return True and
        rely on the IntegrityError path below, which is correct because they do
        not have concurrent writers.
        """
        bind = self.db.get_bind()
        if bind is None or bind.dialect.name != "postgresql":
            return True
        # Two 32-bit keys rather than one 64-bit hash: the pair is readable in
        # pg_locks, which matters when someone is trying to work out why a plan
        # is blocked.
        key1 = zlib.crc32((self.manager_user_id or "global").encode()) % (2 ** 31)
        key2 = int(target_date.strftime("%Y%m%d"))
        got = self.db.execute(
            text("SELECT pg_try_advisory_xact_lock(:k1, :k2)"),
            {"k1": key1, "k2": key2},
        ).scalar()
        return bool(got)

    # ── Trained-model inputs for the allocator ──────────────────────────────
    def _ml_recovery_probabilities(self, cases) -> dict[str, float]:
        """P(material payment) per case, from recovery_risk. Empty when off.

        THE PROBABILITY AND THE VALUE TRANSFORM MOVE TOGETHER, and that pairing
        is enforced here rather than left to two settings agreeing. The measured
        change is `calibrated probability + log_rescaled`. The other two corners
        of that square were never measured, and one of them is actively bad:

          * calibrated probability + log_current  -> -10.1% realised recovery
            (8 of 8 seeds), because a correct probability is anti-correlated
            with balance and the old scale compresses the result until proximity
            outvotes it 8:1;
          * flat 0.43 probability + log_rescaled  -> never evaluated at all.

        So when this returns nothing, plan_next_day also keeps the original
        transform. One switch, one measured configuration.
        """
        self._ml_prediction_rows = []
        if not _settings.ML_SCORING_ENABLED:
            return {}
        try:
            from app.services.ml_scoring_service import MLScoringService

            svc = MLScoringService(self.db)
            # score_cases_and_log, NOT score_many: the latter returns numbers and
            # records nothing, which left model_predictions at 0 rows while the
            # model was driving allocation. The rows are stashed so the allocated
            # agent can be attached once the solve is done.
            out, rows = svc.score_cases_and_log(cases, model="recovery_risk")
            self._ml_prediction_rows = rows
            logger.info("allocation.ml_scored", cases=len(cases),
                        scored=len(out), logged=len(rows))
            return out
        except Exception as exc:
            # A model failure must not take the nightly allocation down with it.
            # Returning nothing degrades to the pre-2026-09-08 behaviour exactly,
            # because the transform is paired to this result.
            # LOGGED AT ERROR, NOT WARNING, and with the type and traceback.
            # This exact path swallowed a TypeError for a full cycle —
            # `disbursement_date` is String(10) and the adapter treated it as a
            # date — while the allocator quietly ran with no model at all and
            # every health surface reported fine.
            logger.error("allocation.ml_scoring_failed", error=str(exc),
                         error_type=type(exc).__name__, exc_info=True)
            self._ml_prediction_rows = []
            return {}

    # ── Legal and preferred contact windows ─────────────────────────────────
    def _contact_windows(self, cases) -> list[tuple[int, int] | None]:
        """Per-stop time windows, in seconds from the start of the working day.

        WHY THE NIGHTLY PLAN NEVER HAD THESE. core/routing has supported VRPTW
        since 2026-07-13, but only the agent's on-demand re-optimise ever passed
        windows; the nightly build passed None. So the plan could hand an agent
        a day whose later stops fall outside RBI contact hours — a route that is
        not merely inefficient but illegal to execute, and the compliance rules
        would then block the visit the planner had just scheduled.

        Two sources, narrowest wins:
          * RBI contact hours (settings.CONTACT_HOUR_START/END), which bound
            every stop;
          * the borrower's own preferred_contact_start/end where recorded.

        Windows are SOFT: solve_tsp retries without them if the set is
        infeasible, because an agent with no beat is worse than an agent with an
        imperfect one.
        """
        day_start_h = _settings.CONTACT_HOUR_START
        day_end_h = _settings.CONTACT_HOUR_END
        span_end = (day_end_h - day_start_h) * 3600

        windows: list[tuple[int, int] | None] = []
        for c in cases:
            lo, hi = 0, span_end
            cust = getattr(c, "customer", None)
            start = getattr(cust, "preferred_contact_start", None) if cust else None
            end = getattr(cust, "preferred_contact_end", None) if cust else None
            if start is not None:
                lo = max(lo, (getattr(start, "hour", day_start_h) - day_start_h) * 3600)
            if end is not None:
                hi = min(hi, (getattr(end, "hour", day_end_h) - day_start_h) * 3600)
            # A preference that inverts the window is data, not a constraint —
            # fall back to the legal bounds rather than making the solve
            # infeasible for the whole beat.
            windows.append((lo, hi) if lo < hi else (0, span_end))
        return windows

    @staticmethod
    def _calc_distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        """Haversine distance in km."""
        r = 6371.0  # Earth radius in km
        phi1, phi2 = math.radians(lat1), math.radians(lat2)
        dphi = math.radians(lat2 - lat1)
        dlambda = math.radians(lon2 - lon1)
        a = math.sin(dphi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2
        c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
        return r * c
