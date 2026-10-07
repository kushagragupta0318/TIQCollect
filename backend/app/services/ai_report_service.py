# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-07-14 (later) — New file. generate_visit_report() moved out of
#   endpoints/agent.py (_generate_visit_report) — a direct extraction, logic
#   unchanged. Purely visit-scoped: only ever called from
#   VisitService.record_visit. Full detail + why: /changelog.md
# ───────────────────────────────────────────────────────────────────────────
"""
AI-generated post-visit audit report (100-150 word summary for
manager/command-centre), via OpenAI.
"""
from __future__ import annotations

from app.core import llm
from app.core.prompting import DATA_RULE, fence
from app.models.case import Case
from app.models.visit import Visit


class AIReportService:
    @staticmethod
    def generate_visit_report(visit: Visit, case: Case) -> str | None:
        """A 100-150 word audit report for this visit, or None.

        2026-08-19 — routed through core/llm.py. Returning None on failure is
        unchanged, but the failure is now classified and logged instead of being
        swallowed by a bare except.
        """
        try:
            outcome_labels = {
                "PAID_FULL": "Full payment collected",
                "PART_PAID": "Partial payment collected",
                "PTP": "Promise to Pay recorded",
                "PART_PAID_PTP": "Partial payment + PTP recorded",
                "BROKEN_PTP": "PTP broken — customer did not honour promise",
                "RTP": "Customer refused to pay",
                "DISPUTE": "Customer disputes loan / amount",
                "NOT_AVAILABLE": "Customer not present at address",
                "ADDRESS_ISSUE": "Address issue / customer shifted",
                "DECEASED": "Customer deceased",
                "REVISIT": "Revisit required",
            }
            # Facts the product itself wrote go in ctx_parts; anything a PERSON
            # typed or said goes in `quoted`, fenced (core/prompting).
            quoted: list[str] = []
            ctx_parts = [
                f"Agent visit #{visit.visit_number} to collect overdue loan.",
                f"Customer: {case.customer.full_name} | Loan: {case.loan.loan_type.replace('_',' ')} with {case.loan.bank_name} | DPD: {case.loan.dpd} days | Bucket: {case.loan.dpd_bucket}.",
                f"Visit date: {visit.check_in_time.strftime('%d %b %Y %I:%M %p')} IST.",
                f"Outcome: {outcome_labels.get(visit.outcome, visit.outcome)}.",
                f"Customer met: {'Yes' if visit.customer_met else 'No'}.",
            ]
            if visit.person_met:
                ctx_parts.append(f"Person met: {visit.person_met.replace('_',' ')}.")
            if visit.default_reason:
                ctx_parts.append(f"Default reason given: {visit.default_reason.replace('_',' ')}.")
            if visit.not_met_reason:
                ctx_parts.append(f"Not met reason: {visit.not_met_reason.replace('_',' ')}.")
            if visit.notes:
                quoted.append(fence("agent field notes", visit.notes))
            if visit.geo_verified:
                ctx_parts.append("GPS geo-fence verified (agent was within 100m of customer address).")
            if not visit.within_contact_hours:
                ctx_parts.append("Visit was outside RBI-mandated contact hours (8 AM–7 PM).")
            if visit.property_type:
                ctx_parts.append(f"Property type observed: {visit.property_type}.")
            if visit.occupancy_status:
                ctx_parts.append(f"Occupancy status: {visit.occupancy_status}.")
            if visit.vehicle_present is not None:
                ctx_parts.append(f"Vehicle present at premises: {'Yes' if visit.vehicle_present else 'No'}.")
            if visit.business_running is not None:
                ctx_parts.append(f"Business/commerce activity observed: {'Yes' if visit.business_running else 'No'}.")
            if visit.agent_recording_transcript:
                # A doorstep recording: the agent's words and the borrower's.
                quoted.append(fence("visit recording transcript", visit.agent_recording_transcript))
            if visit.ai_visit_note:
                # An earlier model's output, fed back in. Fenced too, or an
                # injection that once landed keeps arriving as instructions.
                quoted.append(fence("earlier ai visit note", visit.ai_visit_note))

            # Document uploads
            docs = []
            if visit.agent_photo_key:  docs.append("agent selfie")
            if visit.borrower_photo_key: docs.append("borrower photo")
            if visit.object_photo_key: docs.append("vehicle/asset photo")
            if visit.agent_recording_key: docs.append("agent audio recording")
            if visit.borrower_recording_key: docs.append("borrower audio recording")
            if visit.selfie_photo_key: docs.append("check-in selfie")
            if docs:
                ctx_parts.append(f"Documents uploaded: {', '.join(docs)}.")
            else:
                ctx_parts.append("No documents uploaded for this visit.")

            # Escalation
            if case.is_escalated:
                reason = case.escalation_reason.replace("_", " ") if case.escalation_reason else "unspecified"
                ctx_parts.append(f"Case has been ESCALATED — reason: {reason}.")
                if case.escalation_notes:
                    quoted.append(fence("escalation details", case.escalation_notes))

            context = " ".join(ctx_parts)
            prompt = (
                f"You are a collections audit system. Write a professional, concise field visit report "
                f"in 100-150 words based on the data below. Use third-person, past tense. "
                f"Cover every relevant detail: outcome, who was met, payments/PTP if any, escalation if raised, "
                f"field observations (property/vehicle/business), documents uploaded, and a brief next-step recommendation. "
                f"Exclude irrelevant or redundant details. Do NOT use headings or bullet points — "
                f"write a single continuous paragraph.\n\n{DATA_RULE}\n\nData: {context}"
                + ("\n\n" + "\n".join(quoted) if quoted else "")
            )
            result = llm.complete(
                prompt, purpose="visit_report", max_tokens=250, temperature=0.4,
                names=[case.customer.full_name], bank_id=case.bank_id,
            )
            return result.text if result.ai_generated and result.text else None
        except Exception:
            return None