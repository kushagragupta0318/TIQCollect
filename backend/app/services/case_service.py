# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-07-13 — New file. reoptimize_beat() moved out of the router in
#   endpoints/agent.py and extended: blocked-case filtering
#   (call_log.blocked_until_date), time-window building, and urgent-override
#   selection via _build_windows()/_latest_call_log_map() below. First cut of
#   the router→service split for case/beat logic. Full detail + why:
#   /changelog.md
# 2026-07-22 — Added list_cases(), ranked_cases(), case_detail(),
#   handover_case(), flag_customer() — the remaining pieces
#   final_changes.md §7.1 listed as "not started" for this file. Pure
#   extraction from endpoints/agent.py, same recipe as reoptimize_beat:
#   logic unchanged, only relocated. `visit-strategy` (also listed in that
#   §7.1 row) is deliberately NOT included in this pass — it's a large,
#   separate AI-generation block; left inline in agent.py, tracked as its
#   own follow-up rather than bundled in here. Full detail: /changelog.md
# 2026-09-24 (A03, coordinator HIGH) — list_cases, ranked_cases and
#   reoptimize_beat read the agent's LATEST beat of any date and loaded every
#   id on it unchecked, so a case from a stale beat, since given to a
#   teammate, was listed with the borrower's phone and address while its
#   detail page answered 404, and reoptimize wrote onto that stale beat. All
#   three now go through scope.today_beat_cases (today's IST beat, agent's
#   agency only) and date their "today" by scope.access_day(), not
#   endpoints/agent._effective_day (the container's UTC date.today()).
# ───────────────────────────────────────────────────────────────────────────
"""
Business logic for agent-facing case/beat operations.

Router functions in endpoints/agent.py stay thin: fetch the current agent,
delegate to CaseService, return the result. This is the first cut of the
router/service split — more case-related endpoints (listing, ranking,
handover) belong here over time, same pattern.
"""
from __future__ import annotations

from datetime import datetime, timezone

import structlog
from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.core import llm
from app.core.prompting import DATA_RULE, fence
from app.core.config import settings
from app.core.routing import optimize_route
from app.models.agent import Agent
from app.models.call_log import CallLog
from app.models.case import RESOLVED_STATUSES, Case, CaseStatus
from app.models.customer import Customer
from app.models.ptp import PTP, PTPStatus
from app.models.visit import Visit
from app.core.errors import AppException, ErrorCode
from app.services.scope import _not_found, access_day, agent_case_or_404, today_beat_cases
from app.services.media_service import MediaService

logger = structlog.get_logger()

_URGENT_WINDOW_SECONDS = 45 * 60

# _PRIORITY_ORDER (CRITICAL/HIGH/MEDIUM/LOW -> 0..3) lived here and ordered
# list_cases until 2026-08-27, when the visit-priority score replaced it. Deleted
# on 2026-08-28 rather than left as a spare: Case.priority is frozen at case
# creation and never recomputed, so a sort built on it silently reintroduces the
# staleness the score exists to fix. app/ml/visit_priority.py owns the order now,
# and visit_priority_service.sort_key is the single tie-break definition.

# No repayment score is shown for these — see _repayment_block. Imported rather
# than restated; models/case.py owns it, shared with visit_priority_service.
_RESOLVED_STATUSES = RESOLVED_STATUSES


class CaseService:
    def __init__(self, db: Session):
        self.db = db

    def reoptimize_beat(self, agent: Agent, lat: float, lon: float) -> dict:
        """Re-optimize the active beat from the agent's current GPS position.

        Workflow:
          1. Load all cases in the beat.
          2. Separate: done (visited/PAID/CLOSED/WRITTEN_OFF), blocked
             (customer asked to come back on a future date —
             call_log.blocked_until_date), pending (everyone else, routed today).
          3. For pending cases with a customer-given time window
             (call_log.available_from/until), turn it into an OR-Tools time
             window — unless the window closes within _URGENT_WINDOW_SECONDS,
             in which case the case is forced as the very next stop instead
             of a soft constraint (a tight window can make the whole solve
             infeasible; forcing it next never does).
          4. Fetch real road-time matrix via OSRM Table API.
          5. Solve TSP/VRPTW with OR-Tools (falls back to nearest-neighbour
             on failure).
          6. Persist the new order back to beat.ordered_case_ids.
        """
        # Deferred import breaks the agent.py <-> case_service.py circular
        # import (agent.py imports CaseService at module load time; these
        # two helpers are only needed once this method actually runs, by
        # which point agent.py is already fully loaded).
        from app.api.v1.endpoints.agent import _visited_today

        beat, all_cases = today_beat_cases(self.db, agent, options=(joinedload(Case.customer),))
        if not beat or not all_cases:
            raise HTTPException(status_code=404, detail="No active beat found")
        # Ids on the beat this agent may not act on are not re-ordered, and not
        # dropped either: they keep their place at the end, untouched.
        visible_ids = {c.id for c in all_cases}
        untouched = [i for i in (beat.ordered_case_ids or []) if i not in visible_ids]

        eff_day = access_day()
        # Cases that already have a visit recorded today should not be re-ordered —
        # the agent has physically been there regardless of their current status.
        visited_today = _visited_today(agent.id, eff_day, self.db)
        _DONE_STATUS = {CaseStatus.PAID, CaseStatus.CLOSED, CaseStatus.WRITTEN_OFF}

        candidates = [c for c in all_cases if c.id not in visited_today and c.status not in _DONE_STATUS]
        done = [c for c in all_cases if c.id in visited_today or c.status in _DONE_STATUS]

        call_map = self._latest_call_log_map([c.id for c in candidates])

        # Customer asked to come back on a future date — drop from today's route entirely.
        blocked = [
            c for c in candidates
            if (call := call_map.get(c.id)) and call.blocked_until_date and call.blocked_until_date > eff_day
        ]
        blocked_ids = {c.id for c in blocked}
        pending = [c for c in candidates if c.id not in blocked_ids]

        if not pending:
            new_order = [c.id for c in done] + [c.id for c in blocked] + untouched
            beat.ordered_case_ids = new_order
            self.db.commit()
            return {
                "ordered_case_ids": new_order,
                "optimized": False,
                "message": "No routable cases — remaining ones are done or blocked until a future date",
            }

        coords = [(c.customer.latitude, c.customer.longitude) for c in pending]
        time_windows, forced_next = self._build_windows(pending, call_map)

        has_windows = any(w is not None for w in time_windows)
        order = optimize_route(
            coords,
            start_lat=lat,
            start_lon=lon,
            time_windows=time_windows if has_windows else None,
            forced_next=forced_next,
        )

        ordered_pending = [pending[i] for i in order if i < len(pending)]
        new_order = [c.id for c in ordered_pending] + [c.id for c in done] + [c.id for c in blocked] + untouched

        beat.ordered_case_ids = new_order
        # Update agent's last known location while we're at it
        agent.last_known_latitude = lat
        agent.last_known_longitude = lon
        self.db.commit()

        return {
            "ordered_case_ids": new_order,
            "optimized": True,
            "pending_count": len(pending),
            "done_count": len(done),
            "blocked_count": len(blocked),
            "urgent_case_id": pending[forced_next].id if forced_next is not None else None,
        }

    def _latest_call_log_map(self, case_ids: list[str]) -> dict:
        """Latest call log per case id — drives blocking + time windows above."""
        if not case_ids:
            return {}
        subq = (
            self.db.query(CallLog.case_id, func.max(CallLog.called_at).label("max_at"))
            .filter(CallLog.case_id.in_(case_ids))
            .group_by(CallLog.case_id)
            .subquery()
        )
        return {
            cl.case_id: cl for cl in
            self.db.query(CallLog)
            .join(subq, (CallLog.case_id == subq.c.case_id) & (CallLog.called_at == subq.c.max_at))
            .all()
        }

    def _build_windows(
        self, pending: list[Case], call_map: dict
    ) -> tuple[list["tuple[int, int] | None"], int | None]:
        now = datetime.now(timezone.utc)

        # First pass: collect every case with a still-live deadline.
        deadlines: list[tuple[int, datetime]] = []
        for i, c in enumerate(pending):
            call = call_map.get(c.id)
            if not call or not call.available_until:
                continue
            latest = call.available_until
            if latest <= now:
                continue  # window already passed; treat as unconstrained rather than fail the solve
            deadlines.append((i, latest))

        # Only the single soonest deadline within the urgent threshold gets
        # forced next — a hard override, guaranteed to be honoured. Only one
        # case can occupy that slot; it must be the most time-critical one.
        urgent = [(i, latest) for i, latest in deadlines
                  if (latest - now).total_seconds() <= _URGENT_WINDOW_SECONDS]
        forced_next = min(urgent, key=lambda x: x[1])[0] if urgent else None

        # Every other case with a deadline — including ones that were
        # "urgent enough" but lost the forced-next slot to something more
        # time-critical — still gets a real soft time-window constraint
        # instead of being silently dropped back to unconstrained.
        time_windows: list = [None] * len(pending)
        for i, latest in deadlines:
            if i == forced_next:
                continue
            call = call_map[pending[i].id]
            latest_s = int((latest - now).total_seconds())
            earliest_s = 0
            if call.available_from and call.available_from > now:
                earliest_s = int((call.available_from - now).total_seconds())
            time_windows[i] = (earliest_s, latest_s)

        return time_windows, forced_next

    # -----------------------------------------------------------------
    # GET /agent/cases
    # -----------------------------------------------------------------
    def list_cases(self, agent: Agent) -> list[dict]:
        """Return today's beat cases — pending first (by visit priority), done last."""
        from app.api.v1.endpoints.agent import _visited_today, _format_case
        from app.services.visit_priority_service import score_cases, sort_key

        _beat, cases = today_beat_cases(self.db, agent, options=(joinedload(Case.customer), joinedload(Case.loan)))
        if not cases:
            return []

        eff_day = access_day()
        visited_today_ids = _visited_today(agent.id, eff_day, self.db)

        def _is_done(c: Case) -> bool:
            return (
                c.id in visited_today_ids
                or c.status in _RESOLVED_STATUSES
                or c.status == CaseStatus.PAID
                or (c.target_amount > 0 and c.collected_amount >= c.target_amount)
            )

        pending = [c for c in cases if not _is_done(c)]
        done = [c for c in cases if _is_done(c)]

        # 2026-08-27 — ordered by VISIT PRIORITY, replacing the Case.priority
        # band sort. Case.priority is frozen at case creation and knows nothing
        # about effort already spent or what is actually recoverable; see
        # ml/visit_priority.py. Loans are joinedload-ed above, so the score's
        # value component has the balance it needs.
        #
        # Scored for pending AND done: the agent's card shows the score either
        # way, and a completed case that silently lost its reasoning reads as
        # though it never had any.
        scored = score_cases(self.db, cases, today=eff_day)
        pending.sort(key=sort_key(scored))

        result = []
        for c in pending + done:
            formatted = _format_case(c)
            formatted["is_visited_today"] = c.id in visited_today_ids
            formatted["visit_priority"] = scored.get(c.id)
            result.append(formatted)
        return result

    # -----------------------------------------------------------------
    # GET /agent/cases/ranked
    # -----------------------------------------------------------------
    def ranked_cases(self, agent: Agent) -> list[dict]:
        """Return today's beat cases ranked by AI priority signals.

        Scoring: call log intel + last visit outcome + PTP + customer flags.
        One GPT-4o-mini call at the end generates a one-line reason per top-8 case.
        """
        import json as _json
        from datetime import datetime as _dt
        from app.api.v1.endpoints.agent import _visited_today, _format_case

        agent_row = agent
        _beat, cases = today_beat_cases(self.db, agent_row, options=(joinedload(Case.customer), joinedload(Case.loan)))
        if not cases:
            return []

        case_ids: list[str] = [c.id for c in cases]
        eff_day = access_day()
        visited_today_ids = _visited_today(agent_row.id, eff_day, self.db)

        # Latest call log per case (single bulk query)
        _call_subq = (
            self.db.query(CallLog.case_id, func.max(CallLog.called_at).label("max_at"))
            .filter(CallLog.case_id.in_(case_ids))
            .group_by(CallLog.case_id)
            .subquery()
        )
        call_map: dict = {
            cl.case_id: cl for cl in
            self.db.query(CallLog)
            .join(_call_subq, (CallLog.case_id == _call_subq.c.case_id) & (CallLog.called_at == _call_subq.c.max_at))
            .all()
        }

        # Latest visit per case (single bulk query)
        _visit_subq = (
            self.db.query(Visit.case_id, func.max(Visit.check_in_time).label("max_at"))
            .filter(Visit.case_id.in_(case_ids))
            .group_by(Visit.case_id)
            .subquery()
        )
        visit_map: dict = {
            v.case_id: v for v in
            self.db.query(Visit)
            .join(_visit_subq, (Visit.case_id == _visit_subq.c.case_id) & (Visit.check_in_time == _visit_subq.c.max_at))
            .all()
        }

        # PTPs due on the effective day
        ptp_today_ids: set[str] = set(
            row[0] for row in
            self.db.query(PTP.case_id)
            .filter(PTP.case_id.in_(case_ids), PTP.committed_date == eff_day, PTP.status == PTPStatus.ACTIVE)
            .all()
        )

        now_hour = _dt.now().hour
        _PRIORITY_BONUS = {"CRITICAL": 8, "HIGH": 5, "MEDIUM": 2, "LOW": 0}

        def _score_case(c: Case):
            call = call_map.get(c.id)
            visit = visit_map.get(c.id)
            cust = c.customer

            if cust.do_not_contact:
                return -9999, "DO NOT VISIT", "red"

            if call and call.blocked_until_date and call.blocked_until_date > eff_day:
                return -999, f"BLOCKED · {call.blocked_until_date.strftime('%d %b')}", "red"

            score = 0
            badge, badge_color = "", "grey"

            if c.id in ptp_today_ids:
                score += 60
                badge, badge_color = "PTP TODAY", "red"

            if call and call.verbal_payment_date and call.verbal_payment_date == eff_day:
                score += 50
                if not badge:
                    badge, badge_color = "PAYMENT TODAY", "green"

            if call and call.payment_intent_signalled:
                score += 45
                if not badge:
                    badge, badge_color = "HIGH READINESS", "green"

            if call and call.best_time_to_visit:
                bttv = call.best_time_to_visit.lower()
                matched = (
                    ("before 10" in bttv and now_hour < 10)
                    or ("morning" in bttv and now_hour < 12)
                    or ("afternoon" in bttv and 12 <= now_hour < 16)
                    or (any(w in bttv for w in ("evening", "after 5", "after 6", "6 pm", "7 pm")) and now_hour >= 17)
                )
                if matched:
                    score += 35
                    if not badge:
                        badge, badge_color = "BEST TIME NOW", "blue"

            if visit and visit.outcome == "BROKEN_PTP":
                score += 25
                if not badge:
                    badge, badge_color = "BROKEN PTP", "orange"

            if c.loan and c.loan.npa_flag:
                score += 10

            score += _PRIORITY_BONUS.get(c.priority, 0)

            if cust.is_hostile:
                score -= 15

            return score, badge, badge_color

        # Build scored list: Active Pending (AI Score Desc) -> Done Visited -> Blocked / DNC Cases
        pending_active = []
        done_scored = []
        blocked_scored = []

        for c in cases:
            score, badge, badge_color = _score_case(c)
            row = _format_case(c)
            row["is_visited_today"] = c.id in visited_today_ids
            row["ptp_due_today"] = c.id in ptp_today_ids
            row["rank_score"] = score
            row["rank_badge"] = badge
            row["rank_badge_color"] = badge_color
            row["rank_reason"] = ""

            is_blocked = (score <= -900) or getattr(c.customer, "do_not_contact", False)
            row["is_blocked"] = is_blocked

            is_done = (
                row["is_visited_today"]
                or (c.status in _RESOLVED_STATUSES)
                or (c.status == CaseStatus.PAID)
                or (c.target_amount > 0 and c.collected_amount >= c.target_amount)
            )

            if is_blocked:
                blocked_scored.append(row)
            elif is_done:
                done_scored.append(row)
            else:
                pending_active.append(row)

        pending_active.sort(key=lambda x: -x["rank_score"])
        for i, row in enumerate(pending_active, 1):
            row["rank"] = i

        done_scored.sort(key=lambda x: -x["rank_score"])
        for i, row in enumerate(done_scored, len(pending_active) + 1):
            row["rank"] = i

        blocked_scored.sort(key=lambda x: -x["rank_score"])
        for i, row in enumerate(blocked_scored, len(pending_active) + len(done_scored) + 1):
            row["rank"] = i

        scored = pending_active + done_scored + blocked_scored

        # Single LLM call — one-line reason for top 8 non-blocked cases.
        # 2026-08-19 — routed through core/llm.py. These reasons are cosmetic, so
        # a failure must never cost the agent their ranked list: the list is
        # returned either way and the reasons are simply absent.
        if True:
            try:
                eligible = [r for r in pending_active if r["rank_score"] > -999][:8]
                if eligible:
                    lines = []
                    notes: list[str] = []      # free text, fenced (core/prompting)
                    for i, r in enumerate(eligible, 1):
                        call = call_map.get(r["id"])
                        visit = visit_map.get(r["id"])
                        signals = []
                        if r.get("ptp_due_today"):
                            signals.append("PTP due today")
                        if call:
                            if call.payment_intent_signalled:
                                signals.append("payment intent confirmed")
                            if call.verbal_payment_date == eff_day:
                                signals.append("verbal pay date today")
                            if call.best_time_to_visit:
                                notes.append(fence(f"best time to visit, case {i}",
                                                   call.best_time_to_visit, limit=120))
                            if call.customer_response_notes:
                                # The agent's own words about this borrower: quoted
                                # below the list, not inlined as another "signal".
                                notes.append(fence(f"call note for case {i}",
                                                   call.customer_response_notes, limit=200))
                        if visit:
                            signals.append(f"last visit: {visit.outcome}")
                        dpd = r.get("loan", {}).get("dpd", 0)
                        lines.append(f"{i}. {r['customer']['full_name']} | DPD {dpd} | {', '.join(signals) or 'standard follow-up'}")

                    result = llm.complete(
                        "Generate a one-line visit priority reason (max 10 words, specific, actionable) for each case. "
                        "Return ONLY JSON mapping number to reason.\n\n" + DATA_RULE +
                        "\n\nCases:\n" + "\n".join(lines) +
                        ("\n\n" + "\n".join(notes) if notes else "") +
                        '\n\nFormat: {"1": "reason", "2": "reason", ...}',
                        purpose="case_ranking", json_mode=True, bank_id=agent.bank_id,
                        max_tokens=900, temperature=0.3,
                        names=[r["customer"]["full_name"] for r in eligible],
                    )
                    if result.ai_generated:
                        for i, r in enumerate(eligible, 1):
                            r["rank_reason"] = result.data.get(str(i), "")
            except Exception:
                pass

        return scored

    # -----------------------------------------------------------------
    # GET /agent/cases/{case_id}
    # -----------------------------------------------------------------
    def _repayment_block(self, case: Case) -> dict | None:
        """The repayment likelihood for this case's loan, with its reasons.

        Returns None rather than raising if anything is missing or the scorer
        misbehaves: an agent standing at a door must still get their case
        detail. A missing score degrades the page; an exception loses it.
        """
        if case.loan is None:
            return None

        # Nothing to decide about a case that is already settled. The score
        # answers "how should I approach this visit"; on a PAID, CLOSED or
        # WRITTEN_OFF case there is no visit, and showing "63/100 — Uncertain"
        # beside "100% collected · Case resolved" reads as the product
        # contradicting itself. The loan is still scored for the nightly
        # snapshot — that is about the borrower, not this case — but the agent
        # is not asked to act on it.
        #
        # ESCALATED is deliberately NOT here: it is still open, still visitable,
        # and the manager reviewing it has more use for the reasoning, not less.
        if case.status in _RESOLVED_STATUSES:
            return None

        try:
            from datetime import date as _date

            from app.services.repayment_service import RepaymentService

            svc = RepaymentService(self.db)
            outcome = svc.score_loan(
                case.loan, case.customer, _date.today(),
                cases=[case], visits=list(case.visits or []),
                ptps=list(case.ptps or []), payments=list(case.payments or []),
            )
            return svc.to_payload(outcome)
        except Exception as exc:                      # noqa: BLE001
            logger.warning("repayment_block.failed", case_id=case.id, error=str(exc))
            return None

    def case_detail(self, agent: Agent, case_id: str) -> dict:
        from app.api.v1.endpoints.agent import _format_case
        from app.services.visit_priority_service import score_cases

        case = (
            self.db.query(Case)
            .options(
                joinedload(Case.customer),
                joinedload(Case.loan),
                joinedload(Case.visits),
                joinedload(Case.payments),
                joinedload(Case.ptps),
            )
            .filter(Case.id == case_id)
            .first()
        )
        # 2026-09-24 (A03): access through the one rule, AFTER the eager load
        # above (a refused case is the same 404 as a missing one).
        if not case:
            raise AppException(404, ErrorCode.NOT_FOUND, "Not found")
        agent_case_or_404(self.db, agent, case_id)

        base = _format_case(case)

        # Keep the detail badge on the same live score and the same day as the
        # agent's case list. Case.priority remains only for backwards-compatible
        # storage; it is no longer decision-support data for the UI.
        # 2026-09-28 (audit LOW): dated by scope.access_day() (IST), as the list
        # is since A03 — it read endpoints/agent._effective_day, so list and
        # detail could score one case against two different days.
        base["visit_priority"] = score_cases(
            self.db, [case], today=access_day()
        ).get(case.id)

        # Repayment likelihood, computed live for this one loan rather than read
        # from the nightly snapshot. The snapshot is a point-in-time record for
        # training; what an agent needs at the doorstep is the CURRENT picture,
        # including the visit they logged an hour ago. Scoring one loan is cheap;
        # this is deliberately not done on the case LIST, which would score the
        # whole book on every page load.
        #
        # Decision support only. It carries no verdict and gates nothing —
        # eligibility lives in ml/eligibility.py. See repayment_service.py.
        base["repayment"] = self._repayment_block(case)

        # Resolve agent names for visit history (may include prior agents).
        # One IN query for the set, not one per agent — a reallocated case's
        # history can span several agents, and this ran a query for each.
        visit_agent_ids = {v.agent_id for v in case.visits}
        agent_names: dict[str, str] = {}
        if visit_agent_ids:
            for ag_row in (self.db.query(Agent)
                           .options(joinedload(Agent.user))
                           .filter(Agent.id.in_(visit_agent_ids))
                           .all()):
                if ag_row.user:
                    agent_names[ag_row.id] = ag_row.user.full_name

        base["visits"] = [
            {
                "id": v.id,
                "visit_number": v.visit_number,
                "outcome": v.outcome,
                "customer_met": v.customer_met,
                "person_met": v.person_met,
                "default_reason": v.default_reason,
                "not_met_reason": v.not_met_reason,
                "notes": v.notes,
                "consent_given": v.consent_given,
                "ai_visit_note": v.ai_visit_note,
                "geo_verified": v.geo_verified,
                "within_contact_hours": v.within_contact_hours,
                "distance_from_customer_metres": v.distance_from_customer_metres,
                "check_in_time": v.check_in_time.isoformat(),
                "agent_name": agent_names.get(v.agent_id, "Unknown Agent"),
                "property_type": v.property_type,
                "occupancy_status": v.occupancy_status,
                "vehicle_present": v.vehicle_present,
                "business_running": v.business_running,
                "agent_recording_transcript": v.agent_recording_transcript,
                "borrower_recording_transcript": v.borrower_recording_transcript,
                # N1: what the agent typed and collected, which used to be dropped on submit
                "escalation_notes": v.escalation_notes,
                "witness_present": v.witness_present,
                "witness_name": v.witness_name,
                "documents": MediaService.document_entries(v),
            }
            for v in sorted(case.visits, key=lambda x: x.check_in_time)
        ]

        # Case photos — latest per type extracted from visits, with presigned GET URLs
        base["photos"] = MediaService.photos_from_visits(case.visits)
        base["payments"] = [
            {
                "id": p.id,
                "receipt_number": p.receipt_number,
                "amount": p.amount,
                "mode": p.mode,
                "status": p.status,
                "payment_date": p.payment_date.isoformat(),
            }
            for p in case.payments
        ]
        base["ptps"] = [
            {
                "id": ptp.id,
                "committed_amount": ptp.committed_amount,
                "committed_date": ptp.committed_date.isoformat(),
                "follow_up_date": ptp.follow_up_date.isoformat() if ptp.follow_up_date else None,
                "status": ptp.status,
                "customer_reason": ptp.customer_reason,
            }
            for ptp in case.ptps
        ]
        return base

    # -----------------------------------------------------------------
    # POST /agent/cases/{case_id}/handover
    # -----------------------------------------------------------------
    def handover_case(self, agent: Agent, case_id: str, req) -> dict:
        # Only the ASSIGNEE hands a case over (stricter than scope's read rule:
        # a today's-beat grant must not let a caller return a teammate's case
        # to the pool). Refusal is the uniform 404 (was a bespoke body that
        # said "not assigned to you" — i.e. "it exists"), 2026-09-24.
        case = agent_case_or_404(self.db, agent, case_id)
        if case.agent_id != agent.id:
            raise _not_found()
        case.handover_notes = req.notes
        if req.return_to_pool:
            case.status = CaseStatus.UNASSIGNED
            case.agent_id = None
        self.db.commit()
        return {"success": True, "returned_to_pool": req.return_to_pool}

    # -----------------------------------------------------------------
    # PATCH /agent/customers/{customer_id}/flag
    # -----------------------------------------------------------------
    def flag_customer(self, agent: Agent, customer_id: str, req) -> dict:
        # The agent may flag a customer only through a case scope grants them:
        # assigned, or on today's beat, inside their agency. 2026-09-24: this
        # was a strict assignee copy of the rule answering 403 "no active case"
        # for a real customer and 404 for a missing one — a probe for which
        # customer ids exist. Now one uniform 404 for every refusal.
        _beat, today = today_beat_cases(self.db, agent)
        today_ids = {c.id for c in today}
        grants = (self.db.query(Case.id, Case.agent_id)
                  .filter(Case.customer_id == customer_id, Case.agency_id == agent.agency_id).all())
        if not any(aid == agent.id or cid in today_ids for cid, aid in grants):
            raise _not_found()
        customer = self.db.query(Customer).filter(Customer.id == customer_id).first()
        if not customer:
            raise _not_found()
        if req.is_hostile is not None:
            customer.is_hostile = req.is_hostile
        if req.do_not_contact is not None:
            customer.do_not_contact = req.do_not_contact
        self.db.commit()
        return {"success": True, "is_hostile": customer.is_hostile, "do_not_contact": customer.do_not_contact}
