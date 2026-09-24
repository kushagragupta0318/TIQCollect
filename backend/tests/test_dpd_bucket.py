# ─── CHANGELOG (prototype → product) ───
# New file, 2026-09-07. Pins models/loan.dpd_bucket_for, which replaced SIX
# copies of the same rule — two of which disagreed with the thresholds
# documented on the DPDBucket enum itself.
#
# NEITHER DISAGREEMENT WAS LIVE, and this file proves it rather than asserting
# it: seed_data draws DPD from a list whose minimum is 35 and demo_daily_feed
# from one whose minimum is 32, so the missing branches were unreachable. The
# consolidation is therefore a refactor, and the equivalence tests below are
# what make that claim checkable instead of a promise in a comment.
#
# It matters more than a tidy-up because EmpiricalBayesAgentAdjuster is KEYED on
# this bucket: two spellings of a boundary put an agent's evidence in one cell
# and the lookup in another, and nothing anywhere would fail.
import pytest

from app.models.loan import DPDBucket, dpd_bucket_for


def test_the_boundaries_are_the_ones_the_enum_documents():
    assert dpd_bucket_for(0) == DPDBucket.CURRENT
    assert dpd_bucket_for(1) == DPDBucket.BUCKET_1
    assert dpd_bucket_for(30) == DPDBucket.BUCKET_1
    assert dpd_bucket_for(31) == DPDBucket.BUCKET_2
    assert dpd_bucket_for(60) == DPDBucket.BUCKET_2
    assert dpd_bucket_for(61) == DPDBucket.BUCKET_3
    assert dpd_bucket_for(90) == DPDBucket.BUCKET_3
    assert dpd_bucket_for(91) == DPDBucket.NPA


def test_none_and_negative_read_as_current_not_as_a_crash():
    """DPD is nullable upstream and a negative can arrive from a bad feed. A
    bucket function that raises inside the nightly scorer is worse than one that
    reads an absent delinquency as no delinquency."""
    assert dpd_bucket_for(None) == DPDBucket.CURRENT
    assert dpd_bucket_for(-5) == DPDBucket.CURRENT
    assert dpd_bucket_for(0.0) == DPDBucket.CURRENT
    assert dpd_bucket_for(45.9) == DPDBucket.BUCKET_2      # truncates, as before


def test_it_is_monotone_and_total_over_the_whole_range():
    order = [DPDBucket.CURRENT, DPDBucket.BUCKET_1, DPDBucket.BUCKET_2,
             DPDBucket.BUCKET_3, DPDBucket.NPA]
    seen = [order.index(dpd_bucket_for(d)) for d in range(0, 500)]
    assert seen == sorted(seen), "buckets must never go backwards as DPD rises"
    assert set(seen) == set(range(5)), "every bucket must be reachable"


# ── Equivalence with the copies it replaced ─────────────────────────────────

def _old_seed_data(dpd):
    if dpd <= 30:
        return DPDBucket.BUCKET_1
    elif dpd <= 60:
        return DPDBucket.BUCKET_2
    elif dpd <= 90:
        return DPDBucket.BUCKET_3
    return DPDBucket.NPA


def _old_demo_feed(dpd):
    return (DPDBucket.NPA if dpd > 90
            else DPDBucket.BUCKET_3 if dpd > 60 else DPDBucket.BUCKET_2)


def _old_ingest(dpd):
    if dpd == 0:
        return DPDBucket.CURRENT
    elif dpd <= 30:
        return DPDBucket.BUCKET_1
    elif dpd <= 60:
        return DPDBucket.BUCKET_2
    elif dpd <= 90:
        return DPDBucket.BUCKET_3
    return DPDBucket.NPA


def test_the_two_copies_that_agreed_are_reproduced_exactly():
    """ingest_daily and backfill_eb_features were already correct. If this ever
    fails, the consolidation changed a live path."""
    for dpd in range(0, 500):
        assert dpd_bucket_for(dpd) == _old_ingest(dpd), dpd


def test_no_value_seed_data_actually_generates_changes_bucket():
    """seed_data's copy had no CURRENT branch, but DPD_CHOICES starts at 35 —
    so the branch was unreachable and nothing it writes moves."""
    from scripts.seed_data import DPD_CHOICES
    assert min(DPD_CHOICES) >= 31, "the unreachability argument no longer holds"
    for dpd in DPD_CHOICES:
        assert dpd_bucket_for(dpd) == _old_seed_data(dpd), dpd
    # The fixed demo fixtures had their own inline copy (the seventh), with
    # neither CURRENT nor BUCKET_1. Every DPD it is handed is >= 33.
    demo_dpds = [95, 62, 45, 38, 55, 33, 44, 120, 44, 72, 55, 35]
    for dpd in demo_dpds:
        assert dpd_bucket_for(dpd) == _old_demo_feed(dpd), dpd


def test_no_value_the_demo_feed_generates_changes_bucket():
    """demo_daily_feed's inline chain had neither CURRENT nor BUCKET_1, but it
    draws from [32, 47, 65, 88, 95, 120, 155]."""
    feed_values = [32, 47, 65, 88, 95, 120, 155]
    for dpd in feed_values:
        assert dpd_bucket_for(dpd) == _old_demo_feed(dpd), dpd


def test_the_disagreements_were_real_even_if_unreachable():
    """The whole reason for consolidating: widen either range by one value and
    the old copies start writing the wrong segment key, silently."""
    assert _old_seed_data(0) == DPDBucket.BUCKET_1
    assert dpd_bucket_for(0) == DPDBucket.CURRENT
    assert _old_demo_feed(5) == DPDBucket.BUCKET_2
    assert dpd_bucket_for(5) == DPDBucket.BUCKET_1


def test_every_caller_now_delegates_rather_than_restating():
    """One definition, one place. A seventh copy is the failure mode here."""
    import inspect
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1]
    for rel in ("scripts/seed_data.py", "scripts/ingest_daily.py",
                "app/workers/tasks/demo_daily_feed.py"):
        src = (root / rel).read_text(encoding="utf-8")
        assert "dpd_bucket_for" in src, rel
        # No local re-derivation of the boundaries alongside the delegation.
        assert "DPDBucket.BUCKET_3 if" not in src, rel
