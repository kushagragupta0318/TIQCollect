"""E04 input: a HistoricalPanel built from loan_dpd_history, for the backtest.

The backtest (backtest.py) measures whether the engine's p10-p90 bands actually
cover what happened, month by month. That is how UNCALIBRATED becomes a measured
statement rather than an apology (ADR 0014) — so the panel it scores must be the
book's real month-end history, with each loan's state from the ONE definition.

State is computed by `analytics.portfolio_state(dpd_bucket, loan_status, npa_since,
as_of)` — the SQL function generated from models/loan.portfolio_state — so the
panel's states and the engine's states cannot drift. npa_since comes from the loan
(it splits NPA_SUB from NPA_DOUBTFUL), which is why this joins lending.loans.

Bank-scoped through the tenant-bound session AND a named bank_id, like
transitions.py. Month-end rows only (is_month_end): the daily current-month rows
are not month-to-month transitions.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.errors import AppException, ErrorCode
from app.strategy.backtest import HistoricalPanel
from app.strategy.states import STATE_INDEX
from app.strategy.transitions import MIN_BANK_MONTHS, segment_key

# A demo book is ~1,500 loans × a couple of years; these bound the query so a
# misconfigured call cannot pull an unbounded panel into memory synchronously.
MAX_LOANS = 200_000
MAX_MONTHS = 60


@dataclass(frozen=True)
class PanelReading:
    panel: HistoricalPanel
    month_ends: tuple[date, ...]
    loans: int

    @property
    def months(self) -> int:
        return len(self.month_ends)


_SQL = text("""
    SELECT h.loan_id,
           h.as_of_date,
           analytics.portfolio_state(h.dpd_bucket::text, h.loan_status::text, l.npa_since, h.as_of_date) AS state,
           h.total_outstanding,
           h.loan_type,
           h.region_id
      FROM lending.loan_dpd_history h
      JOIN lending.loans l ON l.id = h.loan_id
     WHERE h.bank_id = :bank
       AND h.is_month_end
       AND h.as_of_date <= :as_of
     ORDER BY h.as_of_date
""")


def read_panel(adb: Session, bank_id: str, *, as_of: date | None = None,
               months: int = MAX_MONTHS, synthetic: bool = False) -> PanelReading:
    """Build the bank's month-end state panel for the backtest.

    `adb` is the tenant-bound analytics session (the query names bank_id too, so
    an unbound session yields nothing rather than another tenant's rows).

    Segment is (loan_type, region_id), the grain transitions.py uses, so a
    backtest fits and scores the same segments the simulator runs.

    Raises AppException(INSUFFICIENT_HISTORY) under MIN_BANK_MONTHS month-ends:
    a backtest needs a fit window and a scoring window, and neither exists on a
    book too short to have migrated. The caller records it as ABSTAINED, the same
    outcome transitions.py gives for the same reason.
    """
    as_of = as_of or date.today()
    rows = adb.execute(_SQL, {"bank": bank_id, "as_of": as_of}).mappings().all()
    if not rows:
        raise AppException(422, ErrorCode.INSUFFICIENT_HISTORY,
                           "This bank has no month-end loan history; a backtest has nothing to score.")

    month_list = sorted({r["as_of_date"] for r in rows})
    if len(month_list) > months:
        month_list = month_list[-months:]
    kept = set(month_list)
    if len(month_list) < MIN_BANK_MONTHS:
        raise AppException(
            422, ErrorCode.INSUFFICIENT_HISTORY,
            f"This bank has {len(month_list)} month-end(s) of history; a backtest needs at least "
            f"{MIN_BANK_MONTHS} to fit on one window and score on another.")

    month_ix = {m: i for i, m in enumerate(month_list)}
    loan_ids = sorted({r["loan_id"] for r in rows if r["as_of_date"] in kept})
    if len(loan_ids) > MAX_LOANS:
        raise AppException(422, ErrorCode.VALIDATION_ERROR,
                           f"{len(loan_ids)} loans exceeds the synchronous panel cap of {MAX_LOANS}.")
    loan_ix = {lid: i for i, lid in enumerate(loan_ids)}

    n, m = len(loan_ids), len(month_list)
    state = np.full((n, m), -1, dtype=np.int8)                   # -1 = not observed that month
    balance = np.zeros((n, m), dtype=np.float64)
    seg_of_loan: dict[int, str] = {}
    seg_identity: dict[str, tuple[str | None, str | None]] = {}
    unknown_states: set[str] = set()
    for r in rows:
        if r["as_of_date"] not in kept:
            continue
        a, mo = loan_ix[r["loan_id"]], month_ix[r["as_of_date"]]
        si = STATE_INDEX.get(r["state"])
        if si is None:
            # "UNKNOWN" from the SQL fn (a data defect) is left unobserved rather
            # than mapped to a real state; counted so it is not silent.
            unknown_states.add(r["state"])
            continue
        state[a, mo] = si
        balance[a, mo] = float(r["total_outstanding"] or 0.0)
        key = segment_key(r["loan_type"], r["region_id"])
        seg_of_loan[a] = key
        seg_identity.setdefault(key, (r["loan_type"], r["region_id"]))

    seg_keys = sorted(seg_identity)
    if not seg_keys:
        raise AppException(422, ErrorCode.INSUFFICIENT_HISTORY,
                           "This bank's month-end rows hold no state the engine models; "
                           "a backtest has nothing to score.")
    seg_pos = {k: i for i, k in enumerate(seg_keys)}
    # A loan with only UNKNOWN rows never got a segment; park it in the first
    # segment — its row is all -1, so it contributes to no transition.
    segment = np.array([seg_pos.get(seg_of_loan.get(a), 0) for a in range(n)], dtype=np.int32)

    panel = HistoricalPanel(
        state=state, balance=balance, segment=segment,
        segment_keys=tuple(seg_keys),
        segment_loan_types=tuple(seg_identity[k][0] for k in seg_keys),
        synthetic=synthetic,
    )
    return PanelReading(panel=panel, month_ends=tuple(month_list), loans=n)
