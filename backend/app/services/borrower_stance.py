# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-09-24 — NEW (board ML-1, option A; the owner's decision: "record the
#   stance").
#
#   `borrower_disposition` exists on `Visit` and `CallLog` since 2026-09-16
#   (migration c9a3d5e7f102) and recovery_risk 2.2.0 reads it as
#   `latest_disposition` — its strongest behavioural feature, IV 0.31. The
#   product never wrote it: measured on the live book, 0 of 2,404 visits and
#   0 of 1,089 call logs carried one, so every one of the 11,049 predictions
#   2.2.0 had served read NONE. ce measured what that costs on the synthetic
#   OOT rows: Gini 0.5122 -> 0.4796, KS 38.66 -> 35.74.
#
#   The visit form (Borrower path) and the call-log form (answered calls) now
#   ask for it. This module is the one place that says WHEN a stance may be
#   recorded: only by someone who heard the borrower — a visit where the
#   borrower was the person met, or an ANSWERED call. The model's adapter
#   (ml_scoring_service._disposition_features) pools "answered calls and met
#   visits" on exactly that understanding, and the ledger simulator the model
#   was trained on only ever emits a reading on those contacts. A stance on
#   anything else is refused (422), never silently dropped: a client that
#   sends one has a bug worth seeing.
#
#   Never defaulted. NULL means "not recorded", and a default would be a
#   reading nobody took (models/call_log.BorrowerDisposition says the same).
#
#   "Required" is the UI's rule, not the server's: RecordVisitPage will not
#   submit a Borrower-path visit without a stance, but a request with none is
#   accepted and stored as NULL. Deliberate (coordinator, 2026-09-24): ce's
#   offline outbox (I01) replays visits queued before this change, with no
#   stance, and those must land rather than 422. The only refusal here is a
#   stance on a contact that did not reach the borrower.
# ────────────────────────────────────────────────────────────────────────────
"""When a borrower's stance (BorrowerDisposition) may be recorded."""
from __future__ import annotations

from app.core.errors import AppException, ErrorCode
from app.models.call_log import BorrowerDisposition, CallOutcome
from app.models.visit import PersonMet


def check_visit_stance(disposition: BorrowerDisposition | None, *, customer_met: bool,
                       person_met: PersonMet | None) -> None:
    """A visit's stance is the BORROWER's: refused unless the borrower was met.
    person_met None with customer_met True is the API's older shape for "the
    borrower" (the form always sends BORROWER on that path)."""
    if disposition is None:
        return
    if not customer_met or person_met not in (None, PersonMet.BORROWER):
        raise AppException(
            422, ErrorCode.DISPOSITION_WITHOUT_BORROWER,
            "The borrower's stance can only be recorded when the borrower was the person met.",
        )


def check_call_stance(disposition: BorrowerDisposition | None, *, outcome: CallOutcome) -> None:
    """A call's stance needs an answered call: nobody said anything otherwise."""
    if disposition is None:
        return
    if outcome != CallOutcome.ANSWERED:
        raise AppException(
            422, ErrorCode.DISPOSITION_WITHOUT_BORROWER,
            "The borrower's stance can only be recorded on an answered call.",
        )
