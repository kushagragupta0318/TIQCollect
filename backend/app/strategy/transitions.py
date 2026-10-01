"""E01: month-to-month transition COUNTS per segment, read from analytics.

Produces `SegmentMatrices` and nothing else. The Dirichlet posterior over these
counts already exists as `SegmentMatrices.dirichlet_alpha` (ADR 0013) — it is not
restated here, and `EngineConfig.prior_strength` is its pooled-prior weight.

Three things this layer decides, because they are questions about EVIDENCE rather
than about arithmetic:

  - A segment with too little history is POOLED onto the bank-wide row and says so.
  - A bank with too little history ABSTAINS: the run refuses with a typed reason
    rather than fabricating a matrix (ADR 0005's "abstain rather than impute").
  - Pairs the view could not read (stale or missing) are REPORTED, never imputed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import numpy as np
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.errors import AppException, ErrorCode
from app.strategy.monte_carlo import SegmentMatrices
from app.strategy.states import N_STATES, STATE_INDEX

VIEW = "analytics.bucket_transitions_monthly_scoped"
DEFAULT_MONTHS = 12
# Below either threshold a segment is not read on its own. Chosen, not fitted:
# six months is the shortest span in which a seasonal book shows more than one
# regime, and thirty accounts is where a single borrower stops moving a rate by
# more than a few points.
MIN_MONTHS = 6
MIN_FROM_ACCOUNTS = 30
# A bank under this many complete months has no transition history worth pooling.
MIN_BANK_MONTHS = MIN_MONTHS

OBSERVED, POOLED = "OBSERVED", "POOLED"
# The view records a month a loan was not read as NO_READING. It is not a state.
NO_READING = "NO_READING"


@dataclass(frozen=True)
class SegmentReading:
    """What the evidence for one segment was, and how it was treated."""
    key: str
    loan_type: str | None
    region_id: str | None
    status: str                  # OBSERVED | POOLED
    months: int
    accounts: float              # transitions observed in this segment
    reason: str = ""


@dataclass(frozen=True)
class TransitionReading:
    """Counts for the engine, with everything needed to say what they rest on."""
    matrices: SegmentMatrices
    segments: tuple[SegmentReading, ...]
    month_ends: tuple[date, ...]
    excluded_stale_pairs: float = 0.0
    excluded_missing_pairs: float = 0.0
    max_staleness_days: int = 0
    rows_outside_the_state_space: tuple[str, ...] = field(default_factory=tuple)

    @property
    def months(self) -> int:
        return len(self.month_ends)

    @property
    def pooled_segments(self) -> tuple[str, ...]:
        return tuple(s.key for s in self.segments if s.status == POOLED)

    def as_dict(self) -> dict:
        """The reading's provenance, for a run record and for the screen."""
        return {
            "months": self.months,
            "month_ends": [d.isoformat() for d in self.month_ends],
            "segments": len(self.segments),
            "segments_pooled": list(self.pooled_segments),
            "excluded_stale_pairs": self.excluded_stale_pairs,
            "excluded_missing_pairs": self.excluded_missing_pairs,
            "max_staleness_days": self.max_staleness_days,
            "rows_outside_the_state_space": list(self.rows_outside_the_state_space),
            "min_months": MIN_MONTHS,
            "min_from_accounts": MIN_FROM_ACCOUNTS,
        }


def segment_key(loan_type: str | None, region_id: str | None) -> str:
    return f"{loan_type or 'ANY'}|{region_id or 'ANY'}"


_SQL = text(f"""
    SELECT month_end, loan_type, region_id, from_state, to_state,
           SUM(accounts) AS accounts,
           SUM(COALESCE(excluded_stale_pairs, 0)) AS stale,
           SUM(COALESCE(excluded_missing_pairs, 0)) AS missing,
           MAX(COALESCE(max_staleness_days, 0)) AS staleness
      FROM {VIEW}
     WHERE bank_id = :bank
       AND month_end <= :as_of
       AND to_state <> :no_reading
     GROUP BY month_end, loan_type, region_id, from_state, to_state
     ORDER BY month_end DESC
""")


def read_transitions(adb: Session, bank_id: str, *, as_of: date | None = None,
                     months: int = DEFAULT_MONTHS, synthetic: bool = False) -> TransitionReading:
    """Read the bank's transition counts, at (loan_type, region_id) grain.

    `adb` must be the tenant-bound analytics session: the `*_scoped` view filters
    on it and returns nothing unbound. `bank_id` is named in the query too, so a
    missing binding yields no rows rather than another bank's.

    Agency is aggregated out: a bank-level question about how its book migrates,
    not about who collected it.

    Raises AppException(INSUFFICIENT_HISTORY) when the bank has fewer than
    MIN_BANK_MONTHS complete months. That refusal is the honest answer, and the
    caller records it as an ABSTAINED run (v2_0018), not as a failure.
    """
    as_of = as_of or date.today()
    rows = adb.execute(_SQL, {"bank": bank_id, "as_of": as_of, "no_reading": NO_READING}).mappings().all()

    all_months = sorted({r["month_end"] for r in rows}, reverse=True)
    if len(all_months) < MIN_BANK_MONTHS:
        raise AppException(
            422, ErrorCode.INSUFFICIENT_HISTORY,
            f"This bank has {len(all_months)} complete month(s) of transition history; "
            f"{MIN_BANK_MONTHS} are needed to estimate how its book migrates. "
            "No matrix is produced: a simulation on less would be arithmetic on an assumption.")

    kept_months = set(all_months[:months])
    rows = [r for r in rows if r["month_end"] in kept_months]
    month_ends = tuple(sorted(kept_months))

    stale = float(sum(r["stale"] or 0 for r in rows))
    missing = float(sum(r["missing"] or 0 for r in rows))
    staleness = max((int(r["staleness"] or 0) for r in rows), default=0)

    # (key -> 8x8), plus what each segment is and how much it saw.
    counts: dict[str, np.ndarray] = {}
    identity: dict[str, tuple[str | None, str | None]] = {}
    seen_months: dict[str, set[date]] = {}
    unknown: set[str] = set()
    for r in rows:
        i, j = STATE_INDEX.get(r["from_state"]), STATE_INDEX.get(r["to_state"])
        if i is None or j is None:
            # A state the engine does not model. Reported, never guessed into a
            # neighbouring bucket: that would invent a transition.
            unknown.add(r["from_state"] if i is None else r["to_state"])
            continue
        key = segment_key(r["loan_type"], r["region_id"])
        counts.setdefault(key, np.zeros((N_STATES, N_STATES)))[i, j] += float(r["accounts"] or 0)
        identity.setdefault(key, (r["loan_type"], r["region_id"]))
        seen_months.setdefault(key, set()).add(r["month_end"])

    if not counts:
        raise AppException(
            422, ErrorCode.INSUFFICIENT_HISTORY,
            "This bank has month-end readings but no transition the engine models; "
            "no matrix is produced.")

    pooled_counts = np.sum(list(counts.values()), axis=0)
    keys = sorted(counts)
    readings, stack = [], []
    for key in keys:
        own, n_months = counts[key], len(seen_months[key])
        accounts = float(own.sum())
        thin_months = n_months < MIN_MONTHS
        thin_accounts = accounts < MIN_FROM_ACCOUNTS
        if thin_months or thin_accounts:
            why = []
            if thin_months:
                why.append(f"{n_months} month(s) < {MIN_MONTHS}")
            if thin_accounts:
                why.append(f"{accounts:.0f} transitions < {MIN_FROM_ACCOUNTS}")
            readings.append(SegmentReading(key, *identity[key], POOLED, n_months, accounts,
                                           "pooled onto the bank-wide row: " + " and ".join(why)))
            stack.append(pooled_counts)
        else:
            readings.append(SegmentReading(key, *identity[key], OBSERVED, n_months, accounts))
            stack.append(own)

    matrices = SegmentMatrices(
        keys=tuple(keys), counts=np.stack(stack),
        loan_types=tuple(identity[k][0] for k in keys),
        synthetic=synthetic,
        source=f"{VIEW} {month_ends[0].isoformat()}..{month_ends[-1].isoformat()}",
    )
    return TransitionReading(
        matrices=matrices, segments=tuple(readings), month_ends=month_ends,
        excluded_stale_pairs=stale, excluded_missing_pairs=missing,
        max_staleness_days=staleness, rows_outside_the_state_space=tuple(sorted(unknown)),
    )
