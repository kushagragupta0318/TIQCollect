# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-08-27 — New file. Tests for the visit-priority score.
#
#   THE HARM THIS FILE EXISTS TO PREVENT. A previous ranking attempt ordered
#   cases by DPD band alone. Under a tight visit budget it allocated 0% of
#   visits to NPA — it abandoned the hardest accounts and nothing failed. It was
#   removed on request (archive/dpd-allocation).
#
#   So the load-bearing tests here are not the arithmetic ones. They are:
#     * a large NPA case must outrank a small pre-NPA case
#     * the score order must differ from the DPD order
#     * urgency must peak BEFORE the 90-day line and fall after it
#   Each of those, if it silently reverted, would reproduce the failure while
#   the score kept reporting itself as three-component and balanced.
#
#   Style follows tests/test_recovery_scorecard.py: no database, SimpleNamespace
#   feature stubs, a local features(**over) builder. There is no conftest.py in
#   this suite by design.
# ─────────────────────────────────────────────────────────────────────────────
"""The visit-priority score: value, urgency, effort — and what must not drift."""
from __future__ import annotations

import itertools
from types import SimpleNamespace as NS

from app.ml.repayment_scorecard import _DPD_NEUTRAL
from app.ml.visit_priority import (
    COMPONENT_EFFORT,
    COMPONENT_ORDER,
    COMPONENT_URGENCY,
    COMPONENT_VALUE,
    MAX_EFFORT_PENALTY,
    MAX_URGENCY_POINTS,
    MAX_VALUE_POINTS,
    PTP_PROTECTION_DAYS,
    SCORE_VERSION,
    score,
)


def features(**over):
    """A mid-book case: worthwhile balance, 80 DPD, never visited."""
    base = dict(
        recovery_rate_90=0.40,
        total_outstanding=1_000_000.0,
        dpd=80,
        visit_count=0,
        max_visits_allowed=3,
        ptp_due_in_days=None,
    )
    base.update(over)
    return NS(**base)


def _pts(result, code):
    return next(c["points"] for c in result["components"] if c["code"] == code)


def _component(result, code):
    """The whole component, for the tests that assert on its summary or flags."""
    return next(c for c in result["components"] if c["code"] == code)


# ── The failure this score exists to correct ────────────────────────────────
def test_a_large_npa_case_outranks_a_small_pre_npa_case():
    """THE ONE THAT MATTERS.

    The removed DPD policy sent 0% of visits to NPA because it ranked on
    delinquency band alone, so every pre-NPA case beat every NPA case regardless
    of how much money was behind it. If this assertion ever fails, that policy
    is back under a different name.
    """
    big_npa = score(features(dpd=200, total_outstanding=4_000_000.0))
    small_pre = score(features(dpd=80, total_outstanding=80_000.0))
    assert big_npa["score"] > small_pre["score"], (big_npa, small_pre)


def test_the_score_order_is_not_the_dpd_order():
    """Measured on the live book: rank corr(value, DPD) = -0.163, and 0 of the
    top 20 by value appear in the top 20 by DPD. Encoded here as a standing
    guarantee, because "it balances three things" is exactly the claim that
    quietly stops being true.
    """
    grid = [features(dpd=d, total_outstanding=o)
            for d, o in itertools.product((40, 70, 85, 110, 200),
                                          (50_000.0, 400_000.0, 3_000_000.0))]
    by_score = sorted(range(len(grid)), key=lambda i: -score(grid[i])["score"])
    by_dpd = sorted(range(len(grid)), key=lambda i: -float(grid[i].dpd))
    assert by_score != by_dpd
    # And not merely a reshuffle at the margins: the top pick must differ.
    assert by_score[0] != by_dpd[0]


def test_loan_size_alone_can_change_the_order():
    """Two cases identical but for balance must not tie. If they do, the value
    component has stopped contributing and only DPD is deciding."""
    small = score(features(total_outstanding=60_000.0))
    large = score(features(total_outstanding=5_000_000.0))
    assert large["score"] > small["score"]
    assert _pts(large, COMPONENT_VALUE) > _pts(small, COMPONENT_VALUE)


# ── Urgency: the 90-day line is a turning point, not a label ────────────────
def test_urgency_peaks_before_the_npa_line_and_falls_after_it():
    """The fortnight before 90 days is the last chance to stop the loan being
    classified non-performing, so it is where urgency is highest. Once the line
    is crossed the classification damage is done.

    Guards against the ladder being "simplified" into a monotonic ramp, which
    would make the 90-day line decorative.
    """
    just_before = _pts(score(features(dpd=85)), COMPONENT_URGENCY)
    just_after = _pts(score(features(dpd=100)), COMPONENT_URGENCY)
    much_later = _pts(score(features(dpd=250)), COMPONENT_URGENCY)
    assert just_before == MAX_URGENCY_POINTS
    assert just_before > just_after > much_later


def test_urgency_rises_up_to_the_peak():
    """Monotonic on the way up — a fresher case is never more urgent than an
    older one before the line."""
    ladder = [_pts(score(features(dpd=d)), COMPONENT_URGENCY)
              for d in (20, 40, 55, 70, 85)]
    assert ladder == sorted(ladder), ladder


def test_the_npa_line_comes_from_the_shared_constant():
    """_DPD_NEUTRAL is the RBI line and already exists in repayment_scorecard.
    A second hardcoded 90 here would let the two drift silently."""
    assert _DPD_NEUTRAL == 90.0
    at_line = _pts(score(features(dpd=90)), COMPONENT_URGENCY)
    past_line = _pts(score(features(dpd=91)), COMPONENT_URGENCY)
    assert at_line > past_line
    assert at_line - past_line <= 1.0


# ── Effort: subtracts, unless a promise is about to land ────────────────────
def test_repeat_visits_push_a_case_down():
    """Measured on the benchmark book: a first visit returned ~Rs 6,667 against
    ~Rs 2,872 by the third. Limited hours are better spent on a case nobody has
    tried."""
    ladder = [_pts(score(features(visit_count=n)), COMPONENT_EFFORT)
              for n in (0, 1, 2, 3)]
    assert ladder == sorted(ladder, reverse=True), ladder
    assert ladder[0] == 0.0
    assert ladder[-1] == MAX_EFFORT_PENALTY


def test_exhausting_the_visit_allowance_counts_as_fully_worked():
    """A case at its max_visits_allowed has nothing left to spend, even if that
    max is below three."""
    assert _pts(score(features(visit_count=2, max_visits_allowed=2)),
                COMPONENT_EFFORT) == MAX_EFFORT_PENALTY


def test_an_imminent_meaningful_promise_lifts_but_does_not_erase_effort():
    """A good PTP earns a follow-up, without pretending failed visits never happened."""
    worked = score(features(visit_count=3))
    worked_with_promise = score(features(
        visit_count=3, ptp_due_in_days=0, ptp_committed_amount=80_000.0,
        remaining_target=100_000.0, ptp_resolved_count=2, ptp_kept_count=2,
    ))
    assert _pts(worked, COMPONENT_EFFORT) == MAX_EFFORT_PENALTY
    assert _pts(worked_with_promise, COMPONENT_EFFORT) > _pts(worked, COMPONENT_EFFORT)
    assert _pts(worked_with_promise, COMPONENT_EFFORT) < 0
    assert worked_with_promise["score"] > worked["score"]


def test_a_broken_promise_history_reduces_the_follow_up_lift():
    common = dict(ptp_due_in_days=0, ptp_committed_amount=80_000.0,
                  remaining_target=100_000.0, ptp_resolved_count=2)
    reliable = score(features(**common, ptp_kept_count=2))
    unreliable = score(features(**common, ptp_kept_count=0))
    assert _pts(reliable, COMPONENT_EFFORT) > _pts(unreliable, COMPONENT_EFFORT)


def test_a_distant_promise_does_not_earn_the_exemption():
    """Bounded deliberately. A promise three weeks out is not imminent money,
    and exempting it would waive the penalty on most of the book."""
    far = PTP_PROTECTION_DAYS + 18
    assert _pts(score(features(visit_count=3, ptp_due_in_days=far)),
                COMPONENT_EFFORT) == MAX_EFFORT_PENALTY


# ── Honesty of the output ───────────────────────────────────────────────────
def test_all_three_components_are_always_present_and_in_order():
    """A manager comparing two cases must read the same rows in the same places.
    A component that vanishes when it has no data turns "why is this higher"
    into a hunt."""
    for f in (features(), features(recovery_rate_90=None), features(dpd=None),
              features(total_outstanding=0.0)):
        result = score(f)
        assert [c["code"] for c in result["components"]] == list(COMPONENT_ORDER)


def test_an_unscored_loan_says_so_rather_than_claiming_a_low_balance():
    """'Little or no recoverable balance' is a statement about the LOAN.
    'Not yet estimated' is a statement about our information. Reporting the
    first when the second is true is the mislabelling this codebase keeps
    having to correct."""
    result = score(features(recovery_rate_90=None))
    value = next(c for c in result["components"] if c["code"] == COMPONENT_VALUE)
    assert value["abstained"] is True
    assert "not yet estimated" in result["reason"].lower()
    assert "little or no" not in result["reason"].lower()


def test_a_stale_recovery_estimate_is_discounted_and_flagged_for_rescore():
    fresh = _component(score(features(recovery_rate_age_days=1)), COMPONENT_VALUE)
    stale = _component(score(features(recovery_rate_age_days=8)), COMPONENT_VALUE)
    assert stale["points"] < fresh["points"]
    assert stale["evidence"]["needs_rescore"] is True
    assert "needs rescore" in stale["summary"]


def test_no_rupee_figure_escapes_on_the_payload():
    """tests/test_manager_recovery_surface.py forbids a rupee estimate on a case,
    because a rupee figure on a case row is read as "collect this" and
    rate_90 x total_outstanding is not that. The value component reports POINTS;
    the amount is computed internally and discarded.

    Swept, not spot-checked: no number anywhere in the result may equal it.
    """
    f = features(recovery_rate_90=0.4, total_outstanding=1_000_000.0)
    estimate = 0.4 * 1_000_000.0
    result = score(f)

    def numbers(node):
        if isinstance(node, (int, float)) and not isinstance(node, bool):
            yield float(node)
        elif isinstance(node, dict):
            for k, v in node.items():
                # evidence deliberately carries the two INPUTS so the points can
                # be checked; it is their product that must not appear.
                yield from numbers(v)
        elif isinstance(node, (list, tuple)):
            for v in node:
                yield from numbers(v)

    assert not any(abs(n - estimate) < 1.0 for n in numbers(result)), result


def test_the_score_is_deterministic():
    """case_service._score_case reads datetime.now().hour, so the same case
    ranks differently at 10am and 4pm. This one must not — the allocator's queue
    has to be reproducible, and a manager re-checking an order must see it."""
    runs = [score(features(dpd=77, visit_count=1, ptp_due_in_days=2))
            for _ in range(3)]
    assert runs[0] == runs[1] == runs[2]


def test_the_score_is_never_negative_and_never_over_100():
    """Effort alone can drive the raw sum below zero on a worthless, ancient,
    heavily-worked case. A negative score on a screen reads as a bug."""
    worst = score(features(recovery_rate_90=0.0, total_outstanding=1.0,
                           dpd=400, visit_count=9, max_visits_allowed=3))
    best = score(features(recovery_rate_90=0.95,
                          total_outstanding=50_000_000.0, dpd=85,
                          visit_count=0, ptp_due_in_days=0))
    assert 0.0 <= worst["score"] <= 100.0
    assert 0.0 <= best["score"] <= 100.0
    assert best["score"] > worst["score"]


def test_the_weights_are_what_the_plan_agreed():
    """Pinned because these three numbers ARE the policy. Value and urgency are
    near-orthogonal on real data (rank corr -0.163), so unlike a design whose
    components agree there is no safety net — changing a weight changes the
    order, and it should take a deliberate edit here to do it."""
    assert (MAX_VALUE_POINTS, MAX_URGENCY_POINTS, MAX_EFFORT_PENALTY) == \
        (50.0, 35.0, -25.0)


def test_the_band_gives_the_bare_score_a_meaning():
    """A panel showed "11 / 100" under the heading "Why this case is visited
    FIRST" — a claim that was false for all but a handful of cases. The band is
    what lets a screen say where a case actually sits without asserting a queue
    position the score does not support."""
    from app.ml.visit_priority import BAND_HIGH, BAND_LOW, BAND_MEDIUM, band_for
    assert band_for(0.0) == band_for(34.9) == BAND_LOW
    assert band_for(35.0) == band_for(59.9) == BAND_MEDIUM
    assert band_for(60.0) == band_for(100.0) == BAND_HIGH
    # And every score carries it, so no screen has to derive it.
    for f in (features(), features(recovery_rate_90=None), features(dpd=250)):
        r = score(f)
        assert r["band"] == band_for(r["score"])


def test_it_never_claims_to_be_a_model():
    """A hand-weighted scorecard. Calling a sort order ML would be the single
    most misleading thing this feature could do, and the flag is what the UI
    reads to label it."""
    result = score(features())
    assert result["is_modelled"] is False
    assert result["model_version"] == SCORE_VERSION
    assert "ml" not in SCORE_VERSION.lower().replace("visit-priority", "")


def test_the_reason_agrees_with_the_components():
    """A one-line reason that can contradict the numbers printed beside it is
    worse than no reason. It is derived from the components, not written
    alongside them."""
    worked = score(features(visit_count=3))
    assert "already visited repeatedly" in worked["reason"]
    promised = score(features(visit_count=3, ptp_due_in_days=0))
    assert "promise falling due" in promised["reason"]
    late = score(features(dpd=250))
    assert "past the npa line" in late["reason"].lower()


def test_the_reason_never_contradicts_the_urgency_summary():
    """THE ONE THAT CAUGHT A REAL BUG.

    The urgency ladder is deliberately non-monotonic — it peaks at 76-90 and
    falls past the line — so the SAME points occur at both ends of the DPD
    range: 8 points is "under 30 days", 10 points is "over 180". The reason
    line used to read position off the points, which collapsed those two into
    one branch. On 2026-08-28 that meant 256 of 591 open cases described
    themselves wrongly: 12 days late reading "long past the NPA line" directly
    under a summary saying "12 days late", and 95 days late reading
    "mid-delinquency".

    Swept across the whole range rather than spot-checked, because the failure
    was invisible at the two DPDs anyone would have tried by hand.
    """
    for dpd in (0, 1, 12, 28, 29, 30, 45, 46, 60, 75, 76, 80, 89, 90,
                91, 95, 100, 120, 121, 180, 181, 250, 400):
        result = score(features(dpd=dpd))
        mid = result["reason"].split(", ")[1]
        past_by_reason = "past the" in mid
        past_in_fact = dpd > 90
        assert past_by_reason == past_in_fact, (
            f"dpd {dpd}: reason says {mid!r}, which is "
            f"{'not ' if past_in_fact else ''}past the line")

        # And it must not claim lateness the summary does not support.
        if dpd <= 45:
            assert mid == "early-stage", (dpd, mid)


def test_a_case_with_no_dpd_says_so_instead_of_guessing():
    """Both abstaining components behave the same way. _value already refused
    to call an unmeasured loan empty; urgency used to fall to the floor and be
    described as "long past the NPA line" — a claim about the borrower built
    out of a gap in our own data."""
    result = score(features(dpd=None))
    assert "not recorded" in result["reason"]
    assert "npa line" not in result["reason"].lower()
    urgency = _component(result, COMPONENT_URGENCY)
    assert urgency["abstained"] is True
    assert urgency["points"] is not None       # still scored, still present


def test_only_today_and_tomorrow_promises_receive_a_follow_up_lift():
    today = _component(score(features(ptp_due_in_days=0)), COMPONENT_EFFORT)
    tomorrow = _component(score(features(ptp_due_in_days=1)), COMPONENT_EFFORT)
    later = _component(score(features(ptp_due_in_days=2)), COMPONENT_EFFORT)
    assert "PTP due today" in today["summary"]
    assert "PTP due tomorrow" in tomorrow["summary"]
    assert later["evidence"]["ptp_due_in_days"] == 2
    assert "PTP due" not in later["summary"]
