"""
Rule-based Case Allocator (dummy — no ML model required).

Allocation logic:
  1. Load all UNASSIGNED cases ordered by priority (CRITICAL first)
  2. For each case, find agents whose territory matches the customer's city
  3. Among matching agents, pick the one with most remaining capacity
     (tiebreak: highest ranking_score, then TIER_1 > TIER_2 > TIER_3)
  4. If no city match, fall back to any agent with remaining capacity
  5. Assign: set agent_id, status=ASSIGNED, allocation_date=today

Drop-in replacement for a future ML model — swap this class,
keep the same run() -> dict interface.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import TYPE_CHECKING

from app.models.agent import Agent, AgentStatus, AgentTier
from app.models.case import Case, CaseStatus, CasePriority
from app.models.customer import Customer

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

    def run(self) -> dict:
        unassigned = self._load_unassigned_cases()
        agents = self._load_active_agents()

        if not unassigned:
            return {"cases_assigned": 0, "cases_unallocated": 0, "reason": "no_unassigned_cases"}

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

        for case in unassigned:
            agent = self._pick_agent(case, by_city, agents, assigned_today)
            if agent is None:
                unallocated_count += 1
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
            "method": "rule_based_v1",
        }

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

    def _pick_agent(
        self,
        case: Case,
        by_city: dict[str, list[Agent]],
        all_agents: list[Agent],
        assigned_today: dict[str, int],
    ) -> Agent | None:
        customer = self.db.query(Customer).filter(Customer.id == case.customer_id).first()
        customer_city = (customer.city or "").lower() if customer else ""

        # Try city-matched agents first, then fall back to all agents
        for pool in [by_city.get(customer_city, []), all_agents]:
            agent = self._best_available(pool, assigned_today)
            if agent:
                return agent

        return None

    def _best_available(
        self, agents: list[Agent], assigned_today: dict[str, int]
    ) -> Agent | None:
        eligible = [a for a in agents if assigned_today[a.id] < a.max_cases_per_day]
        if not eligible:
            return None

        return sorted(
            eligible,
            key=lambda a: (
                -(a.max_cases_per_day - assigned_today[a.id]),
                TIER_ORDER.get(a.tier, 9),
                -a.ranking_score,
            ),
        )[0]
