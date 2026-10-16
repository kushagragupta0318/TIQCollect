# Agency-admin management of the agency's AGENCY_MANAGERs — list and add.
# Shares the "/manager" prefix (like manager_agents_admin for agents), but every
# route is AGENCY_ADMIN-only: regular managers get 403. The business logic lives
# in services/agency_manager_service.py; this file is routing + request shapes.
#
# No `from __future__ import annotations` (same reason as manager_agents_admin):
# FastAPI/Pydantic must resolve the request model's annotations at import.
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from app.core.dependencies import DbSession
from app.core.dependencies import require_roles
from app.core.emails import AccountEmail
from app.models.user import User, UserRole
from app.services import agency_manager_service

router = APIRouter(prefix="/manager", tags=["agency-managers-admin"])

AgencyAdminOnly = Annotated[User, Depends(require_roles(UserRole.AGENCY_ADMIN))]


class CreateManagerRequest(BaseModel):
    full_name: str = Field(min_length=1, max_length=200)
    email: AccountEmail   # `.test` demo domains allowed (core/emails.py)
    phone: str = Field(min_length=10, max_length=20)


@router.get("/managers")
def list_managers_route(current_user: AgencyAdminOnly, db: DbSession):
    return agency_manager_service.list_managers(db, current_user)


@router.post("/managers", status_code=201)
def create_manager_route(
    body: CreateManagerRequest, request: Request, db: DbSession, current_user: AgencyAdminOnly,
):
    return agency_manager_service.create_manager(
        db, current_user, full_name=body.full_name, email=body.email, phone=body.phone, request=request,
    )
