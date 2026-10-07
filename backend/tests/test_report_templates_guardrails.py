"""The figure-fabrication guard wired into _board_narrative / _agency_narrative
(owner-directed RAG + guardrails build, 2026-10-07). Goes through the real
LLM seam via `llm.use_fake()` (no network, no SDK needed — `_candidates()`
returns the fake provider unconditionally once installed), so this is the
actual call path production runs, minus the network.
"""
from __future__ import annotations

from app.core import llm
from app.services.bank.kpi_catalog import Overview
from app.services.bank.report_templates import _agency_narrative, _board_narrative

BANK_ID = "bank-1"


def _overview(*lines: str) -> Overview:
    return Overview(as_of=None, narrative=list(lines))


def test_board_narrative_accepts_a_generated_paragraph_that_only_cites_given_figures():
    ov = _overview("Collection efficiency is 68.5%.", "Delinquent exposure is Rs 24.5 Cr.")
    with llm.use_fake(["Collections held at 68.5% this period, with delinquent exposure of Rs 24.5 Cr."]):
        out = _board_narrative(ov, "Girivan Finance Ltd", BANK_ID)
    assert out.ai_generated is True


def test_board_narrative_falls_back_to_the_template_on_a_fabricated_figure():
    ov = _overview("Collection efficiency is 68.5%.")
    with llm.use_fake(["Collections reached an outstanding 94.2% this period, a record high."]):
        out = _board_narrative(ov, "Girivan Finance Ltd", BANK_ID)
    assert out.ai_generated is False
    assert out.text == "Collection efficiency is 68.5%."


def test_agency_narrative_falls_back_to_the_template_on_a_fabricated_figure():
    card = {"n_rows": 0}
    with llm.use_fake(["This agency recovered Rs 50,00,000 against target, far exceeding expectations."]):
        out = _agency_narrative(card, "Aravalli Field Services", "Girivan Finance Ltd", BANK_ID)
    assert out.ai_generated is False


def test_agency_narrative_accepts_a_generated_paragraph_with_no_numbers_at_all():
    card = {"n_rows": 0}
    with llm.use_fake(["No scorecard rows were recorded for this agency in the period under review."]):
        out = _agency_narrative(card, "Aravalli Field Services", "Girivan Finance Ltd", BANK_ID)
    assert out.ai_generated is True
