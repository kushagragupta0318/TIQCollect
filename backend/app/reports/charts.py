# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-09-24 — New file. E09: a payload Chart drawn as a PNG for the PDF.
#   (The PPTX draws NATIVE charts from the same Chart; the XLSX carries its
#   data as a sheet.) matplotlib's object API only — no pyplot, so no global
#   backend or figure state is touched in a worker that also trains models
#   (ml/pipeline/eda.py uses Agg the same way).
# ───────────────────────────────────────────────────────────────────────────
from __future__ import annotations

import io
import math

from matplotlib import font_manager
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.ticker import FuncFormatter

from app.reports import theme
from app.reports.formatting import axis_scale, axis_tick, fmt
from app.reports.payload import Chart, ChartKind, Unit

_FONTS_ADDED = False


def _ensure_fonts() -> None:
    """Register the bundled Noto Sans with matplotlib once per process, so the
    rupee sign in tick labels comes from a font that has it."""
    global _FONTS_ADDED
    if not _FONTS_ADDED:
        font_manager.fontManager.addfont(str(theme.FONT_REGULAR))
        font_manager.fontManager.addfont(str(theme.FONT_BOLD))
        _FONTS_ADDED = True


def _nan(v: float | None) -> float:
    return math.nan if v is None else float(v)


def chart_png(chart: Chart, *, width_in: float = 6.6, height_in: float = 2.9, dpi: int = 200) -> bytes:
    _ensure_fonts()
    fig = Figure(figsize=(width_in, height_in), dpi=dpi, facecolor=theme.SURFACE)
    FigureCanvasAgg(fig)
    ax = fig.add_subplot(111)
    font = {"family": theme.FONT_FAMILY}
    colors = theme.series_colors(chart)
    x = list(range(len(chart.categories)))
    data_series = [s for s in chart.series if not s.reference]

    if chart.kind is ChartKind.LINE:
        for s, c in zip(chart.series, colors):
            ax.plot(x, [_nan(v) for v in s.values], color=c, linewidth=2 if not s.reference else 1.5,
                    linestyle=(0, (5, 5)) if s.reference else "-",
                    marker=None if s.reference else "o", markersize=4, label=s.name, zorder=3)
    elif chart.kind is ChartKind.STACKED_BAR:
        bottoms = [0.0] * len(x)
        for s, c in zip(chart.series, colors):
            vals = [0.0 if v is None else float(v) for v in s.values]
            # White edge: the 2px surface gap between stacked fills.
            ax.bar(x, vals, bottom=bottoms, width=0.55, color=c, edgecolor=theme.SURFACE, linewidth=1.2,
                   label=s.name, zorder=3)
            bottoms = [b + v for b, v in zip(bottoms, vals)]
    else:  # clustered bars
        n = max(1, len(data_series))
        width = min(0.7 / n, 0.45)
        for i, (s, c) in enumerate(zip(chart.series, colors)):
            offs = [xi + (i - (n - 1) / 2) * width for xi in x]
            bars = ax.bar(offs, [_nan(v) for v in s.values], width=width * 0.92, color=c,
                          edgecolor=theme.SURFACE, linewidth=1.2, label=s.name, zorder=3)
            if n == 1:
                # Direct labels: this is the relief for low-contrast hues.
                for b, v in zip(bars, s.values):
                    if v is not None:
                        ax.annotate(fmt(v, chart.unit, compact=True), (b.get_x() + b.get_width() / 2, b.get_height()),
                                    xytext=(0, 3), textcoords="offset points", ha="center", va="bottom",
                                    fontsize=7, color=theme.INK, fontfamily=theme.FONT_FAMILY)

    ax.set_xticks(x, chart.categories, fontsize=7.5, color=theme.TEXT_MUTED, fontfamily=theme.FONT_FAMILY)
    peak = max((abs(v) for s in chart.series for v in s.values if v is not None), default=0.0)
    if chart.kind is ChartKind.STACKED_BAR:
        peak = max((sum(v or 0.0 for v in col) for col in zip(*(s.values for s in data_series))), default=peak)
    divisor, suffix = axis_scale(peak, chart.unit)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _p: axis_tick(v, chart.unit, divisor, suffix)))
    for label in ax.get_yticklabels():
        label.set_fontfamily(theme.FONT_FAMILY)
    ax.tick_params(axis="y", labelsize=7, colors=theme.TEXT_MUTED, length=0)
    ax.tick_params(axis="x", length=0)
    ax.grid(axis="y", color=theme.GRID, linestyle=(0, (3, 3)), linewidth=0.8, zorder=0)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(theme.GRID)
    if chart.unit in (Unit.INR, Unit.COUNT) and chart.kind is not ChartKind.LINE:
        ax.set_ylim(bottom=0)
    if len(chart.series) >= 2:
        ax.legend(loc="upper left", bbox_to_anchor=(0, 1.16), ncol=len(chart.series), frameon=False,
                  prop={**font, "size": 7.5}, handlelength=1.2, columnspacing=1.2, labelcolor=theme.INK)

    fig.tight_layout(pad=0.4)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi, facecolor=theme.SURFACE, metadata={"Software": None})
    return buf.getvalue()
