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
# Sized to the team's daily capacity, not to a token trickle.
#
# 2026-09-02 — this was 2..4 a day. The book was seeded with 743 cases on
# 2026-08-27 and then fed a handful daily, so the pool only ever drained: after
# one day of real collections it was down to 246 mostly part-worked cases and
# tomorrow's plan looked anaemic for want of anything to plan. That is not what
# an agency's morning looks like — the bank hands over a fresh batch each day.
#
# 15 agents x 10-15 cases is 150-225, and capacity is 15 x max_cases_per_day
# (12 by default) = 180 slots. The range below straddles that on purpose: some
# days the pool exceeds capacity and the overflow carries to tomorrow, some days
# it does not fill. Both are real states the planner already handles (unallocated
# cases stay in the pool and return the next night), and a feed that always
# exactly matched capacity would never exercise either.
# Monthly instalment bands by product. Must stay in step with EMI_BANDS in
# scripts/rescale_to_realistic_emi.py, which put the existing book on this scale.
_EMI_BANDS = {
    "MICROFINANCE": (1_200, 5_000),
    "CREDIT_CARD":  (1_500, 12_000),
    "GOLD":         (3_000, 18_000),
    "EDUCATION":    (4_000, 22_000),
    "PERSONAL":     (5_000, 30_000),
    "AUTO":         (8_000, 32_000),
    "BUSINESS":     (10_000, 55_000),
    "HOME":         (12_000, 70_000),
}

NEW_CASES_MIN = 150
NEW_CASES_MAX = 210

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
    from app.models.loan import Loan, LoanType, DPDBucket, LoanStatus, dpd_bucket_for
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
        loan_type = random.choice(list(LoanType))
        # EMI first, then the loan derived from it — the opposite of how this
        # used to work, and the reason it has to change.
        #
        # 2026-09-02 — this drew `outstanding` at random and set
        # emi = outstanding / 36 with target_amount = outstanding * 0.30, which
        # made every fresh case a target of up to Rs 96,000 against an EMI of
        # Rs 8,900: a "case" worth six and a half instalments, and no relation to
        # the product. The seeded book had the same fault at larger scale and was
        # rescaled in scripts/rescale_to_realistic_emi.py; a feed that kept the
        # old shape would have quietly walked the book back to it, one morning at
        # a time.
        #
        # A case is one instalment, so the EMI is chosen from the product's band
        # and the balance follows from the tenure left, rather than the other way
        # round. Bands match EMI_BANDS in that script; changing one means changing
        # both.
        emi = float(random.randint(*_EMI_BANDS.get(loan_type.value, (5_000, 30_000))))
        emi = round(emi, -1)
        months_left = random.randint(14, 60)
        outstanding = round(emi * months_left, 2)
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
            # Arrears are missed instalments, so they are a whole number of EMIs
            # matched to how late the account is — not a flat share of the balance.
            overdue_amount=round(emi * max(1, dpd // 30), 2),
            emi_amount=emi,
            disbursement_date="2022-01-15", maturity_date="2025-01-15",
            last_payment_date="2025-11-10", next_due_date=day.strftime("%Y-%m-%d"),
            dpd=dpd,
            # Was an inline chain with no CURRENT and no BUCKET_1 branch, so
            # any DPD at or below 30 would have been written BUCKET_2.
            # Unreachable today (the feed draws from a list starting at 32).
            dpd_bucket=dpd_bucket_for(dpd),
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
            target_amount=emi, collected_amount=0.0,
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
