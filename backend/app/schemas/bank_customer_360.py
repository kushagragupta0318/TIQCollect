"""Response shapes for the bank's borrower page (endpoints/bank_customers.py,
services/bank/customer_360.py).

Nothing here is summed across cases: a borrower-level total or score is a
statistic nobody has defined, so the shape does not offer a place to put one.
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel


class CustomerHeader(BaseModel):
    customer_id: str
    full_name: str
    phone_primary: Optional[str] = None
    address_line1: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    pincode: Optional[str] = None
    #: Masked at the source and never unmasked.
    pan_masked: Optional[str] = None
    aadhaar_masked: Optional[str] = None
    language_preference: Optional[str] = None
    is_hostile: bool
    do_not_contact: bool
    tags: list[str]


class CaseRow(BaseModel):
    case_id: str
    case_number: str
    status: str
    agency_id: Optional[str] = None
    agency_name: Optional[str] = None
    placed_on: Optional[str] = None
    agent_id: Optional[str] = None
    agent_name: Optional[str] = None
    loan_id: str
    loan_type: Optional[str] = None
    dpd: Optional[int] = None
    dpd_bucket: Optional[str] = None
    total_outstanding: Optional[float] = None
    overdue_amount: Optional[float] = None
    target_amount: Optional[float] = None
    collected_verified: Optional[float] = None
    last_visit_at: Optional[str] = None
    last_visit_outcome: Optional[str] = None
    last_call_at: Optional[str] = None
    last_call_outcome: Optional[str] = None
    last_contact_at: Optional[str] = None
    active_ptp_date: Optional[str] = None
    active_ptp_amount: Optional[float] = None
    has_open_dispute: Optional[bool] = None
    is_escalated: Optional[bool] = None
    #: P(no material payment next cycle) for THIS case — never a borrower-level
    #: number. The explanation panel states its polarity and provenance.
    latest_probability: Optional[float] = None
    latest_band: Optional[str] = None
    latest_model_version: Optional[str] = None
    latest_disposition: Optional[str] = None


class LoanWithoutCase(BaseModel):
    loan_id: str
    loan_account_number: str
    loan_type: Optional[str] = None
    dpd: int
    total_outstanding: float
    overdue_amount: float


class Customer360(BaseModel):
    customer: CustomerHeader
    cases: list[CaseRow]
    loans_without_cases: list[LoanWithoutCase]
    #: True when a region limit narrowed what is shown.
    region_limited: bool
    loans_truncated: bool


class TimelineEntry(BaseModel):
    kind: Literal["VISIT", "CALL", "PAYMENT", "PTP"]
    at: Optional[str] = None
    actor_type: str
    actor_id: Optional[str] = None
    entity_id: str
    detail: dict[str, Any]


class CaseTimeline(BaseModel):
    case_id: str
    entries: list[TimelineEntry]
    truncated: bool
    limit: int
