# ─── CHANGELOG (standalone plan) ────────────────────────────────────────────
# 2026-09-24 (A03 "close the three leaks", coordinator audit) — NEW. The one
#   definition of "a case this agent may act on".
#
#   FOUR copies of the rule existed — endpoints/agent.py
#   `_get_accessible_case_or_404`, case_service.case_detail,
#   payment_service._get_accessible_case, visit_service.record_visit — plus a
#   stricter fifth spelling (`Case.agent_id == agent.id`) in media_service,
#   otp_service and voice_service. The four loose copies granted:
#     (a) ANY case with agent_id IS NULL, in ANY tenant;
#     (b) any case on ANY beat the agent ever had, however old;
#     (c) any case of a peer agent under the same manager;
#   and a refusal was a 403 while a missing case was a 404, so the endpoint
#   answered "does this case exist?" for every case in the database. Worse,
#   visit_service and payment_service then RE-ASSIGNED the case to the caller
#   (`case.agent_id = agent.id`), so recording a visit took the case over.
#
#   THE RULE NOW:
#     - the case is in the agent's own AGENCY (the tenant wall, absolute), and
#     - it is assigned to the agent, OR it is on the agent's beat for TODAY
#       (IST calendar, access_day()) (a same-day handover the case row has
#       not caught up with — the only case where the stale assignee is
#       re-synced to the caller, and only within the agency).
#   Everything else — missing, malformed, another agency's, a peer's,
#   yesterday's beat — is the SAME 404 with the same body (core/ids.py).
# ────────────────────────────────────────────────────────────────────────────
from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.core.errors import AppException, ErrorCode
from app.core.ids import parse_uuid

NOT_FOUND_MESSAGE = "Not found"


def _not_found() -> AppException:
    return AppException(404, ErrorCode.NOT_FOUND, NOT_FOUND_MESSAGE)


def access_day() -> date:
    """The day a beat grants access on: TODAY on the IST calendar, through
    the one definition of it (leave_service.leave_today). Deliberately NOT
    the agent screens' "latest day a plan existed" (endpoints/agent.py
    `_effective_day`), which is a display convenience for a paused demo book
    and would let yesterday's beat open cases today (coordinator, CLAUDE.md
    issue 13)."""
    from app.services.leave_service import leave_today
    return leave_today()


def agent_case_or_404(db: Session, agent, case_id, *, options=(), sync_assignee: bool = False):
    """The case, if `agent` may act on it; otherwise the uniform 404.

    `sync_assignee=True` (write paths: record a visit, collect a payment) moves
    a stale assignee to the caller ONLY when the grant came from today's beat.
    """
    from app.models.beat import Beat
    from app.models.case import Case

    cid = parse_uuid(case_id)
    if cid is None or agent is None:
        raise _not_found()
    q = db.query(Case)
    if options:
        q = q.options(*options)
    case = q.filter(Case.id == cid, Case.agency_id == agent.agency_id).first()
    if case is None:
        raise _not_found()
    if case.agent_id == agent.id:
        return case
    beat = (db.query(Beat)
            .filter(Beat.agent_id == agent.id, Beat.beat_date == access_day()).first())
    if beat is not None and cid in (beat.ordered_case_ids or []):
        if sync_assignee:
            case.agent_id = agent.id
        return case
    raise _not_found()
