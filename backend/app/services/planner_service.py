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
from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session, joinedload

from app.core.routing import optimize_route
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
        month_start = datetime(plan_date.year, plan_date.month, 1, tzinfo=timezone.utc)
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
                        PTP.committed_date > plan_date)
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

        # Score cases for visit priority
        scored_priorities = score_cases_priority(self.db, candidate_cases, today=date.today())

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
            global_allocator = GlobalAllocator(objective=objective, eb_adjuster=eb_adjuster)
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

            # Build route using OSRM + OR-Tools
            stops = [
                (float(c.customer.latitude), float(c.customer.longitude))
                for c in cases_for_agent if c.customer and c.customer.latitude and c.customer.longitude
            ]

            ordered_case_ids = [c.id for c in cases_for_agent]
            est_distance_km = 0.0
            est_duration_min = len(cases_for_agent) * 30  # fallback duration

            if stops and len(stops) == len(cases_for_agent):
                try:
                    ordered_indices = optimize_route(
                        case_coords=stops,
                        start_lat=ag.base_latitude,
                        start_lon=ag.base_longitude,
                    )
                    # Reorder case IDs based on optimal sequence
                    ordered_case_ids = [cases_for_agent[idx].id for idx in ordered_indices]
                    
                    # Compute realistic driving distance with road network factor
                    tot_dist = 0.0
                    curr_pos = (ag.base_latitude, ag.base_longitude)
                    for idx in ordered_indices:
                        nxt_pos = stops[idx]
                        tot_dist += self._calc_distance_km(curr_pos[0], curr_pos[1], nxt_pos[0], nxt_pos[1])
                        curr_pos = nxt_pos
                    est_distance_km = tot_dist * 1.15  # urban road network factor
                    est_duration_min = int(est_distance_km / 25 * 60) + len(stops) * 20
                except Exception as e:
                    logger.warning("Routing optimization fallback used", agent_id=ag.id, error=str(e))
                    est_distance_km = len(stops) * 3.5
                    est_duration_min = len(stops) * 25

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
                for c in cases_for_agent:
                    c.agent_id = ag.id
                    c.allocation_date = target_date.strftime("%Y-%m-%d")
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
            stamp = target_date.strftime("%Y-%m-%d")
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
        """Retrieve the latest allocation run for this manager and target date."""
        query = self.db.query(AllocationRun).options(
            joinedload(AllocationRun.decisions),
            joinedload(AllocationRun.beats),
        ).filter(
            AllocationRun.manager_user_id == self.manager_user_id,
        )
        if plan_date:
            query = query.filter(AllocationRun.plan_date == plan_date)

        return query.order_by(AllocationRun.created_at.desc()).first()

    def export_decisions_csv(self, run_id: str) -> str:
        """Export allocation decisions for a run to a CSV string."""
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
