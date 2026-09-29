# ─── CHANGELOG (standalone plan) ─────────────────────────────────────────────
# 2026-09-29 (P3 D06, ce) — NEW. The agency scorecard: every ratio metric in
#   plan §6.2's table except the Agency Performance Index, which is
#   services/bank/agency_effect.agency_effect (2b's, imported here rather than
#   recomputed — one definition, two readers, same as D09's placement score).
#
#   Reads analytics.agency_scorecard_monthly_scoped (B13b) on the tenant-bound
#   AnalyticsDb session, exactly like agency_effect does — never recomputed
#   from base tables, and the same NULL-is-unknown handling: a metric whose
#   denominator is 0 or unknown is None, never a fabricated 0.
#
#   NOT BUILT HERE: Compliance Score as "the header KPI" — plan §6.2 says to
#   reuse the header KPI's own formula, and that KPI (C01-C03, the Command
#   Center Overview's twelve header KPIs) does not exist on this branch yet.
#   compliance_score below is a documented placeholder computed from the same
#   raw breach/fraud/consent columns, not a copy of a definition that isn't
#   written anywhere yet — when C01-C03 lands, this should import THAT
#   function instead of keeping its own. Recorded so it doesn't quietly
#   become a second definition once the real one exists.
#
#   RECOVERY VS EXPECTED'S COHORT CAVEAT (43's own note, carried forward):
#   expected_recovery_inr is summed only over the month's NEW placements
#   (placed_new's cohort); verified_collections is the whole active book's
#   collections that month, which includes older placements still being
#   worked. Dividing one by the other compares the full book's cash against
#   only the newest slice's baseline — an approximation, not a matched
#   cohort, and the ratio can exceed 1.0 for exactly that reason. Not
#   clamped to [0, 1] here, unlike every other ratio in this module, because
#   clamping would hide the mismatch rather than let it be read honestly.
# ────────────────────────────────────────────────────────────────────────────
"""Agency scorecard metrics (plan §6.2) — everything except the Performance
Index, which is agency_effect's."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.bank.agency_effect import agency_effect, latest_month

VERSION = "agency-scorecard-1.0.0"

_VIEW = "analytics.agency_scorecard_monthly_scoped"


def _month_back(m: date, n: int) -> date:
    """n calendar months before m, day-of-month reset to 1 — same arithmetic
    as agency_effect's own (private) helper; not imported from there since
    that underscore-prefixed name is 2b's module-internal, and 3 lines of
    index math is not the kind of duplication the one-definition rule is
    protecting against (that rule is about business formulas — eb_shrink's
    the example that mattered)."""
    y, mo = divmod(m.year * 12 + (m.month - 1) - n, 12)
    return date(y, mo + 1, 1)

_COLUMNS = (
    "month_start", "agency_id", "region_id", "placed_new", "resolved_placements",
    "collectible_due", "verified_collections", "bank_direct_collections", "expected_recovery_inr",
    "ptps_matured", "ptps_honoured", "visits", "met_visits", "first_visits_within_sla",
    "placements_due_first_visit", "agents_active", "agents_contracted", "agents_exited", "agent_leave_days",
    "commission_accrued", "field_cost", "breaches_out_of_hours", "breaches_geofence", "fraud_confirmed",
    "consent_missing",
)


@dataclass(frozen=True)
class ScorecardRow:
    """One (month, agency, region) cell's raw columns from the view — the
    same grain as agency_effect.ScorecardCell, a wider slice of it."""
    month_start: date
    agency_id: str
    region_id: str
    placed_new: int
    resolved_placements: int
    collectible_due: float | None
    verified_collections: float
    bank_direct_collections: float
    expected_recovery_inr: float | None
    ptps_matured: int
    ptps_honoured: int
    visits: int
    met_visits: int
    first_visits_within_sla: int
    placements_due_first_visit: int
    agents_active: int
    agents_contracted: int
    agents_exited: int
    agent_leave_days: float
    commission_accrued: float
    field_cost: float | None    # NULL until a FIELD_VISIT cost rate exists (43) — never read as 0
    breaches_out_of_hours: int
    breaches_geofence: int
    fraud_confirmed: int
    consent_missing: int


def _ratio(num: float, den: float, *, clamp: bool = True) -> float | None:
    if den <= 0:
        return None
    r = num / den
    return max(0.0, min(1.0, r)) if clamp else r


def _sum(rows: list[ScorecardRow], field: str) -> float:
    return sum(getattr(r, field) or 0 for r in rows)


def _sum_or_none(rows: list[ScorecardRow], field: str) -> float | None:
    """None when EVERY row is unknown for this field (43's NULL-is-unknown
    rule) — distinct from a real 0, which sums normally."""
    values = [getattr(r, field) for r in rows]
    if all(v is None for v in values):
        return None
    return sum(v or 0 for v in values)


def compute_metrics(rows: list[ScorecardRow]) -> dict:
    """The pure arithmetic, over however many (month, agency, region) rows
    the caller already filtered to one agency/region/window — every metric
    a rate over the SUM of its own numerator and denominator across those
    rows, never an average of per-row rates (which would let one high-due,
    low-collection month conceal a mismatched month elsewhere)."""
    if not rows:
        return {"n_rows": 0}

    verified = _sum(rows, "verified_collections")
    bank_direct = _sum(rows, "bank_direct_collections")
    total_collected = verified + bank_direct
    expected = _sum_or_none(rows, "expected_recovery_inr")
    field_cost = _sum_or_none(rows, "field_cost")
    agents_contracted = _sum(rows, "agents_contracted")
    days = 30 * len({r.month_start for r in rows})   # a fixed 30-day month, matching this codebase's SLA-day convention

    # Collection Efficiency's numerator and denominator must come from the
    # SAME rows — a row whose collectible_due is unknown is excluded from
    # BOTH, exactly as agency_effect.shrink() already does ("neither its
    # collections nor its placements count"). Summing verified_collections
    # over every row regardless, while collectible_due only sums the known
    # ones, would credit collections a row's own unknown-due excluded it
    # from — caught by test_a_row_with_unknown_due_does_not_poison_a_window.
    known_due_rows = [r for r in rows if r.collectible_due is not None]
    collection_efficiency = (None if not known_due_rows else
                             _ratio(_sum(known_due_rows, "verified_collections"),
                                    _sum(known_due_rows, "collectible_due")))
    resolution_rate = _ratio(_sum(rows, "resolved_placements"), _sum(rows, "placed_new"))
    recovery_vs_expected = None if not expected else round(total_collected / expected, 4)   # see module docstring: not clamped
    ptp_conversion = _ratio(_sum(rows, "ptps_honoured"), _sum(rows, "ptps_matured"))
    contact_rate = _ratio(_sum(rows, "met_visits"), _sum(rows, "visits"))
    sla_adherence = _ratio(_sum(rows, "first_visits_within_sla"), _sum(rows, "placements_due_first_visit"))
    agents_active = _sum(rows, "agents_active")
    productivity = (None if agents_active <= 0 or days <= 0
                    else round(_sum(rows, "visits") / agents_active / days, 4))
    cost_per_100 = (None if field_cost is None or total_collected <= 0
                    else round((_sum(rows, "commission_accrued") + field_cost) / total_collected * 100, 2))
    evidence_integrity = None if _sum(rows, "visits") <= 0 else round(_sum(rows, "fraud_confirmed") / _sum(rows, "visits") * 100, 2)
    workforce_ratio = _ratio(agents_active, agents_contracted, clamp=False)
    attrition = _ratio(_sum(rows, "agents_exited"), agents_contracted, clamp=False)
    leave_rate = None if agents_contracted <= 0 or days <= 0 else round(_sum(rows, "agent_leave_days") / (agents_contracted * days), 4)

    # Placeholder pending the real header KPI (C01-C03) — see module docstring.
    breach_rate = _ratio(_sum(rows, "breaches_out_of_hours") + _sum(rows, "breaches_geofence"),
                         _sum(rows, "visits"), clamp=False)
    consent_rate = _ratio(_sum(rows, "consent_missing"), _sum(rows, "met_visits"), clamp=False)
    compliance_score = (None if breach_rate is None and consent_rate is None
                        else round(100 * (1 - min(1.0, (breach_rate or 0) + (consent_rate or 0))), 1))

    return {
        "n_rows": len(rows),
        "collection_efficiency": collection_efficiency,
        "resolution_rate": resolution_rate,
        "recovery_vs_expected": recovery_vs_expected,
        "ptp_conversion": ptp_conversion,
        "contact_rate": contact_rate,
        "sla_adherence": sla_adherence,
        "productivity_per_agent_per_day": productivity,
        "cost_per_100_inr": cost_per_100,
        "evidence_integrity_per_100_visits": evidence_integrity,
        "workforce_active_ratio": workforce_ratio,
        "workforce_attrition_ratio": attrition,
        "workforce_leave_rate": leave_rate,
        "compliance_score": compliance_score,
    }


def fetch_rows(adb: Session, *, bank_id: str, first_month: date | None, last_month: date | None) -> list[ScorecardRow]:
    """The scoped view's cells for `bank_id` in [first_month, last_month] —
    same call shape as agency_effect.fetch_cells, wider column set."""
    cols = ", ".join(_COLUMNS)
    sql = [f"SELECT {cols} FROM {_VIEW} WHERE bank_id = :bank"]
    params: dict = {"bank": bank_id}
    if first_month is not None:
        sql.append("AND month_start >= :first")
        params["first"] = first_month
    if last_month is not None:
        sql.append("AND month_start <= :last")
        params["last"] = last_month
    rows = adb.execute(text(" ".join(sql)), params).all()
    return [
        ScorecardRow(
            month_start=r.month_start, agency_id=str(r.agency_id), region_id=str(r.region_id),
            placed_new=int(r.placed_new or 0), resolved_placements=int(r.resolved_placements or 0),
            collectible_due=None if r.collectible_due is None else float(r.collectible_due),
            verified_collections=float(r.verified_collections or 0),
            bank_direct_collections=float(r.bank_direct_collections or 0),
            expected_recovery_inr=None if r.expected_recovery_inr is None else float(r.expected_recovery_inr),
            ptps_matured=int(r.ptps_matured or 0), ptps_honoured=int(r.ptps_honoured or 0),
            visits=int(r.visits or 0), met_visits=int(r.met_visits or 0),
            first_visits_within_sla=int(r.first_visits_within_sla or 0),
            placements_due_first_visit=int(r.placements_due_first_visit or 0),
            agents_active=int(r.agents_active or 0), agents_contracted=int(r.agents_contracted or 0),
            agents_exited=int(r.agents_exited or 0), agent_leave_days=float(r.agent_leave_days or 0),
            commission_accrued=float(r.commission_accrued or 0),
            field_cost=None if r.field_cost is None else float(r.field_cost),
            breaches_out_of_hours=int(r.breaches_out_of_hours or 0), breaches_geofence=int(r.breaches_geofence or 0),
            fraud_confirmed=int(r.fraud_confirmed or 0), consent_missing=int(r.consent_missing or 0),
        )
        for r in rows
    ]


def agency_scorecard(adb: Session, *, bank_id: str, agency_id: str, region_id: str | None = None,
                     month_start: date | None = None, months: int = 1) -> dict:
    """One agency's scorecard over a window (default: the latest month
    alone). Folds in the Performance Index from agency_effect (pooled over
    the same window) rather than computing a second one."""
    months = max(1, int(months))
    last = month_start or latest_month(adb, bank_id=bank_id)
    if last is None:
        return {"agency_id": agency_id, "region_id": region_id, "n_rows": 0}
    first = _month_back(last.replace(day=1), months - 1)
    rows = [r for r in fetch_rows(adb, bank_id=bank_id, first_month=first, last_month=last)
           if r.agency_id == agency_id and (region_id is None or r.region_id == region_id)]
    metrics = compute_metrics(rows)

    effects = agency_effect(adb, bank_id=bank_id, agency_id=agency_id, region_id=region_id,
                            month_start=last, months=months, pooled=True)
    effect = effects[0] if effects else None

    return {
        "agency_id": agency_id, "region_id": region_id, "month_start": last.isoformat(),
        "months": months, "version": VERSION,
        "performance_index": effect,   # None when agency_effect found nothing for the window
        **metrics,
    }


def leaderboard(adb: Session, *, bank_id: str, region_id: str | None = None,
                month_start: date | None = None) -> list[dict]:
    """Every agency in `region_id` (or the whole bank), ranked by Performance
    Index descending for the given month (default latest). A pure re-shape
    of agency_effect's own rows — this function adds no arithmetic of its
    own, only the sort and the None-last placement (an agency the estimator
    could not score is not silently mid-table)."""
    effects = agency_effect(adb, bank_id=bank_id, region_id=region_id, month_start=month_start,
                            months=1, pooled=False)
    return sorted(effects, key=lambda e: (e["index"] is None, -(e["index"] or 0)))
