# ─── CHANGELOG (prototype → product) ───
# New file, 2026-08-21. Covers the Celery wiring for repayment scoring.
#
# Registering a task takes TWO edits to celery_app.py — the include list and
# beat_schedule — and missing either fails silently in a way nothing surfaces:
# omit the include and beat schedules a task the worker cannot resolve; omit
# the schedule and the task is importable but never fires. Neither raises at
# boot, so both are asserted here.
#
# The ordering test is the one that matters operationally. Scoring must sit
# between the bank ingest and the nightly allocation; if it drifts past 20:00 it
# scores against yesterday's book and allocation runs on stale numbers, and
# nothing anywhere would report that.
import pathlib
import re
from datetime import date, timedelta

from app.core.config import settings
from app.ml import repayment
from app.models.repayment_snapshot import SOURCE_SCORECARD
from app.workers.celery_app import celery_app

TASK = "app.workers.tasks.repayment_scoring.run_nightly_repayment_scoring"
SCHEDULE_KEY = "nightly-repayment-scoring"
ALLOCATION_KEY = "nightly-ml-allocation"


def _hours(entry):
    """(hour, minute) from a crontab schedule, whatever celery wraps it in."""
    schedule = entry["schedule"]
    return (min(schedule.hour), min(schedule.minute))


# ── Registration ─────────────────────────────────────────────────────────────
def test_task_module_is_in_the_include_list():
    """Without this the beat fires a task the worker cannot resolve, and the
    only symptom is a NotRegistered buried in the worker log."""
    assert "app.workers.tasks.repayment_scoring" in celery_app.conf.include


def test_task_is_actually_registered_under_that_name():
    from app.workers.tasks import repayment_scoring  # noqa: F401
    assert TASK in celery_app.tasks


def test_beat_schedule_has_an_entry():
    assert SCHEDULE_KEY in celery_app.conf.beat_schedule
    assert celery_app.conf.beat_schedule[SCHEDULE_KEY]["task"] == TASK


def test_scheduled_name_matches_the_decorated_name():
    """A typo here is only discovered at 19:45 in production."""
    from app.workers.tasks.repayment_scoring import run_nightly_repayment_scoring
    assert run_nightly_repayment_scoring.name == TASK


# ── Ordering ─────────────────────────────────────────────────────────────────
def test_scoring_runs_before_allocation():
    """Scoring reads what the bank ingest just wrote; allocation reads what
    scoring leaves. Reversed, allocation would run on the previous day's
    numbers and nothing would say so."""
    scoring = _hours(celery_app.conf.beat_schedule[SCHEDULE_KEY])
    allocation = _hours(celery_app.conf.beat_schedule[ALLOCATION_KEY])
    assert scoring < allocation, f"scoring {scoring} must precede allocation {allocation}"


def test_scoring_leaves_a_margin_before_allocation():
    scoring = _hours(celery_app.conf.beat_schedule[SCHEDULE_KEY])
    allocation = _hours(celery_app.conf.beat_schedule[ALLOCATION_KEY])
    margin = (allocation[0] * 60 + allocation[1]) - (scoring[0] * 60 + scoring[1])
    assert margin >= 15, f"only {margin} minutes to score the whole book"


# ── The gates, as the scheduled task sees them ───────────────────────────────
def test_scheduled_task_resolves_the_scorecard():
    """The task must be able to resolve a scorer without a database, a network
    call or a key — otherwise the 19:45 run fails before it starts."""
    resolution = repayment.resolved_scorer()
    assert resolution.source == SOURCE_SCORECARD
    assert resolution.status == repayment.OK
    assert resolution.is_modelled is False


def test_write_gate_is_closed_by_default():
    """Scheduling the task must not be the act that turns the column live.

    Asserts the FIELD DEFAULT, not the effective value. Enabling the gate is a
    deployment decision made in .env for one box at a time; a test that asserted
    the runtime value would fail the moment someone did the approved thing,
    which trains people to ignore it. What must never drift is the default a
    fresh deployment inherits.
    """
    field = type(settings).model_fields["REPAYMENT_WRITE_RISK_SCORE"]
    assert field.default is False


def test_reprice_gate_is_closed_by_default_and_in_effect():
    """Both, deliberately. Repricing open cases is not implemented at all, so
    unlike the write gate there is no legitimate reason for any deployment to
    have it on — the effective value is asserted too."""
    field = type(settings).model_fields["REPAYMENT_REPRICE_OPEN_CASES"]
    assert field.default is False
    assert settings.REPAYMENT_REPRICE_OPEN_CASES is False


def test_task_accepts_a_dry_run_argument():
    """So the shift can be inspected from a running worker before the gate is
    opened, without a second code path to keep in step."""
    import inspect

    from app.workers.tasks.repayment_scoring import run_nightly_repayment_scoring
    params = inspect.signature(run_nightly_repayment_scoring.run).parameters
    assert "dry_run" in params
    assert params["dry_run"].default is False


# ── Snapshot idempotency, which is what makes retry safe ─────────────────────
def test_retry_cannot_double_write():
    """The task retries on failure. That is only safe because the snapshot
    grain is (loan_id, as_of_date) with a unique constraint, so a partial run
    followed by a retry converges instead of duplicating."""
    from app.models.repayment_snapshot import RepaymentSnapshot
    constraint = next(
        c for c in RepaymentSnapshot.__table__.constraints
        if c.name == "uq_repayment_snapshot_grain"
    )
    assert {c.name for c in constraint.columns} == {"loan_id", "as_of_date"}


def test_same_evening_double_run_is_a_no_op():
    """Ingest may call the service inline at ~19:30 and the beat fires at
    19:45. Both must be safe to leave enabled."""
    from types import SimpleNamespace as NS

    from app.services.repayment_service import RepaymentService, ScoreOutcome
    from app.ml.repayment_scorecard import band_for, risk_category_for

    today = date.today()
    outcome = ScoreOutcome(
        loan_id="l", customer_id="c", case_id=None, as_of=today,
        likelihood=60.0, risk_score=40.0, band=band_for(60.0),
        risk_category=risk_category_for(40.0), evidence_coverage=0.8,
        model_version="scorecard-1.0.0", source=SOURCE_SCORECARD,
        features={}, factors=[])
    existing = NS(likelihood=60.0, as_of_date=today, scored_at=None)
    assert RepaymentService(db=None).should_snapshot(outcome, existing) is False
    # ...but a genuine state change from ingest always writes.
    assert RepaymentService(db=None).should_snapshot(
        outcome, existing, state_changed=True) is True
    stale = NS(likelihood=60.0, scored_at=None,
               as_of_date=today - timedelta(
                   days=settings.REPAYMENT_SNAPSHOT_ANCHOR_DAYS))
    assert RepaymentService(db=None).should_snapshot(outcome, stale) is True


# ── No second scorer may come back ───────────────────────────────────────────
# This is not hypothetical. seed_data.py and ingest_daily.py each held a copy of
# dpd/90*60 + (750-cibil)/750*40 and they had ALREADY drifted apart before
# anyone looked — one floored at 30 and could never produce RiskCategory.LOW,
# the other floored at 0 and could. Deleting them is only half the fix; the
# other half is making it fail loudly if one returns.
_REPO = pathlib.Path(__file__).resolve().parents[1]


def _python_sources():
    for root in ("app", "scripts"):
        for path in (_REPO / root).rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            yield path, path.read_text(encoding="utf-8-sig")


def test_the_deleted_scorers_stay_deleted():
    banned = ("_risk_from_dpd_cibil", "_risk_from_dpd")
    offenders = []
    for path, text in _python_sources():
        for num, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            # Changelog comments naming them are the record of the deletion and
            # are deliberately allowed; a def or a call is not.
            if stripped.startswith("#"):
                continue
            if any(name in line for name in banned):
                offenders.append(f"{path.relative_to(_REPO)}:{num}: {stripped[:70]}")
    assert offenders == [], (
        "the deleted risk formulas are back as live code:\n  " + "\n  ".join(offenders))


def test_the_old_formula_itself_is_gone():
    """Catches a rename. Deleting the two functions is not enough if the same
    arithmetic reappears inline under a new name, so the coefficient is what is
    asserted on: the customer-risk formula was `dpd / 90 * 60` paired with a
    (750 - cibil) bureau term.

    Two things are deliberately NOT offenders, and being explicit beats a
    silently loose regex:
      * Loan.bank_risk_score (seed_data.py) is a DIFFERENT column, standing in
        for a number the bank supplies. It uses /90*50 and is out of scope.
      * scripts/analyse_priority_shift.py reproduced the old formula on purpose,
        to measure the before/after shift. The shift was measured before the
        scorer went live; the script is in tag archive/research-2026-09.
    """
    exempt: set[str] = set()
    offenders = []
    for path, text in _python_sources():
        if path.name in exempt:
            continue
        for num, line in enumerate(text.splitlines(), 1):
            if line.strip().startswith("#"):
                continue
            if "/ 90 * 60" in line.replace("  ", " ") and "cibil" in line.lower():
                offenders.append(f"{path.relative_to(_REPO)}:{num}: {line.strip()[:60]}")
    assert offenders == [], f"the old customer-risk formula is back: {offenders}"


def test_risk_score_has_exactly_one_attribute_writer():
    """`grep -rn "_apply_risk_score"` must remain the complete answer to
    "what can change Customer.risk_score"."""
    offenders = []
    for path, text in _python_sources():
        if path.name == "repayment_service.py":
            continue
        for num, line in enumerate(text.splitlines(), 1):
            if line.strip().startswith("#"):
                continue
            if re.search(r"\.risk_score\s*=(?!=)", line) or re.search(
                    r"\.risk_category\s*=(?!=)", line):
                offenders.append(f"{path.relative_to(_REPO)}:{num}: {line.strip()[:60]}")
    assert offenders == [], f"risk score written outside the single site: {offenders}"


def test_no_constructor_writers_remain():
    """Passing risk_score= to Customer(...) is a write too, and greps for
    `.risk_score =` miss it entirely.

    This started as an allow-list holding workers/tasks/demo_daily_feed.py,
    which minted demo customers with a hardcoded 85/65/42 ladder — the THIRD
    independent scorer, after the two in seed_data.py and ingest_daily.py that
    had already drifted apart. That ladder is gone (2026-08-21), so the allow-
    list is now empty and the assertion is absolute: nothing outside the
    repayment modules may hand Customer() a risk score.

    Both halves of the pair are checked. Setting only ONE of them is the same
    contradiction from the other side, and that is not hypothetical: after the
    ladder was removed, seed_data.py still set risk_category= on its demo and
    bank blocks, leaving 16 customers labelled CRITICAL/HIGH/LOW while their
    risk_score sat at the 50.0 MEDIUM default. Fixed 2026-08-21.
    """
    # These legitimately mention the name: the scorer that computes it and the
    # column that stores it.
    infrastructure = {"repayment_service.py", "repayment_scorecard.py",
                      "repayment_snapshot.py"}
    offenders = []
    for path, text in _python_sources():
        if path.name in infrastructure:
            continue
        for num, line in enumerate(text.splitlines(), 1):
            if line.strip().startswith("#"):
                continue
            # `risk_score=` / `risk_category=` as keyword arguments, not `x.y =`
            if re.search(r"(?<!\.)\brisk_(score|category)\s*=", line) and "==" not in line:
                offenders.append(f"{path.relative_to(_REPO)}:{num}: {line.strip()[:60]}")
    assert offenders == [], (
        "risk_score/risk_category constructed outside the scorer:\n  "
        + "\n  ".join(offenders))


def test_seed_never_hardcodes_a_risk_category():
    """seed_data.py must carry no RiskCategory literal at all. A dead
    `"risk": RiskCategory.CRITICAL` dict key is not an assignment, but it is a
    hardcoded category sitting in reach — 15 of them were removed on
    2026-08-21 precisely so nothing could be wired back up to one."""
    source = (_REPO / "scripts/seed_data.py").read_text(encoding="utf-8-sig")
    live = "\n".join(l for l in source.splitlines()
                     if not l.strip().startswith("#"))
    assert "RiskCategory" not in live


def test_the_old_demo_ladder_is_gone():
    """The specific values, so a copy-paste revival is caught by name. The
    ladder had no LOW branch, so a LOW customer got 42.0 — a number every
    threshold in the codebase reads as MEDIUM, contradicting its own category."""
    source = (_REPO / "app/workers/tasks/demo_daily_feed.py").read_text(
        encoding="utf-8-sig")
    live = [line for line in source.splitlines()
            if not line.strip().startswith("#")]
    body = "\n".join(live)
    assert "85.0" not in body and "65.0" not in body and "42.0" not in body


def test_demo_feed_defers_to_the_scoring_service():
    """It creates the facts, then asks the one scorer to derive the score —
    the same contract ingest_daily.py follows."""
    source = (_REPO / "app/workers/tasks/demo_daily_feed.py").read_text(
        encoding="utf-8-sig")
    assert "RepaymentService" in source
    assert ".rescore(" in source
