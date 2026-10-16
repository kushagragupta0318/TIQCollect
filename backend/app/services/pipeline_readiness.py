"""Is the nightly chain safe to continue? One definition, for every stage.

THE CHAIN. Three stages, each reading what the one before it wrote:

    19:30  feed cutoff          today's bank feed has arrived and finished
    19:45  repayment scoring     reads the DPD and amounts ingest wrote
    20:00  nightly allocation    reads the scores scoring left behind

Until now the order was a comment in three files and an assumption in a
schedule: nothing checked it. A 20:00 allocation would plan happily on
yesterday's book if the feed never came, and the only evidence was an absence —
the same failure mode AllocationRunStatus.FAILED was added to close in
September (allocation.py's changelog).

WHAT THIS MODULE IS. The readiness question, asked once and answered from
PERSISTED FACTS rather than from task return values:

  * ingestion  — lending.bank_feed_batches for the business date, and whether
                 they reached a terminal, successful status.
  * scoring    — repayment_snapshots.as_of_date, whose (loan_id, as_of_date)
                 grain is already the record of "scoring ran for that day".

Nothing new is stored, so this needs no migration. A task return value would
not do: it lives in a result backend that expires, it is lost on a worker
restart, and it cannot answer "did this happen for THAT business date".

WHY IT GATES RATHER THAN ORDERS. Celery's beat fires on wall-clock time; it
has no notion of "after". A chain built on `chord`/`link` would couple the
three tasks into one unit that cannot be re-run stage by stage, and would still
not survive a worker restart mid-chain. Gating on persisted state instead means
each stage can be re-run by hand, in any order, and still refuses to act on
inputs that are not there — which is the property that actually matters.

TIMEZONE. Every date here is the IST business date, from core/geo.IST — the
project's one definition. The containers run UTC (celery_app sets
enable_utc=True), so `date.today()` in a worker is the UTC date: correct in the
19:30–20:00 window by luck of the +05:30 offset, and WRONG for any stage that
moves past 18:30 UTC. leave_service.leave_today() fixed exactly this bug in
September; this module does not reintroduce it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.geo import IST

# A batch in one of these has FINISHED ingesting. PARTIAL counts: rows were
# quarantined, which is a reviewable outcome (Phase 6), not a failed ingest —
# refusing to allocate because a single row was held would stop the whole
# agency's night over one bad record.
INGEST_DONE_STATUSES = ("LOADED", "PARTIAL")

# Still moving. Not a failure, but not ready either: allocating now would read
# a half-written book.
INGEST_IN_FLIGHT_STATUSES = ("RECEIVED", "VALIDATING")

# How old the newest finished batch may be and still count as "today's feed".
# The business date is the primary check; this is the belt-and-braces one for a
# batch stamped with today's date but loaded days ago (a backfill).
MAX_INGEST_AGE_HOURS = 24


def business_date(now: datetime | None = None) -> date:
    """The IST calendar date the nightly chain is working on.

    The one date function for the pipeline. `date.today()` in a UTC worker names
    the wrong day for anything scheduled after 18:30 UTC, and the nightly chain
    sits at 19:30–20:00 IST = 14:00–14:30 UTC — on the right side of midnight
    today, but one DST-free accident away from not being. Named explicitly so
    nobody has to redo that arithmetic.
    """
    return (now or datetime.now(IST)).astimezone(IST).date()


def _aware(value: datetime | None) -> datetime | None:
    """UTC-aware, whatever the driver returned.

    Postgres hands back an aware TIMESTAMPTZ; SQLite does not. Comparing a
    naive value to an aware one raises TypeError, so every datetime read from a
    row passes through here — the same fix feed_ingestion._aware makes, for the
    same reason.
    """
    if value is None:
        return None
    # A naive value is assumed UTC, because that is what the app wrote: every
    # loaded_at is set from datetime.now(timezone.utc) (feed_ingestion,
    # feed_upload_service). Assuming IST here would shift a real timestamp by
    # 5.5 hours and could make tonight's feed look like a backfill.
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


@dataclass
class StageStatus:
    """One upstream stage's verdict. `ready` is the only thing a caller should
    branch on; the rest is for the operator reading the log line."""
    stage: str
    ready: bool
    reason: str
    detail: dict = field(default_factory=dict)

    def as_json(self) -> dict:
        return {"stage": self.stage, "ready": self.ready, "reason": self.reason, **self.detail}


def ingestion_status(db: Session, on: date, *, now: datetime | None = None) -> StageStatus:
    """Did the day's bank feed arrive and finish ingesting?

    Read from bank_feed_batches, which is the record of work the upload API and
    the CLI both write. A deployment with several banks is ready when AT LEAST
    ONE bank's feed landed: a bank that sends nothing tonight must not stop
    every other bank's agencies from being planned. Which banks are missing is
    reported, so that is visible rather than silent.
    """
    from app.models.lending import BankFeedBatch

    rows = (db.query(BankFeedBatch.bank_id, BankFeedBatch.status, BankFeedBatch.loaded_at,
                     BankFeedBatch.rows_accepted, BankFeedBatch.rows_quarantined)
            .filter(BankFeedBatch.business_date == on,
                    BankFeedBatch.feed_type == "DAILY_BOOK")
            .all())

    if not rows:
        return StageStatus("ingestion", False, "no bank feed has been ingested for this business date",
                           {"business_date": str(on), "batches": 0})

    done = [r for r in rows if r.status in INGEST_DONE_STATUSES]
    in_flight = [r for r in rows if r.status in INGEST_IN_FLIGHT_STATUSES]
    rejected = [r for r in rows if r.status == "REJECTED"]

    if not done:
        # Every batch for the day is either still moving or refused. Both mean
        # "do not allocate on this", and they are told apart because the
        # operator's next action differs: wait, versus fix and re-upload.
        if in_flight:
            return StageStatus("ingestion", False, "the day's feed is still being ingested",
                               {"business_date": str(on), "in_flight": len(in_flight),
                                "rejected": len(rejected)})
        return StageStatus("ingestion", False, "every feed for this business date was rejected",
                           {"business_date": str(on), "rejected": len(rejected)})

    # Stale check. A batch carrying today's business_date but loaded long ago is
    # a backfill, not tonight's feed.
    newest = max((_aware(r.loaded_at) for r in done if r.loaded_at is not None), default=None)
    if newest is not None:
        age = (now or datetime.now(IST)).astimezone(IST) - newest.astimezone(IST)
        if age > timedelta(hours=MAX_INGEST_AGE_HOURS):
            return StageStatus("ingestion", False,
                               f"the newest finished feed for this date is {age.days} day(s) old",
                               {"business_date": str(on), "loaded_at": newest.isoformat()})

    return StageStatus("ingestion", True, "the day's feed finished ingesting",
                       {"business_date": str(on), "banks_loaded": len({r.bank_id for r in done}),
                        "banks_missing": len({r.bank_id for r in rows}) - len({r.bank_id for r in done}),
                        "in_flight": len(in_flight), "rejected": len(rejected)})


# A scoring run that died part-way must not read as a finished stage. The run
# scores EVERY loan (repayment_service.rescore has no status filter), so the
# snapshot count for the date is compared against the book.
#
# The bar is deliberately low rather than exact. A loan can legitimately
# abstain, loans are added between runs, and the gate's job is to catch a run
# that STOPPED EARLY — not to re-audit the scorer. A crashed run leaves a
# handful of rows against a book of thousands, which this catches easily; it
# will not catch a run that covered 60% and died, and that is an accepted
# limit rather than an oversight.
MIN_SCORED_FRACTION = 0.5

# Below this, the ratio is not evidence of anything: a small test book or a
# freshly seeded environment would trip it for no operational reason.
MIN_LOANS_FOR_RATIO = 20


def scoring_status(db: Session, on: date) -> StageStatus:
    """Did repayment scoring run — to completion — for this business date?

    repayment_snapshots.as_of_date IS the record: the task's grain is
    (loan_id, as_of_date) with a unique constraint, so a row for `on` means
    scoring reached that loan for that day.

    COUNTED, NOT MERELY PRESENT. Until 2026-10-16 this asked only whether ANY
    snapshot existed, so a run that crashed after its first loan reported the
    stage ready and allocation planned on a book that was almost entirely
    unscored. The docstring claimed counting; the code did not do it.
    """
    from app.models.loan import Loan
    from app.models.repayment_snapshot import RepaymentSnapshot

    scored = (db.query(func.count(RepaymentSnapshot.id))
              .filter(RepaymentSnapshot.as_of_date == on).scalar() or 0)
    if not scored:
        return StageStatus("scoring", False, "no repayment snapshot exists for this business date",
                           {"business_date": str(on), "snapshots": 0, "loans": None})

    # Scoped to the banks that actually have a snapshot for this date, so one
    # bank's unscored book cannot close the gate on every other bank's night —
    # and so a shared test database full of unrelated loans does not either.
    scored_banks = [b for (b,) in db.query(RepaymentSnapshot.bank_id)
                    .filter(RepaymentSnapshot.as_of_date == on).distinct().all()]
    loans = (db.query(func.count(Loan.id))
             .filter(Loan.bank_id.in_(scored_banks)).scalar() or 0) if scored_banks else 0

    # Small books carry no signal: a handful of loans makes the ratio noise.
    if loans >= MIN_LOANS_FOR_RATIO and scored < loans * MIN_SCORED_FRACTION:
        return StageStatus(
            "scoring", False,
            f"scoring covered only {scored} of {loans} loans for this business date, "
            "which suggests a run that did not finish",
            {"business_date": str(on), "snapshots": scored, "loans": loans})

    return StageStatus("scoring", True, "scoring has written snapshots for this business date",
                       {"business_date": str(on), "snapshots": scored, "loans": loans or None})


def allocation_gate(db: Session, on: date, *, now: datetime | None = None) -> tuple[bool, list[StageStatus]]:
    """May the 20:00 allocation proceed for business date `on`?

    Both upstream stages are evaluated — not short-circuited — so the operator
    sees everything that is wrong in one line rather than fixing one problem to
    discover the next. The feed is checked for the SAME business date the plan
    is being built from, which is what "for the correct business date" means:
    a feed that landed for yesterday does not authorise tonight's plan.
    """
    stages = [ingestion_status(db, on, now=now), scoring_status(db, on)]
    return all(s.ready for s in stages), stages


def gate_summary(stages: list[StageStatus]) -> dict:
    """The gate's verdict, shaped for a log line and for summary_metadata."""
    return {
        "ready": all(s.ready for s in stages),
        "blocked_by": [s.stage for s in stages if not s.ready],
        "stages": [s.as_json() for s in stages],
    }
