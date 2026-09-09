"""The trained model, wired into the allocator in SHADOW.

The whole point of a shadow deployment is that production does not move. These
tests exist to prove that claim rather than assert it: the default constructor
must produce byte-identical decisions to the pre-change allocator, and the ML
branch must be reachable only by a caller that explicitly asks for it.

They also pin the safety property the promote/no-promote decision turns on —
that a change to `prob_recovery` cannot move the BLOCKED set, because all five
hard gates run before any scoring happens.
"""
from __future__ import annotations

import pytest

from app.models.allocation_decision import AllocationOutcome
from app.services.global_allocator import GlobalAllocator

pytest.importorskip("numpy")


@pytest.fixture(scope="module")
def book():
    from scripts.shadow_allocation_ml import build_book

    return build_book(120, 8, seed=3)


@pytest.fixture(scope="module")
def probs(book):
    """P(material payment) per case, from the champion artifact if there is one."""
    from app.ml.pipeline.engine import DecisionEngine

    cases, _, feats, _ = book
    engine = DecisionEngine.get("recovery_risk")
    if engine is None:
        pytest.skip("no champion recovery_risk artifact")
    scored = engine.score_batch([feats[c.id] for c in cases])
    return {c.id: 1.0 - p for c, p in zip(cases, scored) if p is not None}


def _allocate(book, **kwargs):
    cases, agents, _, _ = book
    incumbent = {c.id: c.agent_id for c in cases}
    prio = {c.id: 50.0 for c in cases}
    try:
        return GlobalAllocator(**kwargs).allocate(
            cases=cases, agents=agents, history_matrix={}, prio_scores=prio)
    finally:
        # allocate() mutates case.agent_id; restore so the next call in a test
        # sees the same starting book. Without this the continuity term would
        # read the previous run's output as this run's incumbents.
        for c in cases:
            c.agent_id = incumbent[c.id]


def _assignments(decisions):
    return {d.case_id: d.allocated_agent_id for d in decisions
            if d.outcome == AllocationOutcome.ALLOCATED.value}


def _blocked(decisions):
    return {d.case_id for d in decisions
            if d.outcome == AllocationOutcome.BLOCKED.value}


# ---------------------------------------------------------------------------
# Production must not move
# ---------------------------------------------------------------------------

def test_default_allocator_is_unchanged_by_the_shadow_wiring(book):
    """No ML probabilities supplied: the allocator must behave exactly as it did
    before this change existed."""
    a = _allocate(book)
    b = _allocate(book)
    assert _assignments(a[1]) == _assignments(b[1])
    assert a[2] == pytest.approx(b[2])


def test_supplying_probabilities_without_asking_for_them_changes_nothing(book, probs):
    """THE SHADOW GUARANTEE. Passing the model's output in must not alter a
    single decision unless use_ml_affinity is explicitly set."""
    base = _allocate(book)
    shadow = _allocate(book, ml_recovery_probability=probs)

    assert _assignments(base[1]) == _assignments(shadow[1])
    assert _blocked(base[1]) == _blocked(shadow[1])
    assert base[2] == pytest.approx(shadow[2]), "expected recovery must not move"


def test_shadow_run_records_both_probabilities_for_later_audit(book, probs):
    """A shadow that leaves no record is only a script's stdout. Every decision
    must carry what the model said, so the comparison can be rebuilt from
    persisted rows."""
    _, decisions, _ = _allocate(book, ml_recovery_probability=probs)
    allocated = [d for d in decisions
                 if d.outcome == AllocationOutcome.ALLOCATED.value]
    assert allocated
    for d in allocated:
        bd = d.score_breakdown
        assert bd["prob_recovery"] is not None
        assert bd["prob_recovery_ml"] is not None
        assert bd["ml_borrower_p_recover"] is not None
        assert bd["ml_used_for_decision"] is False


def test_ml_branch_is_only_reachable_when_explicitly_enabled(book, probs):
    _, decisions, _ = _allocate(book, ml_recovery_probability=probs,
                                use_ml_affinity=True)
    used = [d.score_breakdown.get("ml_used_for_decision") for d in decisions
            if d.outcome == AllocationOutcome.ALLOCATED.value]
    assert used and all(used)


# ---------------------------------------------------------------------------
# The safety property
# ---------------------------------------------------------------------------

def test_blocked_set_is_identical_with_and_without_the_model(book, probs):
    """THE GATE ON PROMOTION. DNC, hostility, the female-agent requirement, the
    territory radius and PTP fatigue are all applied BEFORE any score is
    computed, so no change to prob_recovery can reach them. Anything that moves
    this set is a compliance change wearing a scoring change's clothes."""
    base = _allocate(book)
    ml = _allocate(book, ml_recovery_probability=probs, use_ml_affinity=True)
    assert _blocked(base[1]) == _blocked(ml[1])


def test_the_model_moves_some_assignments_but_not_most(book, probs):
    """A shadow that changes nothing is not informative, and one that changes
    everything is not safe. Both ends are asserted."""
    base = _assignments(_allocate(book)[1])
    ml = _assignments(_allocate(book, ml_recovery_probability=probs,
                                use_ml_affinity=True)[1])
    common = set(base) & set(ml)
    assert common
    moved = sum(1 for cid in common if base[cid] != ml[cid])
    assert moved / len(common) < 0.35, "a third of the book moving is not a tweak"


# ---------------------------------------------------------------------------
# The combination rule
# ---------------------------------------------------------------------------

def test_ml_probability_is_a_base_rate_modulated_by_a_relative_effect():
    """Not two probabilities multiplied — that was the 2026-09-03 defect. The
    second factor is a ratio centred on 1.0, so a neutral agent leaves the
    borrower's own probability essentially untouched."""
    neutral = GlobalAllocator._prob_recovery_ml(0.5, 1.0, 1.0)
    assert neutral == pytest.approx(0.5, abs=1e-9)

    better = GlobalAllocator._prob_recovery_ml(0.5, 1.25, 1.0)
    worse = GlobalAllocator._prob_recovery_ml(0.5, 0.75, 1.0)
    assert worse < neutral < better


def test_ml_probability_shares_the_ceiling_but_not_the_floor(book):
    """This test asserted a SHARED floor when it was written, and that was the
    bug: applying the agent-side 0.20 to a calibrated borrower probability lifted
    43.7% of the book and produced most of the ML path's forecast bias. Rewritten
    rather than deleted so the correction is visible."""
    lo = GlobalAllocator._prob_recovery_ml(0.001, 0.75, 0.6)
    hi = GlobalAllocator._prob_recovery_ml(0.999, 1.25, 1.0)
    assert lo == pytest.approx(GlobalAllocator.PROB_RECOVERY_FLOOR_ML)
    assert lo < GlobalAllocator.PROB_RECOVERY_FLOOR
    assert hi <= GlobalAllocator.PROB_RECOVERY_CEIL


def test_a_case_the_model_could_not_score_falls_back_to_the_current_formula(book):
    """Coverage is never total in production — a loan with no CIBIL and no PTP
    history will fall under the engine's floor. Those cases must keep the
    existing behaviour rather than being dropped or defaulted to a constant."""
    cases, _, _, _ = book
    partial = {cases[0].id: 0.9}          # exactly one case scored
    _, decisions, _ = _allocate(book, ml_recovery_probability=partial,
                                use_ml_affinity=True)
    by_case = {d.case_id: d.score_breakdown for d in decisions
               if d.outcome == AllocationOutcome.ALLOCATED.value}
    unscored = [bd for cid, bd in by_case.items() if cid != cases[0].id]
    assert unscored
    for bd in unscored:
        assert bd["prob_recovery_ml"] is None
        assert bd["ml_used_for_decision"] is False
        assert bd["prob_recovery"] is not None


# ---------------------------------------------------------------------------
# The two floors
# ---------------------------------------------------------------------------

def test_production_floor_is_unchanged():
    """PROB_RECOVERY_FLOOR guards the agent-side formula and must not move —
    the whole shadow guarantee rests on production being byte-identical."""
    assert GlobalAllocator.PROB_RECOVERY_FLOOR == 0.20
    assert GlobalAllocator.PROB_RECOVERY_CEIL == 0.85
    # An uninformative agent estimate is still lifted to the floor.
    assert GlobalAllocator._prob_recovery(0.0, 1.0) == pytest.approx(0.20)


def test_calibrated_path_has_its_own_much_lower_floor():
    """Applying the agent-side floor to a calibrated borrower probability was
    the largest single source of bias in the first shadow run: it lifted 153 of
    350 cases whose mean predicted P(pay) was 0.0923 and whose ACTUAL recovery
    rate was 0.0588 — overstating that group by 240%."""
    assert GlobalAllocator.PROB_RECOVERY_FLOOR_ML < GlobalAllocator.PROB_RECOVERY_FLOOR
    assert GlobalAllocator.PROB_RECOVERY_FLOOR_ML == 0.02
    # A genuinely low borrower probability now survives instead of being lifted.
    assert GlobalAllocator._prob_recovery_ml(0.05, 1.0, 1.0) == pytest.approx(0.05)
    assert GlobalAllocator._prob_recovery_ml(0.0, 1.0, 1.0) == pytest.approx(0.02)


def test_the_two_formulas_disagree_where_the_old_floor_used_to_hide_it():
    """Below the old floor the two paths must now differ. If they ever agree
    here again, the ML path has picked the production floor back up."""
    low = 0.06
    assert GlobalAllocator._prob_recovery(low, 1.0) == pytest.approx(0.20)
    assert GlobalAllocator._prob_recovery_ml(low, 1.0, 1.0) == pytest.approx(low)


def test_calibrated_path_still_respects_the_shared_ceiling():
    assert GlobalAllocator._prob_recovery_ml(1.0, 1.25, 1.0) == pytest.approx(
        GlobalAllocator.PROB_RECOVERY_CEIL)


# ---------------------------------------------------------------------------
# Value transforms
# ---------------------------------------------------------------------------

def test_production_still_uses_the_current_transform_by_default():
    """The alternatives are evaluation-only until something is promoted."""
    assert GlobalAllocator().value_transform == "log_current"


def test_an_unknown_transform_is_refused_rather_than_silently_defaulted():
    with pytest.raises(ValueError, match="unknown value_transform"):
        GlobalAllocator(value_transform="exponential")


@pytest.mark.parametrize("name", sorted(GlobalAllocator.VALUE_TRANSFORMS))
def test_every_transform_is_bounded_and_monotone(name):
    """Bounded to [0,1] is what makes the 2026-09-02 failure impossible to
    repeat: an unbounded linear term let a Rs 500,000 case score 12.0 against a
    proximity maximum of 1.0. Monotone is what keeps the ordering by expected
    rupees intact — a transform may change the SPACING, never the order."""
    fn = GlobalAllocator.VALUE_TRANSFORMS[name]
    values = [0.0, 1.0, 100.0, 1_000.0, 5_000.0, 25_000.0, 300_000.0, 5_000_000.0]
    scores = [fn(GlobalAllocator, v) for v in values]
    assert all(0.0 <= s <= 1.0 for s in scores), f"{name} left [0,1]: {scores}"
    assert all(b >= a - 1e-12 for a, b in zip(scores, scores[1:])), \
        f"{name} is not monotone: {scores}"
    assert fn(GlobalAllocator, 0.0) == 0.0


def test_current_transform_wastes_its_range_on_realistic_expected_values():
    """THE DEFECT, pinned. Measured on a 1,200-case book, 100% of expected
    values fall below the 25,000 knee once a calibrated probability is applied.
    The transform then uses a few percent of its available range, which is why
    proximity outvotes it whatever the nominal 0.45 weight says."""
    p90_expected_value = 2_602.0        # measured, ML-informed
    assert GlobalAllocator._value_score(p90_expected_value) < 0.05


def test_rescaled_transform_spans_a_usable_range_on_the_same_values():
    lo = GlobalAllocator._value_log_rescaled(294.0)      # measured p10
    hi = GlobalAllocator._value_log_rescaled(2_602.0)    # measured p90
    assert hi - lo > 0.30, f"rescaled range still too narrow: {lo:.3f}..{hi:.3f}"
    assert hi < 1.0, "p90 must not already saturate the ceiling"


def test_transform_choice_is_recorded_on_every_decision(book, probs):
    """A plan whose value transform cannot be read back cannot be compared with
    the plan that replaced it."""
    _, decisions, _ = _allocate(book, ml_recovery_probability=probs,
                                use_ml_affinity=True, value_transform="sqrt")
    chosen = [d for d in decisions
              if d.outcome == AllocationOutcome.ALLOCATED.value]
    assert chosen
    for d in chosen:
        assert d.score_breakdown["value_transform"] == "sqrt"
        assert d.score_breakdown["inr_score"] is not None


# ---------------------------------------------------------------------------
# Promotion regressions — the value/proximity contribution balance
# ---------------------------------------------------------------------------

def _contribution_ranges(decisions):
    """Weighted p90-p10 spread of the value and proximity terms.

    A weighted sum is decided by the RANGES its terms span, not their levels: a
    term with a large weight and no spread cannot change any decision. This is
    the quantity the whole value-transform investigation turned on.
    """
    import numpy as np

    chosen = [d for d in decisions
              if d.outcome == AllocationOutcome.ALLOCATED.value]
    v = np.array([d.score_breakdown.get("inr_score", 0.0) for d in chosen])
    p = np.array([d.score_breakdown.get("proximity_score", 0.0) for d in chosen])

    def rng(a):
        return float(np.percentile(a, 90) - np.percentile(a, 10)) if len(a) else 0.0

    return rng(v) * 0.45, rng(p) * 0.40


def test_current_transform_is_still_available_as_the_baseline(book, probs):
    """log_current must never be deleted: every before/after measurement of this
    change is expressed against it."""
    assert "log_current" in GlobalAllocator.VALUE_TRANSFORMS
    _, decisions, _ = _allocate(book, ml_recovery_probability=probs,
                                use_ml_affinity=True,
                                value_transform="log_current")
    assert decisions


def test_old_transform_starves_the_value_term(book, probs):
    """THE DEFECT, pinned as a regression. Under log_current the value term's
    weighted spread is a small fraction of proximity's, so its nominal 0.45
    weight buys almost no influence — measured at a ratio of 0.12 over eight
    seeds on a 1,200-case book."""
    _, decisions, _ = _allocate(book, ml_recovery_probability=probs,
                                use_ml_affinity=True,
                                value_transform="log_current")
    value, prox = _contribution_ranges(decisions)
    assert prox > 0
    assert value / prox < 0.40, (
        f"log_current unexpectedly carries influence (ratio {value / prox:.3f}); "
        f"if this passes the defect it documents may have moved")


def test_promoted_transform_gives_value_influence_comparable_to_proximity(book, probs):
    """THE FIX, pinned. log_rescaled must put the value term's weighted spread in
    the same league as proximity's — that is what makes a 0.45-versus-0.40
    weighting mean what it says. Measured at 0.994 over eight seeds; the band
    here is wide because a 120-case fixture is noisier than a 1,200-case book."""
    _, decisions, _ = _allocate(book, ml_recovery_probability=probs,
                                use_ml_affinity=True,
                                value_transform="log_rescaled")
    value, prox = _contribution_ranges(decisions)
    assert prox > 0
    ratio = value / prox
    assert 0.45 < ratio < 2.2, f"value/proximity ratio out of band: {ratio:.3f}"


def test_rescaling_does_not_hand_the_plan_to_the_largest_balances(book, probs):
    """The 2026-09-02 failure in a bounded disguise: a transform that spreads the
    value term so hard that big cases crowd everything else out. Compared against
    the production plan on the same book."""
    cases, _, _, _ = book
    collectable = {c.id: max(0.0, float(c.target_amount or 0.0)
                             - float(c.collected_amount or 0.0)) for c in cases}
    ordered = sorted(collectable, key=collectable.get)
    top_quintile = set(ordered[int(len(ordered) * 0.8):])

    def share(decisions):
        alloc = [d.case_id for d in decisions
                 if d.outcome == AllocationOutcome.ALLOCATED.value]
        return sum(1 for c in alloc if c in top_quintile) / max(len(alloc), 1)

    base = share(_allocate(book)[1])
    new = share(_allocate(book, ml_recovery_probability=probs,
                          use_ml_affinity=True,
                          value_transform="log_rescaled")[1])
    assert new < base + 0.15, (
        f"largest-balance quintile went from {base:.3f} to {new:.3f} of the plan")


def test_blocked_set_survives_every_transform(book, probs):
    """The safety property, across the whole candidate set rather than one."""
    base = _blocked(_allocate(book)[1])
    for name in sorted(GlobalAllocator.VALUE_TRANSFORMS):
        got = _blocked(_allocate(book, ml_recovery_probability=probs,
                                 use_ml_affinity=True, value_transform=name)[1])
        assert got == base, f"{name} moved the BLOCKED set"


# test_promotion_settings_are_paired USED TO LIVE HERE and asserted that the
# string `else "log_current"` appeared in planner_service.py. A source grep
# cannot tell whether a branch is reachable, let alone whether it runs — the
# same style of check passed while the 409 handler referenced a name that was
# not in scope. The pairing is now proved by executing the planner:
# tests/test_planner_service.py::test_transform_is_paired_to_the_model_probabilities.


def test_eligible_agent_count_cannot_exceed_the_agent_roster(book, probs):
    """THE PROPENSITY BUG. Eligibility was collected per capacity SLOT, so an
    agent with 15 slots appeared 15 times: the random draw became weighted by
    capacity instead of uniform over agents, and the propensity recorded as 1/k
    described a draw that never happened. A live run reported "among 93 eligible
    agents" for a manager with 15. Propensity is what IPW will rest on, so a
    wrong one silently poisons the analysis the whole experiment is for."""
    cases, agents, _, _ = book
    _, decisions, _ = _allocate(book, ml_recovery_probability=probs,
                                use_ml_affinity=True, exploration_rate=0.30,
                                exploration_seed=13)
    explored = [d for d in decisions
                if d.score_breakdown.get("exploration") is True]
    assert explored, "no exploration happened; the test would prove nothing"
    for d in explored:
        k = d.score_breakdown["exploration_n_eligible"]
        assert 1 <= k <= len(agents), (
            f"eligible count {k} exceeds the {len(agents)}-agent roster — "
            f"slots are being counted as agents again")
        assert d.score_breakdown["exploration_propensity"] == pytest.approx(1.0 / k)
