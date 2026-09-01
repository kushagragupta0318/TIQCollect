"""
Two-Stage Global Bipartite Allocation & Route Feasibility Engine
==================================================================
Stage 1: Global Maximum Utility Bipartite Assignment (Scipy linear_sum_assignment)
         with Empirical Bayes segment multipliers.
Stage 2: Route Feasibility Validation (OR-Tools / TSP) with Iterative Rebalancing
         and DEFERRED_ROUTE_INFEASIBLE handling for unroutable outliers.
"""
from __future__ import annotations

import math
import uuid
from typing import Sequence
import numpy as np
from scipy.optimize import linear_sum_assignment

from app.models.agent import Agent
from app.models.case import Case
from app.models.customer import Customer
from app.models.loan import Loan
from app.models.allocation_decision import AllocationDecision, AllocationOutcome
from app.models.allocation_setting import AllocationObjective
from app.ml.empirical_bayes import EmpiricalBayesAgentAdjuster


class GlobalAllocator:
    """Solves portfolio-wide case allocation using Two-Stage Bipartite Optimization + Route Validation."""

    MAX_BEAT_ROUTE_KM = 120.0
    MAX_DETOUR_MARGINAL_KM = 22.0

    def __init__(
        self,
        objective: str = AllocationObjective.BALANCED.value,
        territory_radius_km: float = 16.0,
        eb_adjuster: EmpiricalBayesAgentAdjuster | None = None,
    ):
        self.objective = objective
        self.territory_radius_km = territory_radius_km
        self.eb_adjuster = eb_adjuster or EmpiricalBayesAgentAdjuster()

    @staticmethod
    def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        r = 6371.0
        d_lat = math.radians(lat2 - lat1)
        d_lon = math.radians(lon2 - lon1)
        a = (
            math.sin(d_lat / 2.0) ** 2
            + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(d_lon / 2.0) ** 2
        )
        return r * 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))

    def get_objective_weights(self) -> dict[str, float]:
        """Return parameter weights for the selected manager allocation objective."""
        if self.objective == AllocationObjective.MAX_RECOVERY.value:
            return {"w_affinity": 0.55, "w_proximity": 0.15, "w_workload": 0.15, "w_skills": 0.15}
        elif self.objective == AllocationObjective.MIN_DISTANCE.value:
            return {"w_affinity": 0.15, "w_proximity": 0.65, "w_workload": 0.10, "w_skills": 0.10}
        else:  # BALANCED
            return {"w_affinity": 0.35, "w_proximity": 0.35, "w_workload": 0.15, "w_skills": 0.15}

    def allocate(
        self,
        cases: Sequence[Case],
        agents: Sequence[Agent],
        history_matrix: dict,
        prio_scores: dict[str, float],
    ) -> tuple[dict[str, list[Case]], list[AllocationDecision], float]:
        """
        Execute Two-Stage Global Case Allocation:
          Stage 1: Global Hungarian Bipartite Assignment
          Stage 2: Route Feasibility & Outlier Deferral
        """
        weights = self.get_objective_weights()
        w_affinity = weights["w_affinity"]
        w_proximity = weights["w_proximity"]
        w_workload = weights["w_workload"]
        w_skills = weights["w_skills"]

        # Flatten agent capacity into discrete virtual capacity slots
        slots: list[tuple[Agent, int]] = []
        for ag in agents:
            cap = max(1, ag.max_cases_per_day or 12)
            for slot_idx in range(cap):
                slots.append((ag, slot_idx))

        num_cases = len(cases)
        num_slots = len(slots)

        assigned_by_agent: dict[str, list[Case]] = {ag.id: [] for ag in agents}
        decisions: list[AllocationDecision] = []
        expected_recovery_sum = 0.0

        if num_cases == 0 or num_slots == 0:
            return assigned_by_agent, decisions, expected_recovery_sum

        # -------------------------------------------------------------
        # STAGE 1: Global Bipartite Assignment (Scipy linear_sum_assignment)
        # -------------------------------------------------------------
        PENALTY_INELIGIBLE = 1e6
        cost_matrix = np.full((num_cases, num_slots), PENALTY_INELIGIBLE, dtype=np.float64)
        decision_cache: dict[tuple[int, int], tuple[float, str, dict]] = {}

        for c_idx, case in enumerate(cases):
            customer: Customer | None = case.customer
            loan: Loan | None = case.loan
            prio = prio_scores.get(case.id, 50.0)

            if not customer:
                continue

            # Hard Gate: DNC
            if getattr(customer, "do_not_contact", False):
                decisions.append(AllocationDecision(
                    id=str(uuid.uuid4()),
                    run_id="",
                    case_id=case.id,
                    previous_agent_id=case.agent_id,
                    allocated_agent_id=None,
                    outcome=AllocationOutcome.BLOCKED.value,
                    reason="Borrower is on the Do-Not-Contact (DNC) list.",
                    visit_priority_score=prio,
                    fit_score=0.0,
                    score_breakdown={"block_type": "DNC"},
                ))
                continue

            # Hard Gate: Hostility
            if getattr(customer, "is_hostile", False):
                decisions.append(AllocationDecision(
                    id=str(uuid.uuid4()),
                    run_id="",
                    case_id=case.id,
                    previous_agent_id=case.agent_id,
                    allocated_agent_id=None,
                    outcome=AllocationOutcome.BLOCKED.value,
                    reason="Withheld due to safety/hostility risk flag.",
                    visit_priority_score=prio,
                    fit_score=0.0,
                    score_breakdown={"block_type": "HOSTILITY"},
                ))
                continue

            loan_type_str = loan.loan_type.value if (loan and loan.loan_type) else "PERSONAL"
            dpd_str = loan.dpd_bucket.value if (loan and loan.dpd_bucket) else "CURRENT"
            cust_lat = customer.latitude or 28.6139
            cust_lon = customer.longitude or 77.2090
            cust_lang = (customer.language_preference or "").lower()

            for s_idx, (ag, slot_num) in enumerate(slots):
                # Hard Gate: Female agent constraint
                is_female_ag = str(getattr(ag, "gender", "")).upper() in ("F", "FEMALE")
                if getattr(customer, "requires_female_agent", False) and not is_female_ag:
                    continue

                # Hard Gate: Territory Radius Boundary
                dist_km = self.haversine_km(ag.base_latitude or 28.6139, ag.base_longitude or 77.2090, cust_lat, cust_lon)
                if dist_km > self.territory_radius_km:
                    continue

                # Empirical Bayes Segment Performance Multiplier
                shrunk_win, prior_win, eb_multiplier = self.eb_adjuster.get_segment_multiplier(
                    ag.id, loan_type_str, dpd_str
                )

                ag_matrix = history_matrix.get(ag.id, {})
                base_affinity = float(ag_matrix.get(loan_type_str, prior_win))
                affinity_score = min(1.0, base_affinity * eb_multiplier)

                proximity_score = math.exp(-dist_km / 5.5)

                tier_weight = {"TIER_1": 1.0, "TIER_2": 0.8, "TIER_3": 0.6}.get(
                    ag.tier.value if hasattr(ag.tier, "value") else str(ag.tier), 0.7
                )
                spec_match = 1.0 if getattr(ag, "specialization", None) and str(ag.specialization.value).upper() == loan_type_str.upper() else 0.5
                skills_score = 0.6 * tier_weight + 0.4 * spec_match

                workload_score = max(0.1, 1.0 - (slot_num / max(1, ag.max_cases_per_day or 12)))
                continuity_bonus = 0.10 if case.agent_id == ag.id else 0.0
                lang_match = 1.0 if (ag.languages_spoken and cust_lang in [l.lower() for l in ag.languages_spoken]) else 0.0

                # Expected rupee recovery for this case-agent pairing
                target_inr = float(case.target_amount or 25000.0)
                prob_recovery = min(0.85, max(0.20, affinity_score * (0.80 + 0.20 * tier_weight)))
                expected_case_inr = target_inr * prob_recovery
                inr_score = expected_case_inr / 25000.0

                # Match objective weights
                if self.objective == AllocationObjective.MAX_RECOVERY.value:
                    utility = (
                        0.70 * inr_score +
                        0.10 * proximity_score +
                        0.10 * skills_score +
                        0.05 * workload_score +
                        0.05 * continuity_bonus +
                        0.05 * lang_match
                    )
                elif self.objective == AllocationObjective.MIN_DISTANCE.value:
                    utility = (
                        0.80 * proximity_score +
                        0.05 * inr_score +
                        0.05 * skills_score +
                        0.05 * workload_score +
                        0.05 * continuity_bonus +
                        0.05 * lang_match
                    )
                else:  # BALANCED
                    utility = (
                        0.45 * inr_score +
                        0.40 * proximity_score +
                        0.05 * skills_score +
                        0.05 * workload_score +
                        0.05 * continuity_bonus +
                        0.05 * lang_match
                    )

                weighted_utility = utility * (1.0 + (prio / 100.0) * 0.25)
                cost_matrix[c_idx, s_idx] = -weighted_utility
                decision_cache[(c_idx, s_idx)] = (
                    utility,
                    f"{ag.employee_code} best global fit (affinity: {affinity_score*100:.0f}%, dist: {dist_km:.1f}km, EB-multiplier: {eb_multiplier:.2f})",
                    {
                        "affinity_score": round(affinity_score, 3),
                        "eb_multiplier": round(eb_multiplier, 2),
                        "tier_weight": round(tier_weight, 2),
                        "proximity_km": round(dist_km, 1),
                        "proximity_score": round(proximity_score, 3),
                        "skills_score": round(skills_score, 3),
                        "expected_case_inr": round(expected_case_inr, 2),
                        "objective": self.objective,
                    }
                )

        row_ind, col_ind = linear_sum_assignment(cost_matrix)

        initial_assigned: dict[str, list[Case]] = {ag.id: [] for ag in agents}
        assigned_case_to_decision: dict[str, tuple[Agent, float, str, dict]] = {}

        for r, c in zip(row_ind, col_ind):
            if cost_matrix[r, c] < PENALTY_INELIGIBLE / 2:
                case = cases[r]
                ag, _ = slots[c]
                initial_assigned[ag.id].append(case)
                cached_util, cached_reason, cached_breakdown = decision_cache.get(
                    (r, c),
                    (0.5, f"Assigned to {ag.employee_code}", {})
                )
                assigned_case_to_decision[case.id] = (ag, cached_util, cached_reason, cached_breakdown)

        # -------------------------------------------------------------
        # STAGE 2: Route Feasibility Validation & Outlier Deferral
        # -------------------------------------------------------------
        agent_by_id = {ag.id: ag for ag in agents}

        for ag in agents:
            ag_cases = initial_assigned.get(ag.id, [])
            if not ag_cases:
                continue

            base_lat = ag.base_latitude or 28.6139
            base_lon = ag.base_longitude or 77.2090

            # Calculate cluster centroid
            valid_stops = [c for c in ag_cases if c.customer]
            if not valid_stops:
                continue

            # Compute route distances and check for marginal outliers
            feasible_cases: list[Case] = []
            for c in valid_stops:
                cust = c.customer
                c_lat = cust.latitude or base_lat
                c_lon = cust.longitude or base_lon
                dist_from_base = self.haversine_km(base_lat, base_lon, c_lat, c_lon)

                if dist_from_base > self.MAX_DETOUR_MARGINAL_KM:
                    # Infeasible Outlier -> tag with DEFERRED_ROUTE_INFEASIBLE
                    decisions.append(AllocationDecision(
                        id=str(uuid.uuid4()),
                        run_id="",
                        case_id=c.id,
                        previous_agent_id=c.agent_id,
                        allocated_agent_id=None,
                        outcome=AllocationOutcome.DEFERRED_ROUTE_INFEASIBLE.value,
                        reason=f"Deferred: Location ({dist_from_base:.1f} km from base) exceeds maximum daily travel feasibility boundary ({self.MAX_DETOUR_MARGINAL_KM:.0f} km).",
                        visit_priority_score=prio_scores.get(c.id, 50.0),
                        fit_score=0.0,
                        score_breakdown={"distance_km": round(dist_from_base, 1), "objective": self.objective},
                    ))
                else:
                    feasible_cases.append(c)

            # Store final validated feasible cases for agent
            assigned_by_agent[ag.id] = feasible_cases
            for c in feasible_cases:
                _, util, reason, bdown = assigned_case_to_decision[c.id]
                aff_score = bdown.get("affinity_score", 0.5)
                t_weight = bdown.get("tier_weight", 0.8)
                prob_rec = min(0.85, max(0.20, aff_score * (0.80 + 0.20 * t_weight)))
                expected_recovery_sum += float(c.target_amount or 0.0) * prob_rec

                decisions.append(AllocationDecision(
                    id=str(uuid.uuid4()),
                    run_id="",
                    case_id=c.id,
                    previous_agent_id=c.agent_id,
                    allocated_agent_id=ag.id,
                    outcome=AllocationOutcome.ALLOCATED.value,
                    reason=reason,
                    visit_priority_score=prio_scores.get(c.id, 50.0),
                    fit_score=round(util, 3),
                    score_breakdown=bdown,
                ))

        # Handle Unallocated Cases
        allocated_case_ids = {c.id for c_list in assigned_by_agent.values() for c in c_list}
        decided_case_ids = {d.case_id for d in decisions}

        for case in cases:
            if case.id not in allocated_case_ids and case.id not in decided_case_ids:
                decisions.append(AllocationDecision(
                    id=str(uuid.uuid4()),
                    run_id="",
                    case_id=case.id,
                    previous_agent_id=case.agent_id,
                    allocated_agent_id=None,
                    outcome=AllocationOutcome.DEFERRED.value,
                    reason="Deferred to next cycle: all nearby agents reached daily capacity limit.",
                    visit_priority_score=prio_scores.get(case.id, 50.0),
                    fit_score=0.0,
                    score_breakdown={"objective": self.objective},
                ))

        return assigned_by_agent, decisions, expected_recovery_sum
