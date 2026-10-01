"""The bank's view of what scores its book: the six layers as a set and the
recovery_risk model card (GET /bank/models), and one loan's latest score with its
reasons and provenance (GET /bank/loans/{loan_id}/explanation).

Read-only. Bank from the caller's own row (RequestContext), region limit applied
to the loan; a foreign, out-of-region or missing loan is the same 404. The work
is in services/bank/model_showcase.py.
"""
from __future__ import annotations

from fastapi import APIRouter

from app.core.dependencies import DbSession
from app.core.ids import UUIDPath
from app.core.permissions import require_perm
from app.core.request_context import CurrentContext
from app.models.user import User
from app.schemas.bank_models import LoanExplanation, ModelsOverview
from app.services.bank import model_showcase
from app.services.scope import region_limit_path

router = APIRouter(prefix="/bank", tags=["bank-models"])


@router.get("/models", response_model=ModelsOverview)
def models_overview(ctx: CurrentContext, db: DbSession, _user: User = require_perm("ml.read")):
    return model_showcase.models_overview(db, ctx)


@router.get("/loans/{loan_id}/explanation", response_model=LoanExplanation)
def loan_explanation(loan_id: UUIDPath, ctx: CurrentContext, db: DbSession,
                     _user: User = require_perm("placement.read")):
    return model_showcase.loan_explanation(db, ctx, loan_id, region_limit=region_limit_path(db, _user))
