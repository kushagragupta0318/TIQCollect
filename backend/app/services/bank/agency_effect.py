"""The agency effect: how well an agency collects in a region, shrunk toward
its regional peers (plan §6.2 Agency Performance Index, §6.3 placement score).

One definition, two readers: D06's scorecard and leaderboard (ce) and D09's
placement engine. Read from analytics.agency_scorecard_monthly_scoped (B13b),
never recomputed from base tables.

    rate   = verified_collections / collectible_due          (per agency, region)
    peer   = the same ratio over every agency in the region
    shrunk = n/(n+k) * rate + k/(n+k) * peer                  (ml/empirical_bayes)
    index  = 100 * shrunk                                     (0-100)
    multiplier = shrunk / peer, bounded to [0.75, 1.25]       (the EB agent bound)

n is placement-months (active placements at each month end), so a thin agency
is pulled to its peers and says so ("based on n"). The grain is the view's,
(month, agency, region): there is no product or bucket dimension, so this is
region-peer shrinkage, not a product x bucket mix adjustment (coordinator,
2026-09-29). The placement score gets the loan's product and bucket signal
from P(pay) instead.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date

from sqlalchemy import text
from sqlalchemy.orm import Session

VERSION = "agency-effect-1.0.0"

#: Evidence (placement-months) at which an agency's own rate and its peers'
#: weigh the same. The agent estimator uses 10 cases; a placement-month is a
#: coarser unit, so the same k is deliberately conservative.
SMOOTHING_K = 10.0
MULTIPLIER_BOUNDS = (0.75, 1.25)
#: A peer rate below this is noise, not a denominator (as the EB agent floor).
PEER_FLOOR = 0.05

_VIEW = "analytics.agency_scorecard_monthly_scoped"


@dataclass(frozen=True)
class ScorecardCell:
    """The view's columns this estimator reads, for one (month, agency, region)."""
    month_start: date
    agency_id: str
    region_id: str
    verified_collections: float
    #: NULL in the view when a placement's opening arrears were never read or
    #: instalments don't cover the month (B13b's honesty rule): unknown, not 0.
    collectible_due: float | None
    active_placements_eom: int


@dataclass(frozen=True)
class AgencyEffect:
    agency_id: str
    region_id: str
    #: The month a row describes; for a pooled row, the latest month in the window.
    month_start: date
    months: int
    n: int                      # placement-months behind the estimate
    months_unread: int          # cells skipped because collectible_due was unknown
    raw_rate: float | None      # None when the agency had nothing due
    peer_rate: float | None
    shrunk_rate: float | None
    index: float | None         # 0-100
    multiplier: float           # 1.0 = no evidence either way
    version: str = VERSION

    def as_dict(self) -> dict:
        d = asdict(self)
        d["month_start"] = self.month_start.isoformat()
        return d


def _ratio(num: float, den: float) -> float | None:
    return None if den <= 0 else max(0.0, min(1.0, num / den))


def shrink(cells: list[ScorecardCell], *, pooled: bool, k: float = SMOOTHING_K) -> list[AgencyEffect]:
    """The estimator, pure. `pooled` sums the cells of each (agency, region)
    across months into one row; otherwise one row per (agency, region, month).
    Peers are every agency in the same region over the same cells."""
    if pooled:
        latest = max((c.month_start for c in cells), default=None)
        months = len({c.month_start for c in cells})
        key = lambda c: (latest, c.agency_id, c.region_id)            # noqa: E731
    else:
        months = 1
        key = lambda c: (c.month_start, c.agency_id, c.region_id)     # noqa: E731

    own: dict[tuple, list[float]] = {}
    peer: dict[tuple, list[float]] = {}
    for c in cells:
        o = own.setdefault(key(c), [0.0, 0.0, 0, 0])
        if c.collectible_due is None:
            # Unknown due: neither its collections nor its placements count,
            # or the rate would read high for exactly the months least known.
            o[3] += 1
            continue
        o[0] += float(c.verified_collections or 0)
        o[1] += float(c.collectible_due or 0)
        o[2] += int(c.active_placements_eom or 0)
        pk = (key(c)[0], c.region_id)
        p = peer.setdefault(pk, [0.0, 0.0])
        p[0] += float(c.verified_collections or 0)
        p[1] += float(c.collectible_due or 0)

    out = []
    lo, hi = MULTIPLIER_BOUNDS
    for (month, agency_id, region_id), (got, due, n, unread) in sorted(own.items(), key=lambda kv: (kv[0][0], kv[0][2], kv[0][1])):
        raw = _ratio(got, due)
        pr = peer.get((month, region_id), [0.0, 0.0])
        peer_rate = _ratio(pr[0], pr[1])
        if raw is None or peer_rate is None:
            shrunk, index, mult = None, None, 1.0
        else:
            w = n / (n + k) if (n + k) > 0 else 0.0
            shrunk = w * raw + (1 - w) * peer_rate
            index = round(100.0 * shrunk, 1)
            mult = max(lo, min(hi, shrunk / max(peer_rate, PEER_FLOOR)))
        out.append(AgencyEffect(agency_id=agency_id, region_id=region_id, month_start=month, months=months, n=n,
                                months_unread=unread,
                                raw_rate=raw, peer_rate=peer_rate, shrunk_rate=shrunk, index=index,
                                multiplier=round(mult, 4)))
    return out


def fetch_cells(adb: Session, *, bank_id: str, first_month: date | None, last_month: date | None) -> list[ScorecardCell]:
    """The scoped view's cells for `bank_id` in [first_month, last_month].
    `adb` is the tenant-bound analytics session (core.dependencies.AnalyticsDb);
    the view filters on that tenant, and bank_id is filtered again here."""
    sql = [f"SELECT month_start, agency_id, region_id, verified_collections, collectible_due, "
           f"active_placements_eom FROM {_VIEW} WHERE bank_id = :bank"]
    params: dict = {"bank": bank_id}
    if first_month is not None:
        sql.append("AND month_start >= :first")
        params["first"] = first_month
    if last_month is not None:
        sql.append("AND month_start <= :last")
        params["last"] = last_month
    rows = adb.execute(text(" ".join(sql)), params).all()
    return [ScorecardCell(r.month_start, str(r.agency_id), str(r.region_id), float(r.verified_collections or 0),
                          None if r.collectible_due is None else float(r.collectible_due),
                          int(r.active_placements_eom or 0)) for r in rows]


def _month_back(m: date, n: int) -> date:
    y, mo = divmod(m.year * 12 + (m.month - 1) - n, 12)
    return date(y, mo + 1, 1)


def latest_month(adb: Session, *, bank_id: str) -> date | None:
    row = adb.execute(text(f"SELECT max(month_start) AS m FROM {_VIEW} WHERE bank_id = :bank"),
                      {"bank": bank_id}).first()
    return row.m if row is not None else None


def agency_effect(adb: Session, *, bank_id: str, agency_id: str | None = None, region_id: str | None = None,
                  month_start: date | None = None, months: int = 1, pooled: bool = False) -> list[dict]:
    """ce's D06 contract (2026-09-29): rows keyed (agency_id, region_id,
    month_start) with index 0-100 and the n behind it. `month_start` defaults
    to the latest month in the view; `months` looks back from it. `pooled`
    folds the window into one row per (agency, region), which is what the
    placement engine reads. Peers are always computed over the whole region,
    so filtering by agency never changes an agency's score."""
    months = max(1, int(months))
    last = month_start or latest_month(adb, bank_id=bank_id)
    if last is None:
        return []
    first = _month_back(last.replace(day=1), months - 1)
    effects = shrink(fetch_cells(adb, bank_id=bank_id, first_month=first, last_month=last), pooled=pooled)
    return [e.as_dict() for e in effects
            if (agency_id is None or e.agency_id == agency_id) and (region_id is None or e.region_id == region_id)]
