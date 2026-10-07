# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-09-24 — New file. E09: the ONE seam between a rendered report and the
#   outside world — MinIO, the presigned download link, and the DATA_EXPORT
#   audit row. Renderers never touch storage; callers never touch the audit.
# 2026-10-07 — E10's route (endpoints/bank_reports.py, services/bank/
#   report_templates.py) now calls export(). Added bank_id/agency_id so the
#   audit row is readable by the bank's own scoped trail (bank_audit.py's
#   own precedent for the same reason).
# ───────────────────────────────────────────────────────────────────────────
"""render → upload → presign → audit, in that order, and the audit row only
after the upload and the link both succeeded. The same rule as the two
existing DATA_EXPORT writers in endpoints/manager.py: a row on a refused or
failed request would make the trail claim a report left the platform when it
did not. The row records the SHAPE of the export (format, report id, object
key, byte count, sha256) — never its content, which would copy the very data
being logged into the audit table.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Callable

import structlog
from sqlalchemy.orm import Session

from app.core import storage
from app.core.audit import write_audit
from app.core.errors import AppException, ErrorCode
from app.models.audit_log import AuditAction
from app.reports.payload import ReportPayload

logger = structlog.get_logger()

LINK_TTL_MINUTES = 15


def _pdf(p: ReportPayload) -> bytes:
    from app.reports.render_pdf import render_pdf
    return render_pdf(p)


def _pptx(p: ReportPayload) -> bytes:
    from app.reports.render_pptx import render_pptx
    return render_pptx(p)


def _xlsx(p: ReportPayload) -> bytes:
    from app.reports.render_xlsx import render_xlsx
    return render_xlsx(p)


# format → (renderer, content type). Imports are deferred so a worker that only
# ever renders XLSX never loads ReportLab and matplotlib.
FORMATS: dict[str, tuple[Callable[[ReportPayload], bytes], str]] = {
    "pdf": (_pdf, "application/pdf"),
    "pptx": (_pptx, "application/vnd.openxmlformats-officedocument.presentationml.presentation"),
    "xlsx": (_xlsx, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
}


@dataclass(frozen=True)
class ExportedReport:
    report_id: str
    fmt: str
    key: str
    content_type: str
    size_bytes: int
    sha256: str
    url: str
    expires_minutes: int


def render(payload: ReportPayload, fmt: str) -> bytes:
    if fmt not in FORMATS:
        raise ValueError(f"unknown report format {fmt!r}; expected one of {sorted(FORMATS)}")
    return FORMATS[fmt][0](payload)


def object_key(payload: ReportPayload, fmt: str, sha256: str) -> str:
    """Content-addressed under the report id: re-rendering identical bytes
    reuses the key, a changed report gets a new one, nothing is overwritten."""
    return f"reports/{payload.report_id}/{sha256[:16]}.{fmt}"


def export(db: Session, payload: ReportPayload, fmt: str, *, user_id: str | None,
           bank_id: str | None = None, agency_id: str | None = None,
           endpoint: str | None = None) -> ExportedReport:
    """Render, store, sign a download link, and only then audit. Raises if
    rendering, the upload, the signing, or the audit write fails — and never
    hands out a link without a recorded row (the row IS the evidence an
    export happened; a link with no row is the thing this module exists to
    prevent).

    bank_id/agency_id are passed through to the audit row explicitly (the same
    reason bank_audit.py's own DATA_EXPORT writer does it): AuditLog carries no
    tenancy_listener that backfills a tenant from user_id, so a row written
    without one is unreadable by any bank's own scoped audit trail."""
    data = render(payload, fmt)
    content_type = FORMATS[fmt][1]
    digest = hashlib.sha256(data).hexdigest()
    key = object_key(payload, fmt, digest)

    try:
        storage.upload_bytes(key, data, content_type)
        url = storage.presigned_download_url(key, expires_minutes=LINK_TTL_MINUTES)
    except Exception as exc:
        logger.error("report.storage_failed", report_id=payload.report_id, fmt=fmt, key=key,
                     error=str(exc), error_type=type(exc).__name__)
        raise AppException(503, ErrorCode.SERVICE_UNAVAILABLE,
                           "Could not store the report right now. Try again shortly.") from exc

    # entity_id is audit.audit_logs.varchar(50); a board/agency_review report_id
    # (bank.id + agency.id + a date, see report_templates.py) runs 53-97 chars
    # and would DataError on Postgres (SQLite's test harness does not enforce
    # the column length, which is how this went unnoticed). A short, stable
    # hash stands in; the real id travels in `details`, which has no such cap.
    entity_id = hashlib.sha256(payload.report_id.encode()).hexdigest()[:16]
    audited = write_audit(
        db, action=AuditAction.DATA_EXPORT, user_id=user_id, bank_id=bank_id, agency_id=agency_id,
        entity_type="Report", entity_id=entity_id,
        details={"report_id": payload.report_id, "format": fmt, "template": payload.template,
                 "key": key, "bytes": len(data), "sha256": digest,
                 "period_start": payload.period_start.isoformat(),
                 "period_end": payload.period_end.isoformat(), "endpoint": endpoint},
    )
    if not audited:
        # write_audit never raises -- it rolls back and returns False so the
        # caller decides what a missing row means. Here it means the export
        # did not happen: handing out the link anyway is exactly the silent
        # gap this module's docstring says it closes.
        raise AppException(500, ErrorCode.INTERNAL_ERROR,
                           "The report rendered but could not be recorded. Try again.")
    logger.info("report.exported", report_id=payload.report_id, fmt=fmt, bytes=len(data), key=key)
    return ExportedReport(payload.report_id, fmt, key, content_type, len(data), digest, url, LINK_TTL_MINUTES)
