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
#   It deliberately does NOT import `app.models.loan`. The data layer is being
#   rewritten on another branch and this package must stay importable without
#   the ORM. The bucket and status spellings are matched by VALUE (both enums
#   are `str` enums, so `DPDBucket.NPA == "NPA"`), and
#   tests/test_monte_carlo.py asserts every member of both enums maps here, so
#   a renamed or added member fails a test instead of falling through quietly.
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
#   4. DPD_RANGE restates 30/60/90 — an eighth copy of the DPD -> bucket rule.
#      It cannot import models/loan.dpd_bucket_for: importing app.models runs
#      app.core.database, which builds the SQLAlchemy engine from Settings and
#      needs the DB env and driver, and this package must stay DB-free. So it
#      is pinned instead: test_dpd_range_agrees_with_dpd_bucket_for checks
#      agreement over 0..400 DPD.
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

STATES: tuple[str, ...] = (
    "CURRENT", "SMA_0", "SMA_1", "SMA_2",
    "NPA_SUB", "NPA_DOUBTFUL", "WRITTEN_OFF", "RESOLVED",
)
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

# NPA -> DOUBTFUL after this many months as an NPA (RBI Master Circular on
# IRAC norms: "remained in the sub-standard category for a period of 12
# months"). REGULATORY, not an assumption — and applied as a rule, at exactly
# this age, by the engine; never as a monthly probability.
DOUBTFUL_AFTER_MONTHS = 12

# DPD range per delinquent state, used by the legal-threshold lever to ask how
# much of a state's DPD range lies beyond the threshold. NPA_SUB spans 91 DPD
# to 91 + 12 months; NPA_DOUBTFUL is open-ended.
# THIS RESTATES models/loan.dpd_bucket_for's 30/60/90 (see CHANGELOG 4); the
# two are held together by test_dpd_range_agrees_with_dpd_bucket_for.
DPD_RANGE: dict[int, tuple[float, float]] = {
    SMA_0: (1.0, 30.0),
    SMA_1: (31.0, 60.0),
    SMA_2: (61.0, 90.0),
    NPA_SUB: (91.0, 91.0 + 365.0),
    NPA_DOUBTFUL: (456.0, float("inf")),
}


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

    Used ONLY to place the Dirichlet prior's floor mass; observed counts in any
    cell are always kept. DPD rises by at most ~31 days a month, so forward
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

# ── Mapping from the repo's own enums ────────────────────────────────────────
# Matched by value. app.models.loan.DPDBucket and LoanStatus are str enums.
BUCKET_TO_STATE: dict[str, int] = {
    "CURRENT": CURRENT,
    "BUCKET_1": SMA_0,
    "BUCKET_2": SMA_1,
    "BUCKET_3": SMA_2,
    "NPA": NPA_SUB,  # split into DOUBTFUL by NPA age, below
}
# Terminal statuses win over the bucket (a written-off loan keeps its last DPD).
TERMINAL_STATUS_TO_STATE: dict[str, int] = {
    "WRITTEN_OFF": WRITTEN_OFF,
    "CLOSED": RESOLVED,
    "SETTLED": RESOLVED,
}
# An NPA status also wins over the bucket: the account stays an NPA until it
# is regularised, whatever its DPD has fallen to (CHANGELOG 3).
NPA_STATUS = "NPA"
NON_TERMINAL_STATUSES: frozenset[str] = frozenset({"ACTIVE", NPA_STATUS})


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
    """The 8-state code for one loan. The single definition (see CHANGELOG).

    - A terminal `loan_status` wins: WRITTEN_OFF -> WRITTEN_OFF,
      CLOSED / SETTLED -> RESOLVED.
    - An NPA `loan_status` wins over the bucket: a part-paid NPA stays an NPA
      until regularised (RBI), whatever its DPD now reads.
    - Otherwise the DPD bucket decides. NPA splits at 12 whole months of
      `npa_since` (REGULATORY). With no `npa_since` an NPA reads as
      NPA_SUB — a lower bound, the same one DATA-MODEL-V2 §9.5 records for
      loans whose NPA spell starts at the earliest observation.
    """
    status = _value(loan_status)
    if status is not None:
        if status in TERMINAL_STATUS_TO_STATE:
            return STATES[TERMINAL_STATUS_TO_STATE[status]]
        if status not in NON_TERMINAL_STATUSES:
            raise ValueError(f"unknown loan_status {status!r}")
    bucket = _value(dpd_bucket)
    if bucket not in BUCKET_TO_STATE:
        raise ValueError(f"unknown dpd_bucket {bucket!r}")
    state = NPA_SUB if status == NPA_STATUS else BUCKET_TO_STATE[bucket]
    if state == NPA_SUB and npa_since is not None:
        if as_of is None:
            raise ValueError("npa_since given without as_of")
        if months_between(npa_since, as_of) >= DOUBTFUL_AFTER_MONTHS:
            state = NPA_DOUBTFUL
    return STATES[state]


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
        doubtful = (out == NPA_SUB) & (np.nan_to_num(age, nan=-1.0) >= DOUBTFUL_AFTER_MONTHS)
        out[doubtful] = NPA_DOUBTFUL
    if status is not None:
        for code, s in TERMINAL_STATUS_TO_STATE.items():
            out[status == code] = s                  # terminal statuses win over everything
    return out
