# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-07-14 — New file. record_visit() (+ helpers _apply_outcome_transition,
#   _notify_visit_completed) moved out of the router in endpoints/agent.py —
#   a direct extraction, logic unchanged, not a rewrite. Second module through
#   the router→service split after case_service.py.
# 2026-07-14 (API fix pass) — Visit(...) construction now passes
#   notes/consent_given/signature_key through — all three were accepted by
#   RecordVisitRequest but silently discarded (no schema field for notes, no
#   model column and no read of the other two here).
# 2026-07-14 (later) — Deferred import back into endpoints/agent.py for
#   _generate_visit_report/_send_twilio/_normalize_phone replaced with direct
#   imports of AIReportService/NotificationService — both now real service
#   files, so VisitService no longer depends on the router module at all.
#   _parse_dt inlined locally (it was only ever used here).
# 2026-07-14 (later still) — Added an idempotency guard: a duplicate submit
#   for the same case/agent/outcome within _DUPLICATE_SUBMIT_WINDOW_SECONDS
#   now returns the existing Visit instead of inserting a second row.
# 2026-07-14 (later still) — Geo-fence/contact-hours changed from
#   record-only to hard-blocking: a submission outside RBI contact hours or
#   >100m from the customer's address now raises 403 instead of just being
#   flagged on the saved row. ADDRESS_ISSUE is exempt from the geo-fence
#   check (reporting a wrong/nonexistent address can never satisfy a
#   geo-fence around that same wrong address) — contact-hours has no
#   exceptions (RBI rule, not a soft preference). Full detail + why:
#   /changelog.md
# ───────────────────────────────────────────────────────────────────────────
"""
Business logic for recording a field visit.

Router in endpoints/agent.py stays thin: fetch the current agent, delegate
to VisitService, return the result. Same router/service split already
applied to CaseService.reoptimize_beat and AuthService — see
prototype_to_product/final_changes.md §7.1 for the full extraction plan.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import structlog

from fastapi import HTTPException
from sqlalchemy.orm import Session, joinedload

from app.core.events import publish_event
from app.core.geo import GEO_FENCE_METRES, IST, RBI_CONTACT_END, RBI_CONTACT_START, is_within_contact_hours, within_geo_fence
from app.models.audit_log import AuditAction, AuditLog
from app.core.audit import write_audit
from app.models.agent import Agent
from app.models.case import Case, CaseStatus, EscalationReason
from app.models.visit import Visit, VisitOutcome
from app.schemas.agent import RecordVisitRequest
from app.services.scope import agent_case_or_404, sync_assignee
from app.services.ai_report_service import AIReportService
from app.services.brand import brand_for
from app.services.notification_service import NotificationService

logger = structlog.get_logger()

_PAYMENT_OUTCOMES = {VisitOutcome.PAID_FULL, VisitOutcome.PART_PAID, VisitOutcome.PART_PAID_PTP}
# A flaky connection can make an agent's app retry the same submit. Anything
# for the same case/agent/outcome within this window is treated as the same
# physical check-in, not a second real visit.
_DUPLICATE_SUBMIT_WINDOW_SECONDS = 15


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


class VisitService:
    def __init__(self, db: Session):
        self.db = db

    def record_visit(self, agent: Agent, case_id: str, req: RecordVisitRequest) -> dict:
        """Record a field visit: geo/contact-hours checks, case status transition,
        AI audit-note generation, and the post-visit SMS/WhatsApp notification.

        Workflow:
          1. Load the case (must belong to this agent) and reject a
             Do-Not-Contact customer outright.
          2. Compute geo-fence distance and contact-hours compliance
             server-side (never trusted from the client).
          3. Persist the Visit row with all photo/recording metadata.
          4. Transition case.status based on the reported outcome.
          5. Generate an AI audit note (best-effort, OpenAI-gated).
          6. Notify the customer by SMS/WhatsApp for non-payment outcomes
             (payment outcomes are notified by collect_payment instead).
        """
        # 2026-09-24 (A03): the one access rule. The old copy here granted any
        # unassigned case in any tenant and then RE-ASSIGNED it to the caller,
        # so recording a visit took the case over. A stale assignee is synced
        # only for a case on the caller's beat today, inside their agency.
        case = agent_case_or_404(self.db, agent, case_id,
                                 options=(joinedload(Case.customer), joinedload(Case.loan)))

        if case.customer.do_not_contact:
            raise HTTPException(status_code=403, detail="Customer is marked Do Not Contact")

        now_utc = datetime.now(timezone.utc)

        # Idempotency guard — see _DUPLICATE_SUBMIT_WINDOW_SECONDS above.
        recent_duplicate = (
            self.db.query(Visit)
            .filter(
                Visit.case_id == case.id,
                Visit.agent_id == agent.id,
                Visit.outcome == req.outcome,
                Visit.check_in_time >= now_utc - timedelta(seconds=_DUPLICATE_SUBMIT_WINDOW_SECONDS),
            )
            .order_by(Visit.check_in_time.desc())
            .first()
        )
        if recent_duplicate:
            return self._to_response(recent_duplicate)

        within_hours = is_within_contact_hours(now_utc)
        distance_m, geo_ok = within_geo_fence(
            req.check_in_latitude, req.check_in_longitude,
            case.customer.latitude, case.customer.longitude,
        )

        if not within_hours:
            # Written BEFORE the refusal, and committed on its own, because
            # get_db never commits and a row left pending here would be
            # discarded with the session when the 403 propagates.
            #
            # 2026-09-11 — CONTACT_HOUR_VIOLATION_ATTEMPT had been declared
            # since the first schema and written by nothing, which made the
            # compliance tile a tautology: it counted stored visits with
            # within_contact_hours = False, and this refusal is the only place
            # the flag could ever be False on the real path — a refused visit is
            # never stored. So "0 of N outside hours" was true of every book
            # forever and measured nothing. The number a manager actually
            # wants is how many attempts this rule stopped. This row is that.
            #
            # user_id is the AGENT'S user, which is what tenant-scopes it: the
            # manager's audit query reads user_id IN (their agents' users).
            # success=False, because the attempted action did not happen.
            self._audit_contact_hour_violation(agent, case, now_utc, req)
            raise HTTPException(
                status_code=403,
                detail=(
                    f"Visits can only be recorded between {RBI_CONTACT_START}:00 and "
                    f"{RBI_CONTACT_END}:00 IST (RBI-mandated contact hours)."
                ),
            )
        # ADDRESS_ISSUE is exempt: its entire purpose is reporting that the
        # registered address is wrong/nonexistent/the customer has moved —
        # an agent reporting that can, by definition, never be within the
        # geo-fence of the (wrong) address on file. Every other outcome
        # implies the agent reached the real address, so the fence applies.
        if not geo_ok and req.outcome != VisitOutcome.ADDRESS_ISSUE:
            raise HTTPException(
                status_code=403,
                detail=(
                    f"You are {distance_m:.0f}m from the customer's registered address — "
                    f"must be within {GEO_FENCE_METRES:.0f}m to record a visit. "
                    f"If the address itself is wrong, select 'Address Issue' as the outcome instead."
                ),
            )

        # Every refusal is behind us: only now may a same-day handover move the
        # case to the caller (scope.sync_assignee — never at the read, because
        # the contact-hours refusal above commits its audit row).
        sync_assignee(case, agent)

        visit_num = case.visit_count + 1
        visit = Visit(
            case_id=case.id,
            agent_id=agent.id,
            check_in_latitude=req.check_in_latitude,
            check_in_longitude=req.check_in_longitude,
            check_in_time=now_utc,
            distance_from_customer_metres=round(distance_m, 1),
            geo_verified=geo_ok,
            within_contact_hours=within_hours,
            customer_met=req.customer_met,
            outcome=req.outcome,
            person_met=req.person_met,
            default_reason=req.default_reason,
            not_met_reason=req.not_met_reason,
            notes=req.notes,
            consent_given=req.consent_given,
            signature_key=req.signature_key,
            selfie_photo_key=req.selfie_photo_key,
            agent_photo_key=req.agent_photo_key,
            agent_photo_lat=req.agent_photo_lat,
            agent_photo_lon=req.agent_photo_lon,
            agent_photo_accuracy=req.agent_photo_accuracy,
            agent_photo_altitude=req.agent_photo_altitude,
            agent_photo_captured_at=_parse_dt(req.agent_photo_captured_at),
            agent_photo_sha256=req.agent_photo_sha256,
            borrower_photo_key=req.borrower_photo_key,
            borrower_photo_lat=req.borrower_photo_lat,
            borrower_photo_lon=req.borrower_photo_lon,
            borrower_photo_accuracy=req.borrower_photo_accuracy,
            borrower_photo_altitude=req.borrower_photo_altitude,
            borrower_photo_captured_at=_parse_dt(req.borrower_photo_captured_at),
            borrower_photo_sha256=req.borrower_photo_sha256,
            object_photo_key=req.object_photo_key,
            object_photo_lat=req.object_photo_lat,
            object_photo_lon=req.object_photo_lon,
            object_photo_accuracy=req.object_photo_accuracy,
            object_photo_altitude=req.object_photo_altitude,
            object_photo_captured_at=_parse_dt(req.object_photo_captured_at),
            object_photo_sha256=req.object_photo_sha256,
            device_id=req.device_id,
            agent_recording_key=req.agent_recording_key,
            borrower_recording_key=req.borrower_recording_key,
            visit_number=visit_num,
            property_type=req.property_type,
            occupancy_status=req.occupancy_status,
            vehicle_present=req.vehicle_present,
            business_running=req.business_running,
        )
        self.db.add(visit)
        case.visit_count = visit_num

        self._apply_outcome_transition(case, req, now_utc)

        agent.last_known_latitude = req.check_in_latitude
        agent.last_known_longitude = req.check_in_longitude
        agent.last_location_update = now_utc
        agent.current_month_visits += 1

        self.db.commit()
        self.db.refresh(visit)

        # 2026-09-11 — VISIT_RECORDED, declared since the first schema and
        # written by nothing. After the commit, on its own commit, so a
        # failure to record the event cannot un-record the visit.
        write_audit(
            self.db, action=AuditAction.VISIT_RECORDED, user_id=agent.user_id,
            entity_type="Visit", entity_id=visit.id,
            details={"case_id": case.id, "case_number": case.case_number,
                     "agent_id": agent.id, "outcome": str(getattr(visit.outcome, "value", visit.outcome)),
                     "customer_met": bool(visit.customer_met),
                     "geo_verified": bool(visit.geo_verified),
                     "distance_m": visit.distance_from_customer_metres},
        )
        # 2026-09-24 — the live event, same post-commit contract as the audit row.
        publish_event("visit.recorded", agent=agent, data={
            "visit_id": visit.id, "case_id": case.id, "case_number": case.case_number,
            "outcome": str(getattr(visit.outcome, "value", visit.outcome)),
            "customer_met": bool(visit.customer_met),
            "geo_verified": bool(visit.geo_verified),
            "lat": visit.check_in_latitude, "lon": visit.check_in_longitude,
        })

        # Generate AI audit report in the background — saved back to visit
        ai_visit_note = AIReportService.generate_visit_report(visit, case)
        if ai_visit_note:
            visit.ai_visit_note = ai_visit_note
            self.db.commit()

        # Send visit completion message for non-payment outcomes (payment
        # outcomes are handled by collect_payment)
        if req.outcome not in _PAYMENT_OUTCOMES and case.customer and case.customer.phone_primary:
            self._notify_visit_completed(agent, case, now_utc)

        return self._to_response(visit)

    def _audit_contact_hour_violation(self, agent: Agent, case: Case,
                                      attempted_at: datetime, req: RecordVisitRequest) -> None:
        """One CONTACT_HOUR_VIOLATION_ATTEMPT row per refused visit.

        The refusal is the point; this row is the evidence of it. It must
        never weaken the refusal, so a failure to write it is logged at
        ERROR — the same treatment notification_service gives a swallowed
        delivery failure — and the 403 still follows. No IP or user-agent:
        the service has no Request object, and inventing one to carry them is
        not worth a second code path for the sake of two nullable columns.
        """
        ist = attempted_at.astimezone(IST)
        try:
            self.db.add(AuditLog(
                id=str(uuid.uuid4()),
                created_at=attempted_at,
                user_id=agent.user_id,
                action=AuditAction.CONTACT_HOUR_VIOLATION_ATTEMPT,
                entity_type="Case",
                entity_id=case.id,
                details={
                    "agent_id": agent.id,
                    "case_number": case.case_number,
                    "attempted_at_ist": ist.isoformat(),
                    "ist_hour": ist.hour,
                    "window": f"{RBI_CONTACT_START:02d}:00-{RBI_CONTACT_END:02d}:00 IST",
                    "outcome_attempted": str(getattr(req.outcome, "value", req.outcome)),
                    "channel": "visit",
                },
                success=False,
                failure_reason="outside contact hours",
            ))
            self.db.commit()
        except Exception as exc:  # noqa: BLE001 — the refusal must still happen
            self.db.rollback()
            logger.error("visit.contact_hour_audit_failed", case_id=case.id,
                         agent_id=agent.id, error=str(exc),
                         error_type=type(exc).__name__, exc_info=True)

    def _to_response(self, visit: Visit) -> dict:
        return {
            "id": visit.id,
            "case_id": visit.case_id,
            "outcome": visit.outcome,
            "geo_verified": visit.geo_verified,
            "distance_from_customer_metres": visit.distance_from_customer_metres,
            "within_contact_hours": visit.within_contact_hours,
            "visit_number": visit.visit_number,
            "check_in_time": visit.check_in_time.isoformat(),
            "ai_visit_note": visit.ai_visit_note,
        }

    def _apply_outcome_transition(self, case: Case, req: RecordVisitRequest, now_utc: datetime) -> None:
        """Case status transitions based on the reported visit outcome."""
        outcome = req.outcome
        if outcome in (VisitOutcome.PAID_FULL,):
            case.status = CaseStatus.PAID
            case.resolved_at = now_utc

        elif outcome in (VisitOutcome.PART_PAID,):
            case.status = CaseStatus.PARTIALLY_PAID

        elif outcome in (VisitOutcome.PTP, VisitOutcome.PART_PAID_PTP):
            case.status = CaseStatus.PTP_SET

        elif outcome in (VisitOutcome.DISPUTE,):
            case.status = CaseStatus.ESCALATED
            case.is_escalated = True
            case.escalation_reason = EscalationReason.DISPUTED_AMOUNT
            case.escalated_at = now_utc
            # `agent_recording_transcript` lives on CaseVisitHistoryItem (a
            # response schema), never on RecordVisitRequest — reading it here
            # raised AttributeError and turned every DISPUTE visit into a 500.
            # getattr keeps the original intent if the request ever gains the
            # field; notes is what the client actually sends today, and
            # RecordVisitPage already folds the customer statement into it.
            escalation_detail = getattr(req, "agent_recording_transcript", None) or req.notes
            if escalation_detail:
                case.escalation_notes = escalation_detail

        elif outcome in (VisitOutcome.RTP,):
            case.status = CaseStatus.ESCALATED
            case.is_escalated = True
            case.escalation_reason = EscalationReason.CUSTOMER_HOSTILE
            case.escalated_at = now_utc

        elif outcome in (VisitOutcome.ADDRESS_ISSUE,):
            case.status = CaseStatus.ESCALATED
            case.is_escalated = True
            case.escalation_reason = EscalationReason.OTHER
            case.escalated_at = now_utc

        elif outcome in (VisitOutcome.DECEASED,):
            case.status = CaseStatus.CLOSED
            case.resolved_at = now_utc
            case.resolution_notes = "Customer deceased — do not contact"
            case.customer.do_not_contact = True
            case.customer.tags = list(set(case.customer.tags or []) | {"DECEASED"})

        elif case.status == CaseStatus.ASSIGNED:
            case.status = CaseStatus.IN_PROGRESS

    def _notify_visit_completed(self, agent: Agent, case: Case, now_utc: datetime) -> None:
        """Best-effort SMS/WhatsApp telling the customer a visit happened with no payment collected."""
        loan = case.loan
        masked_acct = "XXXX" + loan.loan_account_number[-4:] if loan else "XXXXXXXX"
        outstanding = case.target_amount - case.collected_amount
        visit_date = now_utc.strftime("%d %b %Y")
        bn = brand_for(self.db, case=case).bank_name
        e164 = "+" + NotificationService.normalize_phone(case.customer.phone_primary)
        sms_body = (
            f"Dear {case.customer.full_name}, {bn}'s field agent {agent.user.full_name} "
            f"completed a visit on {visit_date} for loan {masked_acct}. "
            f"No payment collected. Outstanding: Rs.{outstanding:,.0f}. - {bn}"
        )
        wa_body = (
            f"*Visit Completed – {bn}*\n\n"
            f"Dear {case.customer.full_name},\n\n"
            f"Agent *{agent.user.full_name}* visited on {visit_date}.\n"
            f"Loan Account: {masked_acct}\n"
            f"Outstanding: Rs.{outstanding:,.0f}\n"
            f"Payment: none collected.\n\n"
            f"Please contact us to resolve your dues.\n– {bn}"
        )
        NotificationService.send_twilio(e164, sms_body, wa_body, db=self.db, case_id=case.id)
