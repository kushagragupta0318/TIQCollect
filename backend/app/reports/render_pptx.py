# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-09-24 — New file. E09: ReportPayload → PPTX (python-pptx), 16:9.
#   Charts are NATIVE PowerPoint charts with their data embedded, so a reader
#   can restyle or re-plot them in PowerPoint; a picture of a chart could not
#   be edited or inspected.
# 2026-10-07 — The render lift (owner: "very basic and bland"). Same
#   structure (title slide → per section a summary slide, one slide per
#   chart, tables split across slides); this pass is weight and hierarchy.
#   A KPI's delta triangle is a native autoshape (MSO_SHAPE.ISOSCELES_
#   TRIANGLE), not a glyph — same reason render_pdf.py draws a vector
#   triangle: the bundled font has no ▲▼ in its cmap.
# ───────────────────────────────────────────────────────────────────────────
"""Slides: a branded title slide (header band, wordmark, bank name, period,
"as of" date), then per section a summary slide (narrative + KPI cards),
one slide per chart, and each table split across slides of at most
TABLE_ROWS_PER_SLIDE rows. Rupee charts are plotted in crore or lakh — the
unit is in the chart title — because a PowerPoint number format cannot group
in lakhs; the XLSX holds the exact rupees. Every slide's footer carries
SYNTHETIC DATA / CONFIDENTIAL beside the organisation, title and period.
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
from app.reports.formatting import RUPEE, axis_scale, fmt, kpi_delta, period_label
from app.reports.payload import SYNTHETIC_WARNING, Chart, ChartKind, Kpi, ReportPayload, Section, Table, Unit

SLIDE_W, SLIDE_H = Inches(13.333), Inches(7.5)
MARGIN = Inches(0.6)
TABLE_ROWS_PER_SLIDE = 12
KPI_PER_ROW = 4
HEAD_BAND_H = Inches(1.35)

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


def _rect(slide, left, top, width, height, color_hex: str) -> None:
    shp = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, left, top, width, height)
    shp.fill.solid()
    shp.fill.fore_color.rgb = _rgb(color_hex)
    shp.line.fill.background()
    shp.shadow.inherit = False


def _triangle(slide, left, top, size, color_hex: str, *, up: bool) -> None:
    """A filled up/down triangle, a native PowerPoint autoshape — not a
    glyph. The bundled font has no ▲▼ in its cmap (checked directly); a
    delta's direction is this shape plus the signed number beside it."""
    shp = slide.shapes.add_shape(MSO_SHAPE.ISOSCELES_TRIANGLE, left, top, size, size)
    shp.fill.solid()
    shp.fill.fore_color.rgb = _rgb(color_hex)
    shp.line.fill.background()
    shp.shadow.inherit = False
    if not up:
        shp.rotation = 180


def _blank(prs):
    return prs.slides.add_slide(prs.slide_layouts[6])


def _footer(slide, p: ReportPayload) -> None:
    left = f"{p.organisation} · {p.title} · {period_label(p)}"
    tags = (["SYNTHETIC DATA"] if p.synthetic else []) + ["CONFIDENTIAL"]
    _rect(slide, 0, SLIDE_H - Inches(0.02), SLIDE_W, Inches(0.02), theme.GRID)
    _text(slide, MARGIN, SLIDE_H - Inches(0.42), SLIDE_W - 2 * MARGIN - Inches(2.4), Inches(0.3), left,
         size=9.5, color=theme.TEXT_MUTED)
    _text(slide, SLIDE_W - MARGIN - Inches(2.4), SLIDE_H - Inches(0.42), Inches(2.4), Inches(0.3),
         " · ".join(tags), size=9.5, bold=True, color=theme.WARN_INK if p.synthetic else theme.TEXT_MUTED,
         align=PP_ALIGN.RIGHT)


def _title(slide, text: str) -> None:
    _text(slide, MARGIN, Inches(0.38), SLIDE_W - 2 * MARGIN, Inches(0.6), text, size=26, bold=True)
    _rect(slide, MARGIN, Inches(1.0), Inches(0.55), Inches(0.05), theme.PRIMARY)


def _title_slide(prs, p: ReportPayload) -> None:
    s = _blank(prs)
    _rect(s, 0, 0, SLIDE_W, HEAD_BAND_H, theme.BRAND_BAND)
    _text(s, MARGIN, Inches(0.5), Inches(6), Inches(0.4), theme.WORDMARK, size=15, bold=True, color="#FFFFFF")

    _text(s, MARGIN, Inches(1.75), Inches(11.8), Inches(0.4), p.organisation.upper(), size=13,
         color=theme.TEXT_MUTED)
    _text(s, MARGIN, Inches(2.2), Inches(11.8), Inches(1.1), p.title, size=42, bold=True)
    y = Inches(3.35)
    if p.subtitle:
        _text(s, MARGIN, y, Inches(11.8), Inches(0.5), p.subtitle, size=18, color=theme.TEXT_MUTED)
        y += Inches(0.7)

    meta = [("PERIOD", period_label(p)), ("AS OF", p.period_end.strftime("%d %b %Y").lstrip("0")),
           ("SCOPE", p.scope)]
    if p.prepared_for:
        meta.append(("PREPARED FOR", p.prepared_for))
    x = MARGIN
    for label, value in meta:
        w = Inches(2.6) if label != "SCOPE" else Inches(3.6)
        _text(s, x, y, w, Inches(0.3), label, size=9, bold=True, color=theme.TEXT_MUTED)
        _text(s, x, y + Inches(0.26), w, Inches(0.4), value, size=13)
        x += w
    y += Inches(0.95)

    # The short SYNTHETIC DATA / CONFIDENTIAL marks run in the footer on
    # every slide (below); the full sentence stays here, once, quietly. A
    # run-specific note (e.g. an abstaining KPI set) follows it.
    if p.synthetic:
        _text(s, MARGIN, y, SLIDE_W - 2 * MARGIN, Inches(0.5), SYNTHETIC_WARNING, size=11, color=theme.WARN_INK)
        y += Inches(0.4)
    if p.data_note:
        _text(s, MARGIN, y, SLIDE_W - 2 * MARGIN, Inches(0.5), p.data_note, size=11, color=theme.WARN_INK)
    _footer(s, p)


def _kpi_tile(slide, k: Kpi, left, top, width, height) -> None:
    box = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, left, top, width, height)
    box.adjustments[0] = 0.06
    box.fill.solid()
    box.fill.fore_color.rgb = _rgb(theme.SURFACE)
    box.line.color.rgb = _rgb(theme.GRID)
    box.shadow.inherit = False
    # The card's accent edge — brand colour, not a good/bad signal (same
    # reasoning as render_pdf.py's LINEBEFORE: direction alone does not say
    # whether the CURRENT value is good).
    _rect(slide, left, top, Inches(0.05), height, theme.PRIMARY)

    pad = Inches(0.18)
    tf_box = slide.shapes.add_textbox(left + pad, top + Inches(0.1), width - 2 * pad, height - Inches(0.2))
    tf = tf_box.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = MSO_ANCHOR.TOP
    lines = [(k.label.upper(), 10.5, True, theme.TEXT_MUTED),
            (fmt(k.value, k.unit, compact=True), 28, True, theme.INK)]
    for i, (text, size, bold, color) in enumerate(lines):
        para = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        run = para.add_run()
        run.text = text
        run.font.size = Pt(size)
        run.font.bold = bold
        run.font.color.rgb = _rgb(color)

    d = kpi_delta(k.value, k.prior, k.direction, k.unit)
    if d:
        text, up, good = d
        color = theme.GOOD if good else (theme.BAD if good is False else theme.TEXT_MUTED)
        tri_top = top + height - Inches(0.42)
        _triangle(slide, left + pad, tri_top + Inches(0.03), Inches(0.14), color, up=up)
        _text(slide, left + pad + Inches(0.22), tri_top - Inches(0.02), width - 2 * pad - Inches(0.22),
             Inches(0.3), f"{text} vs prior period", size=10.5, bold=True, color=color)


def _summary_slide(prs, p: ReportPayload, s: Section) -> None:
    slide = _blank(prs)
    _title(slide, s.title)
    y = Inches(1.25)
    if s.narrative:
        if s.narrative.ai_generated:
            _text(slide, MARGIN, y, Inches(12), Inches(0.3),
                 "AI-WRITTEN COMMENTARY — figures are taken from the report data",
                 size=10.5, bold=True, color=theme.AI_ACCENT)
            y += Inches(0.32)
        _text(slide, MARGIN, y, SLIDE_W - 2 * MARGIN, Inches(1.0), s.narrative.text, size=14.5)
        y += Inches(1.1)
    if s.kpis:
        gap = Inches(0.22)
        w = int((SLIDE_W - 2 * MARGIN - gap * (KPI_PER_ROW - 1)) / KPI_PER_ROW)
        h = Inches(1.55)
        for i, k in enumerate(s.kpis):
            r, c = divmod(i, KPI_PER_ROW)
            _kpi_tile(slide, k, MARGIN + c * (w + gap), y + r * (h + gap), w, h)
    _footer(slide, p)


def _chart_slide(prs, p: ReportPayload, c: Chart) -> None:
    slide = _blank(prs)
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
    frame = slide.shapes.add_chart(_CHART_TYPE[c.kind], MARGIN, Inches(1.3), SLIDE_W - 2 * MARGIN, Inches(5.4),
                                   data)
    chart = frame.chart
    chart.font.size = Pt(12)
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
            series.format.line.width = Pt(1.5 if spec.reference else 2.5)
            if spec.reference:
                series.format.line.dash_style = MSO_LINE_DASH_STYLE.DASH
                series.marker.style = XL_MARKER_STYLE.NONE
            else:
                series.marker.style = XL_MARKER_STYLE.CIRCLE
                series.marker.size = 8
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
            labels.font.size = Pt(12)
            labels.font.bold = True
            labels.font.color.rgb = _rgb(theme.INK)
    _footer(slide, p)


def _table_slides(prs, p: ReportPayload, t: Table) -> None:
    chunks = [t.rows[i:i + TABLE_ROWS_PER_SLIDE] for i in range(0, len(t.rows), TABLE_ROWS_PER_SLIDE)] or [[]]
    weights = [2.4 if c.unit is Unit.TEXT else 1.0 for c in t.columns]
    width = SLIDE_W - 2 * MARGIN
    for n, rows in enumerate(chunks):
        slide = _blank(prs)
        _title(slide, t.title + (" (continued)" if n else ""))
        shape = slide.shapes.add_table(len(rows) + 1, len(t.columns), MARGIN, Inches(1.3), width,
                                       Inches(0.4) * (len(rows) + 1))
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
            _text(slide, MARGIN, SLIDE_H - Inches(0.85), width, Inches(0.3), t.note, size=10.5,
                 color=theme.TEXT_MUTED)
        _footer(slide, p)


def _cell(cell, text: str, *, bold: bool = False, numeric: bool = False, header: bool = False,
          zebra: bool = False) -> None:
    cell.text = ""
    para = cell.text_frame.paragraphs[0]
    para.alignment = PP_ALIGN.RIGHT if numeric else PP_ALIGN.LEFT
    run = para.add_run()
    run.text = text
    run.font.size = Pt(11.5)
    run.font.bold = bold
    run.font.color.rgb = _rgb("#FFFFFF" if header else theme.INK)
    cell.fill.solid()
    cell.fill.fore_color.rgb = _rgb(theme.PRIMARY if header else (theme.ZEBRA if zebra else theme.SURFACE))
    cell.margin_top = cell.margin_bottom = Inches(0.045)


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
