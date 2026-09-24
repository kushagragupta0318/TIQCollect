"""
Rule-based Case Allocator (dummy — no ML model required).

Allocation logic:
  1. Load all UNASSIGNED cases, then order them by VISIT PRIORITY — recoverable
     value, urgency around the 90-day NPA line, and effort already spent
     (see ml/visit_priority.py)
  2. Drop cases the bank has told us not to contact — these are never assigned
  3. For each case, find agents whose territory matches the customer's city
  4. Among matching agents, keep only those ELIGIBLE for this customer
     (see ml/eligibility.py — currently the female-agent requirement)
  5. Rank the eligible ones by match quality (language, then specialisation),
     then by remaining capacity, then tier, then ranking_score
  6. If no city match, fall back to any eligible agent with remaining capacity
  7. Assign: set agent_id, status=ASSIGNED, allocation_date=today

Drop-in replacement for a future ML model — swap this class,
keep the same run() -> dict interface.

─── CHANGELOG (prototype → product) ────────────────────────────────────────
2026-08-19 — Steps 2, 4 and 5 are new. This allocator previously matched on
  territory and spare capacity alone, using none of the eight matching facts
  the schema carries. Two of them are legal obligations: do_not_contact
  customers were being handed to agents and visited, and requires_female_agent
  could not be honoured at all because no agent gender was recorded anywhere.
  The rules themselves live in ml/eligibility.py so the manager's reallocation
  plan can adopt the same ones instead of re-deriving them.

  run() now also reports what it refused to do — blocked and unallocated
  counts with reasons — because an allocator that silently drops cases is
  indistinguishable from one that had nothing to allocate.

2026-08-27 — THE QUEUE ORDER NOW COMES FROM ml/visit_priority.py, replacing
  `(PRIORITY_ORDER[case.priority], -target_amount)`.

  Why the old key was not good enough: Case.priority is computed once at case
  creation (seed_data.py:1577) and never recomputed, so it could not reflect
  effort already spent or a recovery estimate that did not exist when the case
  was created. `-target_amount` is the monthly ask, not what is recoverable.

  ONLY THE SEQUENCE CHANGED. Eligibility, territory matching and capacity are
  untouched, so the compliance guarantees hold by construction rather than by
  re-testing — do_not_contact and requires_female_agent are still applied at the
  same two points, on the same two pools.

  run() gained `explanations`: per case, the three named components and their
  points, in allocation order. The brief asks for a ranking "simple enough that
  a manager can explain to their team why one case sits above another", and a
  score without its components is not that.
─────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import TYPE_CHECKING

from app.ml.eligibility import (
    agent_block_reason,
    case_block_reason,
    match_score,
)
from app.ml.visit_priority import SCORE_VERSION
from app.models.agent import Agent, AgentStatus, AgentTier
from app.models.case import Case, CaseStatus, CasePriority
from app.models.customer import Customer
from app.models.loan import Loan
from app.services.visit_priority_service import score_cases, sort_key

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

PRIORITY_ORDER = {
    CasePriority.CRITICAL: 0,
    CasePriority.HIGH: 1,
    CasePriority.MEDIUM: 2,
    CasePriority.LOW: 3,
}

TIER_ORDER = {
    AgentTier.TIER_1: 0,
    AgentTier.TIER_2: 1,
    AgentTier.TIER_3: 2,
}


class CaseAllocator:
    def __init__(self, db: "Session") -> None:
        self.db = db
        self.today = date.today()
        # Customers and loans for the cases in play, fetched once. The previous
        # version issued one Customer query per case inside the assignment loop.
        self._customers: dict[str, Customer] = {}
        self._loans: dict[str, Loan] = {}
        # case_id -> visit-priority score dict, populated by _order_cases.
        self._scored: dict[str, dict] = {}

    def run(self) -> dict:
        unassigned = self._load_unassigned_cases()
        agents = self._load_active_agents()

        if not unassigned:
            return {"cases_assigned": 0, "cases_unallocated": 0, "reason": "no_unassigned_cases"}

        self._load_case_context(unassigned)
        # Ordering AFTER the context load — see _order_cases.
        unassigned = self._order_cases(unassigned)

        if not agents:
            return {"cases_assigned": 0, "cases_unallocated": len(unassigned), "reason": "no_active_agents"}

        # Track cases assigned to each agent today
        assigned_today: dict[str, int] = defaultdict(int)
        existing = (
            self.db.query(Case.agent_id)
            .filter(Case.allocation_date == self.today, Case.agent_id.isnot(None))
            .all()
        )
        for (aid,) in existing:
            assigned_today[aid] += 1

        # Group agents by territory (city)
        by_city: dict[str, list[Agent]] = defaultdict(list)
        for agent in agents:
            by_city[agent.territory.lower()].append(agent)

        assigned_count = 0
        unallocated_count = 0
        blocked: dict[str, int] = defaultdict(int)
        no_eligible_agent = 0
        # Why each case sat where it sat, in allocation order. An allocator whose
        # reasoning cannot be read back is indistinguishable from one that
        # ordered at random.
        explanations: list[dict] = []

        for case in unassigned:
            customer = self._customers.get(case.customer_id)

            # Step 2 — some cases must never be allocated, however much capacity
            # exists. Left UNASSIGNED rather than closed: the bank owns that
            # decision, and a status change here would be us making it for them.
            reason = case_block_reason(customer)
            if reason is not None:
                blocked[reason] += 1
                unallocated_count += 1
                continue

            agent = self._pick_agent(case, by_city, agents, assigned_today)
            if agent is None:
                unallocated_count += 1
                # Distinguish "everyone is full" from "nobody is permitted",
                # because the second is a staffing problem, not a capacity one.
                if not self._any_eligible(agents, customer):
                    no_eligible_agent += 1
                continue

            entry = (self._scored or {}).get(case.id) or {}

            case.agent_id = agent.id
            case.status = CaseStatus.ASSIGNED
            case.allocation_date = self.today
            # The visit-priority score, 0-100, HIGHER = work sooner.
            #
            # This column already carried three incompatible conventions across
            # four writers (this allocator's old 0-3 rank ordinal, the seed's
            # 0-100 higher-is-worse, the demo feed's 95/75/45/20 band map). It is
            # not being unified here — that is a separate migration — but what
            # this allocator writes is now at least one self-consistent scale,
            # and it is the same number the explanation below reports.
            case.allocation_score = float(entry.get("score") or 0.0)
            # A hand-weighted scorecard. Nothing here was learned from data.
            case.is_ml_allocated = False

            assigned_today[agent.id] += 1
            assigned_count += 1
            explanations.append({
                "rank": assigned_count,
                "case_number": case.case_number,
                "score": entry.get("score"),
                "components": entry.get("components", []),
                "reason": entry.get("reason"),
                "assigned_agent": agent.employee_code,
                "rate_as_of": entry.get("rate_as_of"),
            })

        self.db.commit()

        return {
            "cases_assigned": assigned_count,
            "cases_unallocated": unallocated_count,
            "agents_used": len([a for a, c in assigned_today.items() if c > 0]),
            "allocation_date": self.today.isoformat(),
            # "rule" is load-bearing: a transparent scorecard, not a model.
            "method": f"rule_based_v3_visit_priority_{SCORE_VERSION}",
            "ordering": "visit_priority",
            "explanations": explanations,
            # What the allocator refused to do, and why.
            "blocked_by_rule": dict(blocked),
            "no_eligible_agent": no_eligible_agent,
        }

    # -----------------------------------------------------------------
    def _load_unassigned_cases(self) -> list[Case]:
        """Query only. Ordering happens in _order_cases, AFTER the loans load.

        Sorting here is what the previous version did, and it is a trap: the
        visit-priority score reads loan.total_outstanding and loan.dpd, and
        self._loans is empty until _load_case_context has run. Sorting first
        produced a queue that looked ranked, was actually ordered by nothing,
        and reported itself as ranked — a wrong answer wearing a right one's
        clothes.
        """
        return self.db.query(Case).filter(Case.status == CaseStatus.UNASSIGNED).all()

    def _order_cases(self, cases: list[Case]) -> list[Case]:
        """Highest visit-priority first. MUST be called after _load_case_context.

        This is the whole behavioural change: everything downstream — territory
        matching, eligibility, capacity — is untouched, so any difference in
        outcome comes from sequence alone and the compliance rules cannot be
        affected by construction.
        """
        self._scored = score_cases(self.db, cases, loans=self._loans)
        return sorted(cases, key=sort_key(self._scored))

    def _load_active_agents(self) -> list[Agent]:
        return (
            self.db.query(Agent)
            .filter(Agent.status == AgentStatus.ON_DUTY)
            .order_by(Agent.ranking_score.desc())
            .all()
        )

    def _load_case_context(self, cases: list[Case]) -> None:
        """Bulk-load the customers and loans the eligibility rules need."""
        customer_ids = {c.customer_id for c in cases}
        loan_ids = {c.loan_id for c in cases if c.loan_id}
        if customer_ids:
            self._customers = {
                c.id: c for c in
                self.db.query(Customer).filter(Customer.id.in_(customer_ids)).all()
            }
        if loan_ids:
            self._loans = {
                l.id: l for l in
                self.db.query(Loan).filter(Loan.id.in_(loan_ids)).all()
            }

    def _any_eligible(self, agents: list[Agent], customer: Customer | None) -> bool:
        return any(agent_block_reason(a, customer) is None for a in agents)

    def _pick_agent(
        self,
        case: Case,
        by_city: dict[str, list[Agent]],
        all_agents: list[Agent],
        assigned_today: dict[str, int],
    ) -> Agent | None:
        customer = self._customers.get(case.customer_id)
        loan = self._loans.get(case.loan_id) if case.loan_id else None
        customer_city = (customer.city or "").lower() if customer else ""

        # Try city-matched agents first, then fall back to all agents
        for pool in [by_city.get(customer_city, []), all_agents]:
            agent = self._best_available(pool, assigned_today, customer, loan)
            if agent:
                return agent

        return None

    def _best_available(
        self,
        agents: list[Agent],
        assigned_today: dict[str, int],
        customer: Customer | None = None,
        loan: Loan | None = None,
    ) -> Agent | None:
        eligible = [
            a for a in agents
            if assigned_today[a.id] < a.max_cases_per_day
            and agent_block_reason(a, customer) is None
        ]
        if not eligible:
            return None

        # Match quality outranks spare capacity: capacity is already bounded by
        # max_cases_per_day, so ranking on fit first cannot overload anyone, but
        # ranking on capacity first would routinely send a customer an agent who
        # does not speak their language purely to level the day's workload.
        return sorted(
            eligible,
            key=lambda a: (
                -match_score(a, customer, loan),
                -(a.max_cases_per_day - assigned_today[a.id]),
                TIER_ORDER.get(a.tier, 9),
                -a.ranking_score,
            ),
        )[0]
