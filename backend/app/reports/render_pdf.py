# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-09-24 — New file. E09: ReportPayload → PDF (ReportLab platypus).
#   The platform's board pack printed "Rs" because its PDF font had no rupee
#   glyph. This one embeds Noto Sans (fonts/, SIL OFL) for every run of text,
#   so "₹" is a real glyph in the document, not a substitution.
# ───────────────────────────────────────────────────────────────────────────
"""A4 portrait: a cover (title, organisation, scope, period, the synthetic
warning), then each section in order — heading, narrative (labelled when AI
wrote it), KPI tiles, charts (matplotlib PNGs from charts.py), tables with a
repeating header. Every page carries a footer naming the report and period.

Amounts in tables and tiles use compact crore/lakh; the XLSX carries the exact
figures. Nothing is computed here: every number is formatted from the payload.
"""
from __future__ import annotations

import io

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    CondPageBreak, Image, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer,
    Table as RLTable, TableStyle,
)
from xml.sax.saxutils import escape

from app.reports import theme
from app.reports.charts import chart_png
from app.reports.formatting import delta, fmt, period_label
from app.reports.payload import SYNTHETIC_WARNING, Chart, Kpi, ReportPayload, Section, Table, Unit

FONT, FONT_BOLD = "NotoSans", "NotoSans-Bold"
_REGISTERED = False


def _register_fonts() -> None:
    global _REGISTERED
    if _REGISTERED:
        return
    pdfmetrics.registerFont(TTFont(FONT, str(theme.FONT_REGULAR)))
    pdfmetrics.registerFont(TTFont(FONT_BOLD, str(theme.FONT_BOLD)))
    pdfmetrics.registerFontFamily(FONT, normal=FONT, bold=FONT_BOLD, italic=FONT, boldItalic=FONT_BOLD)
    _REGISTERED = True


def _c(hex_: str) -> colors.Color:
    return colors.HexColor(hex_)


def _styles() -> dict[str, ParagraphStyle]:
    base = ParagraphStyle("base", fontName=FONT, fontSize=9, leading=12.5, textColor=_c(theme.INK))
    return {
        "base": base,
        "muted": ParagraphStyle("muted", parent=base, fontSize=7.5, leading=10, textColor=_c(theme.TEXT_MUTED)),
        "cover_title": ParagraphStyle("cover_title", parent=base, fontName=FONT_BOLD, fontSize=26, leading=31),
        "cover_sub": ParagraphStyle("cover_sub", parent=base, fontSize=13, leading=17, textColor=_c(theme.TEXT_MUTED)),
        "cover_meta": ParagraphStyle("cover_meta", parent=base, fontSize=10, leading=15),
        "h1": ParagraphStyle("h1", parent=base, fontName=FONT_BOLD, fontSize=15, leading=19,
                             textColor=_c(theme.INK), spaceBefore=4, spaceAfter=6),
        "h2": ParagraphStyle("h2", parent=base, fontName=FONT_BOLD, fontSize=10, leading=13, spaceBefore=6, spaceAfter=4),
        "ai": ParagraphStyle("ai", parent=base, fontName=FONT_BOLD, fontSize=7, leading=9, textColor=_c(theme.AI_ACCENT)),
        "warn": ParagraphStyle("warn", parent=base, fontSize=8, leading=11, textColor=_c(theme.WARN_INK)),
        "tile_label": ParagraphStyle("tile_label", parent=base, fontSize=7, leading=9, textColor=_c(theme.TEXT_MUTED)),
        "tile_value": ParagraphStyle("tile_value", parent=base, fontName=FONT_BOLD, fontSize=14, leading=17),
        "tile_note": ParagraphStyle("tile_note", parent=base, fontSize=6.5, leading=8.5, textColor=_c(theme.TEXT_MUTED)),
        "cell": ParagraphStyle("cell", parent=base, fontSize=7.5, leading=9.5),
        "cell_num": ParagraphStyle("cell_num", parent=base, fontSize=7.5, leading=9.5, alignment=TA_RIGHT),
        "head": ParagraphStyle("head", parent=base, fontName=FONT_BOLD, fontSize=7.5, leading=9.5,
                               textColor=_c(theme.TEXT_MUTED), alignment=TA_LEFT),
        "head_num": ParagraphStyle("head_num", parent=base, fontName=FONT_BOLD, fontSize=7.5, leading=9.5,
                                   textColor=_c(theme.TEXT_MUTED), alignment=TA_RIGHT),
    }


def _p(text: str, style: ParagraphStyle) -> Paragraph:
    return Paragraph(escape(text), style)


def _warning_box(p: ReportPayload, st, width: float) -> RLTable:
    lines = [SYNTHETIC_WARNING] if p.synthetic else []
    if p.data_note:
        lines.append(p.data_note)
    t = RLTable([[_p(" ".join(lines), st["warn"])]], colWidths=[width])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), _c(theme.WARN_FILL)),
        ("BOX", (0, 0), (-1, -1), 0.6, _c("#FEC84B")),
        ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    return t


def _cover(p: ReportPayload, st, width: float) -> list:
    out: list = [Spacer(1, 40 * mm), _p(p.organisation.upper(), st["muted"]), Spacer(1, 4 * mm),
                 _p(p.title, st["cover_title"])]
    if p.subtitle:
        out += [Spacer(1, 2 * mm), _p(p.subtitle, st["cover_sub"])]
    meta = [("Period", period_label(p)), ("Scope", p.scope)]
    if p.prepared_for:
        meta.append(("Prepared for", p.prepared_for))
    meta.append(("Generated", p.generated_at.strftime("%d %b %Y, %H:%M UTC").lstrip("0")))
    out.append(Spacer(1, 12 * mm))
    for k, v in meta:
        out.append(Paragraph(f'<font color="{theme.TEXT_MUTED}">{escape(k)}</font>&nbsp;&nbsp;{escape(v)}',
                             st["cover_meta"]))
    if p.synthetic or p.data_note:
        out += [Spacer(1, 14 * mm), _warning_box(p, st, width)]
    out.append(PageBreak())
    return out


def _tile(k: Kpi, st) -> list:
    cell = [_p(k.label, st["tile_label"]), _p(fmt(k.value, k.unit, compact=True), st["tile_value"])]
    d = delta(k.value, k.prior, k.unit)
    if d:
        cell.append(_p(f"{d} vs prior period", st["tile_note"]))
    if k.basis:
        cell.append(_p(k.basis, st["tile_note"]))
    return cell


def _kpi_grid(kpis: list[Kpi], st, width: float, per_row: int = 3) -> RLTable:
    cells = [_tile(k, st) for k in kpis]
    while len(cells) % per_row:
        cells.append("")
    rows = [cells[i:i + per_row] for i in range(0, len(cells), per_row)]
    gap = 3 * mm
    t = RLTable(rows, colWidths=[(width - gap * (per_row - 1)) / per_row] * per_row)
    style = [("VALIGN", (0, 0), (-1, -1), "TOP"),
             ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
             ("LEFTPADDING", (0, 0), (-1, -1), 7), ("RIGHTPADDING", (0, 0), (-1, -1), 7)]
    for r, row in enumerate(rows):
        for c, cell in enumerate(row):
            if cell != "":
                style.append(("BOX", (c, r), (c, r), 0.6, _c(theme.GRID)))
    t.setStyle(TableStyle(style))
    return t


def _chart(c: Chart, st, width: float) -> KeepTogether:
    png = chart_png(c)
    img = ImageReader(io.BytesIO(png))
    iw, ih = img.getSize()
    h = width * ih / iw
    return KeepTogether([_p(c.title, st["h2"]), Image(io.BytesIO(png), width=width, height=h), Spacer(1, 3 * mm)])


def _table(t: Table, st, width: float) -> list:
    weights = [2.4 if c.unit is Unit.TEXT else 1.0 for c in t.columns]
    total = sum(weights)
    col_w = [width * w / total for w in weights]
    head = [_p(c.label, st["head"] if c.unit is Unit.TEXT else st["head_num"]) for c in t.columns]
    body = [[_p(fmt(row.get(c.key), c.unit, compact=True), st["cell"] if c.unit is Unit.TEXT else st["cell_num"])
             for c in t.columns] for row in t.rows]
    rl = RLTable([head, *body], colWidths=col_w, repeatRows=1)
    style = [("LINEBELOW", (0, 0), (-1, 0), 0.8, _c(theme.PRIMARY)),
             ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
             ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
             ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4)]
    for r in range(1, len(body) + 1):
        if r % 2 == 0:
            style.append(("BACKGROUND", (0, r), (-1, r), _c(theme.ZEBRA)))
    rl.setStyle(TableStyle(style))
    out: list = [CondPageBreak(30 * mm), _p(t.title, st["h2"]), rl]
    if t.note:
        out += [Spacer(1, 1.5 * mm), _p(t.note, st["muted"])]
    out.append(Spacer(1, 5 * mm))
    return out


def _section(s: Section, st, width: float) -> list:
    out: list = [CondPageBreak(45 * mm), _p(s.title, st["h1"])]
    if s.narrative:
        if s.narrative.ai_generated:
            out.append(_p("AI-WRITTEN COMMENTARY — figures are taken from the report data", st["ai"]))
        out += [_p(s.narrative.text, st["base"]), Spacer(1, 4 * mm)]
    if s.kpis:
        out += [_kpi_grid(s.kpis, st, width), Spacer(1, 5 * mm)]
    for c in s.charts:
        out.append(_chart(c, st, width))
    for t in s.tables:
        out += _table(t, st, width)
    return out


def render_pdf(p: ReportPayload, *, compress: bool = True) -> bytes:
    """`compress=False` leaves the content and font streams readable, which is
    how the tests check that the rupee glyph is really in the embedded font."""
    _register_fonts()
    st = _styles()
    buf = io.BytesIO()
    margin = 18 * mm
    doc = SimpleDocTemplate(
        buf, pagesize=A4, leftMargin=margin, rightMargin=margin, topMargin=margin, bottomMargin=margin + 4 * mm,
        title=p.title, author=p.organisation, subject=f"{p.title} · {period_label(p)}",
        creator="TIQCollect report engine", pageCompression=1 if compress else 0, invariant=1,
    )
    width = A4[0] - 2 * margin

    footer_left = f"{p.organisation} · {p.title} · {period_label(p)}"

    def _footer(canvas, d) -> None:
        canvas.saveState()
        canvas.setFont(FONT, 7)
        canvas.setFillColor(_c(theme.TEXT_MUTED))
        canvas.drawString(margin, 10 * mm, footer_left)
        right = f"Page {d.page}"
        if p.synthetic:
            right = f"SYNTHETIC DATA · {right}"
        canvas.drawRightString(A4[0] - margin, 10 * mm, right)
        canvas.setStrokeColor(_c(theme.GRID))
        canvas.line(margin, 13 * mm, A4[0] - margin, 13 * mm)
        canvas.restoreState()

    story = _cover(p, st, width)
    for s in p.sections:
        story += _section(s, st, width)
    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buf.getvalue()
