# ─── CHANGELOG (standalone plan) ─────────────────────────────────────────────
# 2026-10-07 — NEW (K01 Admin → Settings). The typed shapes for GET/PATCH
#   /bank/settings. One bank-config surface: the editable per-bank policy
#   values the bank admin sets, and the engine constants + DPD thresholds it
#   may only read. Every number the read side returns is sourced from
#   core/config.py or models/loan.dpd_bucket_for, never restated here.
# ────────────────────────────────────────────────────────────────────────────
"""Pydantic shapes for the bank-portal settings page.

`editable` fields are stored per bank (tenancy.banks.brand["ops"]) and audited
on change. `engine_constants` and `dpd_buckets` are read-only: they come from
core/config.py and the one DPD-bucket rule, and a bank cannot edit a value that
the engine reads from its own deployment config.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class BankInfo(BaseModel):
    id: str
    display_name: str
    timezone: str


class EditableField(BaseModel):
    """A per-bank policy value, with the engine default it falls back to."""
    value: int
    default: int
    # True when the bank has stored its own value (which may still equal the
    # default — the point is that it was set explicitly, not inherited).
    is_override: bool
    editable: bool = True


class ContactHours(BaseModel):
    start: int
    end: int
    default_start: int
    default_end: int
    is_override: bool
    editable: bool = True


class EngineConstant(BaseModel):
    """A value the engine reads from deployment config — shown, never edited."""
    key: str
    label: str
    value: float
    unit: str
    description: str
    editable: bool = False


class DpdBucketRow(BaseModel):
    bucket: str
    label: str
    min_dpd: int
    # None = the open-ended top bucket (NPA).
    max_dpd: int | None


class BankSettingsResponse(BaseModel):
    bank: BankInfo
    contact_hours: ContactHours
    geofence_metres: EditableField
    sla_first_visit_days: EditableField
    engine_constants: list[EngineConstant]
    dpd_buckets: list[DpdBucketRow]
    # Honest note: editable overrides are stored and audited; wiring the engine
    # to prefer them over the global deployment config is a flagged follow-up.
    note: str


class UpdateBankSettingsRequest(BaseModel):
    """A partial update: every field is optional; only the ones sent change.

    The start<end window is validated on the EFFECTIVE merged window in the
    service, since a partial PATCH may send only one end of it.
    """
    contact_hour_start: int | None = Field(default=None, ge=0, le=23)
    contact_hour_end: int | None = Field(default=None, ge=1, le=24)
    geofence_metres: int | None = Field(default=None, ge=10, le=5000)
    sla_first_visit_days: int | None = Field(default=None, ge=1, le=90)
