# ─── CHANGELOG (standalone plan) ─────────────────────────────────────────────
# 2026-09-29 (P3 D06/R3, ce) — NEW. The one definition of "Recovery vs
#   Expected"'s denominator: how much a placement is expected to bring back,
#   at the moment it is placed. 2b (P3 placements) writes the result into
#   placements.expected_recovery_inr at placement time; D06's scorecard
#   later divides ACTUAL verified recovery by the SUM of this column over a
#   cohort — this function only ever answers for one placement.
#
#   Pure — no DB, no clock, no import of ml/ or app.services.ml_scoring_service.
#   2b's own contract (agreed over cross-session message, not guessed): the
#   caller already resolved `prob` from the newest recovery_risk prediction
#   with as_of <= placed_on (1 - the stored P(bad)), and passes None when
#   there is no prediction yet — this function must never impute one
#   (ADR 0005's abstain rule), so None in is None out, always.
#
#   BASE AMOUNT: overdue_at_placement, not exposure_at_placement. A
#   collections placement is asked to recover what is actually OWED now
#   (arrears), not the full remaining loan balance, most of which is not yet
#   due — the same "collectable balance" reasoning services/global_allocator.py
#   already uses for its own expected-recovery term (collectable = target -
#   collected, an arrears figure, never the full exposure). Using exposure
#   here would overstate every placement's expected recovery by the
#   not-yet-due portion of the loan. exposure_at_placement is still taken as
#   a parameter and used as a sanity CAP: a probability times an arrears
#   figure cannot exceed what the loan is actually worth, however the two
#   inputs were computed upstream.
#
#   dpd_bucket and loan_type are accepted (2b's contract names them) but NOT
#   used in v1.0.0's arithmetic. Recorded here rather than left silently
#   unused: a bucket- or product-specific haircut is the kind of thing this
#   codebase's own convention (CLAUDE.md, "nothing hand-weighted may present
#   itself as a model") warns against inventing without evidence — v1.0.0 is
#   deliberately just the modelled probability times the money owed. A
#   future version that DOES use them earns a version bump, which is exactly
#   what VERSION exists for.
# ────────────────────────────────────────────────────────────────────────────
"""expected_recovery_inr — Recovery vs Expected's per-placement denominator."""
from __future__ import annotations

from app.models.loan import DPDBucket, LoanType

VERSION = "expected-recovery-1.0.0"


def expected_recovery_inr(
    *, prob: float | None, overdue_at_placement: float, exposure_at_placement: float,
    dpd_bucket: DPDBucket, loan_type: LoanType,
) -> float | None:
    """P(pay next cycle) x the arrears owed at placement, capped at the
    loan's total exposure. Returns None (never 0, never a guess) when
    `prob` is None — no modelled score yet for this loan.

    `dpd_bucket`/`loan_type` are part of the signature (2b's contract) but
    not read here in v1.0.0 — see this module's own changelog for why.
    """
    if prob is None:
        return None
    overdue = max(0.0, overdue_at_placement)
    exposure = max(0.0, exposure_at_placement)
    expected = min(prob * overdue, exposure)
    return round(max(0.0, expected), 2)
