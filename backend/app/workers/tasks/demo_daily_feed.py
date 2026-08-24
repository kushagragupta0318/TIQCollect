# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-08-21 — Stopped constructing Customer.risk_score here.
#
#   This task minted demo customers with a hand-written ladder:
#
#       risk = CRITICAL if dpd > 90 else HIGH if dpd > 60 else MEDIUM
#       risk_score = 85.0 if CRITICAL else 65.0 if HIGH else 42.0
#
#   which made it the third independent scorer in the codebase, alongside the
#   two in seed_data.py and ingest_daily.py that had already silently drifted
#   apart from each other. It had the same defect as the seed's copy: no LOW
#   branch, so a LOW customer would have been given 42.0 — a value every
#   threshold reads as MEDIUM, contradicting its own category.
#
#   This feed is the demo's stand-in for the bank handing over a file, so it now
#   follows the same rule ingest_daily.py does: create the FACTS (dpd, cibil,
#   amounts, flags) and let services/repayment_service.py derive the score from
#   them at the end of the run. Customer.risk_score has one authoritative source.
# ───────────────────────────────────────────────────────────────────────────
"""
Demo daily feed — DEMO_MODE only.

Simulates the bank handing the agency a fresh batch of overdue accounts each
morning, so opening the app on any new day shows new cases flowing in (they land
in the UNASSIGNED pool and the existing 8 PM allocator assigns them to agents).
This is APPEND-ONLY — it never touches the demo showcase customers (DEMO*) or any
existing data, so Balraj and the fixed demo set persist across days.

Idempotent: re-running on the same calendar day is a no-op (keyed on the date in
the customer_ref), so a restart or a double-fire won't duplicate the batch.

Wire-up: registered in celery_app.py beat_schedule to run ~5:30 AM IST (before
the 6 AM beat push). Also runnable by hand for a demo:
    python -c "from app.workers.tasks.demo_daily_feed import run_demo_daily_feed; run_demo_daily_feed()"
"""
from __future__ import annotations

import random
import uuid
from datetime import date, datetime, timezone

import structlog

from app.workers.celery_app import celery_app

logger = structlog.get_logger()

# How many fresh pool cases the "bank" sends each day.
NEW_CASES_MIN = 2
NEW_CASES_MAX = 4

# Gurugram-ish spread for new accounts (out of the agent's fence — these are new
# pool cases, not demo-anchor customers; they get allocated and travelled to).
_GGN_LAT = (28.40, 28.52)
_GGN_LON = (77.03, 77.10)

_FIRST = ["Amit", "Pooja", "Sanjay", "Neha", "Rahul", "Divya", "Manish", "Kiran",
          "Vijay", "Anjali", "Deepak", "Sneha", "Rohan", "Preeti", "Nikhil"]
_LAST = ["Sharma", "Verma", "Gupta", "Singh", "Yadav", "Mehta", "Nair", "Reddy",
         "Bhatt", "Chauhan", "Malik", "Saxena"]


def _uid() -> str:
    return str(uuid.uuid4())


def _core():
    """Imported lazily so importing this module never triggers a DB connect."""
    from app.core.database import SessionLocal
    from app.models.customer import Customer
    from app.models.loan import Loan, LoanType, DPDBucket, LoanStatus
    from app.models.case import Case, CaseStatus, CasePriority
    return (SessionLocal, Customer, Loan, LoanType, DPDBucket,
            LoanStatus, Case, CaseStatus, CasePriority)


def _seed_day(db, day: date) -> int:
    (_, Customer, Loan, LoanType, DPDBucket, LoanStatus,
     Case, CaseStatus, CasePriority) = _core()

    tag = day.strftime("%Y%m%d")
    ref_prefix = f"DAILY{tag}"
    # Idempotency: if today's batch already exists, do nothing.
    if db.query(Customer).filter(Customer.customer_ref.like(f"{ref_prefix}%")).first():
        logger.info("demo_daily_feed.skip_already_seeded", day=str(day))
        return 0

    n = random.randint(NEW_CASES_MIN, NEW_CASES_MAX)
    created = 0
    new_loan_ids: list[str] = []
    for i in range(n):
        dpd = random.choice([32, 47, 65, 88, 95, 120, 155])
        outstanding = round(random.uniform(35_000, 320_000), 2)
        loan_type = random.choice(list(LoanType))
        priority = (CasePriority.CRITICAL if dpd > 90 else
                    CasePriority.HIGH if dpd > 60 else
                    CasePriority.MEDIUM if dpd > 30 else CasePriority.LOW)

        cust = Customer(
            id=_uid(), customer_ref=f"{ref_prefix}{i:02d}",
            full_name=f"{random.choice(_FIRST)} {random.choice(_LAST)}",
            date_of_birth="1986-03-10", gender=random.choice(["MALE", "FEMALE"]),
            pan_masked="XXXXX1234X", aadhaar_masked="XXXXXXXX5678",
            phone_primary=f"9{random.randint(100000000, 999999999):09d}",
            address_line1=f"{random.randint(1, 200)}, Sector {random.randint(1, 70)}",
            city="Gurugram", state="Haryana", pincode="122001",
            latitude=round(random.uniform(*_GGN_LAT), 6),
            longitude=round(random.uniform(*_GGN_LON), 6),
            # risk_category / risk_score are NOT set here. They are derived, and
            # services/repayment_service.py owns them — see the changelog above.
            # The column defaults (MEDIUM, 50.0) mean "not yet scored" until the
            # rescore call at the end of this function.
            cibil_score=520 if dpd > 60 else 600 if dpd > 30 else 650,
            language_preference="HINDI", customer_segment="SALARIED",
        )
        db.add(cust)
        db.flush()

        loan = Loan(
            id=_uid(), loan_account_number=f"{ref_prefix}LN{i:02d}",
            customer_id=cust.id, loan_type=loan_type,
            bank_name="ABC Bank", branch_code="GGN044",
            sanctioned_amount=round(outstanding * 1.4, 2),
            disbursed_amount=round(outstanding * 1.3, 2),
            outstanding_principal=outstanding,
            outstanding_interest=round(outstanding * 0.06, 2),
            penal_charges=round(outstanding * 0.01, 2),
            total_outstanding=round(outstanding * 1.07, 2),
            overdue_amount=round(outstanding * 0.30, 2),
            emi_amount=round(outstanding / 36, 2),
            disbursement_date="2022-01-15", maturity_date="2025-01-15",
            last_payment_date="2025-11-10", next_due_date=day.strftime("%Y-%m-%d"),
            dpd=dpd,
            dpd_bucket=DPDBucket.NPA if dpd > 90 else DPDBucket.BUCKET_3 if dpd > 60 else DPDBucket.BUCKET_2,
            status=LoanStatus.NPA if dpd > 90 else LoanStatus.ACTIVE,
            interest_rate=14.5, npa_flag=dpd > 90,
            bank_risk_score=round(dpd / 120 * 100, 1),
            collection_priority_score=round(dpd / 120 * 100, 1),
        )
        db.add(loan)
        db.flush()
        new_loan_ids.append(loan.id)

        case = Case(
            id=_uid(), case_number=f"{ref_prefix}C{i:02d}",
            customer_id=cust.id, loan_id=loan.id,
            agent_id=None, status=CaseStatus.UNASSIGNED,
            priority=priority,
            target_amount=round(outstanding * 0.30, 2), collected_amount=0.0,
            allocation_date=day.strftime("%Y-%m-%d"),
            allocation_score=float({"CRITICAL": 95, "HIGH": 75, "MEDIUM": 45, "LOW": 20}[priority.value]),
            is_ml_allocated=False, visit_count=0, max_visits_allowed=5,
            collection_stage="NPA_RECOVERY" if dpd > 90 else "FIELD",
        )
        db.add(case)
        created += 1

    db.commit()

    # Score the accounts this batch created, the same way ingest_daily.py does
    # at the end of its run. This feed is the demo's stand-in for the bank
    # handing over a file, so it follows the same rule: create the FACTS, then
    # let the one scorer derive the score from them.
    #
    # Writing Customer.risk_score is gated on REPAYMENT_WRITE_RISK_SCORE
    # (default False), so by default this records snapshots and the new
    # customers keep the 50.0 default — which is exactly what every other
    # unscored customer shows.
    if new_loan_ids:
        from app.models.repayment_snapshot import TRIGGER_INGEST
        from app.services.repayment_service import RepaymentService

        scored = RepaymentService(db).rescore(
            as_of=day, loan_ids=new_loan_ids, trigger=TRIGGER_INGEST,
            changed_loan_ids=set(new_loan_ids),   # a brand-new loan is a change
        )
        db.commit()
        logger.info("demo_daily_feed.scored",
                    loans=scored["loans_scored"],
                    snapshots=scored["snapshots_written"],
                    customers_written=scored["customers_written"],
                    write_gate_open=scored["write_risk_score_enabled"])

    logger.info("demo_daily_feed.created", day=str(day), new_cases=created)
    return created


def run_demo_daily_feed() -> dict:
    """Callable directly (manual demo) or via the Celery task below."""
    from app.core.config import settings
    if not settings.DEMO_MODE:
        logger.info("demo_daily_feed.disabled")
        return {"created": 0, "reason": "DEMO_MODE off"}

    SessionLocal = _core()[0]
    db = SessionLocal()
    try:
        created = _seed_day(db, datetime.now(timezone.utc).date())
        return {"created": created}
    finally:
        db.close()


@celery_app.task(name="app.workers.tasks.demo_daily_feed.run_demo_daily_feed_task",
                 bind=True, max_retries=3)
def run_demo_daily_feed_task(self):
    try:
        return run_demo_daily_feed()
    except Exception as exc:
        logger.error("demo_daily_feed.error", error=str(exc))
        raise self.retry(exc=exc, countdown=300)
