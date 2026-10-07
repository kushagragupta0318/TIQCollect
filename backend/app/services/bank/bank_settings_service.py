# ─── CHANGELOG (standalone plan) ─────────────────────────────────────────────
# 2026-10-07 — NEW (K01 Admin → Settings). The bank-config read/write use case.
#
#   No new table: the editable per-bank policy lives in the EXISTING
#   tenancy.banks.brand JSON bag (the tenant root's settings document) under a
#   namespaced "ops" key, so this ships with no migration and no models/ change
#   beyond one audit-action enum value (v2_0028, flagged to the coordinator for
#   its number at merge). Brand's other keys (logo_key, sms_sender_id, …) are
#   untouched: we read-modify-write a copy of the whole dict and reassign it so
#   SQLAlchemy sees the change (a JSON column does not track in-place mutation).
#
#   Read-only values (exploration rate, retention days, DPD thresholds) are
#   surfaced FROM core/config.py and models/loan.dpd_bucket_for, never restated
#   (CLAUDE.md "one definition per rule"; the "copy figures from constants"
#   memory). The DPD rows are DERIVED by scanning the one bucket rule, so they
#   follow it if its boundaries ever move.
#
#   HONESTY (flagged): the editable overrides are stored and audited, but the
#   field app / allocator still read CONTACT_HOUR_*/GEO_FENCE_METRES from the
#   deployment config. Wiring those paths to prefer a bank override is a
#   cross-cutting follow-up (touches geo.py, visit_service, allocation); the
#   `note` on the response says so rather than implying live enforcement.
# ────────────────────────────────────────────────────────────────────────────
"""GET/PATCH /bank/settings — the bank admin's one config surface."""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session
from starlette.requests import Request

from app.core.audit import stage_audit
from app.core.config import settings
from app.core.errors import AppException, ErrorCode
from app.models.audit_log import AuditAction
from app.models.loan import dpd_bucket_for
from app.models.tenancy import Bank
from app.models.user import User

# The brand sub-document that holds this page's editable values.
BRAND_OPS_KEY = "ops"

# Mirrors tenancy.AgencyContract.sla_first_visit_days' own column default: the
# per-contract value the engine enforces. This bank-wide figure is the default
# a bank configures for new contracts, not a second enforcement point.
DEFAULT_SLA_FIRST_VISIT_DAYS = 7

# The keys stored in brand["ops"]. Kept here so read and write cannot disagree.
_OPS_CONTACT_START = "contact_hour_start"
_OPS_CONTACT_END = "contact_hour_end"
_OPS_GEOFENCE = "geofence_metres"
_OPS_SLA = "sla_first_visit_days"


def _bank_or_404(db: Session, bank_id: str | None) -> Bank:
    bank = db.get(Bank, bank_id) if bank_id else None
    if bank is None:
        raise AppException(status_code=404, code=ErrorCode.NOT_FOUND, message="Bank not found")
    return bank


def _ops(bank: Bank) -> dict[str, Any]:
    brand = bank.brand or {}
    ops = brand.get(BRAND_OPS_KEY)
    return dict(ops) if isinstance(ops, dict) else {}


def _dpd_buckets() -> list[dict[str, Any]]:
    """The DPD buckets as (bucket, min, max) rows, derived by scanning the one
    rule in models/loan.dpd_bucket_for so the display cannot drift from it. The
    top bucket is open-ended (max_dpd = None)."""
    rows: list[dict[str, Any]] = []
    prev = None
    start = 0
    # 0..399 spans every boundary (NPA begins at 91); the final row is closed
    # off as open-ended below.
    for d in range(0, 400):
        bucket = dpd_bucket_for(d).value
        if bucket != prev:
            if prev is not None:
                rows.append({"bucket": prev, "min_dpd": start, "max_dpd": d - 1})
            prev, start = bucket, d
    rows.append({"bucket": prev, "min_dpd": start, "max_dpd": None})
    for row in rows:
        if row["max_dpd"] is None:
            row["label"] = f"{row['min_dpd']}+ DPD"
        elif row["min_dpd"] == row["max_dpd"]:
            row["label"] = f"{row['min_dpd']} DPD"
        else:
            row["label"] = f"{row['min_dpd']}–{row['max_dpd']} DPD"
    return rows


def _engine_constants() -> list[dict[str, Any]]:
    """Read-only values the engine takes from deployment config."""
    return [
        {"key": "allocator_exploration_rate", "label": "Allocation exploration rate",
         "value": float(settings.ALLOCATOR_EXPLORATION_RATE), "unit": "fraction",
         "description": "Share of each night's allocation reserved for exploration swaps (ADR 0003)."},
        {"key": "location_retention_days", "label": "GPS location retention",
         "value": float(settings.LOCATION_RETENTION_DAYS), "unit": "days",
         "description": "Agent GPS rows older than this are deleted by the nightly sweep."},
        {"key": "repayment_snapshot_retention_days", "label": "Repayment snapshot retention",
         "value": float(settings.REPAYMENT_SNAPSHOT_RETENTION_DAYS), "unit": "days",
         "description": "How long point-in-time repayment scores are kept."},
        {"key": "audit_log_retention_days", "label": "Audit log retention",
         "value": float(settings.AUDIT_LOG_RETENTION_DAYS), "unit": "days",
         "description": "The compliance promise for the audit trail; nothing in app/ deletes audit rows."},
    ]


def _response(bank: Bank) -> dict[str, Any]:
    ops = _ops(bank)
    start = int(ops.get(_OPS_CONTACT_START, settings.CONTACT_HOUR_START))
    end = int(ops.get(_OPS_CONTACT_END, settings.CONTACT_HOUR_END))
    return {
        "bank": {"id": bank.id, "display_name": bank.display_name, "timezone": bank.timezone},
        "contact_hours": {
            "start": start, "end": end,
            "default_start": settings.CONTACT_HOUR_START, "default_end": settings.CONTACT_HOUR_END,
            "is_override": _OPS_CONTACT_START in ops or _OPS_CONTACT_END in ops,
        },
        "geofence_metres": {
            "value": int(ops.get(_OPS_GEOFENCE, settings.GEO_FENCE_METRES)),
            "default": settings.GEO_FENCE_METRES, "is_override": _OPS_GEOFENCE in ops,
        },
        "sla_first_visit_days": {
            "value": int(ops.get(_OPS_SLA, DEFAULT_SLA_FIRST_VISIT_DAYS)),
            "default": DEFAULT_SLA_FIRST_VISIT_DAYS, "is_override": _OPS_SLA in ops,
        },
        "engine_constants": _engine_constants(),
        "dpd_buckets": _dpd_buckets(),
        "note": ("Editable values are stored as this bank's policy and every change is audited. "
                 "The field app and allocator still read contact hours and the geo-fence from the "
                 "deployment configuration; preferring a bank override is planned follow-up work."),
    }


def get_settings(db: Session, current_user: User) -> dict[str, Any]:
    return _response(_bank_or_404(db, current_user.bank_id))


def update_settings(db: Session, current_user: User, *, request: Request | None = None,
                    **fields: Any) -> dict[str, Any]:
    """Apply the sent fields to brand["ops"], validate the merged contact
    window, write one audit row, and return the full settings."""
    # Drop the keys the caller did not send (None): a partial PATCH never
    # clears a value by omission.
    changes = {k: v for k, v in fields.items() if v is not None}

    bank = _bank_or_404(db, current_user.bank_id)
    ops = _ops(bank)
    before = _response(bank)

    merged_start = int(changes.get(_OPS_CONTACT_START, ops.get(_OPS_CONTACT_START, settings.CONTACT_HOUR_START)))
    merged_end = int(changes.get(_OPS_CONTACT_END, ops.get(_OPS_CONTACT_END, settings.CONTACT_HOUR_END)))
    if merged_start >= merged_end:
        raise AppException(status_code=422, code=ErrorCode.VALIDATION_ERROR,
                           message="Contact-hour start must be before end")

    if not changes:
        # Nothing to do — return the current state without an audit row.
        return before

    new_ops = {**ops, **changes}
    # Reassign the whole brand dict so SQLAlchemy tracks the JSON change.
    bank.brand = {**(bank.brand or {}), BRAND_OPS_KEY: new_ops}

    # The prior effective value of each changed field, for the audit detail.
    old = {
        _OPS_CONTACT_START: before["contact_hours"]["start"],
        _OPS_CONTACT_END: before["contact_hours"]["end"],
        _OPS_GEOFENCE: before["geofence_metres"]["value"],
        _OPS_SLA: before["sla_first_visit_days"]["value"],
    }
    stage_audit(
        db, action=AuditAction.BANK_SETTINGS_UPDATED, user_id=current_user.id,
        entity_type="Bank", entity_id=bank.id,
        details={"changed": changes, "old": {k: old[k] for k in changes}},
        bank_id=bank.id,
    )
    db.commit()
    db.refresh(bank)
    return _response(bank)
