# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-09-24 — New file. E09: the one shape every report renderer reads.
#   The KPI catalog (C01) does not exist yet, so nothing here computes a
#   number: a payload arrives with its figures already in it, each KPI naming
#   the catalog id it came from. The PDF, PPTX and XLSX renderers are three
#   views of the same object and may not add, drop or recompute a figure.
# ───────────────────────────────────────────────────────────────────────────
"""ReportPayload — what a board pack, a risk pack or an agency review IS,
independent of the file format it is rendered into.

Units are declared, never inferred from a label. A percentage is carried as a
FRACTION (0.123 → 12.3%), the way the analytics layer computes it, so no
renderer ever has to guess whether 12.3 means 12.3% or 1,230%.
"""
from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class Unit(str, Enum):
    INR = "inr"          # rupees
    PCT = "pct"          # a fraction: 0.123 is 12.3%
    COUNT = "count"      # whole things: cases, agents, visits
    DAYS = "days"
    RATIO = "ratio"      # a plain multiple, e.g. 1.4x
    TEXT = "text"        # a table cell that is not a number


class Direction(str, Enum):
    HIGHER_IS_BETTER = "higher_is_better"
    LOWER_IS_BETTER = "lower_is_better"
    NEUTRAL = "neutral"


class Kpi(BaseModel):
    kpi_id: str                          # the catalog id (C01); the verifier cites it
    label: str
    value: float | None                  # None = not measurable for this scope, shown as "—"
    unit: Unit
    direction: Direction = Direction.NEUTRAL
    basis: str | None = None             # how it is computed, in words
    prior: float | None = None           # the comparison-period value, same unit


class Column(BaseModel):
    key: str
    label: str
    unit: Unit = Unit.TEXT


class Table(BaseModel):
    table_id: str
    title: str
    columns: list[Column]
    rows: list[dict[str, str | float | int | None]]
    note: str | None = None

    @model_validator(mode="after")
    def _rows_match_columns(self) -> "Table":
        keys = {c.key for c in self.columns}
        for i, row in enumerate(self.rows):
            extra = set(row) - keys
            if extra:
                raise ValueError(f"table {self.table_id}: row {i} has keys no column declares: {sorted(extra)}")
        return self


class ChartKind(str, Enum):
    BAR = "bar"                  # clustered columns
    STACKED_BAR = "stacked_bar"
    LINE = "line"


class Series(BaseModel):
    name: str
    values: list[float | None]
    # A benchmark drawn under the data (Command Center's "target pace line":
    # muted and dashed), not a category. It takes no palette slot.
    reference: bool = False


MAX_SERIES = 6      # the validated palette's size (theme.SERIES); a 7th folds into "Other" upstream


class Chart(BaseModel):
    chart_id: str
    title: str
    kind: ChartKind
    unit: Unit
    categories: list[str]
    series: list[Series]
    # "sequential" when the series are ORDERED (DPD buckets): one hue, light to
    # dark. "categorical" when they are identities (zones, agencies).
    scale: Literal["categorical", "sequential"] = "categorical"

    @model_validator(mode="after")
    def _series_fit_categories(self) -> "Chart":
        data_series = [s for s in self.series if not s.reference]
        if len(data_series) > MAX_SERIES:
            raise ValueError(
                f"chart {self.chart_id}: {len(data_series)} series; at most {MAX_SERIES} "
                "(fold the rest into 'Other' — a generated colour is never used)")
        for s in self.series:
            if len(s.values) != len(self.categories):
                raise ValueError(
                    f"chart {self.chart_id}: series {s.name!r} has {len(s.values)} values "
                    f"for {len(self.categories)} categories")
        return self


class Narrative(BaseModel):
    text: str
    # The repo's ai_generated convention: True only when a model wrote it. The
    # renderers label it; a template fallback is False and is not labelled AI.
    ai_generated: bool = False


class Section(BaseModel):
    section_id: str
    title: str
    narrative: Narrative | None = None
    kpis: list[Kpi] = Field(default_factory=list)
    charts: list[Chart] = Field(default_factory=list)
    tables: list[Table] = Field(default_factory=list)


class ReportPayload(BaseModel):
    report_id: str
    template: str                        # E10 names them: board, risk, audit, agency_review, monthly_mis
    title: str
    subtitle: str | None = None
    organisation: str                    # the bank (or agency) the report is about
    scope: str                           # e.g. "All agencies · all zones"
    period_start: date
    period_end: date
    generated_at: datetime
    prepared_for: str | None = None      # "Board of Directors"
    # Rule 5 of the standalone plan: every number from a model, forecast or
    # simulator carries the synthetic warning until real outcomes exist.
    synthetic: bool = True
    data_note: str | None = None
    sections: list[Section]

    @model_validator(mode="after")
    def _ids_are_unique(self) -> "ReportPayload":
        seen: set[str] = set()
        for s in self.sections:
            for obj_id in [s.section_id, *(c.chart_id for c in s.charts), *(t.table_id for t in s.tables)]:
                if obj_id in seen:
                    raise ValueError(f"duplicate id in payload: {obj_id}")
                seen.add(obj_id)
        if self.period_end < self.period_start:
            raise ValueError("period_end is before period_start")
        return self


SYNTHETIC_WARNING = (
    "Prepared from a synthetic demonstration book. The organisations, people and "
    "figures are invented; no number in this report describes a real borrower."
)
