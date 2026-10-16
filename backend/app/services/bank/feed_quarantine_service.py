"""Quarantine review and safe release of held feed rows (Phase 6).

WHAT RELEASE MEANS HERE. It is a RE-TRY, not a correction. placement_service
says so where the reasons are declared: "Stored in bank_feed_rows.dq_errors, so
a released row can be re-tried once the reason is fixed (a contract signed, a
slab authorised)." Every refusal reason names EXTERNAL state — a contract, an
authorisation, a region, a cap, the loan's own status — fixed outside the feed
in the systems that own it. So release re-runs the row through the SAME
placement rules and accepts the verdict they give now.

Nothing in here edits borrower data. `raw` is kept exactly as received, and a
row whose reason has NOT been fixed is refused by the rules and stays
quarantined with a new dq_errors entry. There is deliberately no correction
flow: the engine has no safe mechanism for one, and inventing a path that
mutates a borrower's record to make a release succeed is the failure mode this
module exists to avoid.

TWO CLASSES OF QUARANTINE, AND ONLY ONE IS RELEASABLE. scripts/ingest_daily
holds rows at exactly two places:

  * A PLACEMENT REFUSAL (8 reasons) — the loan was already upserted, so the row
    carries `loan_id`. Re-trying is `place_new_loan` on an existing loan: the
    one engine, through the one service. RELEASABLE.
  * UNKNOWN_BRANCH — held BEFORE any write ("so a quarantined row leaves no
    half-created borrower behind"), so no loan, customer or case exists and
    `loan_id` is NULL. Releasing it would mean CREATING a loan from `raw`,
    which is ingestion — a second implementation of process_row, with its own
    branch gate, customer upsert and date parsing, drifting from the first.
    NOT RELEASABLE from here. The branch is fixed in the bank's branch list and
    the file re-ingested, which the existing pipeline already handles.

`loan_id IS NULL` is what separates them, and it is a property of how the row
was written rather than a guess: see the two _quarantine call sites.

IDEMPOTENCY. A row moves QUARANTINED -> RELEASED exactly once. The claim is a
conditional UPDATE under `with_for_update()`, the same pattern as the Phase 5
batch claim and manual_placement_service's recall, so two concurrent release
requests cannot both place the same loan. `place_new_loan` is itself idempotent
(an existing ACTIVE placement with that agency is returned as it is), which is
the second line of defence rather than the first.

THE BATCH IS NEVER REWRITTEN TO LOOK SUCCESSFUL. A PARTIAL batch stays PARTIAL;
only its rows_quarantined count follows reality. Turning PARTIAL into LOADED to
make a release feel complete would erase the evidence that rows were held.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy.orm import Session

from app.core.audit import stage_audit, write_audit
from app.core.errors import AppException, ErrorCode
from app.models.audit_log import AuditAction
from app.models.lending import BankFeedBatch, BankFeedRow
from app.models.loan import Loan
from app.models.tenancy import Bank
from app.services.placement_service import PlacementRefused, PlacementService

# The batch states whose rows may be reviewed and released. A batch still being
# worked on (RECEIVED/VALIDATING) has not finished deciding what is quarantined,
# and REJECTED means nothing was ingested at all — releasing a row from either
# would act on a half-decided picture.
RELEASABLE_BATCH_STATUSES = ("PARTIAL", "LOADED")

# Why a quarantined row cannot be released from this screen. Not a status: the
# row stays QUARANTINED either way, and this says what the reviewer must do.
NO_LOAN = "NO_LOAN_TO_PLACE"

# RAW BORROWER PII (Phase 9, RISK-1). `row.raw` is the row AS RECEIVED: it
# carries the borrower's name, phone, address and masked PAN. Reading it is a
# SEPARATE grant from reviewing or releasing a held row, because Phase 8
# measured that every holder of bank_feed.upload could read it with no audit
# row — including BANK_TECHOPS and the SERVICE account.
#
# Opt-in is not authorization: `include_raw=true` asks, this capability
# answers. A reviewer without it keeps the whole workflow (held rows, reasons,
# account numbers, Release) and simply never receives `raw`.
PII_CAPABILITY = "bank_feed.pii.read"

# The fields `raw` is permitted to carry out. Phase 8 measured exactly these;
# anything the bank's file adds later is dropped rather than leaked by default
# (§3: "do not expose additional fields beyond those already required").
RAW_FIELDS_ALLOWED = (
    "customer_ref", "customer_name", "loan_account_number", "case_number",
    "phone_primary", "address_line1", "pan_masked", "branch_code", "agency_code",
)

MAX_RELEASE_ROWS = 200


def _bank_or_403(db: Session, bank_id: str | None) -> Bank:
    """The caller's OWN bank, from the authenticated context. Mirrors
    feed_upload_service._bank_or_403 — same question, same answer."""
    if not bank_id:
        raise AppException(403, ErrorCode.FORBIDDEN, "This account is not attached to a bank.")
    bank = db.query(Bank).filter(Bank.id == bank_id).first()
    if bank is None:
        raise AppException(403, ErrorCode.FORBIDDEN, "This account is not attached to a bank.")
    return bank


def _not_found() -> AppException:
    """One uniform 404. Another bank's batch or row is indistinguishable from a
    nonexistent id, so neither can be used to probe what exists."""
    return AppException(404, ErrorCode.NOT_FOUND, "Not found")


def _scoped_batch(db: Session, bank_id: str, batch_id: str) -> BankFeedBatch:
    b = (db.query(BankFeedBatch)
         .filter(BankFeedBatch.id == batch_id, BankFeedBatch.bank_id == bank_id)
         .first())
    if b is None:
        raise _not_found()
    return b


def last_reason(row: BankFeedRow) -> dict:
    """The refusal that is holding the row NOW. dq_errors accumulates — a row
    refused twice carries both — and the latest is the live one."""
    errors = row.dq_errors or []
    return errors[-1] if errors else {}


def row_dict(row: BankFeedRow, *, include_raw: bool = False) -> dict:
    """One quarantined row, for a reviewer.

    BORROWER PII IS NOT SENT BY DEFAULT. The business keys a reviewer needs to
    identify the row (account number, customer ref, case number) are included;
    `raw` — every column as received, which carries name, phone and address —
    only when the caller asked and holds the capability. A count or a status is
    never faked: a field the row does not carry is null, not zero.
    """
    reason = last_reason(row)
    out = {
        "row_id": row.id,
        "row_no": row.row_no,
        "status": row.status,
        "loan_account_number": row.loan_account_number,
        "customer_ref": row.customer_ref,
        "case_number": row.case_number,
        "reason": reason.get("reason"),
        "detail": reason.get("detail"),
        # The whole history, not just the latest: a row refused twice shows both.
        "attempts": len(row.dq_errors or []),
        "releasable": row.status == "QUARANTINED" and row.loan_id is not None,
        "blocked_because": None if row.loan_id is not None else NO_LOAN,
        "processed_at": row.processed_at.isoformat() if row.processed_at else None,
        "released_at": row.released_at.isoformat() if row.released_at else None,
        "released_by": row.released_by,
    }
    if include_raw:
        # Filtered, not passed through. `row.raw` is whatever the bank's file
        # contained; serving it verbatim would leak any new column the bank
        # adds without anyone reviewing it.
        raw = row.raw or {}
        out["raw"] = {k: raw[k] for k in RAW_FIELDS_ALLOWED if k in raw}
    return out


def _may_read_raw(ctx) -> bool:
    """Does this caller hold the raw-PII grant?

    Read from the authenticated RequestContext's own capability set, which is
    derived from the role by core/permissions — never from a client value and
    never from a role name compared by hand.
    """
    has = getattr(ctx, "has", None)
    if callable(has):
        return bool(has(PII_CAPABILITY))
    # A context without `has` cannot prove the grant, so it does not get it.
    return False


def _audit_raw_read(db: Session, ctx, *, batch_id: str | None, row_ids: list[str],
                    endpoint: str, ip_address: str | None = None,
                    user_agent: str | None = None) -> bool:
    """Record one raw-PII read. Returns whether the row landed.

    FAILS CLOSED. The caller must not serve `raw` when this returns False:
    unlogged access to borrower contact data is exactly what RISK-1 was about.
    That is a deliberate departure from write_audit's usual contract ("the
    audit trail records what happened, it does not decide whether it happens")
    — here the trail is a PRECONDITION of the disclosure, not a note about it.

    DATA_EXPORT is the action. It is an existing value of the native
    audit_action_enum and already means "sensitive data left the system"
    (bank_audit.py, manager.py's CSV exports), so no enum value is invented and
    no migration is needed. There is no RAW_PII_READ value and adding one would
    be an ALTER TYPE, which Phase 9 may not do.

    NO BORROWER PII IS RECORDED: ids, counts, the endpoint and the actor only.
    Never a name, phone, address, PAN value or any CSV content.
    """
    return write_audit(
        db,
        action=AuditAction.DATA_EXPORT,
        user_id=getattr(ctx, "user_id", None),
        entity_type="BankFeedRow",
        # The batch for a listing; the row itself for a single-row read.
        entity_id=batch_id or (row_ids[0] if row_ids else None),
        details={
            "access": "raw_pii_read",
            "endpoint": endpoint,
            "batch_id": batch_id,
            "rows_disclosed": len(row_ids),
            # WHICH fields the response may carry, so a reviewer can tell what
            # was exposed without the values ever being stored.
            "fields": list(RAW_FIELDS_ALLOWED),
            "capability": PII_CAPABILITY,
        },
        bank_id=getattr(ctx, "bank_id", None),
        ip_address=ip_address,
        user_agent=user_agent,
        success=True,
    )

def list_quarantined(db: Session, ctx, batch_id: str, *, limit: int = 100, offset: int = 0,
                     include_raw: bool = False, ip_address: str | None = None,
                     user_agent: str | None = None) -> dict:
    """The held rows of ONE batch of the caller's own bank.

    Counts come from the database, not from the page of rows returned, so a
    reviewer paging through 300 rows is never told there are 100.

    `include_raw` is a REQUEST, not a permission. It is honoured only for a
    caller holding bank_feed.pii.read, and only once the read is audited; a
    caller without the grant gets the same listing minus `raw`, with
    `raw_pii_available` saying why. No 403 — refusing the whole listing would
    take the review workflow away from the people who do it.
    """
    bank = _bank_or_403(db, getattr(ctx, "bank_id", None))
    batch = _scoped_batch(db, bank.id, batch_id)

    base = db.query(BankFeedRow).filter(BankFeedRow.batch_id == batch.id,
                                        BankFeedRow.bank_id == bank.id)
    quarantined = base.filter(BankFeedRow.status == "QUARANTINED")
    total = quarantined.count()
    rows = (quarantined.order_by(BankFeedRow.row_no)
            .limit(min(max(limit, 1), 500)).offset(max(offset, 0)).all())

    # Releasable vs not, counted over the WHOLE batch rather than this page, so
    # the screen can say "4 of these 12 cannot be released from here".
    blocked = quarantined.filter(BankFeedRow.loan_id.is_(None)).count()

    # ── raw-PII gate ────────────────────────────────────────────────────────
    # Authorised AND audited, in that order, before a single raw field is
    # serialised. The audit row is committed first: if it cannot be persisted,
    # the response carries no raw data at all (fail closed).
    serve_raw = False
    pii_note = None
    if include_raw:
        if not _may_read_raw(ctx):
            pii_note = "not_authorised"
        elif _audit_raw_read(db, ctx, batch_id=batch.id, row_ids=[r.id for r in rows],
                             endpoint="GET /bank/feed/{batch_id}/quarantine",
                             ip_address=ip_address, user_agent=user_agent):
            serve_raw = True
        else:
            # The disclosure would be unlogged. Refuse the data, not the page.
            pii_note = "audit_unavailable"

    return {
        "batch_id": batch.id,
        "batch_status": batch.status,
        "batch_reviewable": batch.status in RELEASABLE_BATCH_STATUSES,
        "business_date": batch.business_date.isoformat() if batch.business_date else None,
        "file_name": batch.file_name,
        "total_quarantined": total,
        "total_blocked": blocked,
        "total_releasable": total - blocked,
        "released_count": base.filter(BankFeedRow.status == "RELEASED").count(),
        # Why `raw` is absent, when it was asked for and withheld. Stated so a
        # reviewer is not left wondering whether the row simply had no data.
        "raw_pii_available": serve_raw,
        "raw_pii_withheld_reason": pii_note,
        "items": [row_dict(r, include_raw=serve_raw) for r in rows],
    }


def get_row(db: Session, ctx, row_id: str, *, include_raw: bool = False,
            ip_address: str | None = None, user_agent: str | None = None) -> dict:
    """One held row. Another bank's row is the uniform 404.

    The SECOND raw-PII path, gated identically to the listing — a check on one
    endpoint only would be no check at all.
    """
    bank = _bank_or_403(db, getattr(ctx, "bank_id", None))
    row = (db.query(BankFeedRow)
           .filter(BankFeedRow.id == row_id, BankFeedRow.bank_id == bank.id)
           .first())
    if row is None:
        raise _not_found()

    serve_raw = False
    pii_note = None
    if include_raw:
        if not _may_read_raw(ctx):
            pii_note = "not_authorised"
        elif _audit_raw_read(db, ctx, batch_id=row.batch_id, row_ids=[row.id],
                             endpoint="GET /bank/feed/quarantine/rows/{row_id}",
                             ip_address=ip_address, user_agent=user_agent):
            serve_raw = True
        else:
            pii_note = "audit_unavailable"

    out = row_dict(row, include_raw=serve_raw)
    out["raw_pii_available"] = serve_raw
    out["raw_pii_withheld_reason"] = pii_note
    return out


def _claim(db: Session, row_id: str, bank_id: str) -> BankFeedRow | None:
    """Take the row under a lock, or report that someone else has it.

    `with_for_update()` plus the status check is the claim: a second concurrent
    release waits for this transaction, then sees RELEASED and declines. The
    bank_id is in the WHERE clause, so a row of another bank is never locked at
    all, let alone released.
    """
    row = (db.query(BankFeedRow)
           .filter(BankFeedRow.id == row_id, BankFeedRow.bank_id == bank_id,
                   BankFeedRow.status == "QUARANTINED")
           .with_for_update()
           .first())
    return row


def release_rows(db: Session, ctx, batch_id: str, row_ids: list[str], *, on: date | None = None,
                 ip_address: str | None = None) -> dict:
    """Re-try the named held rows through the existing placement rules.

    One transaction for the whole call: either every verdict and its audit row
    land together, or none do. The verdicts are per row — a batch of mixed
    eligible and ineligible rows is normal, and each one says what happened to
    it rather than the call succeeding or failing as a whole.
    """
    bank = _bank_or_403(db, getattr(ctx, "bank_id", None))
    batch = _scoped_batch(db, bank.id, batch_id)
    actor_id = getattr(ctx, "user_id", None)

    if batch.status not in RELEASABLE_BATCH_STATUSES:
        # Not a 404 — the caller may see this batch; it is simply not in a state
        # where releasing a row means anything yet.
        raise AppException(409, ErrorCode.CONFLICT,
                           f"This batch is {batch.status}; rows can be released once it has been processed.")
    if not row_ids:
        raise AppException(422, ErrorCode.VALIDATION_ERROR, "Name at least one row to release.")
    if len(row_ids) > MAX_RELEASE_ROWS:
        raise AppException(422, ErrorCode.VALIDATION_ERROR,
                           f"Release is limited to {MAX_RELEASE_ROWS} rows at a time.")

    rules = PlacementService(db)
    day = on or date.today()
    now = datetime.now(timezone.utc)
    verdicts: list[dict] = []

    try:
        for row_id in dict.fromkeys(row_ids):          # de-duplicated, order kept
            row = _claim(db, row_id, bank.id)
            if row is None:
                # Either it is not ours / does not exist, or it is no longer
                # QUARANTINED. Told apart WITHOUT revealing another bank's row:
                # the second lookup is still scoped to this bank.
                seen = (db.query(BankFeedRow)
                        .filter(BankFeedRow.id == row_id, BankFeedRow.bank_id == bank.id)
                        .first())
                if seen is None:
                    verdicts.append({"row_id": row_id, "outcome": "NOT_FOUND",
                                     "reason": "No such row in this batch."})
                else:
                    # The duplicate-release answer: already done, not done again.
                    verdicts.append({"row_id": row_id, "outcome": "ALREADY_RELEASED",
                                     "reason": f"This row is already {seen.status}.",
                                     "row_no": seen.row_no,
                                     "released_at": seen.released_at.isoformat() if seen.released_at else None})
                continue

            if row.batch_id != batch.id:
                verdicts.append({"row_id": row_id, "outcome": "NOT_FOUND",
                                 "reason": "No such row in this batch."})
                continue

            if row.loan_id is None:
                # UNKNOWN_BRANCH. There is no loan to place, and this module
                # does not create one. Said plainly, and the row is left exactly
                # as it was.
                verdicts.append({
                    "row_id": row.id, "row_no": row.row_no, "outcome": "NOT_RELEASABLE",
                    "reason": "This row never created a loan, so there is nothing to place. "
                              "Add the branch to this bank, then re-ingest the file.",
                    "blocked_because": NO_LOAN,
                })
                continue

            loan = (db.query(Loan)
                    .filter(Loan.id == row.loan_id, Loan.bank_id == bank.id)
                    .first())
            if loan is None:
                verdicts.append({"row_id": row.id, "row_no": row.row_no, "outcome": "NOT_RELEASABLE",
                                 "reason": "The loan this row placed is no longer on this bank's book.",
                                 "blocked_because": NO_LOAN})
                continue

            # SECOND GUARD, independent of the row lock. `with_for_update()`
            # serialises concurrent releases on Postgres, but it is a no-op on
            # SQLite and it cannot help across separate transactions that both
            # read the row as QUARANTINED. A loan that ALREADY has an active
            # placement has been placed — by an earlier release, by the nightly
            # engine, or by a hand placement — and placing it again would open a
            # second case for one loan. Measured: without this, two concurrent
            # releases reached `open_case` and were stopped only by the
            # cases.case_number UNIQUE constraint, i.e. by a 500.
            existing = rules.active_placement(loan.id)
            if existing is not None:
                row.status = "RELEASED"
                row.released_at = row.released_at or now
                row.released_by = row.released_by or actor_id
                verdicts.append({
                    "row_id": row.id, "row_no": row.row_no, "outcome": "ALREADY_RELEASED",
                    "reason": "This loan is already placed with an agency.",
                    "placement_id": existing.id,
                })
                continue

            agency_id = _agency_for(row, batch, db, bank.id)
            try:
                placement = rules.place_new_loan(loan, agency_id=agency_id, on=day,
                                                 source="FEED", placed_by=actor_id)
            except PlacementRefused as refused:
                # THE REASON IS STILL NOT FIXED. The rules refused again, so the
                # row stays QUARANTINED and the new refusal is appended — the
                # explicit decline the reviewer needs to see. Validation is not
                # bypassed to make the release succeed.
                row.dq_errors = [*(row.dq_errors or []),
                                 {"reason": refused.reason, "detail": str(refused),
                                  "at": now.isoformat(), "on_release_by": actor_id}]
                verdicts.append({"row_id": row.id, "row_no": row.row_no, "outcome": "STILL_REFUSED",
                                 "reason": refused.reason, "detail": str(refused)})
                continue

            # Placed. The case is opened through the SAME service the engine
            # uses, so a released row ends up indistinguishable from one that
            # passed on the first run.
            rules.attach_expectation(placement, loan)
            case = rules.open_case(placement, loan,
                                   case_number=rules.case_number_for(placement),
                                   target_amount=rules.case_target_amount(loan))

            row.status = "RELEASED"
            row.released_at = now
            row.released_by = actor_id
            row.loan_id = loan.id

            stage_audit(db, action=AuditAction.PLACEMENT_CREATED, user_id=actor_id,
                        entity_type="Placement", entity_id=placement.id,
                        bank_id=placement.bank_id, agency_id=placement.agency_id,
                        details={"source": "FEED_RELEASE", "batch_id": batch.id,
                                 "feed_row_id": row.id, "row_no": row.row_no,
                                 "loan_id": loan.id, "agency_id": placement.agency_id,
                                 "case_id": case.id,
                                 "released_reason": (last_reason(row) or {}).get("reason")},
                        ip_address=ip_address)

            verdicts.append({"row_id": row.id, "row_no": row.row_no, "outcome": "RELEASED",
                             "placement_id": placement.id, "case_id": case.id,
                             "case_number": case.case_number,
                             "agency_id": placement.agency_id,
                             "released_at": now.isoformat()})

        # The batch's own count follows reality. Its STATUS does not change: a
        # PARTIAL batch is a historical fact about that file.
        remaining = (db.query(BankFeedRow)
                     .filter(BankFeedRow.batch_id == batch.id,
                             BankFeedRow.status == "QUARANTINED")
                     .count())
        batch.rows_quarantined = remaining
        released_now = sum(1 for v in verdicts if v["outcome"] == "RELEASED")
        if released_now and batch.rows_accepted is not None:
            batch.rows_accepted = batch.rows_accepted + released_now
        db.commit()
    except Exception:
        db.rollback()
        raise

    return {
        "batch_id": batch.id,
        "batch_status": batch.status,
        "released": sum(1 for v in verdicts if v["outcome"] == "RELEASED"),
        "still_refused": sum(1 for v in verdicts if v["outcome"] == "STILL_REFUSED"),
        "not_releasable": sum(1 for v in verdicts if v["outcome"] == "NOT_RELEASABLE"),
        "already_released": sum(1 for v in verdicts if v["outcome"] == "ALREADY_RELEASED"),
        "not_found": sum(1 for v in verdicts if v["outcome"] == "NOT_FOUND"),
        "remaining_quarantined": remaining,
        "verdicts": verdicts,
    }


def _agency_for(row: BankFeedRow, batch: BankFeedBatch, db: Session, bank_id: str) -> str | None:
    """Which agency this row is for, decided the way the engine decides it: the
    ROW's own `agency_code` if it names one, else nothing.

    The engine also has the file-level `--agency` default, which is a CLI
    argument and is not persisted on the batch — so a row that named no agency
    cannot be re-tried with a guess here. It is refused as NO_AGENCY by the
    rules, which is the honest answer rather than a silent choice of agency on
    the bank's behalf.
    """
    from app.models.tenancy import Agency

    code = ((row.raw or {}).get("agency_code") or "").strip()
    if not code:
        return None
    agency = (db.query(Agency)
              .filter(Agency.code == code, Agency.bank_id == bank_id)
              .first())
    return agency.id if agency else None
