"""core/guardrails (owner-directed RAG + guardrails build, 2026-10-07): the
figure-fabrication guard and the tone/compliance guard — each a catch case
AND a false-positive-avoidance case, since a guard that flags ordinary
fluent writing is as unusable as one that catches nothing.

Prompt-injection on free text is NOT tested here — it is
`core/prompting.fence()`'s job (tests/test_prompting.py already pins the
escape attempts: a field closing its own fence, opening a fence of its
own, breaking out through its label). See guardrails.py's own docstring
for why a second mechanism here would be a restatement, not a layer.
"""
from __future__ import annotations

from app.core import guardrails as G


# ── check_figures ────────────────────────────────────────────────────────────

def test_a_fabricated_figure_is_caught():
    source = "Collection efficiency is 68.5%. Collectible due this month is Rs 24,50,000."
    generated = "Collection efficiency reached an exceptional 94.2% this month."
    out = G.check_figures(generated, source)
    assert out.ok is False
    assert 94.2 in out.offending


def test_a_figure_that_matches_the_source_with_rounding_passes():
    source = "Collection efficiency is 68.47%."
    generated = "Collection efficiency is around 68.5% this month."
    out = G.check_figures(generated, source)
    assert out.ok is True


def test_small_counts_and_years_in_prose_are_not_flagged_as_fabricated_figures():
    source = "Collection efficiency is 68.5%."
    generated = "Over the 3 months to October 2026, collection efficiency held near 68.5% across 7 agencies."
    out = G.check_figures(generated, source)
    assert out.ok is True


def test_a_number_with_no_source_figures_at_all_still_catches_a_large_invented_one():
    out = G.check_figures("The agency collected Rs 12,00,000 this week.", "No KPI could be computed this period.")
    assert out.ok is False
    assert 1200000.0 in out.offending


# ── check_tone ───────────────────────────────────────────────────────────────

def test_a_coercive_ultimatum_is_caught():
    out = G.check_tone("Pay the full amount today or face serious consequences for you and your family.")
    assert out.ok is False


def test_a_shaming_threat_is_caught():
    out = G.check_tone("If you do not pay, we will make sure your neighbours will know about this debt.")
    assert out.ok is False


def test_a_firm_but_fair_reminder_is_not_flagged():
    # The exact false-positive case the coordinator asked for: a specific,
    # factual reminder must read as fine, not coercive.
    out = G.check_tone("Please pay the outstanding Rs 8,000 by Friday to avoid further action on this case.")
    assert out.ok is True


def test_a_factual_legal_escalation_notice_is_not_flagged():
    # A statement of fact about the loan's status is explicitly allowed by
    # the fair-practice guidance (core/rag/corpus/rbi_fair_practices.md) --
    # only a threat delivered as an ultimatum or implying harm is not.
    out = G.check_tone("This case has moved to the legal escalation stage following three missed promises.")
    assert out.ok is True
