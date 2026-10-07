"""The figure/tone guards wired into manager.py's three narrative routes
(briefing, agent_insight, monthly_report) — owner-directed RAG + guardrails
build, 2026-10-07.

Asserted on the source, same reasoning tests/test_ai_report_prompt_safety.py
already gives for visit_strategy: each route builds its response from a
large, inline block of DB-derived locals with no existing fixture seam
cheap enough to invoke the route itself, and the property under test is
how the guard is WIRED — whether `ai_generated` tracks the actual
accept/reject decision, not the raw LLM-seam flag. check_figures/
check_tone's own behaviour is covered by tests/test_guardrails.py; the
report_templates.py pair (board_report_commentary, agency_review_commentary)
has the dynamic equivalent of this file in
tests/test_report_templates_guardrails.py, going through the real seam via
llm.use_fake() — those two purposes sit on small, fixture-free dataclasses,
which is why they could get the stronger test and these three could not.
"""
from __future__ import annotations

import pathlib

import pytest

SRC = (pathlib.Path(__file__).resolve().parents[1]
      / "app" / "api" / "v1" / "endpoints" / "manager.py").read_text(encoding="utf-8")

_PURPOSES = ("briefing", "agent_insight", "monthly_report")


def _region(purpose: str) -> str:
    """Everything from this purpose string up to the NEXT one of the three
    (or EOF) — the slice a check added to the wrong site would fall outside."""
    start = SRC.index(f'purpose="{purpose}"')
    others = sorted(SRC.index(f'purpose="{p}"') for p in _PURPOSES if p != purpose)
    end = next((o for o in others if o > start), len(SRC))
    return SRC[start:end]


@pytest.mark.parametrize("purpose,figure_var,tone_checked", [
    ("briefing", "_op_data", False),
    ("agent_insight", "_agent_data", True),
    ("monthly_report", "scope_stats", False),
])
def test_each_purpose_gates_acceptance_on_the_figure_guard(purpose, figure_var, tone_checked):
    region = _region(purpose)
    assert "guardrails.check_figures(" in region and figure_var in region
    if tone_checked:
        assert "guardrails.check_tone(" in region
    # reference_block is built into the PROMPT, shortly before this purpose's
    # own `purpose="..."` call -- looked for just before the region starts.
    start = SRC.index(f'purpose="{purpose}"')
    assert "rag.reference_block(" in SRC[max(0, start - 1200):start]


@pytest.mark.parametrize("purpose", _PURPOSES)
def test_ai_generated_in_each_region_tracks_acceptance_not_the_raw_seam_flag(purpose):
    region = _region(purpose)
    assert '"ai_generated": _ai_accepted' in region
    # The exact bug this wiring exists to avoid: reporting the seam's own
    # flag lets a guard-rejected response still claim to be AI-written.
    assert ".ai_generated,\n" not in region.split('"ai_generated"', 1)[-1][:40]
