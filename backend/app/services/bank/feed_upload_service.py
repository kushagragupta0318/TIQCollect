"""Bank loan-feed upload: validate a CSV, stage it, and record the batch.

WHAT THIS DOES NOT DO. It does not re-implement ingestion. scripts/ingest_daily
is the one engine (its `run_ingestion` is the seam), and this service only gets
a file safely as far as a `lending.bank_feed_batches` row that the engine — or a
worker — can pick up. A second ingestion pipeline is exactly what would drift
from the CLI.

WHY THE BATCH IS THE JOB RECORD. `bank_feed_batches` already carries file_name,
file_sha256, storage_key, received_via, uploaded_by, status, the row counts and
a dq_report, and it is already UNIQUE on
(bank_id, feed_type, business_date, file_sha256). That unique key IS the
idempotency key: the same file, same bank, same business date cannot produce a
second batch, whichever request wins the race. No new table, no new permission.

TENANCY. The bank is taken from the authenticated RequestContext, never from the
request body or a form field. A client-supplied bank id is not authorization.
"""
from __future__ import annotations

import csv
import hashlib
import io
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone

import structlog
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AppException, ErrorCode
from app.models.lending import BankFeedBatch
from app.models.tenancy import Bank

logger = structlog.get_logger()

# Limits. Deliberately conservative: a daily book is tens of thousands of rows,
# not a gigabyte, and an oversized upload is far more likely to be a mistake or
# an attack than a real feed.
MAX_UPLOAD_BYTES = 25 * 1024 * 1024      # 25 MB
MAX_ROWS = 200_000
FEED_TYPE = "DAILY_BOOK"

# The columns process_row() cannot work without. Checked here so a hopeless file
# is refused at the door with a readable message, rather than quarantining every
# row one at a time. A SUBSET of the full column list on purpose —
# scripts/ingest_daily stays the authority on the rest.
REQUIRED_HEADERS = ("customer_ref", "loan_account_number", "case_number")

# Excel/Sheets treat a leading = + - @ (and tab/CR) as a formula. Any value we
# echo back into a CSV report is prefixed so a crafted feed cannot run a formula
# on the reviewer's machine.
_FORMULA_LEAD = ("=", "+", "-", "@", "\t", "\r")


def sanitize_for_csv(value: object) -> str:
    """Neutralise spreadsheet formula injection in anything written to CSV."""
    text = "" if value is None else str(value)
    return "'" + text if text.startswith(_FORMULA_LEAD) else text


@dataclass
class FeedValidation:
    """What a file looks like before anything is written. Counts and column
    names only — never a borrower row."""
    ok: bool
    rows: int
    headers: list[str]
    missing_headers: list[str]
    blocking: list[str]
    warnings: list[str]
    duplicate_rows_in_file: int
    sha256: str


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def storage_key(bank_id: str, business_day: date, sha: str) -> str:
    """The object key a staged feed is stored under.

    GENERATED, never the user's filename: the client's name is kept only as a
    display label on the batch. Every component here is either a uuid, a date or
    a hex digest, so the key cannot contain a path separator, '..', a null byte
    or anything else that would escape the prefix — path traversal is impossible
    by construction rather than by sanitising a hostile string.
    """
    safe_bank = re.sub(r"[^0-9a-fA-F-]", "", str(bank_id))
    return "bank-feeds/{}/{}/{}.csv".format(safe_bank, business_day.isoformat(), sha)


def display_filename(raw: str | None) -> str:
    """A filename safe to STORE AND SHOW. Never used as a path."""
    name = (raw or "upload.csv").strip().replace("\\", "/").rsplit("/", 1)[-1]
    name = re.sub(r"[^A-Za-z0-9._-]", "_", name)[:255]
    return name or "upload.csv"


def validate_csv(data: bytes) -> FeedValidation:
    """Read the file as a CSV and report what is wrong with it.

    Content is validated, not the extension or the browser-supplied MIME type:
    anything can be called .csv. Blocking problems stop ingestion; warnings do
    not — a feed whose rows are imperfect is still a feed, and process_row()
    quarantines the bad rows with reasons.
    """
    blocking: list[str] = []
    warnings: list[str] = []
    sha = hashlib.sha256(data).hexdigest()

    if not data:
        return FeedValidation(False, 0, [], [], ["The file is empty."], [], 0, sha)
    if len(data) > MAX_UPLOAD_BYTES:
        return FeedValidation(
            False, 0, [], [],
            ["File is larger than {} MB.".format(MAX_UPLOAD_BYTES // (1024 * 1024))], [], 0, sha)

    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return FeedValidation(False, 0, [], [],
                              ["The file is not valid UTF-8 text — is it really a CSV?"], [], 0, sha)
    if "\x00" in text:
        return FeedValidation(False, 0, [], [],
                              ["The file contains binary data, not CSV rows."], [], 0, sha)

    reader = csv.DictReader(io.StringIO(text))
    headers = [h.strip() for h in (reader.fieldnames or [])]
    if not headers:
        return FeedValidation(False, 0, [], [], ["No header row found."], [], 0, sha)

    missing = [h for h in REQUIRED_HEADERS if h not in headers]
    if missing:
        blocking.append("Missing required column(s): " + ", ".join(missing))

    rows = 0
    seen: set[str] = set()
    duplicates = 0
    blank_keys = 0
    for row in reader:
        rows += 1
        if rows > MAX_ROWS:
            blocking.append("File has more than {:,} rows.".format(MAX_ROWS))
            break
        key = (row.get("loan_account_number") or "").strip()
        if not key:
            blank_keys += 1
            continue
        if key in seen:
            duplicates += 1
        seen.add(key)

    if rows == 0:
        blocking.append("The file has a header but no data rows.")
    if blank_keys:
        warnings.append("{} row(s) have no loan_account_number and will be skipped.".format(blank_keys))
    if duplicates:
        warnings.append(
            "{} duplicate loan_account_number(s) within this file; the last wins.".format(duplicates))

    return FeedValidation(
        ok=not blocking, rows=rows, headers=headers, missing_headers=missing,
        blocking=blocking, warnings=warnings, duplicate_rows_in_file=duplicates, sha256=sha,
    )


def _bank_or_403(db: Session, bank_id: str | None) -> Bank:
    """The caller's OWN bank, from the authenticated context."""
    if not bank_id:
        raise AppException(403, ErrorCode.FORBIDDEN, "This account is not attached to a bank.")
    bank = db.query(Bank).filter(Bank.id == bank_id).first()
    if bank is None:
        raise AppException(403, ErrorCode.FORBIDDEN, "This account is not attached to a bank.")
    return bank


def _validation_dict(v: FeedValidation) -> dict:
    return {
        "ok": v.ok, "rows": v.rows, "headers": v.headers,
        "missing_headers": v.missing_headers, "blocking": v.blocking,
        "warnings": v.warnings, "duplicate_rows_in_file": v.duplicate_rows_in_file,
    }


def batch_dict(b: BankFeedBatch) -> dict:
    """The wire shape of a batch. Operational metadata only — no borrower data."""
    return {
        "batch_id": b.id,
        "feed_type": b.feed_type,
        "business_date": b.business_date.isoformat() if b.business_date else None,
        "file_name": b.file_name,
        "file_sha256": b.file_sha256,
        "received_via": b.received_via,
        "uploaded_by": b.uploaded_by,
        "status": b.status,
        "rows_total": b.rows_total,
        "rows_accepted": b.rows_accepted,
        "rows_quarantined": b.rows_quarantined,
        "rows_skipped": b.rows_skipped,
        "received_at": b.received_at.isoformat() if b.received_at else None,
        "loaded_at": b.loaded_at.isoformat() if b.loaded_at else None,
    }


def upload_feed(db: Session, ctx, *, data: bytes, filename: str | None,
                business_day: date | None = None) -> dict:
    """Validate, stage and record one uploaded feed file.

    Returns the batch plus `duplicate: True` when this exact file was already
    received for this bank and business date — the caller gets the SAME batch id
    rather than a second run. That is the unique constraint doing the work, so
    two concurrent uploads of one file cannot both win.
    """
    bank = _bank_or_403(db, getattr(ctx, "bank_id", None))
    day = business_day or date.today()

    report = validate_csv(data)
    if not report.ok:
        # 422 and the reasons — never a stack trace, never a borrower value.
        raise AppException(422, ErrorCode.VALIDATION_ERROR, "; ".join(report.blocking))

    existing = (
        db.query(BankFeedBatch)
        .filter(BankFeedBatch.bank_id == bank.id,
                BankFeedBatch.feed_type == FEED_TYPE,
                BankFeedBatch.business_date == day,
                BankFeedBatch.file_sha256 == report.sha256)
        .first()
    )
    if existing is not None:
        return {"duplicate": True, "validation": _validation_dict(report), **batch_dict(existing)}

    key = storage_key(bank.id, day, report.sha256)
    # Stored BEFORE the batch row and keyed by content hash: a crash between the
    # two leaves an unreferenced object that the next upload of the same file
    # overwrites, which is cheap. The reverse order would leave a batch pointing
    # at a file that does not exist — a misleading status, which is worse.
    stored = False
    try:
        from app.core import storage
        storage.ensure_bucket()
        storage.upload_bytes(key, data, "text/csv")
        stored = True
    except Exception as exc:
        # Storage is not the system of record for ingestion; the batch is. The
        # filename is safe to log; the file's CONTENT never is.
        logger.warning("bank_feed.stage_failed", bank_id=bank.id, error=str(exc)[:200])

    batch = BankFeedBatch(
        bank_id=bank.id, feed_type=FEED_TYPE, business_date=day,
        file_name=display_filename(filename), file_sha256=report.sha256,
        storage_key=key if stored else None,
        received_via="UPLOAD", uploaded_by=getattr(ctx, "user_id", None),
        status="RECEIVED", rows_total=report.rows,
        dq_report={"validation": _validation_dict(report)},
        received_at=_utc_now(),
    )
    db.add(batch)
    try:
        db.commit()
    except IntegrityError:
        # Lost the race against a concurrent upload of the same file. The other
        # request's batch is the answer — the constraint, not a lock, is what
        # makes this safe.
        db.rollback()
        winner = (
            db.query(BankFeedBatch)
            .filter(BankFeedBatch.bank_id == bank.id,
                    BankFeedBatch.feed_type == FEED_TYPE,
                    BankFeedBatch.business_date == day,
                    BankFeedBatch.file_sha256 == report.sha256)
            .first()
        )
        if winner is None:
            raise
        # A duplicate does NOT re-dispatch: the winner is either already being
        # processed or already terminal, and the claim would refuse a second
        # task anyway. If it is still RECEIVED because its own dispatch was
        # lost, recover_stranded_batches is what picks it up — not this path.
        return {"duplicate": True, "queued": False,
                "validation": _validation_dict(report), **batch_dict(winner)}

    db.refresh(batch)
    queued = _dispatch(batch.id)
    return {"duplicate": False, "queued": queued,
            "validation": _validation_dict(report), **batch_dict(batch)}


def _dispatch(batch_id: str) -> bool:
    """Enqueue the ingestion task for a newly accepted batch.

    AFTER the commit, deliberately. The batch row is the record of work; the
    task is only a prompt to do it now. Enqueueing first would risk a worker
    claiming a batch this transaction then rolled back.

    That ordering leaves one window — committed, not enqueued (the broker is
    down, or the process dies in between). It is CLOSED BY THE ROW, not by this
    call: the batch stays RECEIVED and workers/tasks/feed_ingestion.
    recover_stranded_batches sweeps every RECEIVED batch. So a lost dispatch
    delays ingestion to the next sweep; it never loses it.

    Returns whether the prompt actually went out, and the caller reports that
    verbatim — a failed enqueue must never be shown to a bank user as queued.
    """
    try:
        from app.workers.tasks.feed_ingestion import ingest_feed_batch
        ingest_feed_batch.delay(str(batch_id))
        return True
    except Exception as exc:
        # Never fails the upload: the file is stored and the batch is committed,
        # which is the durable part. Only the prompt was lost.
        logger.warning("bank_feed.dispatch_failed", batch_id=str(batch_id),
                       error_type=type(exc).__name__, error=str(exc)[:200])
        return False


def list_batches(db: Session, ctx, *, limit: int = 50, offset: int = 0) -> dict:
    """This bank's ingestion history. Scoped to the caller's own bank."""
    bank = _bank_or_403(db, getattr(ctx, "bank_id", None))
    q = db.query(BankFeedBatch).filter(BankFeedBatch.bank_id == bank.id)
    total = q.count()
    rows = (q.order_by(BankFeedBatch.received_at.desc())
             .limit(min(max(limit, 1), 200)).offset(max(offset, 0)).all())
    return {"total": total, "items": [batch_dict(b) for b in rows]}


def get_batch(db: Session, ctx, batch_id: str) -> dict:
    """One batch of the caller's own bank.

    A batch belonging to another bank is the SAME 404 a nonexistent id gets, so
    this cannot be used to probe whether another bank's batch exists.
    """
    bank = _bank_or_403(db, getattr(ctx, "bank_id", None))
    b = (db.query(BankFeedBatch)
         .filter(BankFeedBatch.id == batch_id, BankFeedBatch.bank_id == bank.id)
         .first())
    if b is None:
        raise AppException(404, ErrorCode.NOT_FOUND, "Not found")
    return batch_dict(b)
