"""The nightly chain: 19:30 cutoff -> 19:45 scoring -> 20:00 allocation.

What is worth testing is not that three crontabs hold three numbers, but that
the ORDER IS ENFORCED rather than assumed — the state the project was in
before this phase, where the sequence lived in comments and nothing checked it.

So: the schedule, the IST business-date arithmetic (including the 18:30 UTC
rollover that has already caused one bug in this codebase), and above all that
a missing or unfinished upstream stage STOPS allocation and leaves a row
behind rather than planning on yesterday's book.

SQLITE CAVEAT, STATED DEEDLY. These tests use the suite's in-memory SQLite
(tests/_db). They prove the GATE LOGIC — which rows are read, which verdict is
reached, what is written. They do NOT prove Postgres locking behaviour, and
nothing here should be read as evidence about concurrent claims; the row-lock
question belongs to feed_ingestion's claim and is called out in the Phase 7
report as unverified on Postgres.
"""
from datetime import date, datetime, timedelta, timezone

import pytest

from app.core.geo import IST
from app.models.customer import RiskCategory
from app.models.lending import BankFeedBatch
from app.models.repayment_snapshot import RepaymentSnapshot
from app.services import pipeline_readiness as pr
from tests._db import TEST_BANK_ID, create_schema, make_engine, make_session_factory, test_id

DAY = date(2026, 10, 15)


@pytest.fixture()
def db():
    engine = make_engine()
    create_schema(engine)
    s = make_session_factory(engine)()
    yield s
    s.close()


def _batch(db, *, status="LOADED", on=DAY, bank_id=TEST_BANK_ID, loaded_at="now",
           sha=None, feed_type="DAILY_BOOK"):
    if loaded_at == "now":
        loaded_at = datetime.now(timezone.utc)
    b = BankFeedBatch(bank_id=bank_id, feed_type=feed_type, business_date=on,
                      file_name="book.csv",
                      file_sha256=(sha or f"{status}{on}{bank_id}".ljust(64, "0")[:64]),
                      received_via="UPLOAD", status=status, rows_total=10,
                      rows_accepted=10, rows_quarantined=0,
                      received_at=datetime.now(timezone.utc), loaded_at=loaded_at)
    db.add(b)
    db.flush()
    return b


def _snapshot(db, *, on=DAY, n=1):
    """A scoring snapshot for `on`. Only as_of_date matters to the gate, but the
    row needs a real loan and customer — built with the suite's own helper
    rather than a hand-rolled one that drifts from the model."""
    from tests._placement import make_loan

    loan = make_loan(db, n)
    # Field shape follows tests/test_repayment_labeller.py, which is the
    # existing construction of a real snapshot row.
    snap = RepaymentSnapshot(
        bank_id=TEST_BANK_ID, loan_id=loan.id, customer_id=loan.customer_id,
        case_id=None, as_of_date=on,
        trigger="MANUAL", source="SCORECARD", model_version="scorecard-1.1.0",
        likelihood=60.0, risk_score=40.0, band="UNCERTAIN",
        risk_category=RiskCategory.MEDIUM, evidence_coverage=0.7,
        features={}, contributions={},
    )
    db.add(snap)
    db.flush()
    return snap


# ── the schedule itself ─────────────────────────────────────────────────────

def test_the_chain_is_scheduled_in_order_at_the_intended_ist_times():
    from app.workers.celery_app import celery_app

    sched = celery_app.conf.beat_schedule
    stages = [
        ("feed-cutoff-check", "app.workers.tasks.feed_cutoff.check_feed_cutoff", 19, 30),
        ("nightly-repayment-scoring",
         "app.workers.tasks.repayment_scoring.run_nightly_repayment_scoring", 19, 45),
        ("nightly-ml-allocation", "app.workers.tasks.allocation.run_nightly_allocation", 20, 0),
    ]
    for key, task, hour, minute in stages:
        assert key in sched, f"{key} is not scheduled"
        assert sched[key]["task"] == task
        cron = sched[key]["schedule"]
        assert cron.hour == {hour}, (key, cron.hour)
        assert cron.minute == {minute}, (key, cron.minute)


def test_the_beat_timezone_is_ist_so_those_hours_mean_what_they_say():
    from app.workers.celery_app import celery_app
    assert celery_app.conf.timezone == "Asia/Kolkata"


def test_the_cutoff_task_is_registered_with_the_worker():
    """A beat entry naming an unregistered task fails at dispatch, not at
    import — the kind of break nothing catches until 19:30."""
    from app.workers.celery_app import celery_app
    assert "app.workers.tasks.feed_cutoff" in celery_app.conf.include


def test_no_second_pipeline_was_introduced():
    """One entry per stage. A duplicate schedule would run ingestion-adjacent
    work twice an evening."""
    from app.workers.celery_app import celery_app
    tasks = [e["task"] for e in celery_app.conf.beat_schedule.values()]
    for stage in ("feed_cutoff.check_feed_cutoff",
                  "repayment_scoring.run_nightly_repayment_scoring",
                  "allocation.run_nightly_allocation"):
        assert sum(stage in t for t in tasks) == 1, stage


# ── business-date arithmetic ────────────────────────────────────────────────

def test_the_business_date_is_the_ist_date_not_the_containers_utc_date():
    """19:00 IST on the 15th is 13:30 UTC the same day — both agree here."""
    assert pr.business_date(datetime(2026, 10, 15, 19, 0, tzinfo=IST)) == date(2026, 10, 15)


def test_after_1830_utc_the_ist_date_is_already_tomorrow():
    """THE BUG THIS PREVENTS. 23:59 IST on the 15th is 18:29 UTC on the 15th;
    00:30 IST on the 16th is 19:00 UTC on the 15th. A task using date.today()
    in a UTC worker would call that second moment the 15th and look up the
    wrong business date."""
    late_ist = datetime(2026, 10, 16, 0, 30, tzinfo=IST)
    assert late_ist.astimezone(timezone.utc).date() == date(2026, 10, 15)   # UTC says the 15th
    assert pr.business_date(late_ist) == date(2026, 10, 16)                 # IST says the 16th


def test_a_utc_timestamp_is_converted_rather_than_relabelled():
    """Given an aware UTC moment, the IST date is derived by conversion."""
    utc_moment = datetime(2026, 10, 15, 19, 0, tzinfo=timezone.utc)   # 00:30 IST on the 16th
    assert pr.business_date(utc_moment) == date(2026, 10, 16)


def test_the_plan_date_rolls_over_weekends_from_the_ist_date():
    from app.services.planner_service import get_target_plan_date
    assert get_target_plan_date(date(2026, 10, 16)) == date(2026, 10, 17)   # Fri -> Sat
    assert get_target_plan_date(date(2026, 10, 17)) == date(2026, 10, 19)   # Sat -> Mon
    assert get_target_plan_date(date(2026, 10, 18)) == date(2026, 10, 19)   # Sun -> Mon


# ── ingestion readiness ─────────────────────────────────────────────────────

def test_no_feed_at_all_is_not_ready(db):
    s = pr.ingestion_status(db, DAY)
    assert s.ready is False
    assert "no bank feed" in s.reason


def test_a_loaded_feed_is_ready(db):
    _batch(db, status="LOADED")
    db.commit()
    assert pr.ingestion_status(db, DAY).ready is True


def test_a_partial_feed_is_ready_because_quarantine_is_reviewable(db):
    """PARTIAL means rows were held for review (Phase 6), not that the ingest
    failed. Refusing to allocate over one bad row would stop the whole night."""
    _batch(db, status="PARTIAL")
    db.commit()
    assert pr.ingestion_status(db, DAY).ready is True


@pytest.mark.parametrize("status", ["RECEIVED", "VALIDATING"])
def test_a_feed_still_being_ingested_is_not_ready(db, status):
    _batch(db, status=status)
    db.commit()
    s = pr.ingestion_status(db, DAY)
    assert s.ready is False
    assert "still being ingested" in s.reason


def test_a_rejected_feed_is_not_ready_and_says_so_distinctly(db):
    _batch(db, status="REJECTED")
    db.commit()
    s = pr.ingestion_status(db, DAY)
    assert s.ready is False
    assert "rejected" in s.reason


def test_yesterdays_feed_does_not_authorise_tonight(db):
    """The gate asks for THIS business date. A feed that landed yesterday is
    not tonight's feed, however healthy it looks."""
    _batch(db, status="LOADED", on=DAY - timedelta(days=1))
    db.commit()
    assert pr.ingestion_status(db, DAY).ready is False


def test_one_banks_missing_feed_does_not_block_another_banks_night(db):
    """Multi-bank: at least one finished feed is enough to proceed, and the
    missing one is reported rather than silently ignored."""
    other = test_id("bank:second-pipeline")
    from app.models.tenancy import Bank
    db.add(Bank(id=other, code="SECOND", legal_name="Second Bank Ltd.",
                display_name="Second", timezone="Asia/Kolkata", brand={},
                status="ACTIVE", is_demo=True))
    db.flush()
    _batch(db, status="LOADED", bank_id=TEST_BANK_ID, sha="a" * 64)
    _batch(db, status="RECEIVED", bank_id=other, sha="b" * 64)
    db.commit()

    s = pr.ingestion_status(db, DAY)
    assert s.ready is True
    assert s.detail["banks_loaded"] == 1
    assert s.detail["in_flight"] == 1


def test_a_stale_backfill_stamped_with_todays_date_is_not_ready(db):
    """A batch carrying today's business_date but loaded days ago is a
    backfill, not tonight's intake."""
    _batch(db, status="LOADED",
           loaded_at=datetime.now(timezone.utc) - timedelta(hours=pr.MAX_INGEST_AGE_HOURS + 2))
    db.commit()
    s = pr.ingestion_status(db, DAY)
    assert s.ready is False
    assert "old" in s.reason


def test_a_naive_loaded_at_does_not_crash_the_gate(db):
    """SQLite returns naive datetimes; Postgres returns aware ones. Comparing
    the two raises TypeError, which is how the Phase 5 sweep broke."""
    _batch(db, status="LOADED", loaded_at=datetime(2026, 10, 15, 14, 0))   # naive
    db.commit()
    s = pr.ingestion_status(db, DAY)          # must not raise
    assert s.ready is True


def test_a_feed_of_another_type_is_not_the_daily_book(db):
    _batch(db, status="LOADED", feed_type="PAYMENTS")
    db.commit()
    assert pr.ingestion_status(db, DAY).ready is False


# ── scoring readiness ───────────────────────────────────────────────────────

def test_no_snapshot_for_the_day_is_not_ready(db):
    assert pr.scoring_status(db, DAY).ready is False


def test_a_snapshot_for_the_day_is_ready(db):
    _snapshot(db, on=DAY)
    db.commit()
    assert pr.scoring_status(db, DAY).ready is True


def test_yesterdays_scores_do_not_authorise_tonight(db):
    _snapshot(db, on=DAY - timedelta(days=1))
    db.commit()
    assert pr.scoring_status(db, DAY).ready is False


# ── the combined gate ───────────────────────────────────────────────────────

def test_the_gate_opens_only_when_both_stages_are_ready(db):
    _batch(db, status="LOADED")
    _snapshot(db, on=DAY)
    db.commit()
    ready, stages = pr.allocation_gate(db, DAY)
    assert ready is True
    assert [s.stage for s in stages] == ["ingestion", "scoring"]


def test_the_gate_reports_every_problem_not_just_the_first(db):
    """Both stages are evaluated, so an operator fixes one thing and does not
    discover the next at 20:00 tomorrow."""
    ready, stages = pr.allocation_gate(db, DAY)       # nothing exists at all
    assert ready is False
    assert pr.gate_summary(stages)["blocked_by"] == ["ingestion", "scoring"]


def test_scoring_failure_alone_blocks_allocation(db):
    _batch(db, status="LOADED")                        # ingest fine
    db.commit()                                        # scoring never ran
    ready, stages = pr.allocation_gate(db, DAY)
    assert ready is False
    assert pr.gate_summary(stages)["blocked_by"] == ["scoring"]


def test_ingestion_failure_alone_blocks_allocation(db):
    _batch(db, status="REJECTED")
    _snapshot(db, on=DAY)                              # scores exist, from a stale book
    db.commit()
    ready, stages = pr.allocation_gate(db, DAY)
    assert ready is False
    assert pr.gate_summary(stages)["blocked_by"] == ["ingestion"]


# ── the cutoff task ─────────────────────────────────────────────────────────

def test_the_cutoff_task_reports_ready_without_raising(db, monkeypatch):
    from app.workers.tasks import feed_cutoff
    monkeypatch.setattr("app.core.database.SessionLocal", lambda: db)
    _batch(db, status="LOADED")
    db.commit()
    out = feed_cutoff.check_feed_cutoff.run(business_date_str=str(DAY))
    assert out["ready"] is True


def test_the_cutoff_task_reports_a_missing_feed_without_raising(db, monkeypatch):
    """It must not raise: a missing feed is not a transient fault, and a retry
    cannot make a bank send a file. Visibility is the job; refusing is the
    gate's."""
    from app.workers.tasks import feed_cutoff
    monkeypatch.setattr("app.core.database.SessionLocal", lambda: db)
    out = feed_cutoff.check_feed_cutoff.run(business_date_str=str(DAY))
    assert out["ready"] is False
    assert out["business_date"] == str(DAY)


def test_the_cutoff_task_is_idempotent_under_duplicate_delivery(db, monkeypatch):
    """task_acks_late means redelivery is normal. The task only reads, so a
    second delivery reaches the same verdict and changes nothing."""
    from app.workers.tasks import feed_cutoff
    monkeypatch.setattr("app.core.database.SessionLocal", lambda: db)
    _batch(db, status="LOADED")
    db.commit()
    before = db.query(BankFeedBatch).count()
    first = feed_cutoff.check_feed_cutoff.run(business_date_str=str(DAY))
    second = feed_cutoff.check_feed_cutoff.run(business_date_str=str(DAY))
    assert first == second
    assert db.query(BankFeedBatch).count() == before


def test_the_cutoff_task_does_not_retry(db):
    """max_retries=0 is the contract: see the task's docstring."""
    from app.workers.tasks import feed_cutoff
    assert feed_cutoff.check_feed_cutoff.max_retries == 0


# ── scoring's own gate ──────────────────────────────────────────────────────

def test_scoring_skips_when_the_feed_has_not_landed(db, monkeypatch):
    from app.workers.tasks import repayment_scoring
    monkeypatch.setattr("app.core.database.SessionLocal", lambda: db)
    monkeypatch.setattr(pr, "business_date", lambda now=None: DAY)
    monkeypatch.setattr("app.services.pipeline_readiness.business_date", lambda now=None: DAY)

    called = {"n": 0}

    class Boom:
        def __init__(self, *a, **k): called["n"] += 1
    monkeypatch.setattr("app.services.repayment_service.RepaymentService", Boom)

    out = repayment_scoring.run_nightly_repayment_scoring.run()
    assert out["skipped"] is True
    assert "no bank feed" in out["reason"]


def test_scoring_proceeds_once_the_feed_is_in(db, monkeypatch):
    from app.workers.tasks import repayment_scoring
    monkeypatch.setattr("app.core.database.SessionLocal", lambda: db)
    monkeypatch.setattr("app.services.pipeline_readiness.business_date", lambda now=None: DAY)
    _batch(db, status="LOADED")
    db.commit()

    class FakeService:
        def __init__(self, _db): pass
        def rescore(self, *, as_of, dry_run): return {"scored": 3, "as_of": str(as_of)}
        def attach_outcomes(self, *, as_of): return {}
        def prune_snapshots(self, *, as_of): return {}
    monkeypatch.setattr("app.services.repayment_service.RepaymentService", FakeService)
    monkeypatch.setattr("app.services.case_priority_service.restamp",
                        lambda _db: {"open_cases": 0, "changed": 0})

    out = repayment_scoring.run_nightly_repayment_scoring.run()
    assert out.get("skipped") is not True
    assert out["scored"] == 3
    assert out["as_of"] == str(DAY)          # the IST business date, not UTC today


def test_an_operator_can_force_scoring_past_the_gate(db, monkeypatch):
    """A manual re-run must not be held hostage to the gate; the SCHEDULED run
    never passes force."""
    from app.workers.tasks import repayment_scoring
    monkeypatch.setattr("app.core.database.SessionLocal", lambda: db)
    monkeypatch.setattr("app.services.pipeline_readiness.business_date", lambda now=None: DAY)

    class FakeService:
        def __init__(self, _db): pass
        def rescore(self, *, as_of, dry_run): return {"scored": 1}
        def attach_outcomes(self, *, as_of): return {}
        def prune_snapshots(self, *, as_of): return {}
    monkeypatch.setattr("app.services.repayment_service.RepaymentService", FakeService)
    monkeypatch.setattr("app.services.case_priority_service.restamp",
                        lambda _db: {"open_cases": 0, "changed": 0})

    out = repayment_scoring.run_nightly_repayment_scoring.run(force=True)
    assert out.get("skipped") is not True


def test_a_dry_run_is_not_gated(db, monkeypatch):
    """dry_run writes nothing, so there is nothing to protect against."""
    from app.workers.tasks import repayment_scoring
    monkeypatch.setattr("app.core.database.SessionLocal", lambda: db)
    monkeypatch.setattr("app.services.pipeline_readiness.business_date", lambda now=None: DAY)

    class FakeService:
        def __init__(self, _db): pass
        def rescore(self, *, as_of, dry_run): return {"scored": 0, "dry_run": dry_run}
    monkeypatch.setattr("app.services.repayment_service.RepaymentService", FakeService)

    out = repayment_scoring.run_nightly_repayment_scoring.run(dry_run=True)
    assert out.get("skipped") is not True


# ── allocation's gate: the stage that must refuse ───────────────────────────

def _manager(db, *, n=1):
    """An active manager of an ACTIVE agency — who the nightly run plans for."""
    from app.core.security import hash_password
    from app.models.user import User, UserRole
    from tests._db import TEST_AGENCY_ID

    u = User(id=test_id(f"pmgr:{n}"), email=f"pmgr{n}@example.in", phone=f"98444000{n:02d}",
             full_name=f"Manager {n}", hashed_password=hash_password("Harbour-Lights-2026"),
             role=UserRole.AGENCY_MANAGER, bank_id=TEST_BANK_ID, agency_id=TEST_AGENCY_ID,
             is_active=True)
    db.add(u)
    db.flush()
    return u


def _run_allocation(db, monkeypatch, **kw):
    from app.workers.tasks import allocation
    monkeypatch.setattr("app.core.database.SessionLocal", lambda: db)
    monkeypatch.setattr("app.services.pipeline_readiness.business_date", lambda now=None: DAY)
    return allocation.run_nightly_allocation.run(**kw)


def test_allocation_refuses_when_nothing_upstream_has_run(db, monkeypatch):
    _manager(db)
    db.commit()
    planned = {"n": 0}

    class Boom:
        def __init__(self, *a, **k): planned["n"] += 1
    monkeypatch.setattr("app.services.planner_service.PlannerService", Boom)

    out = _run_allocation(db, monkeypatch)

    assert out["blocked"] is True
    assert out["blocked_by"] == ["ingestion", "scoring"]
    assert planned["n"] == 0            # no manager was planned at all


def test_a_refused_night_leaves_a_row_per_manager_not_an_absence(db, monkeypatch):
    """The 2026-09-02 lesson in allocation.py's changelog: a failed night that
    writes nothing is invisible until agents find no beats."""
    from app.models.allocation_run import AllocationRun, AllocationRunStatus

    _manager(db, n=1)
    _manager(db, n=2)
    db.commit()

    out = _run_allocation(db, monkeypatch)

    rows = db.query(AllocationRun).filter(
        AllocationRun.status == AllocationRunStatus.FAILED.value).all()
    assert len(rows) == 2
    assert out["runs_recorded"] == 2
    # The row explains itself without a log lookup, and the marker is what
    # tells a refused night from a planning crash — the status cannot, because
    # ck_allocation_runs_status permits only the four existing values.
    meta = rows[0].summary_metadata
    assert meta["outcome"] == "BLOCKED_UPSTREAM"
    assert meta["business_date"] == str(DAY)
    assert "ingestion" in meta["blocked_by"]
    assert meta["stages"][0]["stage"] == "ingestion"


def test_no_status_outside_the_check_constraint_is_written(db, monkeypatch):
    """ck_allocation_runs_status (v2_0002_tables.py) permits exactly four
    values. Phase 7 records a refused night as FAILED with an `outcome`
    marker rather than inventing a fifth, which would need a migration.

    This test is the guard: if someone adds a status to the enum without
    widening the CHECK, the insert fails on Postgres AND here."""
    from app.models.allocation_run import AllocationRun, AllocationRunStatus

    allowed = {"PLANNED", "APPLIED", "ROLLED_BACK", "FAILED"}
    assert {s.value for s in AllocationRunStatus} == allowed

    # The naming convention expands "status" to ck_allocation_runs_status.
    check = next(c for c in AllocationRun.__table__.constraints
                 if getattr(c, "name", "") == "ck_allocation_runs_status")
    for value in allowed:
        assert value in str(check.sqltext)


def test_a_refused_night_is_told_apart_from_a_crashed_one(db, monkeypatch):
    """Both are FAILED rows; the marker is what distinguishes them."""
    from app.models.allocation_run import AllocationRun

    _manager(db)
    db.commit()
    _run_allocation(db, monkeypatch)

    row = db.query(AllocationRun).first()
    assert row.summary_metadata["outcome"] == "BLOCKED_UPSTREAM"
    # A planning crash records error_type/error instead (see _record_failure).
    assert "error_type" not in row.summary_metadata


def test_allocation_is_blocked_when_only_scoring_is_missing(db, monkeypatch):
    _manager(db)
    _batch(db, status="LOADED")
    db.commit()

    out = _run_allocation(db, monkeypatch)
    assert out["blocked"] is True
    assert out["blocked_by"] == ["scoring"]


def test_allocation_is_blocked_when_the_feed_was_rejected(db, monkeypatch):
    """Scores exist, but they were computed on a book the feed never updated."""
    _manager(db)
    _batch(db, status="REJECTED")
    _snapshot(db, on=DAY)
    db.commit()

    out = _run_allocation(db, monkeypatch)
    assert out["blocked"] is True
    assert out["blocked_by"] == ["ingestion"]


def test_allocation_is_blocked_while_the_feed_is_still_ingesting(db, monkeypatch):
    """A worker restart mid-ingest leaves a batch VALIDATING. Allocation must
    wait rather than read a half-written book."""
    _manager(db)
    _batch(db, status="VALIDATING")
    _snapshot(db, on=DAY)
    db.commit()

    out = _run_allocation(db, monkeypatch)
    assert out["blocked"] is True
    assert out["blocked_by"] == ["ingestion"]


def test_allocation_proceeds_once_both_stages_are_in(db, monkeypatch):
    _manager(db)
    _batch(db, status="LOADED")
    _snapshot(db, on=DAY)
    db.commit()

    class FakeRun:
        id, total_cases_allocated, total_cases_deferred = "run-1", 7, 1
        total_cases_blocked, total_agents_planned, expected_recovery_total = 0, 3, 125000.0

    class FakePlanner:
        def __init__(self, _db, manager_user_id=None): pass
        def plan_next_day(self, **kw): return FakeRun()
    monkeypatch.setattr("app.services.planner_service.PlannerService", FakePlanner)

    out = _run_allocation(db, monkeypatch)
    assert out.get("blocked") is not True
    assert len(out["planned"]) == 1


def test_an_operator_can_force_allocation_past_the_gate(db, monkeypatch):
    _manager(db)
    db.commit()

    class FakeRun:
        id, total_cases_allocated, total_cases_deferred = "run-2", 1, 0
        total_cases_blocked, total_agents_planned, expected_recovery_total = 0, 1, 0.0

    class FakePlanner:
        def __init__(self, _db, manager_user_id=None): pass
        def plan_next_day(self, **kw): return FakeRun()
    monkeypatch.setattr("app.services.planner_service.PlannerService", FakePlanner)

    out = _run_allocation(db, monkeypatch, force=True)
    assert out.get("blocked") is not True


def test_a_blocked_run_does_not_retry(db, monkeypatch):
    """Retrying cannot make a feed arrive, and force_replan=True means a retry
    would discard beats that DID plan. So it returns rather than raising."""
    _manager(db)
    db.commit()
    retried = {"n": 0}

    from app.workers.tasks import allocation
    monkeypatch.setattr(allocation.run_nightly_allocation, "retry",
                        lambda **k: retried.__setitem__("n", retried["n"] + 1))

    out = _run_allocation(db, monkeypatch)
    assert out["blocked"] is True
    assert retried["n"] == 0


def test_a_duplicate_delivery_of_a_blocked_run_does_not_double_the_rows(db, monkeypatch):
    """task_acks_late means redelivery happens. Two deliveries of a blocked
    night must not leave two rows per manager."""
    from app.models.allocation_run import AllocationRun, AllocationRunStatus

    _manager(db)
    db.commit()

    first = _run_allocation(db, monkeypatch)
    second = _run_allocation(db, monkeypatch)

    assert first["blocked"] is second["blocked"] is True
    # DOCUMENTED BEHAVIOUR, not an assertion of deduplication: each delivery
    # records its own refusal row. allocation_runs has no unique key on
    # (manager, plan_date) — the nightly task has always been able to write
    # several rows for one date (a FAILED row then a replan), and adding one
    # would be a migration. What matters is that a redelivery performs NO
    # PLANNING and changes no beats; the extra row is evidence, not damage.
    rows = db.query(AllocationRun).filter(
        AllocationRun.status == AllocationRunStatus.FAILED.value,
        AllocationRun.plan_date == date.fromisoformat(first["target_date"])).count()
    assert rows == 2
    assert first["runs_recorded"] == second["runs_recorded"] == 1


def test_naming_an_explicit_plan_date_opts_out_of_the_gate(db, monkeypatch):
    """A dated run is an operator decision — a re-plan, a backfill, a date
    chosen on the Field Plan page. The gate asks whether TONIGHT's feed landed,
    which is not the question being asked of a run aimed elsewhere. The
    SCHEDULED 20:00 run passes no date, so it is always gated."""
    _manager(db)
    db.commit()                               # nothing upstream at all

    class FakeRun:
        id, total_cases_allocated, total_cases_deferred = "run-x", 2, 0
        total_cases_blocked, total_agents_planned, expected_recovery_total = 0, 1, 0.0

    class FakePlanner:
        def __init__(self, _db, manager_user_id=None): pass
        def plan_next_day(self, **kw): return FakeRun()
    monkeypatch.setattr("app.services.planner_service.PlannerService", FakePlanner)

    out = _run_allocation(db, monkeypatch, plan_date_str="2026-10-20")
    assert out.get("blocked") is not True
    assert out["target_date"] == "2026-10-20"


def test_the_scheduled_run_passes_no_date_and_is_therefore_gated():
    """The guard for the opt-out above: if the beat entry ever started passing
    a date, every night would silently skip the gate."""
    from app.workers.celery_app import celery_app
    entry = celery_app.conf.beat_schedule["nightly-ml-allocation"]
    assert not entry.get("args"), entry.get("args")
    assert not entry.get("kwargs"), entry.get("kwargs")


# ── DEFECT-A1 (audit, 2026-10-16): a crashed scoring run must not read ready ──

def test_a_scoring_run_that_died_early_does_not_count_as_complete(db):
    """THE DEFECT. scoring_status asked only whether ANY snapshot existed, so a
    run that crashed after its first loan reported the stage ready and
    allocation planned on an almost entirely unscored book. Its own docstring
    claimed counting; the code used `.first() is not None`.

    Ten loans, one snapshot -> not ready.
    """
    from tests._placement import make_loan

    # Above MIN_LOANS_FOR_RATIO, so the ratio carries signal.
    for n in range(1, 31):
        make_loan(db, n)
    _snapshot(db, on=DAY, n=31)          # one loan scored out of thirty-one
    db.commit()

    s = pr.scoring_status(db, DAY)
    assert s.ready is False, s.reason
    assert "did not finish" in s.reason
    assert s.detail["snapshots"] == 1
    assert s.detail["loans"] == 31


def test_a_completed_scoring_run_is_ready(db):
    """The other side of the threshold: every loan scored -> ready."""
    from tests._placement import make_loan

    loans = [make_loan(db, n) for n in range(1, 4)]
    for i, loan in enumerate(loans, start=1):
        db.add(RepaymentSnapshot(
            bank_id=TEST_BANK_ID, loan_id=loan.id, customer_id=loan.customer_id,
            case_id=None, as_of_date=DAY, trigger="MANUAL", source="SCORECARD",
            model_version="scorecard-1.1.0", likelihood=60.0, risk_score=40.0,
            band="UNCERTAIN", risk_category=RiskCategory.MEDIUM, evidence_coverage=0.7,
            features={}, contributions={}))
    db.commit()

    s = pr.scoring_status(db, DAY)
    assert s.ready is True, s.reason
    assert s.detail["snapshots"] == 3 and s.detail["loans"] == 3


def test_a_small_book_is_not_held_hostage_by_the_ratio(db):
    """Below MIN_LOANS_FOR_RATIO the ratio is noise, not evidence — a freshly
    seeded or small environment must not be blocked by it."""
    _snapshot(db, on=DAY, n=99)
    db.commit()
    assert pr.scoring_status(db, DAY).ready is True


def test_the_blocked_allocation_gate_reports_partial_scoring(db):
    """End to end: a half-finished scoring run must close the gate."""
    from tests._placement import make_loan

    _batch(db, status="LOADED")
    for n in range(1, 31):
        make_loan(db, n)
    _snapshot(db, on=DAY, n=31)
    db.commit()

    ready, stages = pr.allocation_gate(db, DAY)
    assert ready is False
    assert pr.gate_summary(stages)["blocked_by"] == ["scoring"]
