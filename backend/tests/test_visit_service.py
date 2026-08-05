# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-07-14 (later) — New file. Covers VisitService._apply_outcome_transition
#   (every VisitOutcome -> CaseStatus branch) with lightweight SimpleNamespace
#   stand-ins for Case/RecordVisitRequest — the method only touches attributes,
#   never queries the DB, so a real session/fixtures aren't needed for this
#   piece. Full detail + why: /changelog.md
# ───────────────────────────────────────────────────────────────────────────
from datetime import datetime, timezone
from types import SimpleNamespace

from app.models.case import CaseStatus, EscalationReason
from app.models.visit import VisitOutcome
from app.services.visit_service import VisitService

NOW = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)


def _make_case(status=CaseStatus.ASSIGNED):
    return SimpleNamespace(
        status=status,
        resolved_at=None,
        resolution_notes=None,
        is_escalated=False,
        escalation_reason=None,
        escalation_notes=None,
        escalated_at=None,
        customer=SimpleNamespace(do_not_contact=False, tags=[]),
    )


def _make_req(outcome, agent_recording_transcript=None):
    return SimpleNamespace(outcome=outcome, agent_recording_transcript=agent_recording_transcript)


def _apply(outcome, **case_kwargs):
    svc = VisitService(db=None)
    case = _make_case(**case_kwargs)
    svc._apply_outcome_transition(case, _make_req(outcome), NOW)
    return case


def test_paid_full_resolves_case():
    case = _apply(VisitOutcome.PAID_FULL)
    assert case.status == CaseStatus.PAID
    assert case.resolved_at == NOW


def test_part_paid_sets_partially_paid():
    case = _apply(VisitOutcome.PART_PAID)
    assert case.status == CaseStatus.PARTIALLY_PAID
    assert case.resolved_at is None


def test_ptp_sets_ptp_status():
    case = _apply(VisitOutcome.PTP)
    assert case.status == CaseStatus.PTP_SET


def test_part_paid_ptp_sets_ptp_status():
    case = _apply(VisitOutcome.PART_PAID_PTP)
    assert case.status == CaseStatus.PTP_SET


def test_dispute_escalates_with_reason_and_notes():
    svc = VisitService(db=None)
    case = _make_case()
    svc._apply_outcome_transition(case, _make_req(VisitOutcome.DISPUTE, agent_recording_transcript="customer says already paid"), NOW)
    assert case.status == CaseStatus.ESCALATED
    assert case.is_escalated is True
    assert case.escalation_reason == EscalationReason.DISPUTED_AMOUNT
    assert case.escalated_at == NOW
    assert case.escalation_notes == "customer says already paid"


def test_rtp_escalates_as_customer_hostile():
    case = _apply(VisitOutcome.RTP)
    assert case.status == CaseStatus.ESCALATED
    assert case.is_escalated is True
    assert case.escalation_reason == EscalationReason.CUSTOMER_HOSTILE


def test_address_issue_escalates_as_other():
    case = _apply(VisitOutcome.ADDRESS_ISSUE)
    assert case.status == CaseStatus.ESCALATED
    assert case.escalation_reason == EscalationReason.OTHER


def test_deceased_closes_case_and_flags_do_not_contact():
    case = _apply(VisitOutcome.DECEASED)
    assert case.status == CaseStatus.CLOSED
    assert case.resolved_at == NOW
    assert case.customer.do_not_contact is True
    assert "DECEASED" in case.customer.tags


def test_not_available_moves_assigned_case_to_in_progress():
    case = _apply(VisitOutcome.NOT_AVAILABLE, status=CaseStatus.ASSIGNED)
    assert case.status == CaseStatus.IN_PROGRESS


def test_not_available_leaves_non_assigned_case_untouched():
    case = _apply(VisitOutcome.NOT_AVAILABLE, status=CaseStatus.IN_PROGRESS)
    assert case.status == CaseStatus.IN_PROGRESS
