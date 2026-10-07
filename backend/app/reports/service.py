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
    rendering, the upload or the signing fails — and in each case writes no
    audit row.

    bank_id/agency_id are passed through to the audit row explicitly (the same
    reason bank_audit.py's own DATA_EXPORT writer does it): AuditLog carries no
    tenancy_listener that backfills a tenant from user_id, so a row written
    without one is unreadable by any bank's own scoped audit trail."""
    data = render(payload, fmt)
    content_type = FORMATS[fmt][1]
    digest = hashlib.sha256(data).hexdigest()
    key = object_key(payload, fmt, digest)

    storage.upload_bytes(key, data, content_type)
    url = storage.presigned_download_url(key, expires_minutes=LINK_TTL_MINUTES)

    write_audit(
        db, action=AuditAction.DATA_EXPORT, user_id=user_id, bank_id=bank_id, agency_id=agency_id,
        entity_type="Report", entity_id=payload.report_id,
        details={"format": fmt, "template": payload.template, "key": key, "bytes": len(data),
                 "sha256": digest, "period_start": payload.period_start.isoformat(),
                 "period_end": payload.period_end.isoformat(), "endpoint": endpoint},
    )
    logger.info("report.exported", report_id=payload.report_id, fmt=fmt, bytes=len(data), key=key)
    return ExportedReport(payload.report_id, fmt, key, content_type, len(data), digest, url, LINK_TTL_MINUTES)
