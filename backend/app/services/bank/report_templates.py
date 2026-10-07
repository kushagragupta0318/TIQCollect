# ─── CHANGELOG (standalone plan) ─────────────────────────────────────────────
# 2026-10-07 — New file. E10: the two report packs the Board Reports page
#   offers (board, agency_review), built from the SAME figures the Command
#   Center screens already show — compute_overview (C01) and agency_scorecard
#   (§6.2) — reshaped into the typed ReportPayload (E09) and never recomputed.
#   A KPI with no clean unit in payload.Unit (the scorecard's "score" and
#   "₹ per ₹100" shapes) is left out of the typed Kpi tiles and shown instead
#   in a plain register table, pre-formatted by the SAME formatter the figure
#   already has one of (kpi_catalog's own, or app.reports.formatting) — never
#   a third, reinvented one.
# ────────────────────────────────────────────────────────────────────────────
"""Board Pack and Agency Review: ReportPayload builders over real bank data.

No business figure is computed here. Every number comes from
`kpi_catalog.compute_overview` or `agency_scorecard.agency_scorecard` — the
same functions the Overview and Agencies-tab screens call — and is carried
through as the RAW value with its declared unit, so the report's renderers
(app/reports/render_*) format it exactly as formatting.py defines, once.

The AI commentary is a genuine LLM call (core/llm.py); `Narrative.ai_generated`
is only ever True when the model actually answered, never on a template
fallback (core/llm.py's own contract — `ChatResult.ai_generated` is true only
on a successful response).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.core import llm
from app.ml.pipeline.monitor import serving_version as _ml_serving_version
from app.models.tenancy import Agency, Bank
from app.reports import formatting as rfmt
from app.reports.payload import Column, Direction, Kpi, Narrative, ReportPayload, Section, Table, Unit
from app.services.bank.agency_scorecard import agency_scorecard
from app.services.bank.kpi_catalog import KPIS, SCORECARD, Overview, available_views, compute_overview
from app.services.bank.kpi_filter import KpiFilter
from app.services.bank.model_showcase import LIVE_EQUIVALENT, TRAINED_MODEL, configured_version

#: Only KPIs whose kpi_catalog unit maps cleanly onto a payload.Unit become a
#: typed Kpi tile (and so get a real number + axis-aware formatting in a
#: chart). "score" (compliance_integrity) and "rs" (cost_to_collect) have no
#: clean Unit — they are shown in the register table instead, in the exact
#: string kpi_catalog itself already produced, never reformatted.
_PAYLOAD_UNIT = {"inr": Unit.INR, "pct": Unit.PCT}
_UNIT_BY_ID = {k.id: k.unit for k in KPIS}
_DIRECTION_BY_ID = {k.id: (Direction.HIGHER_IS_BETTER if k.higher_is_better else Direction.LOWER_IS_BETTER)
                   for k in KPIS}


def _month_end(d: date) -> date:
    nxt = date(d.year + 1, 1, 1) if d.month == 12 else date(d.year, d.month + 1, 1)
    return nxt - timedelta(days=1)


def _headline_kpis(ov: Overview) -> list[Kpi]:
    out = []
    for k in ov.kpis:
        unit = _PAYLOAD_UNIT.get(_UNIT_BY_ID.get(k["id"]))
        if unit is None:
            continue
        out.append(Kpi(kpi_id=k["id"], label=k["label"], value=k.get("raw") if k.get("available") else None,
                       unit=unit, direction=_DIRECTION_BY_ID.get(k["id"], Direction.NEUTRAL), basis=k.get("basis")))
    return out


def _kpi_register_table(ov: Overview) -> Table:
    """Every KPI, including the two with no typed-tile unit, in the exact
    string the Command Center Overview page already shows for it."""
    return Table(
        table_id="kpi_register", title="All KPIs this period",
        columns=[Column(key="label", label="KPI"), Column(key="value", label="Value"),
                Column(key="trend", label="Trend"), Column(key="basis", label="Basis")],
        rows=[{"label": k["label"], "value": k["value"], "trend": k["trend"], "basis": k["basis"]}
             for k in ov.kpis],
        note="\"Value\" is the same figure the Overview page shows for the same period; a KPI marked "
             "not available carries its reason instead of a guess.",
    )


def _model_performance_table() -> Table | None:
    """The trained layer's LIVE-EQUIVALENT figures only (ADR 0008) — never the
    training artifact's OOT figures, which the product is not actually
    running at (the borrower-stance feature is not yet recorded)."""
    version = _ml_serving_version(TRAINED_MODEL) or configured_version()
    live = LIVE_EQUIVALENT.get(version or "")
    if not live:
        return None
    return Table(
        table_id="model_performance", title="Recovery-risk model — live-equivalent performance",
        columns=[Column(key="metric", label="Metric"), Column(key="value", label="Value")],
        rows=[
            {"metric": "Model / version", "value": f"{TRAINED_MODEL} {version}"},
            {"metric": "Gini (live-equivalent)", "value": f"{live['gini']:.4f}"},
            {"metric": "KS (live-equivalent)", "value": f"{live['ks']:.2f}"},
            {"metric": "Measured on", "value": live["measured_on"]},
        ],
        note=f"Live-equivalent, not the training artifact's figures: {live['basis']} (ADR 0008). "
             "Every figure in this pack is prepared from a synthetic demonstration book.",
    )


def _board_narrative(ov: Overview, bank_name: str) -> Narrative:
    template_text = " ".join(ov.narrative) or "No KPI could be computed for this bank in this period."
    prompt = (
        "You write the opening paragraph of a bank's board collections report. Write 2-3 sentences, "
        "formal register, third person, no headings or bullet points. Use ONLY the figures given below; "
        "never invent a number or compare to a period not named here.\n\n"
        f"Bank: {bank_name}\n"
        f"This period's KPIs, already summarised by rule: {template_text}"
    )
    # names=[]: the prompt embeds the bank's own name, an organisation, never a person
    # (test_llm_redaction.py's tripwire requires every complete() call to say so explicitly).
    result = llm.complete(prompt, purpose="board_report_commentary", max_tokens=220, temperature=0.3, names=[])
    if result.ai_generated and result.text:
        return Narrative(text=result.text.strip(), ai_generated=True)
    return Narrative(text=template_text, ai_generated=False)


def build_board_payload(adb: Session, bank: Bank, f: KpiFilter, *,
                        generated_at: datetime | None = None) -> ReportPayload:
    """The bank-wide board pack: the same twelve KPIs as /bank/overview, over
    the requested period, plus the trained model's honest live-equivalent
    figures and an AI-written opening paragraph."""
    generated_at = generated_at or datetime.now(timezone.utc)
    ov = compute_overview(adb, bank.id, f)
    as_of = ov.as_of or date.today()
    period_start, period_end = f.window(as_of)

    tables = [_kpi_register_table(ov)]
    model_table = _model_performance_table()
    if model_table:
        tables.append(model_table)

    section = Section(section_id="portfolio_overview", title="Portfolio Overview",
                      narrative=_board_narrative(ov, bank.display_name),
                      kpis=_headline_kpis(ov), tables=tables)
    data_note = None if ov.as_of else "No portfolio reading exists yet for this bank; every KPI abstains."

    return ReportPayload(
        report_id=f"board-{bank.id}-{period_end.isoformat()}",
        template="board", title="Board Report", subtitle=bank.display_name,
        organisation=bank.display_name, scope="All agencies · all zones",
        period_start=period_start, period_end=period_end, generated_at=generated_at,
        prepared_for="Board of Directors", synthetic=True, data_note=data_note,
        sections=[section],
    )


# ── Agency Review ────────────────────────────────────────────────────────────
#: Metrics with a clean payload.Unit become typed Kpi tiles. "cost_per_100_inr"
#: (rupees per rupee-100 collected) and "compliance_score" (a 0-100 score) have
#: none, same reasoning as the board pack's two KPIs — shown in the detail
#: table instead, formatted by app.reports.formatting's own helpers.
_SCORECARD_LABEL = {
    "collection_efficiency": "Collection Efficiency", "resolution_rate": "Resolution Rate",
    "recovery_vs_expected": "Recovery vs Expected", "ptp_conversion": "PTP Keep Rate",
    "contact_rate": "Contact Rate", "sla_adherence": "First-Visit SLA",
    "workforce_active_ratio": "Workforce Active Ratio", "cost_per_100_inr": "Cost to Collect",
    "compliance_score": "Compliance Score",
}
_SCORECARD_UNIT = {
    "collection_efficiency": Unit.PCT, "resolution_rate": Unit.PCT, "recovery_vs_expected": Unit.RATIO,
    "ptp_conversion": Unit.PCT, "contact_rate": Unit.PCT, "sla_adherence": Unit.PCT,
    "workforce_active_ratio": Unit.PCT,
}
_SCORECARD_DETAIL_FMT = {
    "collection_efficiency": rfmt.pct, "resolution_rate": rfmt.pct, "recovery_vs_expected": rfmt.ratio,
    "ptp_conversion": rfmt.pct, "contact_rate": rfmt.pct, "sla_adherence": rfmt.pct,
    "workforce_active_ratio": rfmt.pct,
    "cost_per_100_inr": lambda v: rfmt.MISSING if v is None else f"{rfmt.RUPEE}{v:.2f} per {rfmt.RUPEE}100",
    "compliance_score": lambda v: rfmt.MISSING if v is None else f"{v:.1f} / 100",
}


def _agency_name(agency: Agency) -> str:
    return agency.trade_name or agency.legal_name


def _scorecard_kpis(card: dict) -> list[Kpi]:
    return [Kpi(kpi_id=f"agency_scorecard.{key}", label=_SCORECARD_LABEL[key], value=card.get(key),
               unit=unit, direction=Direction.HIGHER_IS_BETTER)
           for key, unit in _SCORECARD_UNIT.items()]


def _scorecard_detail_table(card: dict) -> Table:
    rows = [{"metric": _SCORECARD_LABEL[key], "value": fmt(card.get(key))}
           for key, fmt in _SCORECARD_DETAIL_FMT.items()]
    return Table(
        table_id="agency_scorecard_detail", title="Scorecard detail",
        columns=[Column(key="metric", label="Metric"), Column(key="value", label="Value")], rows=rows,
        note=f"{card.get('n_rows', 0)} agency-month row(s) in this window.",
    )


def _agency_narrative(card: dict, agency_name: str, bank_name: str) -> Narrative:
    facts = ", ".join(
        f"{_SCORECARD_LABEL[k]} {fmt(card.get(k))}" for k, fmt in _SCORECARD_DETAIL_FMT.items()
    ) if card.get("n_rows") else "no scorecard rows in this window"
    template_text = f"{agency_name}: {facts}."
    prompt = (
        "You write the opening paragraph of an agency performance review for a bank's collections "
        "leadership. Write 2-3 sentences, formal register, third person, no headings or bullet points. "
        "Use ONLY the figures given below; never invent a number.\n\n"
        f"Bank: {bank_name}\nAgency: {agency_name}\nThis period's scorecard: {template_text}"
    )
    # names=[]: bank_name/agency_name are organisations, never a person (same rule as
    # _board_narrative's own call, above).
    result = llm.complete(prompt, purpose="agency_review_commentary", max_tokens=220, temperature=0.3, names=[])
    if result.ai_generated and result.text:
        return Narrative(text=result.text.strip(), ai_generated=True)
    return Narrative(text=template_text, ai_generated=False)


def build_agency_review_payload(adb: Session, bank: Bank, agency: Agency, *,
                                generated_at: datetime | None = None) -> ReportPayload:
    """One agency's scorecard for the latest available month — the same
    window and the same `agency_scorecard()` call the Agencies tab renders on
    screen. A custom date range is not wired here yet (scope note for the
    demo, not an oversight): `agency_scorecard(months=...)` supports it, and
    extending the request is follow-up work once a pilot lender asks for it."""
    generated_at = generated_at or datetime.now(timezone.utc)
    # agency_scorecard() queries analytics.agency_scorecard_monthly_scoped directly,
    # with none of compute_tab()'s own view-availability guard (the "agencies" tab
    # applies it BEFORE calling this same function) — reproduced here rather than
    # calling compute_tab, since a report payload wants the single-agency card, not
    # every agency's. Without this a bank with no refresh yet would raise instead of
    # abstaining, exactly the gap compute_tab's own callers are protected from.
    card = (agency_scorecard(adb, bank_id=bank.id, agency_id=agency.id) if SCORECARD in available_views(adb)
           else {"n_rows": 0})
    name = _agency_name(agency)
    has_data = bool(card.get("n_rows"))

    if has_data:
        month_start = date.fromisoformat(card["month_start"])
        period_start, period_end = month_start, _month_end(month_start)
        data_note = None
    else:
        period_start, period_end = date.today().replace(day=1), date.today()
        data_note = "No scorecard rows for this agency in the latest available month."

    section = Section(section_id="agency_scorecard", title=f"{name} — Scorecard",
                      narrative=_agency_narrative(card, name, bank.display_name),
                      kpis=_scorecard_kpis(card) if has_data else [],
                      tables=[_scorecard_detail_table(card)] if has_data else [])

    return ReportPayload(
        report_id=f"agency_review-{bank.id}-{agency.id}-{period_end.isoformat()}",
        template="agency_review", title="Agency Review", subtitle=name,
        organisation=bank.display_name, scope=f"{name} ({agency.code})",
        period_start=period_start, period_end=period_end, generated_at=generated_at,
        prepared_for=None, synthetic=True, data_note=data_note,
        sections=[section],
    )
