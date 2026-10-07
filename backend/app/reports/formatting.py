# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-09-24 — New file. E09: how a figure is written, once, for all three
#   renderers. The platform's reports printed "Rs" because their PDF font had
#   no rupee glyph; this product embeds one (fonts/), so the sign is "₹".
# ───────────────────────────────────────────────────────────────────────────
"""Indian number formatting. Grouping is lakh/crore (12,34,567), compact
amounts use L and Cr, and a missing value is an em dash — never 0."""
from __future__ import annotations

import math

from app.reports.payload import ReportPayload, Unit

RUPEE = "₹"
MISSING = "—"


def indian_grouping(n: int) -> str:
    """12345678 → '1,23,45,678'."""
    sign = "-" if n < 0 else ""
    s = str(abs(n))
    if len(s) <= 3:
        return sign + s
    head, tail = s[:-3], s[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return sign + ",".join(groups) + "," + tail


def _missing(v: float | None) -> bool:
    return v is None or (isinstance(v, float) and math.isnan(v))


def inr(v: float | None, *, compact: bool = False) -> str:
    if _missing(v):
        return MISSING
    sign = "-" if v < 0 else ""
    a = abs(v)
    if compact and a >= 1e7:
        return f"{sign}{RUPEE}{a / 1e7:.2f} Cr"
    if compact and a >= 1e5:
        return f"{sign}{RUPEE}{a / 1e5:.2f} L"
    return f"{sign}{RUPEE}{indian_grouping(round(a))}"


def pct(v: float | None, dp: int = 1) -> str:
    return MISSING if _missing(v) else f"{v * 100:.{dp}f}%"


def count(v: float | None) -> str:
    return MISSING if _missing(v) else indian_grouping(round(v))


def days(v: float | None) -> str:
    return MISSING if _missing(v) else f"{v:.1f} days"


def ratio(v: float | None) -> str:
    return MISSING if _missing(v) else f"{v:.2f}x"


def fmt(v: float | int | str | None, unit: Unit, *, compact: bool = False) -> str:
    """The one dispatcher every renderer calls for display text."""
    if unit is Unit.TEXT:
        return MISSING if v is None else str(v)
    if isinstance(v, str):
        return v
    return {
        Unit.INR: lambda x: inr(x, compact=compact),
        Unit.PCT: pct,
        Unit.COUNT: count,
        Unit.DAYS: days,
        Unit.RATIO: ratio,
    }[unit](v)


def _trim(x: float) -> str:
    return f"{x:.2f}".rstrip("0").rstrip(".")


def axis_scale(max_abs: float, unit: Unit) -> tuple[float, str]:
    """ONE scale per chart axis: (divisor, suffix). Rupees are shown in crore
    or lakh throughout, never "₹50 L" under "₹1 Cr" on the same axis. The PDF's
    tick labels and the PPTX's native chart values both use this."""
    if unit is Unit.INR:
        if max_abs >= 1e7:
            return 1e7, "Cr"
        if max_abs >= 1e5:
            return 1e5, "L"
    return 1.0, ""


def axis_tick(v: float, unit: Unit, divisor: float, suffix: str) -> str:
    if unit is Unit.INR:
        return f"{RUPEE}{_trim(v / divisor)}{' ' + suffix if suffix else ''}" if divisor != 1 else inr(v)
    if unit is Unit.PCT:
        return f"{_trim(v * 100)}%"
    if unit is Unit.RATIO:
        return f"{_trim(v)}x"
    return indian_grouping(round(v)) if unit is Unit.COUNT else _trim(v)


def period_label(p: ReportPayload) -> str:
    """"1 Sep 2026 – 30 Sep 2026" — the one spelling of a report's period."""
    f = "%d %b %Y"
    return f"{p.period_start.strftime(f).lstrip('0')} – {p.period_end.strftime(f).lstrip('0')}"


def delta(value: float | None, prior: float | None, unit: Unit) -> str | None:
    """Change against the prior period, in the KPI's own terms: percentage
    POINTS for a percentage, a relative % for everything else."""
    if _missing(value) or _missing(prior):
        return None
    if unit is Unit.PCT:
        return f"{(value - prior) * 100:+.1f} pp"
    if prior == 0:
        return None
    return f"{(value - prior) / abs(prior) * 100:+.1f}%"


# Excel number formats, so an XLSX cell keeps its number AND reads like the PDF.
# Lakh grouping needs two conditional sections; negatives fall to the plain one.
XLSX_FORMATS: dict[Unit, str] = {
    Unit.INR: '[>=10000000]"₹"##\\,##\\,##\\,##0;[>=100000]"₹"##\\,##\\,##0;"₹"#,##0',
    Unit.PCT: "0.0%",
    Unit.COUNT: "#,##0",
    Unit.DAYS: "0.0",
    Unit.RATIO: '0.00"x"',
    Unit.TEXT: "@",
}
