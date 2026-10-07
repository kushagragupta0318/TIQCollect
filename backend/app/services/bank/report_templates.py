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
from app.models.loan import DPDBucket
from app.models.tenancy import Agency, Bank
from app.reports import formatting as rfmt
from app.reports.payload import Chart, ChartKind, Column, Direction, Kpi, Narrative, ReportPayload, Section, \
    Series, Table, Unit
from app.services.bank import analytics_catalog
from app.services.bank.agency_scorecard import agency_scorecard, fetch_rows
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


def _n_months_before(d: date, n: int) -> date:
    # Not imported from agency_scorecard._month_back — the same call that
    # module itself makes about its own underscore-prefixed helper: module-
    # internal, and 3 lines of index math is not the duplication the
    # one-definition rule protects against.
    y, m = divmod(d.year * 12 + (d.month - 1) - n, 12)
    return date(y, m + 1, 1)


def _month_label(d) -> str:
    """month_start comes back as a real date on Postgres; defensive for a
    driver that hands back a string instead."""
    return d.strftime("%b %Y") if hasattr(d, "strftime") else str(d)[:7]


def _headline_kpis(ov: Overview) -> list[Kpi]:
    out = []
    for k in ov.kpis:
        unit = _PAYLOAD_UNIT.get(_UNIT_BY_ID.get(k["id"]))
        if unit is None:
            continue
        out.append(Kpi(kpi_id=k["id"], label=k["label"], value=k.get("raw") if k.get("available") else None,
                       unit=unit, direction=_DIRECTION_BY_ID.get(k["id"], Direction.NEUTRAL), basis=k.get("basis"),
                       prior=k.get("prior") if k.get("available") else None))
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


#: Canonical order + display label for a DPD bucket chart — DPDBucket's own
#: definition (models/loan.py), not the SQL's alphabetical GROUP BY order
#: (which would put CURRENT between BUCKET_3 and NPA).
_DPD_LABEL = {
    DPDBucket.CURRENT: "Current", DPDBucket.BUCKET_1: "1-30 DPD", DPDBucket.BUCKET_2: "31-60 DPD",
    DPDBucket.BUCKET_3: "61-90 DPD", DPDBucket.NPA: "NPA (90+)",
}


def _exposure_chart(db: Session, adb: Session, bank_id: str, f: KpiFilter) -> Chart | None:
    """Delinquent exposure by DPD bucket — analytics_catalog's own Exposure
    tab, the same panel the Analytics screen draws. Abstains (no chart, not
    an empty one) the same way the tab itself does: no reading, no filter
    the view can honour, or nothing in any bucket."""
    out = analytics_catalog.compute_tab(db, adb, "exposure", bank_id, f)
    if not out["available"]:
        return None
    by_bucket = {row["dpd_bucket"]: row for row in out["panels"].get("dpd_ladder", [])}
    categories, values = [], []
    for bucket in DPDBucket:
        row = by_bucket.get(bucket.value)
        if row is None:
            continue
        categories.append(_DPD_LABEL[bucket])
        values.append(float(row["outstanding"] or 0))
    if not categories:
        return None
    # scale="sequential" is deliberately NOT set: it recolours per SERIES, not
    # per bar within one series (theme.series_colors), so on this one-series
    # chart it would be a silent no-op. The single-series "direct value
    # label on each bar" path (charts.py) is the relief that applies here —
    # confirmed by eyeballing the actual render, every bar labelled.
    return Chart(chart_id="exposure_by_dpd", title="Exposure by DPD bucket", kind=ChartKind.BAR, unit=Unit.INR,
                categories=categories, series=[Series(name="Outstanding", values=values)])


def _recovery_chart(db: Session, adb: Session, bank_id: str, f: KpiFilter) -> Chart | None:
    """Collections vs the recovery_risk-implied expectation, by month —
    analytics_catalog's own Recovery tab (recovery_vs_expected, already
    computed there from expected_recovery_inr; nothing recomputed here).
    Up to the last 6 months the view carries."""
    out = analytics_catalog.compute_tab(db, adb, "recovery", bank_id, f)
    if not out["available"]:
        return None
    rows = [r for r in out["panels"].get("by_month", []) if r.get("actual_inr") is not None][-6:]
    if not rows:
        return None
    categories = [_month_label(r["month_start"]) for r in rows]
    actual = [float(r["actual_inr"] or 0) for r in rows]
    expected = [None if r.get("expected_inr") is None else float(r["expected_inr"]) for r in rows]
    series = [Series(name="Collected", values=actual)]
    if any(v is not None for v in expected):
        series.append(Series(name="Expected recovery", values=expected))
    return Chart(chart_id="recovery_vs_expected", title="Recovery vs expected, by month", kind=ChartKind.BAR,
                unit=Unit.INR, categories=categories, series=series)


def _agency_collections_chart(adb: Session, bank_id: str, agency_id: str, last_month: date,
                              months: int = 6) -> Chart | None:
    """One agency's verified + bank-direct collections per month, over up to
    the last 6 months — agency_scorecard.fetch_rows()'s own per-month rows.
    fetch_rows' grain is (month, agency, REGION): an agency covering several
    regions gets one row per region per month, so a month is summed across
    its region rows here — the same rule agency_scorecard.compute_metrics
    itself uses (SUM over every row in the window, never one row per
    region). Found by eyeballing the first render: without the grouping,
    a 3-region agency drew "Apr 2026" three times with three different
    bars instead of one summed bar."""
    first_month = _n_months_before(last_month, months - 1)
    rows = [r for r in fetch_rows(adb, bank_id=bank_id, first_month=first_month, last_month=last_month)
           if r.agency_id == agency_id]
    if not rows:
        return None
    by_month: dict[date, float] = {}
    for r in rows:
        by_month[r.month_start] = by_month.get(r.month_start, 0.0) + float(r.verified_collections
                                                                            + r.bank_direct_collections)
    categories = [_month_label(m) for m in sorted(by_month)]
    values = [by_month[m] for m in sorted(by_month)]
    return Chart(chart_id="agency_collections_by_month", title="Collections by month", kind=ChartKind.BAR,
                unit=Unit.INR, categories=categories, series=[Series(name="Collected", values=values)])


def _board_narrative(ov: Overview, bank_name: str, bank_id: str) -> Narrative:
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
    result = llm.complete(prompt, purpose="board_report_commentary", max_tokens=220, temperature=0.3, names=[],
                          bank_id=bank_id)
    if result.ai_generated and result.text:
        return Narrative(text=result.text.strip(), ai_generated=True)
    return Narrative(text=template_text, ai_generated=False)


def build_board_payload(db: Session, adb: Session, bank: Bank, f: KpiFilter, *,
                        generated_at: datetime | None = None) -> ReportPayload:
    """The bank-wide board pack: the same twelve KPIs as /bank/overview, over
    the requested period, the exposure and recovery charts analytics_catalog
    already computes, the trained model's honest live-equivalent figures,
    and an AI-written opening paragraph."""
    generated_at = generated_at or datetime.now(timezone.utc)
    ov = compute_overview(adb, bank.id, f)
    as_of = ov.as_of or date.today()
    period_start, period_end = f.window(as_of)

    tables = [_kpi_register_table(ov)]
    model_table = _model_performance_table()
    if model_table:
        tables.append(model_table)
    charts = [c for c in (_exposure_chart(db, adb, bank.id, f), _recovery_chart(db, adb, bank.id, f)) if c]

    section = Section(section_id="portfolio_overview", title="Portfolio Overview",
                      narrative=_board_narrative(ov, bank.display_name, bank.id),
                      kpis=_headline_kpis(ov), charts=charts, tables=tables)
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


def _scorecard_kpis(card: dict, prior_card: dict) -> list[Kpi]:
    """prior_card is the same shape from the prior month, or {} when that
    month has no rows — a KPI's delta chip shows only when both months do."""
    has_prior = bool(prior_card.get("n_rows"))
    return [Kpi(kpi_id=f"agency_scorecard.{key}", label=_SCORECARD_LABEL[key], value=card.get(key),
               unit=unit, direction=Direction.HIGHER_IS_BETTER,
               prior=prior_card.get(key) if has_prior else None)
           for key, unit in _SCORECARD_UNIT.items()]


def _scorecard_detail_table(card: dict) -> Table:
    rows = [{"metric": _SCORECARD_LABEL[key], "value": fmt(card.get(key))}
           for key, fmt in _SCORECARD_DETAIL_FMT.items()]
    return Table(
        table_id="agency_scorecard_detail", title="Scorecard detail",
        columns=[Column(key="metric", label="Metric"), Column(key="value", label="Value")], rows=rows,
        note=f"{card.get('n_rows', 0)} agency-month row(s) in this window.",
    )


def _agency_narrative(card: dict, agency_name: str, bank_name: str, bank_id: str) -> Narrative:
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
    result = llm.complete(prompt, purpose="agency_review_commentary", max_tokens=220, temperature=0.3, names=[],
                          bank_id=bank_id)
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
    views = available_views(adb)
    card = agency_scorecard(adb, bank_id=bank.id, agency_id=agency.id) if SCORECARD in views else {"n_rows": 0}
    name = _agency_name(agency)
    has_data = bool(card.get("n_rows"))

    if has_data:
        month_start = date.fromisoformat(card["month_start"])
        period_start, period_end = month_start, _month_end(month_start)
        data_note = None
        prior_month = _n_months_before(month_start, 1)
        prior_card = (agency_scorecard(adb, bank_id=bank.id, agency_id=agency.id, month_start=prior_month)
                     if SCORECARD in views else {"n_rows": 0})
        charts = [c for c in (_agency_collections_chart(adb, bank.id, agency.id, month_start),) if c]
    else:
        period_start, period_end = date.today().replace(day=1), date.today()
        data_note = "No scorecard rows for this agency in the latest available month."
        prior_card, charts = {"n_rows": 0}, []

    section = Section(section_id="agency_scorecard", title=f"{name} — Scorecard",
                      narrative=_agency_narrative(card, name, bank.display_name, bank.id),
                      kpis=_scorecard_kpis(card, prior_card) if has_data else [], charts=charts,
                      tables=[_scorecard_detail_table(card)] if has_data else [])

    return ReportPayload(
        report_id=f"agency_review-{bank.id}-{agency.id}-{period_end.isoformat()}",
        template="agency_review", title="Agency Review", subtitle=name,
        organisation=bank.display_name, scope=f"{name} ({agency.code})",
        period_start=period_start, period_end=period_end, generated_at=generated_at,
        prepared_for=None, synthetic=True, data_note=data_note,
        sections=[section],
    )
