"""
Rule-based Case Allocator (dummy — no ML model required).

Allocation logic:
  1. Load all UNASSIGNED cases ordered by priority (CRITICAL first)
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
from app.models.agent import Agent, AgentStatus, AgentTier
from app.models.case import Case, CaseStatus, CasePriority
from app.models.customer import Customer
from app.models.loan import Loan

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
        self.today = date.today().isoformat()
        # Customers and loans for the cases in play, fetched once. The previous
        # version issued one Customer query per case inside the assignment loop.
        self._customers: dict[str, Customer] = {}
        self._loans: dict[str, Loan] = {}

    def run(self) -> dict:
        unassigned = self._load_unassigned_cases()
        agents = self._load_active_agents()

        if not unassigned:
            return {"cases_assigned": 0, "cases_unallocated": 0, "reason": "no_unassigned_cases"}

        self._load_case_context(unassigned)

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

            case.agent_id = agent.id
            case.status = CaseStatus.ASSIGNED
            case.allocation_date = self.today
            case.allocation_score = float(PRIORITY_ORDER.get(case.priority, 9))
            case.is_ml_allocated = False

            assigned_today[agent.id] += 1
            assigned_count += 1

        self.db.commit()

        return {
            "cases_assigned": assigned_count,
            "cases_unallocated": unallocated_count,
            "agents_used": len([a for a, c in assigned_today.items() if c > 0]),
            "allocation_date": self.today,
            "method": "rule_based_v2_eligibility",
            # What the allocator refused to do, and why.
            "blocked_by_rule": dict(blocked),
            "no_eligible_agent": no_eligible_agent,
        }

    # -----------------------------------------------------------------
    def _load_unassigned_cases(self) -> list[Case]:
        cases = self.db.query(Case).filter(Case.status == CaseStatus.UNASSIGNED).all()
        return sorted(
            cases,
            key=lambda c: (PRIORITY_ORDER.get(c.priority, 9), -c.target_amount),
        )

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
