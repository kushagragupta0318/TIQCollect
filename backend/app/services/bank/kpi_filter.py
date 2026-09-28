"""The Command Center's global filter (plan §5.2, task C02): one object every
bank endpoint accepts, parsed from the URL, validated, and turned into SQL
per analytics view. A view that does not carry a dimension cannot be filtered
by it, and the KPI reading that view says so rather than showing an
unfiltered figure under a filter the user believes is applied.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from app.models.loan import DPDBucket, LoanType

PERIODS = ("mtd", "l30", "qtd", "fytd", "custom")
SECURITY = ("SECURED", "UNSECURED")
#: The analytics layer's "unknown" key (v2_0007: dimension keys are never NULL).
SENTINEL = "00000000-0000-0000-0000-000000000000"

#: Which filters each scoped view can honour, by the columns it carries (43's contract).
VIEW_DIMENSIONS = {
    "portfolio_daily_scoped": {"geo", "agency", "product", "bucket", "security"},
    "bucket_transitions_monthly_scoped": {"geo", "agency", "product", "security"},
    "agency_scorecard_monthly_scoped": {"geo", "agency"},
    "collections_daily_scoped": {"agency"},
    "field_activity_daily_scoped": {"agency"},
}
DIMENSION_LABELS = {"geo": "geography", "agency": "agency", "product": "product", "bucket": "DPD bucket",
                    "security": "secured / unsecured"}


class FilterError(ValueError):
    pass


@dataclass(frozen=True)
class KpiFilter:
    period: str = "mtd"
    start: date | None = None          # custom only
    end: date | None = None            # custom only; otherwise the latest reading
    geo: str | None = None             # a region id at any level (zone, region, state or city)
    agency: str | None = None
    product: str | None = None         # a LoanType
    bucket: str | None = None          # a DPDBucket
    security: str | None = None        # SECURED | UNSECURED

    def __post_init__(self):
        if self.period not in PERIODS:
            raise FilterError(f"period must be one of {', '.join(PERIODS)}")
        dates = (self.start is not None, self.end is not None)
        if (self.period == "custom" and dates != (True, True)) or (self.period != "custom" and any(dates)):
            raise FilterError("a custom period needs both start and end, and only a custom period takes them")
        if self.start and self.end and self.start > self.end:
            raise FilterError("start is after end")
        if self.product and self.product not in {t.value for t in LoanType}:
            raise FilterError("unknown product")
        if self.bucket and self.bucket not in {b.value for b in DPDBucket}:
            raise FilterError("unknown DPD bucket")
        if self.security and self.security not in SECURITY:
            raise FilterError("security must be SECURED or UNSECURED")

    def active(self) -> set[str]:
        return {d for d in DIMENSION_LABELS if getattr(self, d)}

    def unsupported(self, views) -> set[str]:
        """Active filters that at least one of `views` cannot apply."""
        return {d for d in self.active() for v in views if d not in VIEW_DIMENSIONS.get(v, set())}

    def window(self, reading: date) -> tuple[date, date]:
        """The period as dates, ending on the reading date (or the custom end)."""
        if self.period == "custom":
            return self.start, self.end
        end = reading
        if self.period == "mtd":
            return end.replace(day=1), end
        if self.period == "l30":
            return end - timedelta(days=29), end
        if self.period == "qtd":
            return date(end.year, 3 * ((end.month - 1) // 3) + 1, 1), end
        fy = end.year if end.month >= 4 else end.year - 1       # Indian financial year, from 1 April
        return date(fy, 4, 1), end

    def clause(self, view: str, alias: str = "") -> tuple[str, dict]:
        """SQL conditions (each starting with AND) and parameters for `view`.
        Only the dimensions the view carries; the caller checks unsupported()."""
        p = f"{alias}." if alias else ""
        dims = VIEW_DIMENSIONS.get(view, set())
        parts, params = [], {}
        if self.geo and "geo" in dims:
            parts.append(f"AND {p}region_id IN (SELECT region_id FROM analytics.dim_region WHERE :f_geo IN "
                         f"(region_id, city_id, state_id, region_l_id, zone_id))")
            params["f_geo"] = self.geo
        if self.agency and "agency" in dims:
            parts.append(f"AND {p}agency_id = :f_agency")
            params["f_agency"] = self.agency
        if self.product and "product" in dims:
            parts.append(f"AND {p}loan_type = :f_product")
            params["f_product"] = self.product
        if self.bucket and "bucket" in dims:
            parts.append(f"AND {p}dpd_bucket = :f_bucket")
            params["f_bucket"] = self.bucket
        if self.security and "security" in dims:
            parts.append(f"AND {p}loan_type IN (SELECT loan_type FROM analytics.dim_product "
                         f"WHERE security_class = :f_security)")
            params["f_security"] = self.security
        return " ".join(parts), params
