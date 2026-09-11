# ─── CHANGELOG (prototype → product) ───
# New file, 2026-07-21. Covers PaymentService's pure/no-DB pieces —
# _generate_receipt's format and _payment_response/_ptp_response's dict
# shape — using SimpleNamespace stand-ins for Payment/PTP/Case, same
# no-DB-needed scope as test_visit_service.py. collect_payment/set_ptp/
# create_payment_link (and the new duplicate-submit guard) are NOT
# covered here — they query the database, which this lightweight test
# style deliberately doesn't stand up; see changelog.md for what live
# verification, if any, was done for those instead.
import re
from datetime import datetime, date, timezone
from types import SimpleNamespace

from app.services.payment_service import PaymentService


def test_generate_receipt_format():
    receipt = PaymentService._generate_receipt()
    assert re.match(r"^TIQ-\d{4}-[0-9A-F]{8}$", receipt)


def test_generate_receipt_unique_across_calls():
    receipts = {PaymentService._generate_receipt() for _ in range(20)}
    assert len(receipts) == 20


def test_payment_response_shape():
    payment = SimpleNamespace(
        id="pay-1",
        receipt_number="TIQ-2026-ABCDEF12",
        amount=500.0,
        mode="UPI",
        status="PENDING_VERIFICATION",
        payment_date=datetime(2026, 7, 21, 10, 0, tzinfo=timezone.utc),
    )
    case = SimpleNamespace(status="PARTIALLY_PAID", collected_amount=1500.0)
    resp = PaymentService._payment_response(payment, case)
    assert resp == {
        "id": "pay-1",
        "receipt_number": "TIQ-2026-ABCDEF12",
        "amount": 500.0,
        "mode": "UPI",
        "status": "PENDING_VERIFICATION",
        "payment_date": "2026-07-21T10:00:00+00:00",
        "case_status": "PARTIALLY_PAID",
        "total_collected": 1500.0,
        # 2026-09-11 — whether the receipt reached the transport. Defaults to
        # False; the caller passes the real result. No column behind it.
        "receipt_sent": False,
    }


def test_ptp_response_shape_with_follow_up():
    ptp = SimpleNamespace(
        id="ptp-1",
        case_id="case-1",
        committed_amount=2000.0,
        committed_date=date(2026, 8, 1),
        follow_up_date=date(2026, 8, 3),
        status="ACTIVE",
    )
    resp = PaymentService._ptp_response(ptp)
    assert resp == {
        "id": "ptp-1",
        "case_id": "case-1",
        "committed_amount": 2000.0,
        "committed_date": "2026-08-01",
        "follow_up_date": "2026-08-03",
        "status": "ACTIVE",
    }


def test_ptp_response_shape_without_follow_up():
    ptp = SimpleNamespace(
        id="ptp-2",
        case_id="case-2",
        committed_amount=750.0,
        committed_date=date(2026, 8, 5),
        follow_up_date=None,
        status="ACTIVE",
    )
    resp = PaymentService._ptp_response(ptp)
    assert resp["follow_up_date"] is None
