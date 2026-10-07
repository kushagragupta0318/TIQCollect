"""Bank↔agency messaging (bank↔agency comms feature).

One shared router: both the bank and the owning agency reach the same routes;
`messaging.read`/`messaging.send` are granted to bank roles and the agency
manager/admin, and the service derives the sender's side from the caller's scope.
Cross-tenant visibility goes through l8's RequestContext (CurrentContext), the
same mechanism the reversal bank stage uses. Thin: the thread machine, the
lazy-create and the tenant checks live in MessagingService.
"""
from __future__ import annotations

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from app.core.dependencies import DbSession
from app.core.ids import UUIDPath
from app.core.permissions import require_perm
from app.core.request_context import CurrentContext
from app.models.user import User
from app.services.messaging_service import MessagingService

router = APIRouter(prefix="/messaging", tags=["messaging"])


class MessageIn(BaseModel):
    body: str = Field(min_length=1, max_length=4000)


@router.get("/threads/{subject_type}/{subject_id}", summary="Read a subject's bank↔agency thread")
def get_thread(subject_type: str, subject_id: UUIDPath, ctx: CurrentContext, db: DbSession,
               current_user: User = require_perm("messaging.read")):
    return MessagingService(db).get_thread(ctx, subject_type, subject_id)


@router.post("/threads/{subject_type}/{subject_id}/messages",
             summary="Post a message (lazy-creates the thread)")
def post_message(subject_type: str, subject_id: UUIDPath, body: MessageIn, ctx: CurrentContext, db: DbSession,
                 current_user: User = require_perm("messaging.send")):
    return MessagingService(db).post_message(ctx, subject_type, subject_id, body.body)


@router.get("/threads", summary="Threads awaiting the caller's reply (drives the reminder + Send Message)")
def list_pending(ctx: CurrentContext, db: DbSession,
                 pending: bool = Query(True),
                 current_user: User = require_perm("messaging.read")):
    # `pending` is the only view for now: open threads whose last message came
    # from the other side. The flag is accepted so the surface can grow later.
    return {"threads": MessagingService(db).list_pending(ctx)}
