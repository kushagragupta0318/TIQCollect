# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-09-24 — New file. E09: colours and fonts for every rendered report.
#   Taken from the Collections Command Center (docs/ui/COMMAND-CENTER-UI-SPEC.md
#   §1.3–1.5, §4.11) with two measured departures, both from running the
#   dataviz palette validator rather than reading the hexes:
#
#   - CHART_SERIES, in CC's own order, FAILS colour-blind separation: #DB2777
#     next to #059669 is ΔE 1.1 for a deuteranope (the floor is 8), and the
#     slate #475569 is below the chroma floor, so it reads as grey. The same
#     CC hues, reordered and without the slate, pass every check (worst
#     adjacent CVD ΔE 14.5, normal-vision 28.4).
#   - CC's DPD_COLORS fail the normal-vision floor between 31-60 (#F59E0B) and
#     61-90 (#F97316): ΔE 9.6, where 15 is the floor. DPD buckets are ORDERED,
#     which is a magnitude job, so ordered series use a single-hue ramp.
#
#   #F59E0B and #06B6D4 sit under 3:1 contrast against white. The renderers
#   therefore label values directly on single-series bars, and the XLSX carries
#   every chart's data as a sheet (the table view).
# ───────────────────────────────────────────────────────────────────────────
from __future__ import annotations

from pathlib import Path

from app.reports.payload import Chart

# Command Center hues, validated order. Never cycled: payload.MAX_SERIES caps a chart.
SERIES = ["#2563EB", "#F59E0B", "#DB2777", "#06B6D4", "#7C3AED", "#059669"]
# Single-hue ramp, light → dark, for ordered series (DPD buckets).
SEQUENTIAL = ["#BFDBFE", "#93C5FD", "#60A5FA", "#3B82F6", "#1D4ED8", "#1E3A8A"]
REFERENCE = "#98A2B3"       # BRAND.muted — CC's dashed "target pace line"

INK = "#101828"             # BRAND.ink
TEXT_MUTED = "#667085"      # BRAND.slate
GRID = "#ECEDF1"            # BRAND.grid
PRIMARY = "#4F46E5"         # BRAND.primary — headings and rules
AI_ACCENT = "#6D28D9"       # the violet the product already uses for its ML-assisted badge
SURFACE = "#FFFFFF"
ZEBRA = "#F9FAFB"
WARN_FILL = "#FFFAEB"       # RISK_SOFT_COLORS.High
WARN_INK = "#B54708"

FONT_DIR = Path(__file__).parent / "fonts"
FONT_REGULAR = FONT_DIR / "NotoSans-Regular.ttf"
FONT_BOLD = FONT_DIR / "NotoSans-Bold.ttf"
FONT_FAMILY = "Noto Sans"


def series_colors(chart: Chart) -> list[str]:
    """One colour per series, reference series muted. Fixed order: a colour
    follows the series' POSITION in the payload, never its rank or value."""
    data = [s for s in chart.series if not s.reference]
    if chart.scale == "sequential":
        n = len(data)
        # Evenly spaced along the ramp, so neighbours stay as far apart in
        # lightness as the count allows.
        last = len(SEQUENTIAL) - 1
        palette = ([SEQUENTIAL[round(i * last / (n - 1))] for i in range(n)] if n > 1
                   else [SEQUENTIAL[3]])
    else:
        palette = SERIES
    out, i = [], 0
    for s in chart.series:
        if s.reference:
            out.append(REFERENCE)
        else:
            out.append(palette[i])
            i += 1
    return out
