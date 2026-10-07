# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-09-24 — New file. E09: ReportPayload → PPTX (python-pptx), 16:9.
#   Charts are NATIVE PowerPoint charts with their data embedded, so a reader
#   can restyle or re-plot them in PowerPoint; a picture of a chart could not
#   be edited or inspected.
# ───────────────────────────────────────────────────────────────────────────
"""Slides: a title slide, then per section a summary slide (narrative + KPI
tiles), one slide per chart, and each table split across slides of at most
TABLE_ROWS_PER_SLIDE rows. Rupee charts are plotted in crore or lakh — the
unit is in the chart title — because a PowerPoint number format cannot group
in lakhs; the XLSX holds the exact rupees.
"""
from __future__ import annotations

import io

from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE, XL_LABEL_POSITION, XL_LEGEND_POSITION, XL_MARKER_STYLE
from pptx.enum.dml import MSO_LINE_DASH_STYLE
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Emu, Inches, Pt

from app.reports import theme
from app.reports.formatting import RUPEE, axis_scale, delta, fmt, period_label
from app.reports.payload import SYNTHETIC_WARNING, Chart, ChartKind, Kpi, ReportPayload, Section, Table, Unit

SLIDE_W, SLIDE_H = Inches(13.333), Inches(7.5)
MARGIN = Inches(0.6)
TABLE_ROWS_PER_SLIDE = 12
KPI_PER_ROW = 4

_CHART_TYPE = {
    ChartKind.BAR: XL_CHART_TYPE.COLUMN_CLUSTERED,
    ChartKind.STACKED_BAR: XL_CHART_TYPE.COLUMN_STACKED,
    ChartKind.LINE: XL_CHART_TYPE.LINE_MARKERS,
}


def _rgb(hex_: str) -> RGBColor:
    return RGBColor.from_string(hex_.lstrip("#").upper())


def _text(slide, left, top, width, height, text: str, *, size: float = 12, bold: bool = False,
          color: str = theme.INK, align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP):
    box = slide.shapes.add_textbox(left, top, width, height)
    tf = box.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = anchor
    para = tf.paragraphs[0]
    para.alignment = align
    run = para.add_run()
    run.text = text
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = _rgb(color)
    return box


def _footer(slide, p: ReportPayload) -> None:
    left = f"{p.organisation} · {p.title} · {period_label(p)}"
    if p.synthetic:
        left += " · SYNTHETIC DATA"
    _text(slide, MARGIN, SLIDE_H - Inches(0.45), SLIDE_W - 2 * MARGIN, Inches(0.3), left,
          size=9, color=theme.TEXT_MUTED)


def _title(slide, text: str) -> None:
    _text(slide, MARGIN, Inches(0.35), SLIDE_W - 2 * MARGIN, Inches(0.6), text, size=24, bold=True)


def _title_slide(prs, p: ReportPayload) -> None:
    s = prs.slides.add_slide(prs.slide_layouts[6])
    _text(s, MARGIN, Inches(1.5), Inches(11), Inches(0.4), p.organisation.upper(), size=12, color=theme.TEXT_MUTED)
    _text(s, MARGIN, Inches(1.95), Inches(11.5), Inches(1.0), p.title, size=40, bold=True)
    y = Inches(3.0)
    if p.subtitle:
        _text(s, MARGIN, y, Inches(11.5), Inches(0.5), p.subtitle, size=18, color=theme.TEXT_MUTED)
        y += Inches(0.7)
    meta = [f"Period  {period_label(p)}", f"Scope  {p.scope}"]
    if p.prepared_for:
        meta.append(f"Prepared for  {p.prepared_for}")
    _text(s, MARGIN, y, Inches(11.5), Inches(1.0), "     ·     ".join(meta), size=13)
    if p.synthetic or p.data_note:
        warn = " ".join(([SYNTHETIC_WARNING] if p.synthetic else []) + ([p.data_note] if p.data_note else []))
        box = s.shapes.add_shape(MSO_SHAPE.RECTANGLE, MARGIN, Inches(5.6), SLIDE_W - 2 * MARGIN, Inches(0.8))
        box.fill.solid()
        box.fill.fore_color.rgb = _rgb(theme.WARN_FILL)
        box.line.color.rgb = _rgb("#FEC84B")
        tf = box.text_frame
        tf.word_wrap = True
        run = tf.paragraphs[0].add_run()
        run.text = warn
        run.font.size = Pt(12)
        run.font.color.rgb = _rgb(theme.WARN_INK)
    _footer(s, p)


def _kpi_tile(slide, k: Kpi, left, top, width, height) -> None:
    box = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, left, top, width, height)
    box.adjustments[0] = 0.08
    box.fill.solid()
    box.fill.fore_color.rgb = _rgb(theme.SURFACE)
    box.line.color.rgb = _rgb(theme.GRID)
    box.shadow.inherit = False
    tf = box.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = MSO_ANCHOR.TOP
    lines = [(k.label, 11, False, theme.TEXT_MUTED), (fmt(k.value, k.unit, compact=True), 24, True, theme.INK)]
    d = delta(k.value, k.prior, k.unit)
    if d:
        lines.append((f"{d} vs prior period", 10, False, theme.TEXT_MUTED))
    for i, (text, size, bold, color) in enumerate(lines):
        para = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        run = para.add_run()
        run.text = text
        run.font.size = Pt(size)
        run.font.bold = bold
        run.font.color.rgb = _rgb(color)


def _summary_slide(prs, p: ReportPayload, s: Section) -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _title(slide, s.title)
    y = Inches(1.15)
    if s.narrative:
        if s.narrative.ai_generated:
            _text(slide, MARGIN, y, Inches(12), Inches(0.3),
                  "AI-WRITTEN COMMENTARY — figures are taken from the report data",
                  size=10, bold=True, color=theme.AI_ACCENT)
            y += Inches(0.32)
        _text(slide, MARGIN, y, SLIDE_W - 2 * MARGIN, Inches(1.0), s.narrative.text, size=14)
        y += Inches(1.1)
    if s.kpis:
        gap = Inches(0.2)
        w = int((SLIDE_W - 2 * MARGIN - gap * (KPI_PER_ROW - 1)) / KPI_PER_ROW)
        h = Inches(1.35)
        for i, k in enumerate(s.kpis):
            r, c = divmod(i, KPI_PER_ROW)
            _kpi_tile(slide, k, MARGIN + c * (w + gap), y + r * (h + gap), w, h)
    _footer(slide, p)


def _chart_slide(prs, p: ReportPayload, c: Chart) -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    peak = max((abs(v) for s in c.series for v in s.values if v is not None), default=0.0)
    if c.kind is ChartKind.STACKED_BAR:
        peak = max((sum(v or 0.0 for v in col) for col in zip(*(s.values for s in c.series))), default=peak)
    divisor, suffix = axis_scale(peak, c.unit)
    title = c.title + (f" ({RUPEE} {'crore' if suffix == 'Cr' else 'lakh'})" if suffix else "")
    _title(slide, title)

    data = CategoryChartData()
    data.categories = c.categories
    for s in c.series:
        data.add_series(s.name, [None if v is None else v / divisor for v in s.values])
    frame = slide.shapes.add_chart(_CHART_TYPE[c.kind], MARGIN, Inches(1.2), SLIDE_W - 2 * MARGIN, Inches(5.5), data)
    chart = frame.chart
    chart.font.size = Pt(11)
    chart.font.color.rgb = _rgb(theme.TEXT_MUTED)

    num_fmt = {Unit.PCT: "0.0%", Unit.COUNT: "#,##0", Unit.RATIO: '0.00"x"'}.get(
        c.unit, "0.00" if suffix else "#,##0")
    va = chart.value_axis
    va.tick_labels.number_format = num_fmt
    va.tick_labels.number_format_is_linked = False
    va.has_major_gridlines = True
    va.major_gridlines.format.line.color.rgb = _rgb(theme.GRID)
    va.format.line.fill.background()
    chart.category_axis.format.line.color.rgb = _rgb(theme.GRID)

    plot = chart.plots[0]
    if c.kind is not ChartKind.LINE:
        plot.gap_width = 80
        if c.kind is ChartKind.STACKED_BAR:
            plot.overlap = 100
    colors = theme.series_colors(c)
    for series, spec, color in zip(plot.series, c.series, colors):
        if c.kind is ChartKind.LINE:
            series.smooth = False
            series.format.line.color.rgb = _rgb(color)
            series.format.line.width = Pt(1.5 if spec.reference else 2.25)
            if spec.reference:
                series.format.line.dash_style = MSO_LINE_DASH_STYLE.DASH
                series.marker.style = XL_MARKER_STYLE.NONE
            else:
                series.marker.style = XL_MARKER_STYLE.CIRCLE
                series.marker.size = 7
                series.marker.format.fill.solid()
                series.marker.format.fill.fore_color.rgb = _rgb(color)
                series.marker.format.line.color.rgb = _rgb(color)
        else:
            series.format.fill.solid()
            series.format.fill.fore_color.rgb = _rgb(color)
            series.format.line.color.rgb = _rgb(theme.SURFACE)     # the surface gap between fills

    if len(c.series) >= 2:
        chart.has_legend = True
        chart.legend.position = XL_LEGEND_POSITION.TOP
        chart.legend.include_in_layout = False
    else:
        chart.has_legend = False
        if c.kind is ChartKind.BAR:
            # Direct labels: the relief for the low-contrast hues (theme.py).
            plot.has_data_labels = True
            labels = plot.data_labels
            labels.number_format = num_fmt
            labels.number_format_is_linked = False
            labels.position = XL_LABEL_POSITION.OUTSIDE_END
            labels.font.size = Pt(11)
            labels.font.color.rgb = _rgb(theme.INK)
    _footer(slide, p)


def _table_slides(prs, p: ReportPayload, t: Table) -> None:
    chunks = [t.rows[i:i + TABLE_ROWS_PER_SLIDE] for i in range(0, len(t.rows), TABLE_ROWS_PER_SLIDE)] or [[]]
    weights = [2.4 if c.unit is Unit.TEXT else 1.0 for c in t.columns]
    width = SLIDE_W - 2 * MARGIN
    for n, rows in enumerate(chunks):
        slide = prs.slides.add_slide(prs.slide_layouts[6])
        _title(slide, t.title + (" (continued)" if n else ""))
        shape = slide.shapes.add_table(len(rows) + 1, len(t.columns), MARGIN, Inches(1.2), width,
                                       Inches(0.38) * (len(rows) + 1))
        table = shape.table
        for ci, w in enumerate(weights):
            table.columns[ci].width = Emu(int(width * w / sum(weights)))
        for ci, col in enumerate(t.columns):
            _cell(table.cell(0, ci), col.label, bold=True, numeric=col.unit is not Unit.TEXT, header=True)
        for ri, row in enumerate(rows, start=1):
            for ci, col in enumerate(t.columns):
                _cell(table.cell(ri, ci), fmt(row.get(col.key), col.unit, compact=True),
                      numeric=col.unit is not Unit.TEXT, zebra=ri % 2 == 0)
        if t.note and n == len(chunks) - 1:
            _text(slide, MARGIN, SLIDE_H - Inches(0.9), width, Inches(0.3), t.note, size=10, color=theme.TEXT_MUTED)
        _footer(slide, p)


def _cell(cell, text: str, *, bold: bool = False, numeric: bool = False, header: bool = False,
          zebra: bool = False) -> None:
    cell.text = ""
    para = cell.text_frame.paragraphs[0]
    para.alignment = PP_ALIGN.RIGHT if numeric else PP_ALIGN.LEFT
    run = para.add_run()
    run.text = text
    run.font.size = Pt(11)
    run.font.bold = bold
    run.font.color.rgb = _rgb(theme.TEXT_MUTED if header else theme.INK)
    cell.fill.solid()
    cell.fill.fore_color.rgb = _rgb(theme.ZEBRA if (zebra or header) else theme.SURFACE)
    cell.margin_top = cell.margin_bottom = Inches(0.04)


def render_pptx(p: ReportPayload) -> bytes:
    prs = Presentation()
    prs.slide_width, prs.slide_height = SLIDE_W, SLIDE_H
    props = prs.core_properties
    props.title, props.author, props.subject = p.title, p.organisation, f"{p.title} · {period_label(p)}"
    props.last_modified_by = "TIQCollect report engine"
    naive = p.generated_at.replace(tzinfo=None)
    props.created = props.modified = naive

    _title_slide(prs, p)
    for s in p.sections:
        if s.narrative or s.kpis:
            _summary_slide(prs, p, s)
        for c in s.charts:
            _chart_slide(prs, p, c)
        for t in s.tables:
            _table_slides(prs, p, t)

    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()
