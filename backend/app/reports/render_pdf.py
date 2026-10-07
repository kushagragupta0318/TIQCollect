# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-09-24 — New file. E09: ReportPayload → PDF (ReportLab platypus).
#   The platform's board pack printed "Rs" because its PDF font had no rupee
#   glyph. This one embeds Noto Sans (fonts/, SIL OFL), so "₹" is a real
#   glyph in the document, not a substitution.
# 2026-10-07 — The render lift (owner: "very basic and bland"). Structure is
#   unchanged (cover → per-section heading/narrative/KPIs/charts/tables →
#   footer on every page); this pass is weight and hierarchy, not a new
#   layout. The synthetic/confidential marks moved from a cover-page box to
#   the footer that already runs on every page — more total exposure, not
#   less, and the cover gets room to breathe. A KPI's delta (when the
#   payload carries a prior — most do not yet) is a vector triangle, never a
#   glyph: the bundled Noto Sans has no ▲▼ in its cmap (checked directly),
#   and drawing one anyway is exactly the missing-glyph bug this file's font
#   embedding exists to prevent.
# ───────────────────────────────────────────────────────────────────────────
"""A4 portrait: a branded cover (wordmark band, title, bank name, period and
the "as of" date — no KPI, no warning box, so it breathes), then each
section in order — a coloured divider under the heading, narrative (labelled
when AI wrote it), KPI cards with a delta triangle where the payload has a
prior, charts (matplotlib PNGs from charts.py, sized to lead), tables with a
repeating header as supporting detail beneath. Every page's footer carries
the organisation/title/period on the left and SYNTHETIC DATA / CONFIDENTIAL
/ the page number on the right.

Amounts in tables and tiles use compact crore/lakh; the XLSX carries the
exact figures. Nothing is computed here: every number is formatted from the
payload.
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
from reportlab.graphics.shapes import Drawing, Polygon
from reportlab.platypus import (
    CondPageBreak, HRFlowable, Image, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer,
    Table as RLTable, TableStyle,
)
from xml.sax.saxutils import escape

from app.reports import theme
from app.reports.charts import chart_png
from app.reports.formatting import fmt, kpi_delta, period_label
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
    base = ParagraphStyle("base", fontName=FONT, fontSize=theme.BODY_SIZE, leading=theme.BODY_SIZE + 4,
                          textColor=_c(theme.INK))
    return {
        "base": base,
        "muted": ParagraphStyle("muted", parent=base, fontSize=theme.CAPTION_SIZE, leading=11,
                                textColor=_c(theme.TEXT_MUTED)),
        "wordmark": ParagraphStyle("wordmark", parent=base, fontName=FONT_BOLD, fontSize=11,
                                   textColor=_c("#FFFFFF")),
        "cover_title": ParagraphStyle("cover_title", parent=base, fontName=FONT_BOLD, fontSize=30, leading=35),
        "cover_sub": ParagraphStyle("cover_sub", parent=base, fontSize=14, leading=18,
                                    textColor=_c(theme.TEXT_MUTED)),
        "meta_label": ParagraphStyle("meta_label", parent=base, fontName=FONT_BOLD, fontSize=8,
                                     leading=13, textColor=_c(theme.TEXT_MUTED)),
        "meta_value": ParagraphStyle("meta_value", parent=base, fontSize=11.5, leading=15),
        "cover_caption": ParagraphStyle("cover_caption", parent=base, fontSize=9, leading=13,
                                        textColor=_c(theme.WARN_INK)),
        "h1": ParagraphStyle("h1", parent=base, fontName=FONT_BOLD, fontSize=theme.H1_SIZE, leading=24,
                             textColor=_c(theme.INK), spaceBefore=2, spaceAfter=2),
        "h2": ParagraphStyle("h2", parent=base, fontName=FONT_BOLD, fontSize=theme.H2_SIZE, leading=16,
                             textColor=_c(theme.INK), spaceBefore=6, spaceAfter=4),
        "ai": ParagraphStyle("ai", parent=base, fontName=FONT_BOLD, fontSize=8, leading=11,
                             textColor=_c(theme.AI_ACCENT)),
        "warn": ParagraphStyle("warn", parent=base, fontSize=9, leading=12.5, textColor=_c(theme.WARN_INK)),
        "tile_label": ParagraphStyle("tile_label", parent=base, fontName=FONT_BOLD, fontSize=theme.TILE_LABEL_SIZE,
                                     leading=12, textColor=_c(theme.TEXT_MUTED)),
        "tile_value": ParagraphStyle("tile_value", parent=base, fontName=FONT_BOLD, fontSize=theme.TILE_VALUE_SIZE,
                                     leading=theme.TILE_VALUE_SIZE + 3),
        "tile_delta_good": ParagraphStyle("tile_delta_good", parent=base, fontName=FONT_BOLD, fontSize=9.5,
                                          leading=12, textColor=_c(theme.GOOD)),
        "tile_delta_bad": ParagraphStyle("tile_delta_bad", parent=base, fontName=FONT_BOLD, fontSize=9.5,
                                         leading=12, textColor=_c(theme.BAD)),
        "tile_delta_neutral": ParagraphStyle("tile_delta_neutral", parent=base, fontName=FONT_BOLD, fontSize=9.5,
                                             leading=12, textColor=_c(theme.TEXT_MUTED)),
        "cell": ParagraphStyle("cell", parent=base, fontSize=theme.BODY_SIZE - 1, leading=13),
        "cell_num": ParagraphStyle("cell_num", parent=base, fontSize=theme.BODY_SIZE - 1, leading=13,
                                   alignment=TA_RIGHT),
        "head": ParagraphStyle("head", parent=base, fontName=FONT_BOLD, fontSize=theme.CAPTION_SIZE,
                               leading=11, textColor=_c(theme.TEXT_MUTED), alignment=TA_LEFT),
        "head_num": ParagraphStyle("head_num", parent=base, fontName=FONT_BOLD, fontSize=theme.CAPTION_SIZE,
                                   leading=11, textColor=_c(theme.TEXT_MUTED), alignment=TA_RIGHT),
    }


def _p(text: str, style: ParagraphStyle) -> Paragraph:
    return Paragraph(escape(text), style)


def _triangle(color_hex: str, *, up: bool, size: float = 2.4 * mm) -> Drawing:
    """A filled up/down triangle, drawn as vector shapes — not a glyph. The
    bundled font has no ▲▼ in its cmap (checked directly); a delta's
    direction is this triangle plus the signed number beside it, both
    independent of what the font can draw."""
    d = Drawing(size, size)
    points = [0, 0, size, 0, size / 2, size] if up else [0, size, size, size, size / 2, 0]
    d.add(Polygon(points=points, fillColor=_c(color_hex), strokeColor=None))
    return d


def _brand_band(width: float, st: dict) -> RLTable:
    """The cover's full-width header band — just the wordmark. The
    organisation, title and meta sit in the normal flow beneath it, so a
    long bank name or title is never clipped inside a fixed-height box."""
    t = RLTable([[_p(theme.WORDMARK, st["wordmark"])]], colWidths=[width], rowHeights=[14 * mm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), _c(theme.BRAND_BAND)),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 10 * mm), ("RIGHTPADDING", (0, 0), (-1, -1), 10 * mm),
    ]))
    return t


def _cover(p: ReportPayload, st, width: float) -> list:
    out: list = [_brand_band(width, st), Spacer(1, 22 * mm),
                 _p(p.organisation.upper(), st["muted"]), Spacer(1, 4 * mm), _p(p.title, st["cover_title"])]
    if p.subtitle:
        out += [Spacer(1, 3 * mm), _p(p.subtitle, st["cover_sub"])]
    out.append(Spacer(1, 10 * mm))

    meta = [("PERIOD", period_label(p)), ("AS OF", p.period_end.strftime("%d %b %Y").lstrip("0")),
           ("SCOPE", p.scope)]
    if p.prepared_for:
        meta.append(("PREPARED FOR", p.prepared_for))
    meta.append(("GENERATED", p.generated_at.strftime("%d %b %Y, %H:%M UTC").lstrip("0")))
    rows = [[_p(k, st["meta_label"]), _p(v, st["meta_value"])] for k, v in meta]
    meta_table = RLTable(rows, colWidths=[34 * mm, width - 34 * mm])
    meta_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
    ]))
    out.append(meta_table)
    # The short SYNTHETIC DATA / CONFIDENTIAL marks run in the footer on
    # every page (below); the full sentence stays here, once, quietly —
    # a caption now, not the boxed alert the cover used to carry, but never
    # dropped. A run-specific note (e.g. an abstaining KPI set) follows it.
    if p.synthetic or p.data_note:
        out.append(Spacer(1, 8 * mm))
        if p.synthetic:
            out.append(_p(SYNTHETIC_WARNING, st["cover_caption"]))
        if p.data_note:
            out.append(_p(p.data_note, st["cover_caption"]))
    out.append(PageBreak())
    return out


def _tile(k: Kpi, st) -> list:
    cell: list = [_p(k.label.upper(), st["tile_label"]), Spacer(1, 2), _p(fmt(k.value, k.unit, compact=True),
                 st["tile_value"])]
    d = kpi_delta(k.value, k.prior, k.direction, k.unit)
    if d:
        text, up, good = d
        color = theme.GOOD if good else (theme.BAD if good is False else theme.TEXT_MUTED)
        style = st["tile_delta_good"] if good else (st["tile_delta_bad"] if good is False else
                                                     st["tile_delta_neutral"])
        row = RLTable([[_triangle(color, up=up), _p(f"{text} vs prior period", style)]],
                      colWidths=[2.4 * mm, None])
        row.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (0, 0), 0), ("RIGHTPADDING", (0, 0), (0, 0), 0),
            ("LEFTPADDING", (1, 0), (1, 0), 4), ("RIGHTPADDING", (1, 0), (1, 0), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ]))
        cell += [Spacer(1, 4), row]
    return cell


def _kpi_grid(kpis: list[Kpi], st, width: float, per_row: int = 3) -> RLTable:
    cells = [_tile(k, st) for k in kpis]
    while len(cells) % per_row:
        cells.append("")
    rows = [cells[i:i + per_row] for i in range(0, len(cells), per_row)]
    gap = 4 * mm
    t = RLTable(rows, colWidths=[(width - gap * (per_row - 1)) / per_row] * per_row)
    style = [("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 12), ("BOTTOMPADDING", (0, 0), (-1, -1), 13),
            ("LEFTPADDING", (0, 0), (-1, -1), 12), ("RIGHTPADDING", (0, 0), (-1, -1), 10)]
    for r, row in enumerate(rows):
        for c, cell in enumerate(row):
            if cell != "":
                style.append(("BOX", (c, r), (c, r), 0.75, _c(theme.GRID)))
                # The card's accent edge — brand colour, not a good/bad signal:
                # a KPI's direction alone does not say whether its CURRENT
                # value is good, only which way "better" points.
                style.append(("LINEBEFORE", (c, r), (c, r), 2.6, _c(theme.PRIMARY)))
    t.setStyle(TableStyle(style))
    return t


def _section_heading(title: str, st, width: float) -> list:
    return [_p(title, st["h1"]), Spacer(1, 3), HRFlowable(width=width, thickness=1.4, color=_c(theme.PRIMARY),
            spaceAfter=10, hAlign="LEFT")]


def _chart(c: Chart, st, width: float) -> KeepTogether:
    png = chart_png(c)
    img = ImageReader(io.BytesIO(png))
    iw, ih = img.getSize()
    h = width * ih / iw
    return KeepTogether([_p(c.title, st["h2"]), Image(io.BytesIO(png), width=width, height=h), Spacer(1, 4 * mm)])


def _table(t: Table, st, width: float) -> list:
    weights = [2.4 if c.unit is Unit.TEXT else 1.0 for c in t.columns]
    total = sum(weights)
    col_w = [width * w / total for w in weights]
    head = [_p(c.label, st["head"] if c.unit is Unit.TEXT else st["head_num"]) for c in t.columns]
    body = [[_p(fmt(row.get(c.key), c.unit, compact=True), st["cell"] if c.unit is Unit.TEXT else st["cell_num"])
            for c in t.columns] for row in t.rows]
    rl = RLTable([head, *body], colWidths=col_w, repeatRows=1)
    style = [("LINEBELOW", (0, 0), (-1, 0), 1.1, _c(theme.PRIMARY)),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5)]
    for r in range(1, len(body) + 1):
        if r % 2 == 0:
            style.append(("BACKGROUND", (0, r), (-1, r), _c(theme.ZEBRA)))
    rl.setStyle(TableStyle(style))
    out: list = [CondPageBreak(30 * mm), _p(t.title, st["h2"]), rl]
    if t.note:
        out += [Spacer(1, 2 * mm), _p(t.note, st["muted"])]
    out.append(Spacer(1, 6 * mm))
    return out


def _section(s: Section, st, width: float) -> list:
    out: list = [CondPageBreak(50 * mm), *_section_heading(s.title, st, width)]
    if s.narrative:
        if s.narrative.ai_generated:
            out.append(_p("AI-WRITTEN COMMENTARY — figures are taken from the report data", st["ai"]))
        out += [_p(s.narrative.text, st["base"]), Spacer(1, 5 * mm)]
    if s.kpis:
        out += [_kpi_grid(s.kpis, st, width), Spacer(1, 6 * mm)]
    # Charts lead; tables are supporting detail beneath (unchanged order —
    # the lift is weight: charts.py now renders them taller, and KPI cards
    # above carry more visual weight than the tables below).
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
    tags = (["SYNTHETIC DATA"] if p.synthetic else []) + ["CONFIDENTIAL"]

    def _footer(canvas, d) -> None:
        canvas.saveState()
        canvas.setStrokeColor(_c(theme.GRID))
        canvas.line(margin, 13 * mm, A4[0] - margin, 13 * mm)
        canvas.setFont(FONT, 7.5)
        canvas.setFillColor(_c(theme.TEXT_MUTED))
        canvas.drawString(margin, 9.5 * mm, footer_left)
        canvas.setFont(FONT_BOLD, 7.5)
        canvas.setFillColor(_c(theme.WARN_INK) if p.synthetic else _c(theme.TEXT_MUTED))
        canvas.drawRightString(A4[0] - margin, 9.5 * mm, " · ".join([*tags, f"Page {d.page}"]))
        canvas.restoreState()

    story = _cover(p, st, width)
    for s in p.sections:
        story += _section(s, st, width)
    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buf.getvalue()
