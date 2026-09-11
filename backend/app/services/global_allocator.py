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
import random
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


# ── The hard gates, in one place ─────────────────────────────────────────────
# Added 2026-09-11, when manager-initiated reassignment gave these rules a
# SECOND caller. Until then the cost-matrix loop below was the only place they
# were evaluated and inlining them there was correct. A second caller changes
# that calculus completely: this repo's worst bugs have all been one rule
# written twice and then drifting — two risk_score formulas, two recovery
# writers, two sets of allocator weights, seven DPD-bucket spellings. An
# endpoint that let a manager hand a case to an agent the nightly run would
# never give it to is the same failure wearing a new hat.
#
# Behaviour is unchanged: same predicates, same order, same outcomes. The
# existing gate tests (DNC, hostility, female-agent, PTP fatigue, territory,
# capacity, determinism) all pass against this, which is what makes the
# extraction safe to ship.
#: The nightly run's territory radius. Named so manager reassignment applies
#: the SAME radius rather than restating 16.0 — the planner constructs
#: GlobalAllocator without passing one, so this default IS production.
DEFAULT_TERRITORY_RADIUS_KM = 16.0

BAR_DNC = "DNC"
BAR_HOSTILITY = "HOSTILITY"
BAR_REQUIRES_FEMALE_AGENT = "REQUIRES_FEMALE_AGENT"
BAR_PTP_FATIGUE = "PTP_FATIGUE"
BAR_OUTSIDE_TERRITORY = "OUTSIDE_TERRITORY"
BAR_OWNED_BY_ANOTHER_AGENT = "OWNED_BY_ANOTHER_AGENT"

#: Sentences for a human. The codes above are for machines and tests.
BAR_MESSAGES = {
    BAR_DNC: "Borrower is on the Do-Not-Contact (DNC) list.",
    BAR_HOSTILITY: "Withheld due to safety/hostility risk flag.",
    BAR_REQUIRES_FEMALE_AGENT: "This borrower must be visited by a female agent.",
    BAR_PTP_FATIGUE: ("Three promises to this agent have gone unpaid; the "
                      "pairing is barred."),
    BAR_OUTSIDE_TERRITORY: "The borrower is outside this agent's territory.",
    BAR_OWNED_BY_ANOTHER_AGENT: "The case is assigned to a different agent.",
}


def case_bar(customer) -> str | None:
    """Gates that bar a case from EVERY agent. None means the case is workable.

    Unknown is never permissive: `requires_female_agent`, `do_not_contact` and
    `is_hostile` are read with a False default, matching the columns' nullable
    history — a missing flag means "no such requirement recorded", not "waived".
    """
    if customer is None:
        return None
    if getattr(customer, "do_not_contact", False):
        return BAR_DNC
    if getattr(customer, "is_hostile", False):
        return BAR_HOSTILITY
    return None


def pair_bar(case, customer, agent, *, dist_km: float,
             territory_radius_km: float,
             fatigued_agent_ids=(), enforce_ownership: bool = True) -> str | None:
    """Gates that bar THIS agent from THIS case. None means the pairing is legal.

    `enforce_ownership` is the one parameter a caller may vary, and only
    manager reassignment passes False — a manager moving a case IS the override
    for ownership, and re-applying it there would make the endpoint refuse
    every reassignment it exists to perform. No other gate is optional: a
    manager cannot hand a borrower to an agent the territory, safety or
    PTP-fatigue rules exclude.
    """
    if enforce_ownership and case.agent_id is not None and agent.id != case.agent_id:
        return BAR_OWNED_BY_ANOTHER_AGENT
    is_female_ag = str(getattr(agent, "gender", "")).upper() in ("F", "FEMALE")
    if getattr(customer, "requires_female_agent", False) and not is_female_ag:
        return BAR_REQUIRES_FEMALE_AGENT
    if agent.id in (fatigued_agent_ids or ()):
        return BAR_PTP_FATIGUE
    if dist_km > territory_radius_km:
        return BAR_OUTSIDE_TERRITORY
    return None


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

    # 2026-09-08 — A SECOND FLOOR, FOR THE CALIBRATED PATH ONLY. The 0.20 above
    # is deliberately NOT changed: it guards `_prob_recovery`, whose input is an
    # Empirical Bayes agent estimate that is uninformative for most pairs (297
    # of 315 cells sit below the five-observation threshold), and a floor is the
    # right response to an estimate that cannot be taken literally. Changing it
    # would also move production, which no measurement here justifies.
    #
    # `_prob_recovery_ml` has the opposite problem: its input IS meant to be
    # taken literally, and the 0.20 floor was destroying that. Measured on the
    # 350-case shadow pool, the floor lifted 153 of 350 cases (43.7%) whose mean
    # predicted P(pay) was 0.0923 and whose ACTUAL recovery rate was 0.0588 —
    # it overstated that group by 240%, and accounted for roughly half of the
    # ML path's total +42% forecast bias.
    #
    # WHY 0.02 AND NOT ZERO. It is set below anything the model actually
    # produces on this book (its minimum prediction is 0.0287), so it lifts
    # nothing and is inert as a calibration matter — it exists purely so a
    # pathological input cannot make a case's expected value exactly zero and
    # lose all ordering among hopeless accounts. The reliability curve justifies
    # trusting the low end: over ten bands the model's bottom decile predicts
    # 0.0399 against a realised 0.0000, and no band shows the systematic
    # collapse that would argue for a protective floor.
    PROB_RECOVERY_FLOOR_ML = 0.02
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


    # -----------------------------------------------------------------
    # Epsilon-greedy exploration
    # -----------------------------------------------------------------
    def _explore(self, assigned_by_agent, decisions, agents,
                 eligible_agents) -> tuple[list[dict], float]:
        """Reassign a random epsilon-fraction of cases to a random ELIGIBLE agent.

        WHY THIS EXISTS. Every historical (agent, case, outcome) row in this
        system was produced by this allocator, so good agents systematically
        received good cases. A model fitted on that data learns the allocator,
        not the agents — and no amount of propensity weighting fully removes a
        confound you never broke. A small randomised slice is the only source of
        unconfounded evidence about whether WHICH agent is sent matters at all.

        WHAT IT IS SIZED FOR, and what it is not. At the measured book volume —
        233 allocations a day across 30 agents — epsilon = 10% yields ~23
        randomised visits a day, about one per agent every 1.3 days. That powers
        a VARIANCE COMPONENT ("does agent identity matter?") in roughly 1.3-2.1
        months. It does NOT power per-agent point estimates: detecting a 25%
        relative effect for a named agent needs ~820 randomised visits each,
        24,595 in total, which is 2.9 years at this rate. Anyone reading the
        resulting data as an agent ranking will be reading noise.

        IT CANNOT REACH THE HARD GATES. Candidates come only from
        `eligible_agents`, built as the cost matrix was constructed, which is the
        single place DNC, hostility, the female-agent requirement, the territory
        radius and PTP fatigue are evaluated. A case is never offered to an agent
        the gates excluded, and BLOCKED cases are never touched because they were
        never assigned.

        CAPACITY IS PRESERVED BY SWAPPING, not by moving. Moving a case to an
        agent already at `max_cases_per_day` would silently overfill a day; a
        pairwise swap between two agents keeps every count exactly as the solver
        left it, and is always feasible when a mutually-eligible partner exists.

        DETERMINISTIC GIVEN ITS SEED. A plan that cannot be reproduced cannot be
        audited, which is the same reason the route solver refuses a wall-clock
        limit. The seed is recorded on every explored decision.

        RETURNS the exploration log AND the change it makes to the expected
        recovery total. The total is accumulated in Stage 2, before this runs, so
        without the adjustment the plan would report a forecast for a pairing it
        no longer uses — measured as an identical figure at every epsilon, which
        is exactly the defect _prob_recovery was merged to prevent on
        2026-09-03. Exploration is deliberately sub-optimal; the forecast has to
        say so.
        """
        if self.exploration_rate <= 0.0:
            return [], 0.0

        seed = self.exploration_seed
        if seed is None:
            seed = 0
        rng = random.Random(seed)

        agent_by_id = {a.id: a for a in agents}
        holder = {c.id: aid for aid, cs in assigned_by_agent.items() for c in cs}
        case_by_id = {c.id: c for cs in assigned_by_agent.values() for c in cs}

        # ONLY UNASSIGNED CASES MAY BE RANDOMISED, from 2026-09-11. A swap moves
        # BOTH of its cases, so an owned case had to be excluded as a partner as
        # well as an initiator — filtering only the initiators would still have
        # moved owned cases, silently, as the other half of somebody else's swap.
        #
        # The ownership gate in the cost-matrix loop already makes this
        # unreachable: an owned case's `eligible_agents` set contains just its
        # owner, so `allowed` comes out empty and the swap is skipped. This
        # filter is stated anyway because "exploration never moves an owned
        # case" is a property the product now promises, and a promise that holds
        # only as a side effect of another rule breaks the moment that rule is
        # rephrased. It is also what makes the property directly testable here.
        unowned = {cid for cid, c in case_by_id.items() if c.agent_id is None}
        candidates = sorted(cid for cid in holder if cid in unowned)
        # HALVED ON PURPOSE. Exploration proceeds by SWAPS, and a swap randomises
        # BOTH of its cases — so selecting `N * epsilon` initiators randomised
        # 2 * epsilon of the book. Measured before this correction: a configured
        # 10% produced a realised 19.1%, and 20% produced 35.4%. `epsilon` is the
        # share of assignments that end up randomised, which is the quantity the
        # power calculation and the operational conversation are both about.
        n_explore = int(round(len(candidates) * self.exploration_rate / 2.0))
        if n_explore <= 0:
            return [], 0.0
        chosen = rng.sample(candidates, min(n_explore, len(candidates)))

        log: list[dict] = []
        swapped: set[str] = set()
        for case_id in chosen:
            if case_id in swapped:
                continue
            current = holder[case_id]
            # sorted() so the draw is reproducible from the seed — a set's
            # iteration order is not stable across processes.
            allowed = sorted(a for a in eligible_agents.get(case_id, ()) if a != current)
            if not allowed:
                continue
            target = rng.choice(allowed)

            # Find a case held by `target` that could legally sit with `current`.
            partners = [cid for cid, aid in sorted(holder.items())
                        if aid == target and cid not in swapped
                        and cid in unowned
                        and current in eligible_agents.get(cid, ())]
            if not partners:
                continue
            partner = rng.choice(partners)

            a_case, b_case = case_by_id[case_id], case_by_id[partner]
            assigned_by_agent[current].remove(a_case)
            assigned_by_agent[target].remove(b_case)
            assigned_by_agent[target].append(a_case)
            assigned_by_agent[current].append(b_case)
            holder[case_id], holder[partner] = target, current
            swapped.update({case_id, partner})

            # The propensity of the observed assignment, for IPW later. Uniform
            # over the eligible alternatives, so 1/len(allowed) for the explored
            # case. Recorded rather than reconstructed: the eligible set depends
            # on gate state at plan time and cannot be rebuilt afterwards.
            for cid, moved_to, moved_from, k in (
                    (case_id, target, current, len(allowed)),
                    (partner, current, target,
                     len(eligible_agents.get(partner, ())))):
                log.append({"case_id": cid, "from_agent": moved_from,
                            "to_agent": moved_to, "propensity": 1.0 / max(k, 1),
                            "n_eligible": k, "seed": seed})

        # Stamp the decisions so the slice is self-identifying in the audit
        # trail, and re-price each swapped case against the agent it actually
        # went to. Only the tier uplift can move — the borrower's probability is
        # a property of the borrower, and eb_multiplier is looked up per pair but
        # is 1.0 wherever an agent has too little evidence, which is most of the
        # book (297 of 315 cells).
        recovery_delta = 0.0
        by_case = {e["case_id"]: e for e in log}
        for d in decisions:
            if d.outcome != AllocationOutcome.ALLOCATED.value:
                continue
            entry = by_case.get(d.case_id)
            if entry is None:
                d.score_breakdown["exploration"] = False
                continue
            bd = d.score_breakdown
            old_agent = agent_by_id.get(entry["from_agent"])
            new_agent = agent_by_id.get(entry["to_agent"])
            case = case_by_id.get(d.case_id)
            if case is not None and old_agent is not None and new_agent is not None:
                remaining = max(0.0, float(case.target_amount or 0.0)
                                - float(case.collected_amount or 0.0))

                def _tw(ag):
                    return self.TIER_WEIGHTS.get(
                        ag.tier.value if hasattr(ag.tier, "value") else str(ag.tier),
                        self.TIER_WEIGHT_DEFAULT)

                ml_p = bd.get("ml_borrower_p_recover")
                eb = bd.get("eb_multiplier", 1.0)
                aff = bd.get("affinity_score", 0.5)
                if bd.get("ml_used_for_decision") and ml_p is not None:
                    old_p = self._prob_recovery_ml(ml_p, eb, _tw(old_agent))
                    new_p = self._prob_recovery_ml(ml_p, eb, _tw(new_agent))
                else:
                    old_p = self._prob_recovery(aff, _tw(old_agent))
                    new_p = self._prob_recovery(aff, _tw(new_agent))
                recovery_delta += remaining * (new_p - old_p)
                bd["prob_recovery_after_exploration"] = round(new_p, 4)

            d.allocated_agent_id = entry["to_agent"]
            d.reason = (f"EXPLORATION: randomly reassigned from "
                        f"{entry['from_agent']} among {entry['n_eligible']} "
                        f"eligible agents (epsilon={self.exploration_rate:.0%})")
            d.score_breakdown.update({
                "exploration": True,
                "exploration_propensity": round(entry["propensity"], 6),
                "exploration_from_agent": entry["from_agent"],
                "exploration_n_eligible": entry["n_eligible"],
                "exploration_seed": entry["seed"],
                "exploration_rate": self.exploration_rate,
            })

        logger.info("allocator.exploration", rate=self.exploration_rate,
                    selected=len(chosen), swapped=len(swapped), seed=seed,
                    expected_recovery_delta=round(recovery_delta, 2))
        return log, recovery_delta

    @classmethod
    def _prob_recovery_ml(cls, borrower_p_recover: float, eb_multiplier: float,
                          tier_weight: float) -> float:
        """Chance this agent recovers on this case, using the TRAINED model.

        WHY THIS IS NOT A SUBSTITUTION FOR affinity_score, and why that matters.
        The obvious wiring — feed the model's output in where `shrunk_win` goes —
        is a category error. `shrunk_win` is an AGENT-side estimate: what share of
        target THIS AGENT recovers on work of this kind. `recovery_risk` is a
        BORROWER-side estimate: will THIS BORROWER make a material payment. It
        cannot see the agent at all, so substituting it would make every agent
        score identically on a given case and destroy the per-agent
        differentiation the allocator exists to provide.

        The model's actual place is here, in prob_recovery, which today has NO
        borrower-side input whatsoever: it is clamp(agent_skill x tier, .., ..),
        so the probability that a case is recovered currently takes no account
        of the borrower. That is the gap.

        AND THIS IS NOT THE 2026-09-03 MISTAKE, though it looks like it. That
        fix removed `base_affinity * eb_multiplier` because both factors
        estimated THE SAME QUANTITY — agent skill — from two different windows
        and groupings, so their product counted skill twice. Here the two
        factors are different quantities:

            borrower_p_recover   P(material payment), calibrated, borrower-side,
                                 knows nothing about any agent
            eb_multiplier        the agent's RELATIVE effect, defined in
                                 empirical_bayes.get_segment_multiplier as
                                 shrunk_win / segment_prior and bounded to
                                 [0.75, 1.25] — a ratio centred on 1.0, not a
                                 second probability

        A base rate modulated by a bounded relative effect is the standard way
        to combine them, and it degrades correctly: where an agent has too few
        observations the multiplier is exactly 1.0 and the estimate falls back
        to the borrower's own probability. On this book that is the common path,
        not the edge case — 297 of 315 (agent, segment) cells are below the
        five-observation threshold.

        The CEILING is shared with _prob_recovery; the FLOOR is not. See
        PROB_RECOVERY_FLOOR_ML for why a floor that is correct for an
        uninformative agent estimate is actively wrong for a calibrated
        borrower probability.
        """
        base = min(1.0, max(0.0, float(borrower_p_recover)))
        uplift = (1.0 - cls.TIER_UPLIFT) + cls.TIER_UPLIFT * tier_weight
        # PROB_RECOVERY_FLOOR_ML, not PROB_RECOVERY_FLOOR — see the constant for
        # the measurement. Applying the agent-side floor to a calibrated
        # borrower probability was the single largest source of bias in the
        # first shadow run.
        return min(cls.PROB_RECOVERY_CEIL,
                   max(cls.PROB_RECOVERY_FLOOR_ML,
                       base * float(eb_multiplier) * uplift))

    # Indicator value for "this agent already holds this case". 0/1 like
    # lang_match; the objective table supplies the weight. See where it is used.
    CONTINUITY_BONUS = 1.0

    # ── Candidate value transforms (2026-09-08, evaluation only) ────────────
    #
    # WHY THERE ARE ALTERNATIVES AT ALL. _value_score below is unchanged and is
    # still what production runs. It was calibrated against TARGET AMOUNTS —
    # the 2026-09-02 note quotes Rs 7,031 / 25,901 / 211,701 / 500,000 — but it
    # is fed EXPECTED values, i.e. a target multiplied by a probability below 1.
    # While that probability was effectively a constant 0.43 the mismatch was a
    # scale factor and nothing more. With a calibrated probability (mean 0.27,
    # anti-correlated with balance at -0.41) it becomes structural:
    #
    #   100% of expected values now fall BELOW the knee of 25,000
    #   -> log1p(x/25000) runs entirely in its near-linear region
    #   -> then divides by log1p(12) = 2.565
    #   -> the term uses 3.9% of its available 0-1 range at p90
    #
    # So the log is not taming a heavy tail; there is no tail above the knee.
    # It is shrinking a nearly-linear signal until proximity (range 0.40)
    # outvotes it about 26 to 1, whatever the nominal 0.45 weight says.
    #
    # THE CONSTANTS BELOW ARE FIXED RUPEE FIGURES, NOT POOL-DERIVED. That is the
    # same deliberate choice _value_score documents: a pool-relative scale would
    # make an identical case score differently depending on what else happened
    # to be planned that night, which is not a property anything called an audit
    # trail can have. They were set once, from the measured expected-value
    # distribution on a 1,200-case book:
    #
    #   EV under the current formula   p10   476   p50  2,051   p90  7,458   p99 21,538
    #   EV under the calibrated model  p10   294   p50    835   p90  2,602   p99  6,296
    #
    # so a knee near 1,000 sits between the two medians and a reference of
    # 20,000 sits just under the wider distribution's p99.
    # LOCKED 2026-09-08 after a sensitivity sweep, not chosen by eye. Every
    # combination on an 8x span of knee (500-4,000) and a 4x span of reference
    # (10,000-40,000) beat the production baseline on realised recovery in 3 of
    # 3 seeds, from +21.2% to +38.3%, with the BLOCKED set identical throughout
    # and the largest-balance quintile's share of the plan ranging 0.248-0.294
    # against a baseline of 0.261. The result is therefore a property of the
    # RESCALING, not of these two numbers.
    #
    # 1,000 / 20,000 is deliberately the MIDDLE of that grid rather than its
    # best-scoring corner (500 / 10,000, +38.3%): picking the maximum would be
    # fitting the constants to the measurement, and the mid-grid choice leaves
    # the value term's influence just BELOW proximity's (ratio 0.910) rather
    # than just above it.
    VALUE_EV_KNEE_INR = 1_000.0
    VALUE_EV_REFERENCE_INR = 20_000.0

    @classmethod
    def _value_log_rescaled(cls, expected_inr: float) -> float:
        """The CURRENT SHAPE, rescaled to the quantity actually being fed to it."""
        if expected_inr <= 0:
            return 0.0
        ceiling = math.log1p(cls.VALUE_EV_REFERENCE_INR / cls.VALUE_EV_KNEE_INR)
        return min(1.0, math.log1p(expected_inr / cls.VALUE_EV_KNEE_INR) / ceiling)

    @classmethod
    def _value_sqrt(cls, expected_inr: float) -> float:
        """Square root of the normalised expected value.

        Spreads the low end harder than a log without being linear. Bounded, so
        it cannot reproduce the 2026-09-02 failure where an unbounded linear
        term let a Rs 500,000 case score 12.0 against a proximity maximum of 1.0.
        """
        if expected_inr <= 0:
            return 0.0
        return min(1.0, math.sqrt(expected_inr / cls.VALUE_EV_REFERENCE_INR))

    @classmethod
    def _value_power(cls, expected_inr: float, alpha: float = 0.35) -> float:
        """A tunable power curve; alpha < 0.5 spreads the low end more than sqrt."""
        if expected_inr <= 0:
            return 0.0
        return min(1.0, (expected_inr / cls.VALUE_EV_REFERENCE_INR) ** alpha)

    @classmethod
    def _value_linear_capped(cls, expected_inr: float) -> float:
        """Bounded linear. The honest control: if this wins, the log was the
        whole problem rather than its scale."""
        if expected_inr <= 0:
            return 0.0
        return min(1.0, expected_inr / cls.VALUE_EV_REFERENCE_INR)

    #: Selectable by GlobalAllocator(value_transform=...). "log_current" is the
    #: default and is production; nothing in the product selects another.
    VALUE_TRANSFORMS = {
        "log_current": lambda cls, v: cls._value_score(v),
        "log_rescaled": lambda cls, v: cls._value_log_rescaled(v),
        "sqrt": lambda cls, v: cls._value_sqrt(v),
        "power_035": lambda cls, v: cls._value_power(v, 0.35),
        "linear_capped": lambda cls, v: cls._value_linear_capped(v),
    }

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
        territory_radius_km: float = DEFAULT_TERRITORY_RADIUS_KM,
        eb_adjuster: EmpiricalBayesAgentAdjuster | None = None,
        ml_recovery_probability: dict[str, float] | None = None,
        use_ml_affinity: bool = False,
        value_transform: str = "log_current",
        exploration_rate: float = 0.0,
        exploration_seed: int | None = None,
    ):
        """
        ml_recovery_probability
            case_id -> P(the borrower makes a material payment next cycle), from
            the trained recovery_risk model. Optional. When supplied it is always
            RECORDED in every decision's score_breakdown, whether or not it is
            used, so a shadow run leaves a full audit of what the model would
            have said.
        use_ml_affinity
            False (the default, and what production runs) means the utility is
            computed exactly as before and the model's number is carried along
            for comparison only. True means the model drives prob_recovery.
            Nothing in the product sets this to True — only
            scripts/shadow_allocation_ml.py does, to build the counterfactual.
        """
        self.objective = objective
        self.territory_radius_km = territory_radius_km
        self.eb_adjuster = eb_adjuster or EmpiricalBayesAgentAdjuster()
        self.ml_recovery_probability = ml_recovery_probability or {}
        self.use_ml_affinity = use_ml_affinity
        if value_transform not in self.VALUE_TRANSFORMS:
            raise ValueError(f"unknown value_transform {value_transform!r}; "
                             f"expected one of {sorted(self.VALUE_TRANSFORMS)}")
        self.value_transform = value_transform
        # EPSILON-GREEDY EXPLORATION. Off by default; PlannerService supplies the
        # configured rate. See _explore for what it does and why it is safe.
        if not 0.0 <= exploration_rate <= 1.0:
            raise ValueError(f"exploration_rate must be in [0,1], got {exploration_rate}")
        self.exploration_rate = exploration_rate
        self.exploration_seed = exploration_seed

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

        # Agents that survived EVERY hard gate for each case. Captured here, as
        # the matrix is built, because this is the only place the gates are
        # evaluated — reconstructing it later would mean a second copy of the
        # rules, which is how this repo's worst bugs have started. Exploration
        # draws exclusively from these sets.
        eligible_agents: dict[str, set[str]] = {}

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

            # Hard Gates: DNC and Hostility — case-level, one call. The reason
            # strings and block_type values are the ones this run has always
            # written; only where they are defined has moved (case_bar, above).
            blocked_by = case_bar(customer)
            if blocked_by is not None:
                decisions.append(AllocationDecision(
                    id=str(uuid.uuid4()),
                    run_id="",
                    case_id=case.id,
                    previous_agent_id=case.agent_id,
                    allocated_agent_id=None,
                    outcome=AllocationOutcome.BLOCKED.value,
                    reason=BAR_MESSAGES[blocked_by],
                    visit_priority_score=prio,
                    fit_score=0.0,
                    score_breakdown={"block_type": blocked_by},
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
                # Hard Gate: STICKY CASE OWNERSHIP.
                #
                # A case that already has an agent is only ever offered back to
                # THAT agent. Until 2026-09-11 ownership was a soft preference —
                # CONTINUITY_BONUS, one indicator at objective weight 0.05 — and
                # it lost routinely: measured on the real book, 35 of 214
                # allocated cases (16%) changed hands in a single night, with
                # nothing recording why beyond "a better score elsewhere". A
                # borrower met three different agents in a week and each one
                # arrived knowing nothing about the household.
                #
                # This gate is written here, beside the other four, because this
                # loop is the single place eligibility is decided; expressing
                # ownership anywhere else would mean a second copy of the rule.
                # Three consequences follow from it being a gate rather than a
                # score, and all three are wanted:
                #
                #   * the OTHER gates still win. An owner who is barred by PTP
                #     fatigue, the female-agent requirement or the territory
                #     radius is skipped exactly as before, which leaves the case
                #     with no eligible column at all — so it is deferred by the
                #     existing unallocated path rather than handed to somebody
                #     else. Releasing it to the next-best agent is a manager's
                #     decision now, through POST /manager/cases/{id}/reassign.
                #   * capacity cannot be breached. The owner's columns are their
                #     own capacity slots, so an owner holding more cases than
                #     they can work gets exactly max_cases_per_day of them and
                #     the remainder defer.
                #   * exploration cannot move an owned case, because
                #     `eligible_agents` below is built from what survives this
                #     loop and will contain only the owner.
                #
                # CONTINUITY_BONUS is deliberately left in place and still
                # scored. It now only ever applies to the owner's own columns,
                # where it is a constant and cannot change the choice, but it
                # remains in the decision breakdown the audit panel reads, and
                # it is what still expresses the preference for an UNASSIGNED
                # case that this agent worked before.
                #
                # Hard Gate: Female agent constraint — see pair_bar().
                #
                # Hard Gate: PTP fatigue. Three promises to the same agent with
                # nothing collected means the pairing is not working, whatever the
                # score says. Barring the agent rather than the case is deliberate:
                # the borrower still owes the money and should still be visited,
                # just by somebody else. Because this only removes one column from
                # the row, the bipartite solve then picks that case's next best
                # agent by itself — no reassignment pass, no special casing.
                # (Under sticky ownership "next best" is the owner or nobody, and
                # "nobody" is a deferral the manager can see and act on.)
                #
                # Hard Gate: Territory Radius Boundary.
                #
                # All four pair-level gates are evaluated by pair_bar() at module
                # scope — one definition, shared with manager reassignment —
                # and the distance it needs is computed here because the
                # proximity score below reuses it.
                base_lat = ag.base_latitude if ag.base_latitude is not None else self.FALLBACK_LAT
                base_lon = ag.base_longitude if ag.base_longitude is not None else self.FALLBACK_LON
                dist_km = self.haversine_km(base_lat, base_lon, cust_lat, cust_lon)
                if pair_bar(case, customer, ag, dist_km=dist_km,
                            territory_radius_km=self.territory_radius_km,
                            fatigued_agent_ids=fatigued) is not None:
                    continue

                # A SET, NOT A LIST. The loop below iterates over capacity
                # SLOTS, so an agent with 15 slots was appended 15 times. Two
                # consequences, both wrong: rng.choice became weighted by
                # capacity rather than uniform over agents, and the recorded
                # propensity (1/k) described a uniform draw that never happened.
                # Observed on a live run: "among 93 eligible agents" for a
                # manager who has 15.
                eligible_agents.setdefault(case.id, set()).add(ag.id)

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
                # Both probabilities are computed on every pair. The decision
                # uses the current one unless use_ml_affinity is set, which only
                # the shadow comparator does — production behaviour is unchanged
                # by construction rather than by a flag being read correctly.
                prob_recovery = self._prob_recovery(affinity_score, tier_weight)
                ml_p = self.ml_recovery_probability.get(case.id)
                prob_recovery_ml = (
                    self._prob_recovery_ml(ml_p, eb_multiplier, tier_weight)
                    if ml_p is not None else None)

                effective_prob = (prob_recovery_ml
                                  if (self.use_ml_affinity and prob_recovery_ml is not None)
                                  else prob_recovery)
                expected_case_inr = target_inr * effective_prob
                inr_score = self.VALUE_TRANSFORMS[self.value_transform](
                    type(self), expected_case_inr)

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
                        # Shadow columns. Present on every decision whenever the
                        # model supplied a probability for the case, so the
                        # comparison can be rebuilt from persisted rows later
                        # rather than only from a script's stdout.
                        "prob_recovery": round(prob_recovery, 4),
                        "prob_recovery_ml": (round(prob_recovery_ml, 4)
                                             if prob_recovery_ml is not None else None),
                        "ml_borrower_p_recover": (round(ml_p, 4)
                                                  if ml_p is not None else None),
                        "ml_used_for_decision": bool(
                            self.use_ml_affinity and prob_recovery_ml is not None),
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
                        "value_transform": self.value_transform,
                        "inr_score": round(inr_score, 4),
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
                # Read back the probability the utility was actually built from,
                # rather than recomputing one. Recomputing is how the plan and
                # its own forecast came to disagree once before (see
                # _prob_recovery, merged 2026-09-03) — and under a shadow run
                # the two branches would silently diverge here.
                prob_rec = bdown.get("prob_recovery_ml") if bdown.get("ml_used_for_decision") \
                    else bdown.get("prob_recovery")
                if prob_rec is None:
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

        # -------------------------------------------------------------
        # EPSILON-GREEDY EXPLORATION
        # -------------------------------------------------------------
        exploration_log, exploration_delta = self._explore(
            assigned_by_agent, decisions, agents, eligible_agents)
        expected_recovery_sum += exploration_delta

        # Handle Unallocated Cases
        allocated_case_ids = {c.id for c_list in assigned_by_agent.values() for c in c_list}
        decided_case_ids = {d.case_id for d in decisions}

        for case in cases:
            if case.id not in allocated_case_ids and case.id not in decided_case_ids:
                # The OUTCOME is unchanged — still DEFERRED, still the same
                # existing path. Only the sentence changes, and it has to:
                # sticky ownership gives a second way to land here, and
                # "all nearby agents reached daily capacity limit" would be
                # flatly untrue of a case whose owner was barred by PTP fatigue
                # while fourteen other agents sat idle. A manager reading the
                # audit trail to decide whether to reassign needs to know which
                # of the two happened.
                owned = case.agent_id is not None
                reason = (
                    "Deferred: this case stays with its assigned agent, who "
                    "could not take it tomorrow — their day is full, or a "
                    "hard rule (PTP fatigue, territory, safety) bars the "
                    "pairing. Reassign the case to move it."
                    if owned else
                    "Deferred to next cycle: all nearby agents reached daily capacity limit."
                )
                decisions.append(AllocationDecision(
                    id=str(uuid.uuid4()),
                    run_id="",
                    case_id=case.id,
                    previous_agent_id=case.agent_id,
                    allocated_agent_id=None,
                    outcome=AllocationOutcome.DEFERRED.value,
                    reason=reason,
                    visit_priority_score=prio_scores.get(case.id, 50.0),
                    fit_score=0.0,
                    score_breakdown={"objective": self.objective,
                                     "owner_unavailable": owned},
                ))

        return assigned_by_agent, decisions, expected_recovery_sum
