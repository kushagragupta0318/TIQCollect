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


def agents_in_scope(db: Session, principal):
    """A Query of the agents `principal` (a User) may see. FROZEN INTERFACE
    (P1 split, 2026-09-24): ce builds A04's per-agency planner pools on it.

        FIELD_AGENT      -> themself
        AGENCY_MANAGER   -> the agents they manage (manager_user_id), inside their agency
        AGENCY_ADMIN     -> every agent of their agency
        BANK_* / SERVICE -> every agent of their bank's agencies
        PLATFORM_ADMIN   -> everyone
        anything else    -> nothing
    (BANK_ANALYST region limits arrive with A01/A02's capability layer.)"""
    from sqlalchemy import false
    from app.models.agent import Agent
    from app.models.user import UserRole

    q = db.query(Agent)
    role = getattr(principal, "role", None)
    if role == UserRole.FIELD_AGENT:
        return q.filter(Agent.user_id == principal.id)
    if role == UserRole.AGENCY_MANAGER:
        return q.filter(Agent.agency_id == principal.agency_id, Agent.manager_user_id == principal.id)
    if role == UserRole.AGENCY_ADMIN:
        return q.filter(Agent.agency_id == principal.agency_id)
    if role in (UserRole.BANK_ADMIN, UserRole.BANK_ANALYST, UserRole.BANK_TECHOPS, UserRole.SERVICE):
        return q.filter(Agent.bank_id == principal.bank_id)
    if role == UserRole.PLATFORM_ADMIN:
        return q
    return q.filter(false())


def cases_in_scope(db: Session, principal):
    """A Query of the cases `principal` may see, by the same table as
    agents_in_scope, plus the agency's UNASSIGNED pool for the agency roles
    (a manager allocates from it) — never another agency's pool. FROZEN
    INTERFACE (P1 split)."""
    from sqlalchemy import false, or_
    from app.models.agent import Agent
    from app.models.case import Case
    from app.models.user import UserRole

    q = db.query(Case)
    role = getattr(principal, "role", None)
    if role == UserRole.FIELD_AGENT:
        agent = db.query(Agent).filter(Agent.user_id == principal.id).first()
        if agent is None:
            return q.filter(false())
        return q.filter(Case.agency_id == agent.agency_id, Case.agent_id == agent.id)
    if role == UserRole.AGENCY_MANAGER:
        mine = agents_in_scope(db, principal).with_entities(Agent.id)
        return q.filter(Case.agency_id == principal.agency_id,
                        or_(Case.agent_id.in_(mine), Case.agent_id.is_(None)))
    if role == UserRole.AGENCY_ADMIN:
        return q.filter(Case.agency_id == principal.agency_id)
    if role in (UserRole.BANK_ADMIN, UserRole.BANK_ANALYST, UserRole.BANK_TECHOPS, UserRole.SERVICE):
        return q.filter(Case.bank_id == principal.bank_id)
    if role == UserRole.PLATFORM_ADMIN:
        return q
    return q.filter(false())


def agent_case_or_404(db: Session, agent, case_id, *, options=()):
    """The case, if `agent` may act on it; otherwise the uniform 404. A pure
    read: it never changes the case (see sync_assignee below)."""
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
        return case
    raise _not_found()


def sync_assignee(case, agent) -> None:
    """Move a stale assignee to `agent` — call it at the BUSINESS COMMIT of a
    write (the visit row, the payment, the promise), never earlier, and only
    on a case agent_case_or_404 has just granted: then `case.agent_id !=
    agent.id` can only mean the grant came from today's beat.

    2026-09-24 (coordinator MED 3): this used to be agent_case_or_404's
    `sync_assignee=True`, applied at the READ. record_visit then refused an
    out-of-hours visit after writing its audit row with a commit of its own,
    and that commit carried the reassignment with it — a refused visit still
    took the case over. Now nothing moves unless the write itself lands."""
    if case.agency_id != agent.agency_id:
        raise _not_found()
    if case.agent_id != agent.id:
        case.agent_id = agent.id


def today_beat_cases(db: Session, agent, *, options=()):
    """(today's beat, the cases on it this agent may act on) — the LIST side of
    agent_case_or_404, by the same rule and the same day. `(None, [])` when the
    agent has no beat for access_day().

    2026-09-24 (coordinator HIGH, A03 follow-up): GET /agent/cases,
    /agent/cases/ranked and POST /agent/beat/reoptimize each took the agent's
    LATEST beat of ANY date and loaded every id in `ordered_case_ids` with no
    agency, assignee or date check, then returned the borrower's phone and
    address. A case on a three-day-old beat, since handed to a teammate, was
    still listed with those details while its detail page answered 404, and
    reoptimize wrote a new order onto that stale beat. They now read only
    today's beat, and only its cases inside the agent's agency — which is
    exactly the set agent_case_or_404 grants (on today's beat => allowed).
    Order follows `ordered_case_ids`."""
    from app.models.beat import Beat
    from app.models.case import Case

    if agent is None:
        return None, []
    beat = db.query(Beat).filter(Beat.agent_id == agent.id, Beat.beat_date == access_day()).first()
    ids = list(beat.ordered_case_ids or []) if beat is not None else []
    if not ids:
        return beat, []
    q = db.query(Case)
    if options:
        q = q.options(*options)
    rows = {c.id: c for c in q.filter(Case.id.in_(ids), Case.agency_id == agent.agency_id).all()}
    return beat, [rows[i] for i in ids if i in rows]


def agencies_in_scope(db: Session, principal):
    """A Query of the agencies `principal` (a User) may see. Additive to
    agents_in_scope/cases_in_scope above (2026-09-28, D02), not a change to
    either — bank-side tenancy has no manager-of-agents concept, so this is
    its own, smaller rule:

        AGENCY_ADMIN/MANAGER -> their own agency only (bank-scoped tenants
                                 never read another agency's onboarding draft)
        BANK_* / SERVICE     -> every agency of their bank
        PLATFORM_ADMIN       -> everyone
        anything else        -> nothing"""
    from sqlalchemy import false
    from app.models.tenancy import Agency
    from app.models.user import UserRole

    q = db.query(Agency)
    role = getattr(principal, "role", None)
    if role in (UserRole.AGENCY_ADMIN, UserRole.AGENCY_MANAGER):
        return q.filter(Agency.id == principal.agency_id)
    if role in (UserRole.BANK_ADMIN, UserRole.BANK_ANALYST, UserRole.BANK_TECHOPS, UserRole.SERVICE):
        return q.filter(Agency.bank_id == principal.bank_id)
    if role == UserRole.PLATFORM_ADMIN:
        return q
    return q.filter(false())


def agency_or_404(db: Session, principal, agency_id):
    """The agency, if `principal` may see it; otherwise the uniform 404 —
    same convention as agent_case_or_404: "not found" and "not yours" are
    the identical body."""
    aid = parse_uuid(agency_id)
    if aid is None:
        raise _not_found()
    agency = agencies_in_scope(db, principal).filter_by(id=aid).first()
    if agency is None:
        raise _not_found()
    return agency
