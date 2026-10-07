# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-09-24 — New file. E09: ReportPayload → XLSX (openpyxl).
#   The exact-figure view of a report: every number is a NUMBER cell (never
#   formatted text), wearing the Excel format for its unit, so a reader can
#   sum, sort and pivot it. Every table is a sheet, and so is every chart's
#   data — the table view the charts' low-contrast hues rely on (theme.py).
# ───────────────────────────────────────────────────────────────────────────
from __future__ import annotations

import io
import re

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from app.reports import theme
from app.reports.formatting import XLSX_FORMATS, delta, period_label
from app.reports.payload import SYNTHETIC_WARNING, Chart, ReportPayload, Table, Unit

HEAD_FONT = Font(bold=True, color=theme.TEXT_MUTED.lstrip("#"))
HEAD_FILL = PatternFill("solid", fgColor=theme.ZEBRA.lstrip("#"))
HEAD_RULE = Border(bottom=Side(style="thin", color=theme.PRIMARY.lstrip("#")))
_BAD_SHEET_CHARS = re.compile(r"[\[\]:*?/\\]")


def sheet_name(title: str, used: set[str]) -> str:
    """Excel's rules: at most 31 characters, none of []:*?/\\, unique in the
    workbook (case-insensitively)."""
    base = _BAD_SHEET_CHARS.sub(" ", title).strip()[:31] or "Sheet"
    name, n = base, 2
    while name.lower() in used:
        suffix = f" ({n})"
        name = base[:31 - len(suffix)] + suffix
        n += 1
    used.add(name.lower())
    return name


def _header(ws, row: int, labels: list[str]) -> None:
    for ci, label in enumerate(labels, start=1):
        cell = ws.cell(row=row, column=ci, value=label)
        cell.font, cell.fill, cell.border = HEAD_FONT, HEAD_FILL, HEAD_RULE


def _widths(ws, min_w: int = 8, max_w: int = 60) -> None:
    for col in ws.columns:
        longest = max((len(str(c.value)) for c in col if c.value is not None), default=0)
        ws.column_dimensions[get_column_letter(col[0].column)].width = max(min_w, min(max_w, longest + 2))


def _value_cell(ws, row: int, col: int, value, unit: Unit):
    cell = ws.cell(row=row, column=col, value=value)
    if value is not None and unit is not Unit.TEXT:
        cell.number_format = XLSX_FORMATS[unit]
    return cell


def _report_sheet(ws, p: ReportPayload) -> None:
    ws.title = "Report"
    meta = [("Report", p.title), ("Organisation", p.organisation), ("Scope", p.scope),
            ("Period", period_label(p)), ("Generated (UTC)", p.generated_at.strftime("%Y-%m-%d %H:%M")),
            ("Report id", p.report_id), ("Template", p.template)]
    if p.prepared_for:
        meta.append(("Prepared for", p.prepared_for))
    if p.synthetic:
        meta.append(("Data", SYNTHETIC_WARNING))
    if p.data_note:
        meta.append(("Note", p.data_note))
    for r, (k, v) in enumerate(meta, start=1):
        ws.cell(row=r, column=1, value=k).font = Font(bold=True)
        ws.cell(row=r, column=2, value=v)
    ws.cell(row=1, column=2).font = Font(bold=True, size=14)

    r = len(meta) + 2
    kpis = [(s, k) for s in p.sections for k in s.kpis]
    if kpis:
        ws.cell(row=r, column=1, value="Key figures").font = Font(bold=True, size=12)
        r += 1
        _header(ws, r, ["Section", "KPI", "KPI id", "Value", "Prior", "Change", "Unit", "Basis"])
        for s, k in kpis:
            r += 1
            ws.cell(row=r, column=1, value=s.title)
            ws.cell(row=r, column=2, value=k.label)
            ws.cell(row=r, column=3, value=k.kpi_id)
            _value_cell(ws, r, 4, k.value, k.unit)
            _value_cell(ws, r, 5, k.prior, k.unit)
            ws.cell(row=r, column=6, value=delta(k.value, k.prior, k.unit))
            ws.cell(row=r, column=7, value=k.unit.value)
            ws.cell(row=r, column=8, value=k.basis)
        r += 2

    narratives = [s for s in p.sections if s.narrative]
    if narratives:
        ws.cell(row=r, column=1, value="Commentary").font = Font(bold=True, size=12)
        r += 1
        _header(ws, r, ["Section", "Written by", "Text"])
        for s in narratives:
            r += 1
            ws.cell(row=r, column=1, value=s.title)
            ws.cell(row=r, column=2, value="AI (figures from the report data)" if s.narrative.ai_generated else "Template")
            ws.cell(row=r, column=3, value=s.narrative.text).alignment = Alignment(wrap_text=True, vertical="top")
    _widths(ws)
    ws.column_dimensions["C"].width = max(ws.column_dimensions["C"].width, 24)


def _table_sheet(ws, t: Table) -> None:
    _header(ws, 1, [c.label for c in t.columns])
    for ri, row in enumerate(t.rows, start=2):
        for ci, col in enumerate(t.columns, start=1):
            _value_cell(ws, ri, ci, row.get(col.key), col.unit)
    ws.freeze_panes = "A2"
    if t.rows:
        ws.auto_filter.ref = f"A1:{get_column_letter(len(t.columns))}{len(t.rows) + 1}"
    if t.note:
        ws.cell(row=len(t.rows) + 3, column=1, value=t.note).font = Font(italic=True, color=theme.TEXT_MUTED.lstrip("#"))
    _widths(ws)


def _chart_sheet(ws, c: Chart) -> None:
    _header(ws, 1, ["Category", *[s.name + (" (reference)" if s.reference else "") for s in c.series]])
    for ri, cat in enumerate(c.categories, start=2):
        ws.cell(row=ri, column=1, value=cat)
        for si, s in enumerate(c.series, start=2):
            _value_cell(ws, ri, si, s.values[ri - 2], c.unit)
    ws.freeze_panes = "A2"
    _widths(ws)


def render_xlsx(p: ReportPayload) -> bytes:
    wb = Workbook()
    wb.properties.title = p.title
    wb.properties.creator = "TIQCollect report engine"
    wb.properties.subject = f"{p.organisation} · {period_label(p)}"
    naive = p.generated_at.replace(tzinfo=None)
    wb.properties.created = wb.properties.modified = naive

    used = {"report"}
    _report_sheet(wb.active, p)
    for s in p.sections:
        for t in s.tables:
            _table_sheet(wb.create_sheet(sheet_name(t.title, used)), t)
        for c in s.charts:
            _chart_sheet(wb.create_sheet(sheet_name(f"Chart - {c.title}", used)), c)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
