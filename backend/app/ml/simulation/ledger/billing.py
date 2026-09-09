# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-09-09 — NEW. The billing spine: DPD and overdue as DERIVED quantities.
#
#   THIS IS THE WHOLE POINT OF THE REWRITE. In book_simulator.py, `dpd` and
#   `overdue_amount` are entries in a state dict that the loop updates. Nothing
#   stops a future edit from writing tomorrow's value into today's row, and
#   nothing would detect it. Worse, the production schema has the same shape —
#   `Loan.dpd` is overwritten in place with no history — which is exactly why a
#   retrospective backtest against the live database turned out to be
#   impossible: 3 of the model's 4 features cannot be reconstructed for a past
#   date there.
#
#   Here both are PURE FUNCTIONS of (schedule, ledger, as_of). There is no
#   current-state value to overwrite, so point-in-time correctness is a property
#   of the arithmetic rather than a discipline someone has to maintain.
# ───────────────────────────────────────────────────────────────────────────
"""Installment schedules, FIFO allocation, and the DPD/overdue derivation.

    dpd(t)     = t - due_day(oldest installment not fully covered by t)
    overdue(t) = billed_by(t) - paid_known_by(t)          , floored at 0

FIFO IS NOT A CHOICE MADE HERE, it is the retail convention: money clears the
oldest arrears first. Because every installment on a loan is the same amount
(level EMI), FIFO collapses to a division — the number of fully settled
installments is `floor(paid / emi)` — which makes the whole derivation exact and
vectorised over the book. A schedule with unequal installments would need a
searchsorted over the cumulative amounts; the interface below would not change.

`paid_known_by(t)` IS NOT `sum of payments with date <= t`. It is the sum of
payments **whose VERIFIED status was in force at t**. A payment made on day 10
and reversed on day 25 counts on day 20 and does not count on day 30. That
distinction is invisible in the live database — which stores only a payment's
current status — and it is the mechanism behind the `payment_status`
disagreement the dual-label comparison exists to measure.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class Schedule:
    """One vectorised installment schedule per loan, on a fixed day grid.

    `first_due_day` is a day index relative to the simulation start and is
    NEGATIVE for a seasoned account — a loan disbursed before the book opens has
    already billed installments, which is what lets the book start with a
    realistic spread of delinquency instead of every loan at the same DPD.
    """

    first_due_day: np.ndarray     # int, may be negative
    emi: np.ndarray               # float, level
    n_installments: np.ndarray    # int, tenure in months
    cycle_days: int = 30

    def billed_count(self, t: int) -> np.ndarray:
        """Installments whose due date has passed by day `t`."""
        elapsed = (t - self.first_due_day) // self.cycle_days + 1
        return np.clip(elapsed, 0, self.n_installments)

    def due_day_of(self, k: np.ndarray) -> np.ndarray:
        """Day index on which installment `k` (0-based) falls due."""
        return self.first_due_day + self.cycle_days * k

    def billed_amount(self, t: int) -> np.ndarray:
        return self.billed_count(t) * self.emi


def settled_count(paid_known: np.ndarray, emi: np.ndarray,
                  billed: np.ndarray) -> np.ndarray:
    """How many installments the money on hand has fully cleared, FIFO.

    Capped at `billed`: paying ahead does not make a future installment overdue
    in the negative, and a borrower who prepays should read as DPD 0, not as
    having a settled count that runs past what has been asked of them.
    """
    k = np.floor(np.maximum(paid_known, 0.0) / np.maximum(emi, 1.0)).astype(int)
    return np.minimum(k, billed)


def dpd_at(t: int, sched: Schedule, paid_known: np.ndarray,
           grace_days: int = 0) -> np.ndarray:
    """Days past due at day `t`. Zero when nothing billed is outstanding.

    `grace_days` is the contractual grace before an instalment counts as past
    due. It is not cosmetic: without it an account is CURRENT only in the sliver
    between clearing its arrears and the next instalment billing, so a perfectly
    performing borrower reads as BUCKET_1 for most of every cycle. Measured
    without grace, CURRENT was 0.9% of account-months while the whole 0-30 band
    held 25% — the mass was right and the split was an artefact of where in the
    cycle the snapshot happened to land.
    """
    billed = sched.billed_count(t)
    settled = settled_count(paid_known, sched.emi, billed)
    clean = settled >= billed
    oldest_unpaid_due = sched.due_day_of(np.minimum(settled, sched.n_installments - 1))
    dpd = np.where(clean, 0, t - oldest_unpaid_due - grace_days)
    return np.maximum(dpd, 0)


def overdue_at(t: int, sched: Schedule, paid_known: np.ndarray) -> np.ndarray:
    """Billed but unpaid principal+interest at day `t`, excluding penal."""
    return np.maximum(sched.billed_amount(t) - np.maximum(paid_known, 0.0), 0.0)


def penal_at(overdue, dpd, rate_monthly: float, cycle_days: int):
    """Penal interest on arrears at a given moment. A CLOSED FORM, deliberately.

    An earlier draft accumulated this day by day in the simulator's state, which
    would have made it the one quantity in the panel that `panel.py` could not
    recompute from the ledger — and therefore the one quantity where the
    simulator and the derivation could silently disagree. This repo has been
    bitten by two-copies-of-one-rule often enough (seven DPD-bucket spellings)
    that a second definition is not worth the extra fidelity.

    Charged only past 30 DPD, on the arrears, for the days past that point. It
    approximates the true path integral exactly when arrears are flat through
    the delinquency spell and understates it slightly when they grow.
    """
    import numpy as _np
    months_past = _np.maximum(_np.asarray(dpd, dtype=float) - 30.0, 0.0) / cycle_days
    return _np.asarray(overdue, dtype=float) * rate_monthly * months_past


def seed_seasoned_book(rng, n: int, target_dpd: np.ndarray, months_on_book: np.ndarray,
                       emi: np.ndarray, tenure: np.ndarray, cycle_days: int
                       ) -> tuple[Schedule, np.ndarray]:
    """Build schedules and opening balances that REPRODUCE a target DPD spread.

    The book must not start with every loan at the same delinquency — the demo
    seed starts every case at DPD 35, which both inflates DPD's apparent power
    and leaves a binner nothing to bin at the good end.

    Rather than writing a DPD, this SOLVES for the opening ledger position that
    yields it: pick how long the account has been on book, phase its due date
    within the cycle so DPD is not confined to multiples of 30, then set the
    money already paid so that FIFO lands the oldest unpaid installment the
    intended number of days ago. The resulting DPD is then read back out of the
    same derivation everything else uses, never asserted.
    """
    phase = rng.integers(0, cycle_days, n)
    first_due_day = -(cycle_days * months_on_book + phase)
    sched = Schedule(first_due_day=first_due_day, emi=emi,
                     n_installments=tenure, cycle_days=cycle_days)

    billed0 = sched.billed_count(0)
    # Days-past-due of installment k at t=0 is  -(first_due_day + cycle*k).
    # Solve for the k whose due date sits `target_dpd` days back.
    k = np.floor((-first_due_day - target_dpd) / cycle_days).astype(int)
    k = np.clip(k, 0, billed0)
    # A borrower meant to be CURRENT has cleared everything billed.
    k = np.where(target_dpd <= 0, billed0, k)

    paid_known = k * emi
    # A little partial money on top, so opening balances are not a lattice of
    # exact EMI multiples. Kept strictly below one EMI so FIFO's settled count —
    # and therefore the DPD above — is unchanged.
    partial = rng.random(n) * 0.85 * emi * (rng.random(n) < 0.35)
    paid_known = paid_known + np.where(k < billed0, partial, 0.0)
    return sched, paid_known
