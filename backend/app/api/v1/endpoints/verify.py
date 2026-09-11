"""GET /verify-agent — the endpoint the agent's ID card has promised since day one.

`core/security.create_agent_verify_token` has always minted a signed token for
the QR on the card, and its docstring has always described "the public
/verify-agent endpoint" that validates it. Until 2026-09-11 that endpoint did
not exist anywhere in the tree, so a borrower scanning the card had nothing
to check it against and the anti-impersonation control was inert. The
Compliance page listed it as Partial for exactly that reason.

What this returns, and why nothing more: a borrower at the door needs to know
that the person in front of them is a real agent of a real agency and is
currently allowed to be there. Name, employee code, agency, and whether they
are active — that is the whole question. No ids, no phone, no email, no
manager, no token contents, and nothing about any borrower or case. The
response is the same shape whether the card is genuine or not, except that a
bad one is a 404.

ONE FAILURE MODE ON PURPOSE. A forged signature, an expired token, a token of
the wrong type, a malformed string and an agent who no longer exists all
return the same 404 with the same body. Distinguishing them would tell a
forger which part of their card to fix. The reason is logged server-side at
INFO, where an operator can read it and a caller cannot.

Rate limiting follows /auth/login exactly: the application-wide default in
main.py (RATE_LIMIT_PER_MINUTE per client address). No per-route limit, for
the same reason login has none — the limiter object lives in main.py and the
endpoint modules cannot import it without a cycle.
"""
from __future__ import annotations

import structlog
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.core.dependencies import DbSession
from app.core.security import decode_token
from app.models.agent import Agent, AgentStatus
from app.models.user import User

logger = structlog.get_logger()

router = APIRouter(tags=["Public"])

_NOT_FOUND = HTTPException(status_code=404, detail="Agent not found")


class AgentVerification(BaseModel):
    """Exactly the four fields. `response_model` is what enforces that — a
    field added to the dict below by mistake is dropped at the boundary."""
    agent_name: str
    employee_code: str
    agency: str
    active: bool


@router.get("/verify-agent", response_model=AgentVerification,
            summary="Verify the signed QR on an agent's ID card — public, no login")
def verify_agent(db: DbSession, token: str = Query(..., min_length=1, max_length=2048)):
    try:
        payload = decode_token(token)
    except ValueError:
        logger.info("verify_agent.refused", reason="bad_signature_or_expired")
        raise _NOT_FOUND

    if payload.get("type") != "agent_verify":
        logger.info("verify_agent.refused", reason="wrong_token_type")
        raise _NOT_FOUND

    agent_id = payload.get("sub")
    row = (
        db.query(Agent, User)
        .join(User, User.id == Agent.user_id)
        .filter(Agent.id == agent_id)
        .first()
    ) if agent_id else None
    if not row:
        logger.info("verify_agent.refused", reason="no_such_agent")
        raise _NOT_FOUND

    agent, user = row
    # "Active" is the borrower's question — may this person be at my door today?
    # A suspended agent is not, whatever their card says; an agent whose login
    # has been disabled is not either. Off duty and on leave are still
    # employed and still genuine, so they read as active: the card is real.
    active = bool(user.is_active) and agent.status != AgentStatus.SUSPENDED
    return AgentVerification(
        agent_name=user.full_name,
        employee_code=agent.employee_code,
        agency=agent.agency_id,
        active=active,
    )
