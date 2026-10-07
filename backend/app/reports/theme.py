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
#
# 2026-10-07 — the render lift (owner: "very basic and bland"). Added below,
#   nothing above changed: SERIES/SEQUENTIAL/REFERENCE/series_colors() are the
#   validated palette and stay exactly as measured.
#   - A brand header band (BRAND_BAND + WORDMARK) for the cover/title slide.
#   - GOOD/BAD: semantic colours for a delta, used ONLY beside its own sign
#     (+/-) and number, never as the sole carrier of meaning — the same rule
#     theme.py already states for a low-contrast series ("direct labels").
#     Not CVD-validated as a pair (that check is for adjacent chart series;
#     a single isolated badge with its own text has no adjacency to fail).
#   - A type scale (H1/H2/BODY/CAPTION/TILE_VALUE/TILE_LABEL), named once so
#     the PDF and PPTX renderers read the same sizes instead of each
#     inventing its own.
#   No glyph here that the bundled Noto Sans lacks: checked its cmap directly
#   (TTFont(...).getBestCmap()) — ▲▼✓✗ are ALL absent, so a delta's direction
#   is the sign formatting.delta() already returns (+/-) plus colour, never
#   an arrow icon; '^'/'v' (plain Latin) are the only directional marks used,
#   and only as decoration beside the signed number, never alone.
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

# ── the render lift, 2026-10-07 (additive; nothing above this line changed) ──
# Cover / title-slide header band. PRIMARY darkened one step, not a new hue —
# the band is a big flat fill, where the chart palette's own PRIMARY reads as
# too light to hold large reversed (white) text comfortably.
BRAND_BAND = "#3730A3"
WORDMARK = "TransorgIQ"

# A delta's colour, beside its own +/- sign and a vector triangle (never the
# sole carrier — the glyph-availability note above explains the triangle).
GOOD = "#059669"            # same green as SERIES[5], reused, not a new series colour
BAD = "#DC2626"
GOOD_SOFT = "#ECFDF5"
BAD_SOFT = "#FEF2F2"

# One type scale, named once, so the PDF and PPTX renderers do not each
# invent their own sizes (the "9pt body / 7pt tiles" complaint). Points for
# the PDF; render_pptx.py uses the same numbers via pptx.util.Pt().
H1_SIZE = 20
H2_SIZE = 13
BODY_SIZE = 10.5
CAPTION_SIZE = 8.5
TILE_LABEL_SIZE = 9.5
TILE_VALUE_SIZE = 23

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
