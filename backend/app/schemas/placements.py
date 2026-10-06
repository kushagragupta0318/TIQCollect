"""Typed responses for the placement reads an AGENCY-scope caller can reach
(team rule 18). A response model is an allowlist: a key added later to an
internal dict (PlacementDecision.score_breakdown is a free JSON column) is
dropped at this boundary instead of reaching another tenant.

The engine's two routes answer bank and agency callers with different shapes;
`view` names which, so the union is decided by a field, never by guessing.
"""
from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field


class PlacementRowOut(BaseModel):
    placement_id: str
    loan_id: str
    loan_account_number: str
    agency_id: str
    agency_name: str | None
    source: str
    status: str
    placed_on: str
    ended_on: str | None
    end_reason: str | None
    dpd_at_placement: int
    dpd_bucket_at_placement: str
    exposure_at_placement: float
    expected_recovery_prob: float | None
    sla_first_visit_due: str | None
    placement_run_id: str | None


class PlacementsPageOut(BaseModel):
    items: list[PlacementRowOut]
    total: int
    page: int
    page_size: int
    synthetic_warning: str | None


class AgencyRunOut(BaseModel):
    """What an agency sees of a run: when it was applied, nothing bank-wide."""
    run_id: str
    plan_date: str
    status: str
    applied_at: str | None


class AgencyScoreBreakdownOut(BaseModel):
    """The terms of the agency's OWN placement (placement_engine.AGENCY_BREAKDOWN_KEYS)."""
    expected: float | None = None
    is_modelled: bool | None = None
    p_pay: float | None = None
    multiplier: float | None = None
    effect_n: int | None = None
    commission_pct: float | None = None
    commission_known: bool | None = None
    versions: dict[str, str] | None = None


class AgencyDecisionOut(BaseModel):
    loan_id: str
    loan_account_number: str
    outcome: str
    reason: str
    score: float | None
    chosen_agency_id: str | None
    chosen_agency_name: str | None
    score_breakdown: AgencyScoreBreakdownOut


class AgencyDecisionsOut(BaseModel):
    view: Literal["agency"] = "agency"
    run: AgencyRunOut
    items: list[AgencyDecisionOut]
    total: int
    page: int
    page_size: int


class BankDecisionOut(BaseModel):
    loan_id: str
    loan_account_number: str
    outcome: str
    reason: str
    score: float | None
    chosen_agency_id: str | None
    chosen_agency_name: str | None
    previous_agency_id: str | None
    previous_agency_name: str | None
    score_breakdown: dict[str, Any]
    gate_results: dict[str, Any]


class BankDecisionsOut(BaseModel):
    view: Literal["bank"] = "bank"
    run: dict[str, Any]
    items: list[BankDecisionOut]
    total: int
    page: int
    page_size: int


DecisionsOut = Annotated[Union[AgencyDecisionsOut, BankDecisionsOut], Field(discriminator="view")]


class AgencyRunsOut(BaseModel):
    view: Literal["agency"] = "agency"
    items: list[AgencyRunOut]


class BankRunsOut(BaseModel):
    view: Literal["bank"] = "bank"
    items: list[dict[str, Any]]


RunsOut = Annotated[Union[AgencyRunsOut, BankRunsOut], Field(discriminator="view")]
