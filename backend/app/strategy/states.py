# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-09-24 — New. The 8-state portfolio space of plan §7.1, as ONE definition.
#
#   The Command Center simulator this replaces carried its state space as
#   positional integers restated in three places: the docstring of
#   `generate_transition_matrices` (models/stress_simulator_data.py:6-14), the
#   `DEFAULT_LGD` comment (models/stress_simulator.py:57) and the router's
#   `_BUCKET_TO_SIM_STATE` (routers/simulate.py:18-20), which mapped DPD
#   buckets `90-180` and `180+` onto Sub-standard and Doubtful. That is a DPD
#   rule, and it is not the regulatory one: RBI classes an NPA as DOUBTFUL
#   once it has stayed SUB-STANDARD for 12 months, which is a question about
#   how long the account has been an NPA (`npa_since`), not about its DPD.
#   An account 200 days past due that became an NPA 110 days ago is
#   sub-standard; CC called it doubtful.
#
#   `portfolio_state` below has the signature DATA-MODEL-V2 §dim_portfolio_state
#   gives `analytics.portfolio_state(dpd_bucket, loan_status, npa_since,
#   as_of)` — "generated from one Python definition". This is meant to be that
#   definition: the SQL function should be generated from it, not written
#   beside it (CLAUDE.md, "one definition, one place").
#
#   The rule, the state space and the 12-month constant are IMPORTED from
#   `app.models.loan`, the one definition. Every table below is DERIVED from it
#   at import, so a renamed or added enum member changes them with it.
#
# 2026-09-24 (later) — Four corrections from the coordinator's audit of daadc17.
#
#   1. WRITTEN_OFF IS DERECOGNISED, not stage 3. IFRS9_STAGE put it in stage 3
#      under a comment saying "as the brief specifies (task E02)". That comment
#      was false: the mapping is CC's STAGE_STATES (models/stress_simulator.py
#      :64, `3: [4, 5, 6]`), copied without checking it. A write-off removes
#      the asset from the books (IFRS 9 5.4.4), so keeping its balance in
#      stage-3 EAD made ECL double-count the WRITE_OFFS line and made a harsher
#      write-off policy RAISE provisions. It is stage 0 now, beside RESOLVED;
#      WRITE_OFFS is the loss line. It stays in DEFAULT_STATES — reaching it is
#      still a default for first-passage PD.
#   2. SUB -> DOUBTFUL IS THE AGE RULE, NOT A HAZARD. The engine now moves an
#      account at exactly 12 months of NPA age and folds any matrix mass
#      between the two NPA states into staying put (monte_carlo._run_chunk).
#      REACHABLE therefore no longer offers DOUBTFUL -> SUB, and no longer
#      offers NPA -> SMA: under RBI's 12 Nov 2021 IRAC clarification an NPA is
#      upgraded only when the ENTIRE arrears are paid, i.e. to CURRENT.
#   3. LoanStatus.NPA WINS over the bucket. A part-paid NPA whose DPD fell to
#      45 mapped to SMA_1; the bank still carries it as an NPA until it is
#      regularised, so it is NPA_SUB / NPA_DOUBTFUL by npa_since.
#   4. DPD_RANGE restated 30/60/90 — an eighth copy of the DPD -> bucket rule.
#      It is now SWEPT off models/loan.dpd_bucket_for, so the boundaries live
#      only there. The old reason for the copy ("importing app.models needs the
#      DB") was wrong on the part that mattered: it builds a lazy Engine and
#      opens no connection. It does need the app's settings env, which every
#      caller of this package (the API, a Celery task, pytest) already has.
# ───────────────────────────────────────────────────────────────────────────
"""The 8-state portfolio space used by the Monte Carlo engine and the backtest.

States, in index order (the order is load-bearing — arrays are indexed by it):

    0 CURRENT        0 DPD
    1 SMA_0          1-30 DPD
    2 SMA_1          31-60 DPD
    3 SMA_2          61-90 DPD
    4 NPA_SUB        90+ DPD, an NPA for less than 12 months
    5 NPA_DOUBTFUL   an NPA for 12 months or more
    6 WRITTEN_OFF    absorbing
    7 RESOLVED       absorbing — closed, settled, or repaid in full
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Iterable, Sequence

import numpy as np

from app.models.loan import (
    NPA_DOUBTFUL_AFTER_MONTHS, PORTFOLIO_STATES, DPDBucket, LoanStatus, dpd_bucket_for,
    portfolio_state as _loan_portfolio_state,
)

STATES: tuple[str, ...] = PORTFOLIO_STATES
N_STATES = len(STATES)
CURRENT, SMA_0, SMA_1, SMA_2, NPA_SUB, NPA_DOUBTFUL, WRITTEN_OFF, RESOLVED = range(N_STATES)
STATE_INDEX: dict[str, int] = {code: i for i, code in enumerate(STATES)}

LIVE = (CURRENT, SMA_0, SMA_1, SMA_2, NPA_SUB, NPA_DOUBTFUL)  # on the books
DELINQUENT = (SMA_0, SMA_1, SMA_2, NPA_SUB, NPA_DOUBTFUL)      # a collections target
NPA = (NPA_SUB, NPA_DOUBTFUL)
ABSORBING = (WRITTEN_OFF, RESOLVED)
# IFRS-9's rebuttable presumption: default = 90 DPD. Written-off is default too.
DEFAULT_STATES = (NPA_SUB, NPA_DOUBTFUL, WRITTEN_OFF)

# IFRS-9 stage per state. RESOLVED and WRITTEN_OFF have left the book
# (derecognised, IFRS 9 5.4.4): stage 0, no EAD, no ECL. WRITE_OFFS is their
# loss line. (WRITTEN_OFF read stage 3 until 2026-09-24, see CHANGELOG 1.)
IFRS9_STAGE: tuple[int, ...] = (1, 1, 2, 2, 3, 3, 0, 0)

# NPA -> DOUBTFUL after this many months as an NPA is REGULATORY (RBI IRAC
# norms) and lives in models/loan. Applied by the engine as a rule at exactly
# this age, never as a monthly probability.


def _direction_matrix() -> np.ndarray:
    """+1 where a move is a deterioration, -1 an improvement, 0 staying put.

    Between two live states it is the sign of the step along the DPD ladder.
    Into WRITTEN_OFF is always +1; into RESOLVED always -1. Absorbing rows are
    all 0, so no shift can ever move an account out of them.
    """
    d = np.zeros((N_STATES, N_STATES), dtype=np.int8)
    for i in LIVE:
        for j in LIVE:
            d[i, j] = np.sign(j - i)
        d[i, WRITTEN_OFF] = 1
        d[i, RESOLVED] = -1
    return d


DIRECTION = _direction_matrix()
DIRECTION.setflags(write=False)


def _reachable_matrix() -> np.ndarray:
    """Cells a month-end-to-month-end transition can plausibly take.

    Enforced on the posterior, not merely suggested: since mc-1.2.0
    `SegmentMatrices.dirichlet_alpha` masks observed counts by this matrix as well
    as the prior's floor, so no draw can put mass in a cell forbidden here. What
    the mask drops is reported (`impossible_cells`), never silently discarded.
    DPD rises by at most ~31 days a month, so forward
    moves are one rung; a performing-side improvement can jump any distance
    (a borrower can clear all arrears at once); RESOLVED is reachable from
    every live state; WRITTEN_OFF only from NPA.

    An NPA leaves only to CURRENT (upgraded when the ENTIRE arrears are paid,
    RBI IRAC clarification of 12 Nov 2021), WRITTEN_OFF or RESOLVED — never to
    an SMA state, and never DOUBTFUL -> SUB. SUB -> DOUBTFUL is the 12-month
    age rule, which the engine applies itself.
    """
    r = np.zeros((N_STATES, N_STATES), dtype=bool)
    for i in LIVE:
        r[i, i] = True
        r[i, RESOLVED] = True
        if i + 1 in LIVE:
            r[i, i + 1] = True
        for j in LIVE:
            if j < i and not (i in NPA and j != CURRENT):
                r[i, j] = True
    for i in NPA:
        r[i, WRITTEN_OFF] = True
    for i in ABSORBING:
        r[i, i] = True
    return r


REACHABLE = _reachable_matrix()
REACHABLE.setflags(write=False)

# ── Read off the one definition, never restated ──────────────────────────────
# Every table here is what models/loan.portfolio_state answers when probed, so
# an added or renamed enum member moves them instead of going unnoticed.
NPA_STATUS = LoanStatus.NPA.value
# Any date: no probe below passes an npa_since, and as_of is read only with one.
_PROBE_DAY = date(2000, 1, 1)


def _bucket_to_state() -> dict[str, int]:
    return {b.value: STATE_INDEX[_loan_portfolio_state(b.value, LoanStatus.ACTIVE.value, None, _PROBE_DAY)]
            for b in DPDBucket}


def _status_maps() -> tuple[dict[str, int], frozenset[str]]:
    """Terminal statuses win over the bucket; the rest leave it to the bucket
    (an NPA status reads NPA_SUB here, and wins over the bucket downstream)."""
    terminal: dict[str, int] = {}
    non_terminal: set[str] = set()
    for st in LoanStatus:
        state = _loan_portfolio_state(DPDBucket.CURRENT.value, st.value, None, _PROBE_DAY)
        if state == STATES[CURRENT] or STATE_INDEX[state] in NPA:
            non_terminal.add(st.value)
        else:
            terminal[st.value] = STATE_INDEX[state]
    return terminal, frozenset(non_terminal)


BUCKET_TO_STATE: dict[str, int] = _bucket_to_state()
TERMINAL_STATUS_TO_STATE, NON_TERMINAL_STATUSES = _status_maps()


_SWEEP_CEILING = 10_000     # a DPD no bucket rule would run past; a bad rule fails loudly
_DAYS_PER_YEAR = 365.0      # the NPA_SUB span in days, not a DPD boundary


def _dpd_range() -> dict[int, tuple[float, float]]:
    """Each delinquent state's DPD span, SWEPT off dpd_bucket_for.

    Used by the legal-threshold lever to ask how much of a state's span lies
    beyond a threshold. The 30/60/90 boundaries stay in models/loan; NPA_SUB
    ends where the 12-month age rule promotes it, which is a question about NPA
    age rather than DPD, and NPA_DOUBTFUL is open-ended.
    """
    lo: dict[int, float] = {}
    hi: dict[int, float] = {}
    d = 1
    while BUCKET_TO_STATE[dpd_bucket_for(d).value] != NPA_SUB:
        state = BUCKET_TO_STATE[dpd_bucket_for(d).value]
        lo.setdefault(state, float(d))
        hi[state] = float(d)
        d += 1
        if d > _SWEEP_CEILING:
            raise AssertionError(f"dpd_bucket_for reaches no NPA below {_SWEEP_CEILING} DPD")
    npa_lo = float(d)
    npa_span = NPA_DOUBTFUL_AFTER_MONTHS / 12.0 * _DAYS_PER_YEAR
    spans = {state: (lo[state], hi[state]) for state in lo}
    spans[NPA_SUB] = (npa_lo, npa_lo + npa_span)
    spans[NPA_DOUBTFUL] = (npa_lo + npa_span, float("inf"))
    return spans


DPD_RANGE: dict[int, tuple[float, float]] = _dpd_range()


def _value(x) -> str | None:
    if x is None:
        return None
    return getattr(x, "value", x)


def months_between(start: date, end: date) -> int:
    """Whole calendar months from `start` to `end` (0 if end < start).

    A month is complete on the same day-of-month, clamped to the month's last
    day: 31 Jan -> 29 Feb 2028 is one month.
    """
    if end < start:
        return 0
    months = (end.year - start.year) * 12 + (end.month - start.month)
    if end.day < start.day:
        # Not yet reached start.day this month — unless end is the last day of
        # a month too short to contain start.day.
        is_month_end = (end + timedelta(days=1)).day == 1
        if not is_month_end:
            months -= 1
    return max(months, 0)


def portfolio_state(dpd_bucket, loan_status=None, npa_since: date | None = None,
                    as_of: date | None = None) -> str:
    """models/loan.portfolio_state — the one definition — made strict.

    It decides: a terminal `loan_status` wins (WRITTEN_OFF; CLOSED / SETTLED ->
    RESOLVED); an NPA status wins over the bucket, since a part-paid NPA stays
    an NPA until regularised (RBI); otherwise the bucket decides, and NPA splits
    at `NPA_DOUBTFUL_AFTER_MONTHS` of `npa_since` (with none, NPA_SUB — the
    lower bound DATA-MODEL-V2 §9.5 records).

    Strict because the engine INDEXES arrays by state: an unknown bucket or
    status raises here, where loan.py returns its "UNKNOWN" for an ORM reader to
    surface. "UNKNOWN" is not in STATES and has no index.
    """
    bucket, status = _value(dpd_bucket), _value(loan_status)
    if bucket not in BUCKET_TO_STATE:
        raise ValueError(f"unknown dpd_bucket {bucket!r}")
    if status is not None and status not in TERMINAL_STATUS_TO_STATE and status not in NON_TERMINAL_STATUSES:
        raise ValueError(f"unknown loan_status {status!r}")
    if npa_since is not None and as_of is None:
        raise ValueError("npa_since given without as_of")
    state = _loan_portfolio_state(bucket, status if status is not None else LoanStatus.ACTIVE.value,
                                  npa_since, as_of if as_of is not None else _PROBE_DAY)
    if state not in STATE_INDEX:
        raise ValueError(f"models/loan.portfolio_state returned {state!r}, which is not one of STATES")
    return state


def states_from_buckets(dpd_buckets: Iterable, npa_age_months: Sequence | np.ndarray | None = None,
                        loan_status: Iterable | None = None) -> np.ndarray:
    """Vectorised `portfolio_state` returning state INDICES (int8).

    `npa_age_months` is whole months since `npa_since` (NaN / negative = unknown,
    read as sub-standard). Built on `portfolio_state`'s own tables, so the rule
    exists once.
    """
    buckets = np.asarray([_value(b) for b in dpd_buckets], dtype=object)
    n = buckets.shape[0]
    out = np.empty(n, dtype=np.int8)
    for code, s in BUCKET_TO_STATE.items():
        out[buckets == code] = s
    unknown = ~np.isin(buckets, list(BUCKET_TO_STATE))
    if unknown.any():
        raise ValueError(f"unknown dpd_bucket values: {sorted(set(buckets[unknown]))!r}")
    status = None
    if loan_status is not None:
        status = np.asarray([_value(s) for s in loan_status], dtype=object)
        if status.shape != (n,):
            raise ValueError("loan_status must have one entry per account")
        known = set(TERMINAL_STATUS_TO_STATE) | set(NON_TERMINAL_STATUSES) | {None}
        bad = {s for s in set(status.tolist()) if s not in known}
        if bad:
            raise ValueError(f"unknown loan_status values: {sorted(bad)!r}")
        out[status == NPA_STATUS] = NPA_SUB          # NPA status wins over the bucket
    if npa_age_months is not None:
        age = np.asarray(npa_age_months, dtype=float)
        if age.shape != (n,):
            raise ValueError("npa_age_months must have one entry per account")
        doubtful = (out == NPA_SUB) & (np.nan_to_num(age, nan=-1.0) >= NPA_DOUBTFUL_AFTER_MONTHS)
        out[doubtful] = NPA_DOUBTFUL
    if status is not None:
        for code, s in TERMINAL_STATUS_TO_STATE.items():
            out[status == code] = s                  # terminal statuses win over everything
    return out
