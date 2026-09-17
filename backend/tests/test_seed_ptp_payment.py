# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-08-26 — New file. Tests for the seed's PTP → Payment channel.
#
#   THE BUG THIS GUARDS. Four separate places in seed_data.py created a PTP,
#   set actual_paid_amount to a figure like `overdue * 0.85`, and never wrote a
#   Payment row. On the frozen baseline that was 105 of 458 promises asserting
#   money no ledger recorded. Every collection metric in the product reads
#   Payment, so that money was invisible to all of them — and _ptp_kept's
#   payment clause (manager.py:423) could never fire on seeded data at all.
#
#   WHY THESE TESTS ARE ABOUT CAPS, NOT AMOUNTS. Booking the claimed money is
#   only safe because it is bounded: capped at what the case still asks for, and
#   refused entirely before the promise falls due. Remove either bound and the
#   channel stops being a correction and becomes a way to manufacture a
#   collection rate. So the caps are what is pinned here, not the arithmetic.
#
#   No database. The helper takes a `db` only to call .add(), so a recorder
#   stands in — which also lets the tests assert on the Payment that WOULD be
#   written, including the fields (visit_id, mode) that carry the meaning.
# ───────────────────────────────────────────────────────────────────────────
"""The seed's PTP → Payment channel: bounded, dated honestly, remote-only."""
from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace

from app.models.payment import PaymentMode, PaymentStatus
from scripts.seed_data import _PTP_PAYMENT_MODES, _book_ptp_payment, _stable_rng

TODAY = date(2026, 8, 26)


class _Recorder:
    """Stands in for the Session. Only .add() is ever called."""

    def __init__(self):
        self.added = []

    def add(self, obj):
        self.added.append(obj)


def _case(target=10_000.0, collected=0.0, cid="case-1"):
    return SimpleNamespace(id=cid, case_number="CASE0000001",
                           target_amount=target, collected_amount=collected)


def _book(case, *, committed, due, already=0.0, today=TODAY):
    db = _Recorder()
    paid = _book_ptp_payment(
        db, case=case, agent_id="agent-1", committed_amount=committed,
        committed_date=due, already_collected=already, today=today,
        r=_stable_rng("test", case.id, str(due)))
    return paid, db.added


# ── The caps. These are the whole safety argument. ───────────────────────────
def test_amount_is_capped_by_what_the_case_still_asks_for():
    """A promise for more than the case target books only the target.

    The old code set actual_paid_amount to ~85% of ARREARS while the case target
    is 18-35% of the same arrears — roughly 3x the ask. Booking that verbatim
    would have pushed collected past target and the collection rate past 100%.
    """
    paid, added = _book(_case(target=10_000.0), committed=50_000.0, due=TODAY)
    assert paid == 10_000.0
    assert added[0].amount == 10_000.0


def test_already_collected_reduces_what_is_left():
    """The visit's own payment is deducted first, so a case cannot over-collect
    by being paid twice in one cycle."""
    paid, _ = _book(_case(target=10_000.0), committed=10_000.0, due=TODAY,
                    already=7_500.0)
    assert paid == 2_500.0


def test_nothing_is_booked_once_the_target_is_met():
    paid, added = _book(_case(target=10_000.0), committed=5_000.0, due=TODAY,
                        already=10_000.0)
    assert paid == 0.0
    assert added == []


def test_a_promise_not_yet_due_books_nothing():
    """A payment dated in the future is not a payment. The seed's status draw
    ignores the due date, so without this guard an honoured promise dated next
    week would have produced a future-dated Payment row — money the ledger
    claims to have received before it happened."""
    paid, added = _book(_case(), committed=5_000.0, due=TODAY + timedelta(days=7))
    assert paid == 0.0
    assert added == []


def test_a_promise_due_today_does_book():
    """The boundary is inclusive: today has happened."""
    paid, added = _book(_case(), committed=5_000.0, due=TODAY)
    assert paid == 5_000.0
    assert len(added) == 1


def test_a_zero_or_missing_commitment_books_nothing():
    assert _book(_case(), committed=0.0, due=TODAY)[0] == 0.0
    assert _book(_case(), committed=None, due=TODAY)[0] == 0.0
    assert _book(_case(), committed=5_000.0, due=None)[0] == 0.0


def test_a_case_with_no_target_books_nothing():
    """Guards a division-free but still meaningless payment against a case that
    was never given an ask."""
    paid, added = _book(_case(target=0.0), committed=5_000.0, due=TODAY)
    assert paid == 0.0
    assert added == []


# ── The meaning carried by the Payment's fields ──────────────────────────────
def test_the_payment_carries_no_visit():
    """visit_id=None is the load-bearing field. The borrower paid remotely, days
    after anyone stood at their door. Attributing it to a visit would inflate
    the contact rate and the per-visit yield at field_ops.py:416 — the money is
    real, the doorstep event is not."""
    _, added = _book(_case(), committed=5_000.0, due=TODAY)
    assert added[0].visit_id is None


def test_the_mode_is_remote_and_never_cash():
    """Cash without a visit is incoherent. Pinned against the module constant so
    widening it cannot happen quietly, and CASH is asserted out separately
    because that is the one that would be wrong rather than merely different."""
    # 2026-09-17: ONLINE -> RTGS. ONLINE is a prototype leftover no agent can
    # select (models/payment.py); the seed now draws from the three modes the
    # app actually offers, and test_payment_modes.py trips if ONLINE returns.
    assert set(_PTP_PAYMENT_MODES) == {PaymentMode.UPI, PaymentMode.NEFT,
                                       PaymentMode.RTGS}
    assert PaymentMode.CASH not in _PTP_PAYMENT_MODES
    _, added = _book(_case(), committed=5_000.0, due=TODAY)
    assert added[0].mode in _PTP_PAYMENT_MODES


def test_the_payment_is_verified():
    """The manager pages count VERIFIED only, so an unverified row would be
    money that exists in the table and nowhere on screen."""
    _, added = _book(_case(), committed=5_000.0, due=TODAY)
    assert added[0].status == PaymentStatus.VERIFIED
    assert added[0].verified_at is not None


def test_the_payment_is_dated_on_the_promised_day_not_the_visit_day():
    """NOT moved into the visit's month, deliberately.

    The collection metric buckets its numerator on Payment.payment_date and its
    denominator on Visit.check_in_time — different columns of different tables.
    So a promise made on the 28th and settled on the 4th genuinely lands in the
    next month while its target sat in this one. Clamping the date to line the
    two up would improve the ratio by editing a payment, which is the one thing
    this whole exercise must not do.
    """
    due = date(2026, 8, 4)
    _, added = _book(_case(), committed=5_000.0, due=due)
    assert added[0].payment_date.date() == due


def test_the_payment_lands_inside_contact_hours():
    """A remote settlement still gets a plausible clock time rather than
    midnight, which is what an unset time defaults to."""
    _, added = _book(_case(), committed=5_000.0, due=TODAY)
    assert 10 <= added[0].payment_date.hour <= 18


def test_receipt_numbers_do_not_repeat():
    """receipt_number is UNIQUE (models/payment.py:39). It used to be drawn as
    randint(1e7, 1e8) from three independent sites, which at ~10,000 payments is
    a ~45% chance the seed dies mid-run with an IntegrityError."""
    seen = set()
    for i in range(200):
        _, added = _book(_case(cid=f"case-{i}"), committed=5_000.0, due=TODAY)
        seen.add(added[0].receipt_number)
    assert len(seen) == 200


# ── Determinism ──────────────────────────────────────────────────────────────
def test_the_same_inputs_produce_the_same_payment():
    """The seed promises reproducible behaviour across runs; a payment whose
    amount is stable but whose mode and time are not would make two seeds
    diff for no reason."""
    a_paid, a_added = _book(_case(cid="stable"), committed=5_000.0, due=TODAY)
    b_paid, b_added = _book(_case(cid="stable"), committed=5_000.0, due=TODAY)
    assert a_paid == b_paid
    assert a_added[0].mode == b_added[0].mode
    assert a_added[0].payment_date == b_added[0].payment_date


# ── The invariant, as a statement about the source ───────────────────────────
def test_every_ptp_site_goes_through_the_helper():
    """Asserted on the SOURCE, because the failure mode is a FIFTH site being
    added later that sets actual_paid_amount directly. A behavioural test cannot
    see code that does not exist yet; this can.

    seed_data.py sets actual_paid_amount in five places. Four are the PTP
    creation sites and must be fed by _book_ptp_payment; the fifth is the
    literal 0.0 on a due-today promise, which has nothing to book.
    """
    import pathlib
    import re

    src = (pathlib.Path(__file__).resolve().parents[1]
           / "scripts" / "seed_data.py").read_text(encoding="utf-8")

    # Every non-zero actual_paid_amount must be a variable fed by the helper —
    # never an inline arithmetic expression like `overdue * 0.85`.
    inline = re.findall(r"actual_paid_amount\s*=\s*(?:round\()?[^,\n]*\*", src)
    assert not inline, f"actual_paid_amount computed inline: {inline}"

    # Counted by shape, not by bare name — the runtime invariant's own error
    # message names the helper too, and a raw count of the string picks that up.
    assert len(re.findall(r"^def _book_ptp_payment\(", src, re.M)) == 1
    calls = re.findall(r"=\s*_book_ptp_payment\(", src)
    assert len(calls) == 4, f"expected 4 call sites, found {len(calls)}"

    # And the invariant is enforced at runtime, not merely hoped for.
    assert "PTP invariant violated" in src
