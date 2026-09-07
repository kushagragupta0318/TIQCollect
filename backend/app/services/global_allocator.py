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
import structlog
import numpy as np
from scipy.optimize import linear_sum_assignment

from app.models.agent import Agent
from app.models.case import Case
from app.ml.eligibility import specialisation_fit
from app.models.customer import Customer
from app.models.loan import Loan
from app.models.allocation_decision import AllocationDecision, AllocationOutcome
from app.models.allocation_setting import AllocationObjective
from app.ml.empirical_bayes import EmpiricalBayesAgentAdjuster

logger = structlog.get_logger()


class GlobalAllocator:
    """Solves portfolio-wide case allocation using Two-Stage Bipartite Optimization + Route Validation."""

    MAX_BEAT_ROUTE_KM = 120.0
    MAX_DETOUR_MARGINAL_KM = 22.0

    # Value-term scaling. See _value_score.
    VALUE_KNEE_INR = 25_000.0
    VALUE_REFERENCE_INR = 300_000.0

    # Scoring constants. These were inline literals scattered through the pair
    # loop: an operator could not see what the allocator was tuned to without
    # reading the arithmetic, and two of them appeared twice. Values are
    # unchanged — this names them, it does not retune anything.
    #
    # Not in config.py deliberately. They are a scoring MODEL, versioned with
    # the code that reads them, in the same spirit as the scorecards' weights:
    # a plan is only reproducible if the numbers that produced it travel with
    # the release. Promote to settings if operators ever need to tune them
    # without a deploy, but that is a different decision from removing magic
    # numbers.
    PROXIMITY_DECAY_KM = 5.5      # exp(-km / this); 5.5km -> 0.37
    TIER_WEIGHTS = {"TIER_1": 1.0, "TIER_2": 0.8, "TIER_3": 0.6}
    TIER_WEIGHT_DEFAULT = 0.7     # unrecognised tier
    SKILLS_TIER_SHARE = 0.6       # skills = this x tier + (1-this) x spec_match
    SPEC_MATCH_HIT = 1.0
    SPEC_MATCH_MISS = 0.5
    PRIORITY_UPLIFT = 0.25        # a top-priority case gets up to +25% utility
    PROB_RECOVERY_FLOOR = 0.20
    PROB_RECOVERY_CEIL = 0.85
    TIER_UPLIFT = 0.20            # prob = affinity x ((1-this) + this x tier)

    # Where a borrower or an agent base sits when the record has no coordinates.
    # Nothing in the book is missing them today (checked: 0 customers, 0 agents),
    # so this is a guard, not a code path in use. It is named rather than inline
    # because silently placing someone in central Delhi corrupts distance, the
    # territory gate and the route in one go, and a literal 28.6139 buried in an
    # `or` reads like a real coordinate.
    FALLBACK_LAT = 28.6139
    FALLBACK_LON = 77.2090

    @classmethod
    def _prob_recovery(cls, affinity: float, tier_weight: float) -> float:
        """Chance this agent recovers on this case, bounded.

        ONE definition. This formula was written out twice — once in the pair
        loop where it decides WHICH AGENT GETS THE CASE, and again when the run
        totals up Expected Recovery for the manager's dashboard. Identical then,
        but nothing held them together: changing the clamp in one and not the
        other would have left the plan quietly disagreeing with its own forecast,
        with no test to catch it. Merged 2026-09-03, values unchanged.
        """
        uplift = (1.0 - cls.TIER_UPLIFT) + cls.TIER_UPLIFT * tier_weight
        return min(cls.PROB_RECOVERY_CEIL,
                   max(cls.PROB_RECOVERY_FLOOR, affinity * uplift))

    # Indicator value for "this agent already holds this case". 0/1 like
    # lang_match; the objective table supplies the weight. See where it is used.
    CONTINUITY_BONUS = 1.0

    @classmethod
    def _value_score(cls, expected_inr: float) -> float:
        """Expected rupees -> a 0-1 term, comparable to the other five factors.

        2026-09-02 — this was `expected_case_inr / 25000.0`, which is unbounded.
        Every other term in the utility is 0-1, so the stated weights did not
        describe the actual influence. Measured on the live book (426 open cases):

            target      old inr_score   contribution @0.45   vs proximity max 0.40
            Rs   7,031       0.17              0.08
            Rs  25,901       0.62              0.28           (median)
            Rs  66,700       1.60              0.72
            Rs 211,701       5.08              2.29           5.7x proximity's RANGE
            Rs 500,000      12.00              5.40          13.5x

        So "Balanced" (0.45 value / 0.40 proximity) actually ran at roughly 65/25
        on a median-ish case and 15:1 on a large one — and switching the objective
        to Min Distance barely moved a big case, because 0.05 x 12.0 still beats
        0.80 x 1.0. The manager's objective switch was being overruled by case size.

        Log rather than linear because the book has a heavy right tail (median
        Rs 25,901, max Rs 500,000). Dividing by the max would collapse the median
        to 0.05 and destroy discrimination across the bulk of the portfolio; the
        log keeps p25/median/p75/p95 spread over 0.06/0.19/0.37/0.70.

        The reference is a fixed rupee figure, deliberately NOT derived from the
        pool: a pool-relative scale would make the same case score differently
        depending on what else happened to be planned that night, which is not a
        property you want in something labelled an audit trail.
        """
        if expected_inr <= 0:
            return 0.0
        ceiling = math.log1p(cls.VALUE_REFERENCE_INR / cls.VALUE_KNEE_INR)
        return min(1.0, math.log1p(expected_inr / cls.VALUE_KNEE_INR) / ceiling)

    def __init__(
        self,
        objective: str = AllocationObjective.BALANCED.value,
        territory_radius_km: float = 16.0,
        eb_adjuster: EmpiricalBayesAgentAdjuster | None = None,
    ):
        self.objective = objective
        self.territory_radius_km = territory_radius_km
        self.eb_adjuster = eb_adjuster or EmpiricalBayesAgentAdjuster()

    @classmethod
    def _work_anchor(cls, base_km: float, centroid: tuple[float, float] | None,
                     cust_lat: float, cust_lon: float) -> float:
        """Distance used for the proximity SCORE: nearer of base or current book.

        2026-09-02 — proximity was scored purely as crow-flies from the agent's
        home base, which cannot express clustering: two cases 15 km apart on
        opposite sides of the base score identically, so the assignment had no
        reason to prefer the one sitting beside six cases the agent is already
        visiting. Stage 2 then had to sequence a set that was chosen with no
        regard for how it routes.

        The obvious fix — score from the previous stop — is circular: the route
        does not exist until after the assignment. The obvious other fix, real
        OSRM road distances, means an O(N^2) table (319 cases x 15 bases is 334
        points) on an external service in the middle of the solve; core/routing
        falls back to Haversine when OSRM is down, but the latency and the demo
        server's own limits make that a poor thing to depend on here.

        This uses an anchor that is known BEFORE the solve and needs no network:
        the centroid of the open cases the agent already holds. A case is cheap
        to reach if it is near where they start OR near where they are already
        working, so the score takes the smaller of the two.

        The territory gate is deliberately NOT changed — it still tests base
        distance against territory_radius_km, so eligibility is identical and the
        16 km zone keeps the calibration it was tuned with. Only the ranking of
        eligible pairs moves.
        """
        if centroid is None:
            return base_km
        return min(base_km, cls.haversine_km(centroid[0], centroid[1], cust_lat, cust_lon))

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
        """Weight per utility term for the selected manager objective.

        2026-09-02 — these are now the weights the allocator actually uses.
        This method previously returned a four-key table (w_affinity /
        w_proximity / w_workload / w_skills) that allocate() read into locals
        and then never touched: the utility was built from a SECOND set of
        weights hardcoded inline, once per objective branch. The two disagreed
        — BALANCED was 0.35/0.35 here against the 0.45/0.40 that actually ran —
        and because nothing read this one, nothing ever surfaced the conflict.

        The live values are reproduced exactly, so no allocation changes. What
        changes is that there is one table instead of four, and the per-factor
        explanation on the audit panel reads it. A weight edit now moves the
        decision and the reason given for it together, rather than leaving the
        stated reason describing weights that were retired.
        """
        if self.objective == AllocationObjective.MAX_RECOVERY.value:
            return {"expected_recovery": 0.70, "proximity": 0.10, "skills": 0.10,
                    "workload": 0.05, "continuity": 0.05, "language": 0.05}
        if self.objective == AllocationObjective.MIN_DISTANCE.value:
            return {"expected_recovery": 0.05, "proximity": 0.80, "skills": 0.05,
                    "workload": 0.05, "continuity": 0.05, "language": 0.05}
        return {"expected_recovery": 0.45, "proximity": 0.40, "skills": 0.05,
                "workload": 0.05, "continuity": 0.05, "language": 0.05}

    def allocate(
        self,
        cases: Sequence[Case],
        agents: Sequence[Agent],
        history_matrix: dict,
        prio_scores: dict[str, float],
        ptp_fatigue: dict[str, set[str]] | None = None,
    ) -> tuple[dict[str, list[Case]], list[AllocationDecision], float]:
        """
        Execute Two-Stage Global Case Allocation:
          Stage 1: Global Hungarian Bipartite Assignment
          Stage 2: Route Feasibility & Outlier Deferral
        """
        weights = self.get_objective_weights()

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

        # Where each agent is ALREADY working — the centroid of the open cases
        # they currently hold. See _work_anchor for why this exists.
        work_centroids: dict[str, tuple[float, float]] = {}
        for ag in agents:
            pts = [
                (c.customer.latitude, c.customer.longitude)
                for c in cases
                if c.agent_id == ag.id and c.customer
                and c.customer.latitude is not None and c.customer.longitude is not None
            ]
            if pts:
                work_centroids[ag.id] = (
                    sum(p[0] for p in pts) / len(pts),
                    sum(p[1] for p in pts) / len(pts),
                )

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
            # `or` here would also swallow a genuine 0.0. None-checked instead,
            # and logged: a borrower with no coordinates is silently relocated to
            # central Delhi, which corrupts the distance, the territory gate and
            # the route in one step. Nothing in the book is missing them today, so
            # this warns rather than defers; if it ever fires, deferring the case
            # is the better answer than routing an agent to a guess.
            cust_lat, cust_lon = customer.latitude, customer.longitude
            if cust_lat is None or cust_lon is None:
                logger.warning("allocator.customer_missing_coordinates",
                               case_id=case.id, customer_id=customer.id)
                cust_lat, cust_lon = self.FALLBACK_LAT, self.FALLBACK_LON
            cust_lang = (customer.language_preference or "").lower()

            # Agents this borrower has already promised, and not paid, three
            # times. Built by PlannerService; see _ptp_fatigue_map there for why
            # the threshold is per (case, agent) rather than per case.
            fatigued = (ptp_fatigue or {}).get(case.id) or frozenset()

            for s_idx, (ag, slot_num) in enumerate(slots):
                # Hard Gate: Female agent constraint
                is_female_ag = str(getattr(ag, "gender", "")).upper() in ("F", "FEMALE")
                if getattr(customer, "requires_female_agent", False) and not is_female_ag:
                    continue

                # Hard Gate: PTP fatigue. Three promises to the same agent with
                # nothing collected means the pairing is not working, whatever the
                # score says. Barring the agent rather than the case is deliberate:
                # the borrower still owes the money and should still be visited,
                # just by somebody else. Because this only removes one column from
                # the row, the bipartite solve then picks that case's next best
                # agent by itself — no reassignment pass, no special casing.
                if ag.id in fatigued:
                    continue

                # Hard Gate: Territory Radius Boundary
                base_lat = ag.base_latitude if ag.base_latitude is not None else self.FALLBACK_LAT
                base_lon = ag.base_longitude if ag.base_longitude is not None else self.FALLBACK_LON
                dist_km = self.haversine_km(base_lat, base_lon, cust_lat, cust_lon)
                if dist_km > self.territory_radius_km:
                    continue

                # Empirical Bayes Segment Performance Multiplier
                shrunk_win, prior_win, eb_multiplier = self.eb_adjuster.get_segment_multiplier(
                    ag.id, loan_type_str, dpd_str
                )

                # How much of a target this agent recovers on work like this —
                # ONE estimator, taken at its own grain.
                #
                # shrunk_win is the Empirical Bayes estimate for exactly this
                # (agent, loan_type, DPD bucket): the agent's own observed rate
                # pulled toward the segment prior in proportion to how little
                # evidence there is. Where an agent has history it differentiates
                # them; where they have none it degrades to the segment average
                # rather than inventing a number. On this book 297 of 315
                # (agent, segment) pairs hold fewer than the 5 observations the
                # adjuster requires, so that fallback is the common path, not an
                # edge case.
                #
                # 2026-09-03 — this was `base_affinity * eb_multiplier`, which
                # multiplied TWO DIFFERENT ESTIMATORS of the same quantity:
                #
                #   base_affinity  competency matrix, 90-day window, grouped by
                #                  (agent, loan_type), collected / target
                #   eb_multiplier  Empirical Bayes, 180-day window, grouped by
                #                  (agent, loan_type, DPD), shrunk to a 0.861 prior
                #
                # Different windows, different groupings, different denominators.
                # Their product is not a correction; it is two incompatible
                # measurements of agent skill stacked on each other, and wherever
                # the multiplier is not 1.0 it counts that skill twice, being
                # itself the agent-versus-prior ratio. Measured across 468
                # (agent, loan_type, DPD) combinations, the product and the EB
                # estimate disagreed by a median of 38%.
                #
                # history_matrix stays in the signature: PlannerService still
                # builds it for the manager surface, and it is the raw material
                # the adjuster fits from. It is simply no longer read twice.
                affinity_score = min(1.0, max(0.0, float(shrunk_win)))

                # Travel cost is scored from the NEARER of two anchors: the
                # agent's base, and the centroid of the cases they already hold.
                # The gate above still uses base distance alone, so the 16 km
                # operating zone (commit 51fc4e7) keeps the exact meaning it was
                # calibrated with and the same cases remain eligible.
                effective_km = self._work_anchor(dist_km, work_centroids.get(ag.id), cust_lat, cust_lon)
                proximity_score = math.exp(-effective_km / self.PROXIMITY_DECAY_KM)

                tier_weight = self.TIER_WEIGHTS.get(
                    ag.tier.value if hasattr(ag.tier, "value") else str(ag.tier), 0.7
                )
                # 2026-09-07 — THIS TERM HAD NEVER ONCE BEEN 1.0. It read:
                #
                #   spec_match = 1.0 if ag.specialization and
                #       str(ag.specialization.value).upper() == loan_type_str.upper()
                #       else 0.5
                #
                # comparing an AgentSpecialization (SECURED / UNSECURED / BOTH)
                # against a LoanType (HOME / AUTO / PERSONAL / BUSINESS / GOLD /
                # CREDIT_CARD / EDUCATION / MICROFINANCE). THE TWO ENUMS SHARE NO
                # MEMBER, so the expression was False for every pair ever scored:
                # the term was the constant 0.5, the specialisation half of
                # skills_score was inert, and the audit panel's "specialises in
                # this loan type" reason could never fire.
                #
                # ml/eligibility.specialisation_fit is the correct rule and both
                # scorecards already imported it, which makes this an instance of
                # the one-definition rule rather than a typo. Imported here now
                # rather than restated, so a change to the SECURED/UNSECURED sets
                # moves the allocator with them.
                #
                # The 0.5 floor is kept: a non-specialist is a worse fit, not an
                # ineligible one. Measured effect on the demo book is recorded in
                # tests/test_global_allocator_spec_match.py.
                spec_match = 1.0 if specialisation_fit(ag, loan) else 0.5
                skills_score = (self.SKILLS_TIER_SHARE * tier_weight
                                + (1.0 - self.SKILLS_TIER_SHARE) * spec_match)

                workload_score = max(0.1, 1.0 - (slot_num / max(1, ag.max_cases_per_day or 12)))
                # 2026-09-02 — was 0.10, which the 0.05 objective weight then
                # multiplied down to 0.005: about a third of one percent of a
                # typical fit score, so a borrower was moved to a different agent
                # for a few hundred metres of proximity, discarding whatever the
                # previous agent knew about the household. Every other indicator
                # in this block is 0/1 (lang_match) or 0.5/1.0 (spec_match) and
                # carries its weight in the objective table; 0.10 was a second
                # weight applied on top of the first. Now consistent: the term is
                # the indicator, the table holds the weight.
                continuity_bonus = self.CONTINUITY_BONUS if case.agent_id == ag.id else 0.0
                lang_match = 1.0 if (ag.languages_spoken and cust_lang in [l.lower() for l in ag.languages_spoken]) else 0.0

                # Expected rupee recovery for this case-agent pairing, over what is
                # STILL COLLECTABLE.
                #
                # 2026-09-02 — this was case.target_amount, the lifetime target, so a
                # case 95% collected was valued at the same rupees as an untouched one
                # of equal size. That feeds inr_score, which carries 45-70% of the
                # utility, so the allocator was spending its most heavily weighted term
                # on money already banked and not collectable again. Invisible while the
                # pool was mostly untouched cases; obvious the moment a day of
                # collections left 202 partially paid cases in it.
                target_inr = max(0.0, float(case.target_amount or 0.0)
                                 - float(case.collected_amount or 0.0))
                prob_recovery = self._prob_recovery(affinity_score, tier_weight)
                expected_case_inr = target_inr * prob_recovery
                inr_score = self._value_score(expected_case_inr)

                # The utility is a weighted sum, so each term's weight x score IS
                # its share of the decision. Recording those shares is what lets
                # the audit panel rank the reasons this case went to this agent,
                # rather than dumping the raw factors and leaving a manager to
                # guess which of them mattered. Nothing is recomputed for the
                # explanation — it is the same arithmetic, kept instead of thrown
                # away. Added 2026-09-02.
                terms = {
                    "expected_recovery": inr_score,
                    "proximity": proximity_score,
                    "skills": skills_score,
                    "workload": workload_score,
                    "continuity": continuity_bonus,
                    "language": lang_match,
                }
                contributions = {k: round(weights[k] * v, 4) for k, v in terms.items()}
                utility = sum(weights[k] * v for k, v in terms.items())

                weighted_utility = utility * (1.0 + (prio / 100.0) * self.PRIORITY_UPLIFT)
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
                        # Three factors that were computed and discarded. They are
                        # the most legible reasons on the list — "speaks the
                        # borrower's language", "was already his case" — and the
                        # panel could not state them because nothing stored them.
                        "lang_match": round(lang_match, 2),
                        "workload_score": round(workload_score, 3),
                        "continuity_bonus": round(continuity_bonus, 2),
                        "spec_match": round(spec_match, 2),
                        # Named so the audit panel can say "specialises in personal
                        # loans" rather than "in this loan type".
                        "loan_type": loan_type_str,
                        "contributions": contributions,
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

            base_lat = ag.base_latitude if ag.base_latitude is not None else self.FALLBACK_LAT
            base_lon = ag.base_longitude if ag.base_longitude is not None else self.FALLBACK_LON

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
                prob_rec = self._prob_recovery(aff_score, t_weight)
                # Same correction as target_inr above: forecast what can still be
                # collected. Summing full targets had the plan predicting Rs 48.5L
                # against Rs 16.4L of collectable balance — 296% of the possible.
                c_remaining = max(0.0, float(c.target_amount or 0.0)
                                  - float(c.collected_amount or 0.0))
                expected_recovery_sum += c_remaining * prob_rec

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
