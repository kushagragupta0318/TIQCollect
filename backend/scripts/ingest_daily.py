"""
TIQCollect Daily Data Ingestion Script
---------------------------------------
Run at ~7:30 PM every evening BEFORE the 8 PM Celery allocation task fires.

Real-world bank actions handled:
  ACTIVE       — normal overdue account, update DPD + financial fields
  PAID_DIRECT  — customer paid bank directly (DPD → 0), auto-close case as PAID
  RECALL       — bank recalling account (complaint/legal/transfer), close as CLOSED
  SETTLED      — bank-approved one-time settlement at reduced amount, close as CLOSED
  WRITTEN_OFF  — bank wrote off the loan, close case as WRITTEN_OFF
  DECEASED     — customer deceased, mark do_not_contact, close case as CLOSED

DPD=0 with no bank_action also auto-closes as PAID_DIRECT.

Usage:
  python -m scripts.ingest_daily                             # today's file from ./data/incoming/
  python -m scripts.ingest_daily --file path/to/cases.csv   # explicit path
  python -m scripts.ingest_daily --dry-run                  # validate + preview, no DB writes
  python -m scripts.ingest_daily --generate-sample          # write sample CSV and exit
"""

import sys
import os
import csv
import uuid
import argparse
import logging
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core.database import SessionLocal, engine
import app.models  # noqa: register all SQLAlchemy models

from app.models.base import Base
from app.models.customer import Customer, RiskCategory
from app.models.loan import Loan, LoanType, DPDBucket, LoanStatus, dpd_bucket_for
from app.models.case import Case, CaseStatus, CasePriority, EscalationReason
from app.models.repayment_snapshot import (
    OUTCOME_DECEASED, OUTCOME_RECALLED, OUTCOME_REPAID, OUTCOME_SETTLED,
    OUTCOME_SOURCE_BANK_ACTION, OUTCOME_WRITTEN_OFF, RepaymentSnapshot,
    TRIGGER_INGEST,
)
from app.services.repayment_service import RepaymentService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("ingest_daily")

DEFAULT_INCOMING_DIR = Path(__file__).parent.parent / "data" / "incoming"

# Statuses where agent has already resolved the case — bank closing it too is fine
ALREADY_RESOLVED = {CaseStatus.PAID, CaseStatus.CLOSED, CaseStatus.WRITTEN_OFF}

# Statuses that are live in the field — only bank_action can close these
LIVE_STATUSES = {
    CaseStatus.UNASSIGNED,
    CaseStatus.ASSIGNED,
    CaseStatus.IN_PROGRESS,
    CaseStatus.PTP_SET,
    CaseStatus.PARTIALLY_PAID,
    CaseStatus.ESCALATED,
}

# Valid bank actions
BANK_ACTIONS = {"ACTIVE", "PAID_DIRECT", "RECALL", "SETTLED", "WRITTEN_OFF", "DECEASED"}

# Recall reason mapping to EscalationReason (closest available enum)
RECALL_REASON_MAP = {
    "CUSTOMER_COMPLAINT": EscalationReason.OTHER,
    "LEGAL_PROCEEDINGS":  EscalationReason.LEGAL_NOTICE_REQUIRED,
    "DECEASED":           EscalationReason.OTHER,
    "TRANSFERRED":        EscalationReason.OTHER,
    "COURT_ORDER":        EscalationReason.LEGAL_NOTICE_REQUIRED,
    "BANK_SETTLEMENT":    EscalationReason.DISPUTED_AMOUNT,
}


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _uid() -> str:
    return str(uuid.uuid4())


def _dpd_to_bucket(dpd: int) -> DPDBucket:
    """Delegates to models/loan.dpd_bucket_for; was an identical copy."""
    return dpd_bucket_for(dpd)


# _risk_from_dpd_cibil was DELETED on 2026-08-21, not deprecated.
#
# It computed dpd/90*60 + (750-cibil)/750*40 and overwrote Customer.risk_score on
# every daily run — a relabelling of two columns this script had just written.
# seed_data.py carried a near-identical copy that had already drifted (it floored
# at 30 and could never produce RiskCategory.LOW; this one floored at 0 and
# could), and nobody noticed because nothing rendered the column.
#
# Leaving it here dormant would guarantee it got called again. Scoring now
# belongs to services/repayment_service.py and to nothing else — see
# _apply_risk_score, the single write site. This script's job narrows to writing
# FACTS: dpd, cibil_score, amounts, flags and bank_action consequences.


def _priority_from_score(score: float) -> CasePriority:
    if score >= 85:
        return CasePriority.CRITICAL
    elif score >= 60:
        return CasePriority.HIGH
    elif score >= 35:
        return CasePriority.MEDIUM
    return CasePriority.LOW


def _parse_bool(val: str) -> bool:
    return val.strip().lower() in ("1", "true", "yes", "y")


def _parse_float(val: str, default: float = 0.0) -> float:
    try:
        return float(val.strip()) if val.strip() else default
    except ValueError:
        return default


def _parse_int(val: str, default: int = 0) -> int:
    try:
        return int(val.strip()) if val.strip() else default
    except ValueError:
        return default


def _loan_type(val: str) -> LoanType:
    mapping = {
        "HOME": LoanType.HOME,
        "AUTO": LoanType.AUTO,
        "PERSONAL": LoanType.PERSONAL,
        "BUSINESS": LoanType.BUSINESS,
        "GOLD": LoanType.GOLD,
        "CREDIT_CARD": LoanType.CREDIT_CARD,
        "EDUCATION": LoanType.EDUCATION,
        "MICROFINANCE": LoanType.MICROFINANCE,
    }
    return mapping.get(val.strip().upper(), LoanType.PERSONAL)


# ─── Case closure helpers ──────────────────────────────────────────────────────

def _close_case_paid(case_obj: Case, settlement_amount: float = 0.0) -> str:
    """Bank confirmed customer paid directly. Close as PAID."""
    if case_obj.status in ALREADY_RESOLVED:
        return f"already_{case_obj.status.value.lower()}"
    case_obj.status = CaseStatus.PAID
    case_obj.resolved_at = datetime.now(timezone.utc)
    if settlement_amount > 0:
        case_obj.collected_amount = settlement_amount
        case_obj.resolution_notes = f"Bank settlement: ₹{settlement_amount:,.0f} accepted"
    else:
        case_obj.resolution_notes = "Closed: customer paid bank directly"
    log.info("  AUTO-CLOSE PAID  → %s", case_obj.case_number)
    return "auto_closed_paid"


def _close_case_recall(case_obj: Case, recall_reason: str, bank_remark: str) -> str:
    """Bank recalling this account — agent must stop all visits."""
    if case_obj.status in ALREADY_RESOLVED:
        return f"already_{case_obj.status.value.lower()}"
    case_obj.status = CaseStatus.CLOSED
    case_obj.resolved_at = datetime.now(timezone.utc)
    case_obj.resolution_notes = f"RECALLED by bank. Reason: {recall_reason}. {bank_remark}".strip()
    log.info("  AUTO-CLOSE RECALL → %s  (%s)", case_obj.case_number, recall_reason)
    return "auto_closed_recall"


def _close_case_written_off(case_obj: Case, bank_remark: str) -> str:
    """Bank wrote off the loan."""
    if case_obj.status in ALREADY_RESOLVED:
        return f"already_{case_obj.status.value.lower()}"
    case_obj.status = CaseStatus.WRITTEN_OFF
    case_obj.resolved_at = datetime.now(timezone.utc)
    case_obj.resolution_notes = f"Written off by bank. {bank_remark}".strip()
    log.info("  AUTO-CLOSE WRITTEN_OFF → %s", case_obj.case_number)
    return "auto_closed_written_off"


def _close_case_deceased(case_obj: Case) -> str:
    """Customer deceased — stop all visits."""
    if case_obj.status in ALREADY_RESOLVED:
        return f"already_{case_obj.status.value.lower()}"
    case_obj.status = CaseStatus.CLOSED
    case_obj.resolved_at = datetime.now(timezone.utc)
    case_obj.resolution_notes = "Closed: customer deceased"
    log.info("  AUTO-CLOSE DECEASED  → %s", case_obj.case_number)
    return "auto_closed_deceased"


# ─── Row processor ────────────────────────────────────────────────────────────

def process_row(row: dict, db, dry_run: bool, today: date) -> dict:
    result = {
        "action_customer": None,
        "action_loan": None,
        "action_case": None,
        "skipped_reason": None,
        # Carried out to run_ingestion so it can rescore exactly the loans this
        # file touched, and label the snapshots that predicted a terminal
        # outcome. Both are file-level concerns: scoring needs the whole row set
        # committed first, and a label must not be written for a row that a
        # later error rolls back.
        "loan_id": None,
        "bank_action": None,
        "state_changed": False,
    }

    customer_ref  = row.get("customer_ref", "").strip()
    loan_account  = row.get("loan_account_number", "").strip()
    case_number   = row.get("case_number", "").strip()

    if not customer_ref or not loan_account or not case_number:
        result["skipped_reason"] = "missing required key fields"
        return result

    # ── Parse bank action ─────────────────────────────────────────────────────
    bank_action   = row.get("bank_action", "ACTIVE").strip().upper()
    recall_reason = row.get("recall_reason", "").strip().upper()
    bank_remark   = row.get("bank_remark", "").strip()
    settlement_amount = _parse_float(row.get("settlement_amount", "0"))

    if bank_action not in BANK_ACTIONS:
        log.warning("Unknown bank_action '%s' for %s — treating as ACTIVE", bank_action, case_number)
        bank_action = "ACTIVE"

    # ── Parse financial fields ────────────────────────────────────────────────
    dpd               = _parse_int(row.get("dpd", "0"))
    cibil             = _parse_int(row.get("cibil_score", "650"), 650)
    total_outstanding = _parse_float(row.get("total_outstanding", "0"))
    overdue_amount    = _parse_float(row.get("overdue_amount", "0"))

    # DPD=0 with ACTIVE is a bank payment — treat same as PAID_DIRECT
    if bank_action == "ACTIVE" and dpd == 0:
        bank_action = "PAID_DIRECT"
        log.debug("Row %s: dpd=0 with no bank_action → treating as PAID_DIRECT", case_number)

    # DECEASED forces do_not_contact regardless of bank_action field
    is_deceased = bank_action == "DECEASED"

    # ── Customer UPSERT ───────────────────────────────────────────────────────
    customer = db.query(Customer).filter(Customer.customer_ref == customer_ref).first()
    if customer:
        # risk_score / risk_category are deliberately NOT set here. They are
        # derived values and belong to the repayment scorer, which runs against
        # the facts below once the whole file has landed. cibil_score IS a fact
        # and stays.
        customer.cibil_score   = cibil if cibil else customer.cibil_score
        phone = row.get("phone_primary", "").strip()
        if phone:
            customer.phone_primary = phone
        customer.is_hostile    = _parse_bool(row.get("is_hostile", "false"))
        if is_deceased:
            customer.do_not_contact = True
            customer.tags = list(set(customer.tags or []) | {"DECEASED"})
        result["action_customer"] = "updated"
    else:
        # Skip do-not-contact new customers (bank already flagged them)
        if _parse_bool(row.get("do_not_contact", "false")) and not is_deceased:
            result["skipped_reason"] = "do_not_contact=true on new customer"
            return result

        lat = _parse_float(row.get("latitude", "0"))
        lon = _parse_float(row.get("longitude", "0"))
        if lat == 0.0 or lon == 0.0:
            result["skipped_reason"] = f"missing lat/lon for new customer {customer_ref}"
            return result

        customer = Customer(
            id=_uid(),
            customer_ref=customer_ref,
            full_name=row.get("customer_name", "").strip(),
            date_of_birth=row.get("dob", "1980-01-01").strip(),
            gender=row.get("gender", "MALE").strip().upper(),
            pan_masked=row.get("pan_masked", "XXXXX0000X").strip(),
            aadhaar_masked=row.get("aadhaar_masked", "XXXXXXXX0000").strip(),
            phone_primary=row.get("phone_primary", "").strip(),
            phone_alternate=row.get("phone_alternate", "").strip() or None,
            email=row.get("email", "").strip() or None,
            address_line1=row.get("address_line1", "").strip(),
            address_line2=row.get("address_line2", "").strip() or None,
            city=row.get("city", "").strip(),
            state=row.get("state", "").strip(),
            pincode=row.get("pincode", "000000").strip(),
            latitude=lat,
            longitude=lon,
            # No risk_category / risk_score: the column defaults (MEDIUM, 50.0)
            # now mean exactly what they say — "not yet scored" — until the
            # rescore pass at the end of run_ingestion() gives them a value.
            cibil_score=cibil if cibil else None,
            preferred_contact_start=_parse_int(row.get("preferred_contact_start", "9"), 9),
            preferred_contact_end=_parse_int(row.get("preferred_contact_end", "18"), 18),
            language_preference=row.get("language_preference", "HINDI").strip().upper(),
            is_hostile=_parse_bool(row.get("is_hostile", "false")),
            requires_female_agent=_parse_bool(row.get("requires_female_agent", "false")),
            do_not_contact=is_deceased,
            tags=["DECEASED"] if is_deceased else [],
        )
        if not dry_run:
            db.add(customer)
            db.flush()
        result["action_customer"] = "inserted"

    # ── Loan UPSERT ───────────────────────────────────────────────────────────
    result["bank_action"] = bank_action
    loan = db.query(Loan).filter(Loan.loan_account_number == loan_account).first()
    if loan:
        prev_bucket = loan.dpd_bucket
        loan.dpd              = dpd
        loan.dpd_bucket       = _dpd_to_bucket(dpd)
        loan.overdue_amount   = overdue_amount
        loan.total_outstanding = total_outstanding
        loan.outstanding_principal = _parse_float(
            row.get("outstanding_principal", "0")) or loan.outstanding_principal
        loan.penal_charges    = _parse_float(
            row.get("penal_charges", "0")) or loan.penal_charges
        last_pay = row.get("last_payment_date", "").strip()
        if last_pay:
            loan.last_payment_date = last_pay
        next_due = row.get("next_due_date", "").strip()
        if next_due:
            loan.next_due_date = next_due

        # A DPD move or a terminal bank action is the most valuable moment to
        # have a snapshot for — it is the score as it stood immediately before
        # the thing that changed the account.
        result["state_changed"] = (loan.dpd != dpd) or bank_action != "ACTIVE"

        if bank_action in ("PAID_DIRECT", "SETTLED"):
            loan.status = LoanStatus.SETTLED if bank_action == "SETTLED" else LoanStatus.ACTIVE
            loan.dpd    = 0
            loan.dpd_bucket = DPDBucket.CURRENT
            loan.overdue_amount = 0.0
        elif bank_action == "WRITTEN_OFF":
            loan.status = LoanStatus.WRITTEN_OFF
        elif bank_action == "CLOSED":
            loan.status = LoanStatus.CLOSED
        elif dpd >= 90:
            loan.status = LoanStatus.NPA
        else:
            loan.status = LoanStatus.ACTIVE

        bucket_changed = prev_bucket != loan.dpd_bucket
        result["action_loan"] = f"updated{'_bucket_change' if bucket_changed else ''}"
    else:
        sanctioned = _parse_float(row.get("sanctioned_amount", "0")) or total_outstanding
        disbursed  = _parse_float(row.get("disbursed_amount", "0")) or sanctioned
        loan_status = LoanStatus.ACTIVE
        if bank_action == "WRITTEN_OFF":
            loan_status = LoanStatus.WRITTEN_OFF
        elif bank_action == "SETTLED":
            loan_status = LoanStatus.SETTLED
        elif dpd >= 90:
            loan_status = LoanStatus.NPA

        loan = Loan(
            id=_uid(),
            loan_account_number=loan_account,
            customer_id=customer.id,
            loan_type=_loan_type(row.get("loan_type", "PERSONAL")),
            bank_name=row.get("bank_name", "Unknown Bank").strip(),
            branch_code=row.get("branch_code", "BR0000").strip(),
            sanctioned_amount=sanctioned,
            disbursed_amount=disbursed,
            outstanding_principal=_parse_float(row.get("outstanding_principal", "0")) or total_outstanding,
            total_outstanding=total_outstanding,
            overdue_amount=overdue_amount,
            emi_amount=_parse_float(row.get("emi_amount", "0")),
            disbursement_date=row.get("disbursement_date", today.isoformat()).strip(),
            maturity_date=row.get("maturity_date", today.isoformat()).strip(),
            last_payment_date=row.get("last_payment_date", "").strip() or None,
            next_due_date=row.get("next_due_date", "").strip() or None,
            dpd=dpd,
            dpd_bucket=_dpd_to_bucket(dpd),
            status=loan_status,
            interest_rate=_parse_float(row.get("interest_rate", "12")),
            penal_charges=_parse_float(row.get("penal_charges", "0")),
            collection_priority_score=0.0,
        )
        if not dry_run:
            db.add(loan)
            db.flush()
        result["state_changed"] = True          # a brand-new loan is a change
        result["action_loan"] = "inserted"

    # Both branches converge here with a loan in hand. On a dry run a brand-new
    # loan has no id yet, which is correct — there is nothing to rescore.
    result["loan_id"] = loan.id

    # ── Case logic by bank_action ─────────────────────────────────────────────
    existing_case = db.query(Case).filter(Case.case_number == case_number).first()

    if bank_action == "PAID_DIRECT":
        if existing_case:
            result["action_case"] = _close_case_paid(existing_case)
        else:
            # Case never created in our system — bank paid account we didn't have yet
            result["action_case"] = "paid_before_allocation"

    elif bank_action == "SETTLED":
        if existing_case:
            result["action_case"] = _close_case_paid(existing_case, settlement_amount)
        else:
            result["action_case"] = "settled_before_allocation"

    elif bank_action == "RECALL":
        if existing_case:
            result["action_case"] = _close_case_recall(existing_case, recall_reason, bank_remark)
        else:
            result["action_case"] = "recall_no_case"

    elif bank_action == "WRITTEN_OFF":
        if existing_case:
            result["action_case"] = _close_case_written_off(existing_case, bank_remark)
        else:
            result["action_case"] = "written_off_no_case"

    elif bank_action == "DECEASED":
        if existing_case:
            result["action_case"] = _close_case_deceased(existing_case)
        else:
            result["action_case"] = "deceased_no_case"

    else:
        # bank_action == "ACTIVE" — normal daily refresh
        if existing_case:
            if existing_case.status in ALREADY_RESOLVED:
                # Already closed on our side — don't reopen
                result["action_case"] = f"skipped_already_{existing_case.status.value.lower()}"
            else:
                # Update target if penalties grew the overdue amount
                if overdue_amount > existing_case.target_amount:
                    existing_case.target_amount = overdue_amount
                    result["action_case"] = "target_increased"
                else:
                    result["action_case"] = "no_change"
        else:
            # New overdue account — create UNASSIGNED case for allocator
            # The risk_score * 0.3 term is gone: at this point in the row loop
            # the loan has not been scored yet, so it could only have used the
            # stale value. Priority here is PROVISIONAL — the rescore pass at the
            # end of run_ingestion() is what settles it.
            score = min(100.0, dpd / 90 * 40 + total_outstanding / 500000 * 30)
            new_case = Case(
                id=_uid(),
                case_number=case_number,
                customer_id=customer.id,
                loan_id=loan.id,
                agent_id=None,
                status=CaseStatus.UNASSIGNED,
                priority=_priority_from_score(score),
                target_amount=overdue_amount if overdue_amount > 0 else total_outstanding,
                collected_amount=0.0,
                allocation_date=None,
                allocation_score=0.0,
                is_ml_allocated=False,
                visit_count=0,
                max_visits_allowed=3,
                is_escalated=False,
            )
            if not dry_run:
                db.add(new_case)
            result["action_case"] = "inserted"

    return result


# ─── Main runner ──────────────────────────────────────────────────────────────

# The bank's own verdict on an account, mapped onto a snapshot outcome. This is
# the whole reason repayment_score_snapshots can ever become a training set:
# the truth about whether a borrower paid arrives HERE, in this column, once a
# day — and until today it was applied and then discarded.
#
# SETTLED / RECALLED / WRITTEN_OFF / DECEASED are CENSORED, not negatives. They
# are the bank's administrative decisions; labelling them "the borrower failed
# to pay" would teach a model that the bank's own choice is the borrower's
# fault, and it would then underrate exactly the accounts pulled back for good
# reasons. The documented training pull excludes them.
_BANK_ACTION_OUTCOME = {
    "PAID_DIRECT": OUTCOME_REPAID,
    "SETTLED":     OUTCOME_SETTLED,
    "WRITTEN_OFF": OUTCOME_WRITTEN_OFF,
    "RECALL":      OUTCOME_RECALLED,
    "DECEASED":    OUTCOME_DECEASED,
}


def _label_snapshots(db, verdicts: dict, today: date) -> dict:
    """Attach the bank's verdict to the snapshots that predicted it.

    Only UNLABELLED snapshots are touched, and only for loans this file reported
    a terminal action on. A snapshot already carrying an outcome is left alone —
    the first truth observed is the truth, and overwriting it would quietly
    rewrite history a model has already been trained on.
    """
    labelled = {"rows": 0, "loans": 0}
    for loan_id, action in verdicts.items():
        outcome = _BANK_ACTION_OUTCOME.get(action)
        if outcome is None or not loan_id:
            continue
        rows = (
            db.query(RepaymentSnapshot)
            .filter(RepaymentSnapshot.loan_id == loan_id,
                    RepaymentSnapshot.outcome.is_(None))
            .all()
        )
        if not rows:
            continue
        labelled["loans"] += 1
        for row in rows:
            row.outcome = outcome
            row.outcome_source = OUTCOME_SOURCE_BANK_ACTION
            row.outcome_observed_at = today
            row.outcome_horizon_days = (today - row.as_of_date).days
            # How stale the features were when the truth landed. A modeller can
            # filter on freshness instead of assuming it.
            row.feature_age_days = (today - row.as_of_date).days
            labelled["rows"] += 1
    return labelled


def run_ingestion(file_path: Path, dry_run: bool = False) -> None:
    log.info("=" * 60)
    log.info("TIQCollect Daily Ingestion  %s", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    log.info("File    : %s", file_path)
    log.info("Dry-run : %s", dry_run)
    log.info("=" * 60)

    if not file_path.exists():
        log.error("File not found: %s", file_path)
        sys.exit(1)

    if not dry_run:
        Base.metadata.create_all(bind=engine)

    today = date.today()
    db = SessionLocal()

    counters = {
        "rows_read": 0,
        "rows_skipped": 0,
        # customers
        "customers_inserted": 0,
        "customers_updated": 0,
        # loans
        "loans_inserted": 0,
        "loans_updated": 0,
        "loans_bucket_changed": 0,
        # cases — new
        "cases_inserted": 0,
        # cases — bank closures
        "cases_auto_closed_paid": 0,
        "cases_auto_closed_recall": 0,
        "cases_auto_closed_written_off": 0,
        "cases_auto_closed_deceased": 0,
        # cases — updates
        "cases_target_increased": 0,
        # cases — already done
        "cases_already_resolved": 0,
        # repayment scoring (2026-08-21)
        "loans_rescored": 0,
        "snapshots_written": 0,
        "snapshot_rows_labelled": 0,
        "customers_risk_written": 0,
    }

    # Loans this file touched, and the bank's verdict where it gave one. Both
    # are applied AFTER the row loop: scoring needs every row committed first,
    # and a label must not survive a row-level rollback.
    touched_loan_ids: set[str] = set()
    changed_loan_ids: set[str] = set()
    verdicts: dict[str, str] = {}

    try:
        with open(file_path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for i, row in enumerate(reader, start=1):
                counters["rows_read"] += 1
                try:
                    result = process_row(row, db, dry_run, today)

                    if result["skipped_reason"]:
                        log.debug("Row %d skipped: %s", i, result["skipped_reason"])
                        counters["rows_skipped"] += 1
                        continue

                    # Customer counters
                    if result["action_customer"] == "inserted":
                        counters["customers_inserted"] += 1
                    elif result["action_customer"] == "updated":
                        counters["customers_updated"] += 1

                    # Loan counters
                    action_loan = result["action_loan"] or ""
                    if "inserted" in action_loan:
                        counters["loans_inserted"] += 1
                    elif "updated" in action_loan:
                        counters["loans_updated"] += 1
                        if "bucket_change" in action_loan:
                            counters["loans_bucket_changed"] += 1

                    # Case counters
                    action_case = result["action_case"] or ""
                    if action_case == "inserted":
                        counters["cases_inserted"] += 1
                    elif action_case == "auto_closed_paid":
                        counters["cases_auto_closed_paid"] += 1
                    elif action_case == "auto_closed_recall":
                        counters["cases_auto_closed_recall"] += 1
                    elif action_case == "auto_closed_written_off":
                        counters["cases_auto_closed_written_off"] += 1
                    elif action_case == "auto_closed_deceased":
                        counters["cases_auto_closed_deceased"] += 1
                    elif action_case == "target_increased":
                        counters["cases_target_increased"] += 1
                    elif "already" in action_case or "skipped" in action_case:
                        counters["cases_already_resolved"] += 1

                    if result["loan_id"]:
                        touched_loan_ids.add(result["loan_id"])
                        if result["state_changed"]:
                            changed_loan_ids.add(result["loan_id"])
                        if result["bank_action"] in _BANK_ACTION_OUTCOME:
                            verdicts[result["loan_id"]] = result["bank_action"]

                    if not dry_run and i % 200 == 0:
                        db.commit()
                        log.info("  Batch commit at row %d ...", i)

                except Exception as row_err:
                    log.warning("Row %d error [%s]: %s", i, row.get("case_number", "?"), row_err)
                    counters["rows_skipped"] += 1
                    db.rollback()

        if not dry_run:
            db.commit()
            log.info("Final commit done.")

        # ── Repayment scoring ────────────────────────────────────────────────
        # Runs once, after the facts are in. This is the ONLY thing that gives
        # Customer.risk_score a value; writing it is gated on
        # REPAYMENT_WRITE_RISK_SCORE, which defaults to False, so by default
        # this accrues snapshots and changes nothing a user can see.
        if touched_loan_ids:
            label_result = _label_snapshots(db, verdicts, today) if not dry_run else {
                "rows": 0, "loans": 0}
            score_result = RepaymentService(db).rescore(
                as_of=today,
                loan_ids=sorted(touched_loan_ids),
                trigger=TRIGGER_INGEST,
                dry_run=dry_run,
                changed_loan_ids=changed_loan_ids,
            )
            if not dry_run:
                db.commit()
            counters["loans_rescored"] = score_result["loans_scored"]
            counters["snapshots_written"] = score_result["snapshots_written"]
            counters["snapshot_rows_labelled"] = label_result["rows"]
            counters["customers_risk_written"] = score_result["customers_written"]
            log.info(
                "Repayment scoring: %d loans, %d snapshots, %d rows labelled, "
                "%d customers written (write gate %s)",
                score_result["loans_scored"], score_result["snapshots_written"],
                label_result["rows"], score_result["customers_written"],
                "OPEN" if score_result["write_risk_score_enabled"] else "closed",
            )

    except Exception as e:
        db.rollback()
        log.error("Fatal error: %s", e)
        raise
    finally:
        db.close()

    total_closures = (
        counters["cases_auto_closed_paid"] +
        counters["cases_auto_closed_recall"] +
        counters["cases_auto_closed_written_off"] +
        counters["cases_auto_closed_deceased"]
    )

    log.info("")
    log.info("─── Ingestion Summary ───────────────────────────────────────")
    log.info("  Rows read               : %d", counters["rows_read"])
    log.info("  Rows skipped/errors     : %d", counters["rows_skipped"])
    log.info("")
    log.info("  Customers inserted      : %d", counters["customers_inserted"])
    log.info("  Customers updated       : %d", counters["customers_updated"])
    log.info("")
    log.info("  Loans inserted          : %d", counters["loans_inserted"])
    log.info("  Loans updated           : %d", counters["loans_updated"])
    log.info("  Loans bucket changed    : %d  (re-prioritised at 8 PM)", counters["loans_bucket_changed"])
    log.info("")
    log.info("  Cases inserted          : %d  ← UNASSIGNED, ready for 8 PM allocation", counters["cases_inserted"])
    log.info("  Cases target increased  : %d  (penalties grew overdue amount)", counters["cases_target_increased"])
    log.info("  Cases already resolved  : %d  (no action needed)", counters["cases_already_resolved"])
    log.info("")
    log.info("  ── Bank-triggered closures (%d total) ──", total_closures)
    log.info("  Paid direct by customer : %d", counters["cases_auto_closed_paid"])
    log.info("  Recalled by bank        : %d", counters["cases_auto_closed_recall"])
    log.info("  Written off             : %d", counters["cases_auto_closed_written_off"])
    log.info("  Customer deceased       : %d", counters["cases_auto_closed_deceased"])
    if dry_run:
        log.info("")
        log.info("  DRY-RUN — no changes written to database.")
    log.info("─────────────────────────────────────────────────────────────")
    if not dry_run:
        log.info("")
        log.info("✅ Done. Celery allocation fires at 8:00 PM IST.")
        log.info("   Agents see new cases + beat route from 6:00 AM tomorrow.")


# ─── Sample CSV ───────────────────────────────────────────────────────────────

FIELDNAMES = [
    # Customer
    "customer_ref", "customer_name", "dob", "gender", "pan_masked", "aadhaar_masked",
    "phone_primary", "phone_alternate", "email",
    "address_line1", "address_line2", "city", "state", "pincode",
    "latitude", "longitude", "cibil_score", "language_preference",
    "preferred_contact_start", "preferred_contact_end",
    "is_hostile", "requires_female_agent", "do_not_contact",
    # Loan
    "loan_account_number", "loan_type", "bank_name", "branch_code",
    "sanctioned_amount", "disbursed_amount", "outstanding_principal",
    "total_outstanding", "overdue_amount", "emi_amount",
    "disbursement_date", "maturity_date", "last_payment_date", "next_due_date",
    "dpd", "interest_rate", "penal_charges",
    # Case
    "case_number", "target_amount",
    # Bank action (the key new fields)
    "bank_action", "recall_reason", "settlement_amount", "bank_remark",
]

SAMPLE_ROWS = [
    # Row 1 — Normal active overdue account (most common — appears every day)
    {
        "customer_ref": "CUST000001", "customer_name": "Ramesh Kumar Singh",
        "dob": "1979-03-15", "gender": "MALE",
        "pan_masked": "ABCDE1234F", "aadhaar_masked": "XXXXXXXX5678",
        "phone_primary": "9876543210", "phone_alternate": "8765432109",
        "email": "ramesh.singh@email.com",
        "address_line1": "Flat 4B, Sunrise Apartments, MG Road", "address_line2": "",
        "city": "Mumbai", "state": "Maharashtra", "pincode": "400001",
        "latitude": "19.0760", "longitude": "72.8777",
        "cibil_score": "620", "language_preference": "HINDI",
        "preferred_contact_start": "9", "preferred_contact_end": "18",
        "is_hostile": "false", "requires_female_agent": "false", "do_not_contact": "false",
        "loan_account_number": "LN202401001", "loan_type": "PERSONAL",
        "bank_name": "HDFC Bank", "branch_code": "MUM001",
        "sanctioned_amount": "300000", "disbursed_amount": "295000",
        "outstanding_principal": "180000", "total_outstanding": "192500",
        "overdue_amount": "45000", "emi_amount": "9500",
        "disbursement_date": "2022-06-01", "maturity_date": "2027-06-01",
        "last_payment_date": "2024-09-10", "next_due_date": "2024-10-10",
        "dpd": "46", "interest_rate": "14.5", "penal_charges": "1200",
        "case_number": "CASE0000001", "target_amount": "45000",
        "bank_action": "ACTIVE", "recall_reason": "", "settlement_amount": "", "bank_remark": "",
    },
    # Row 2 — Customer paid bank directly overnight (DPD dropped to 0)
    {
        "customer_ref": "CUST000002", "customer_name": "Priya Sharma",
        "dob": "1985-07-22", "gender": "FEMALE",
        "pan_masked": "FGHIJ5678K", "aadhaar_masked": "XXXXXXXX1234",
        "phone_primary": "9123456789", "phone_alternate": "",
        "email": "", "address_line1": "12, Laxmi Nagar Colony", "address_line2": "Near City Mall",
        "city": "Delhi", "state": "Delhi", "pincode": "110001",
        "latitude": "28.6139", "longitude": "77.2090",
        "cibil_score": "700", "language_preference": "HINDI",
        "preferred_contact_start": "10", "preferred_contact_end": "17",
        "is_hostile": "false", "requires_female_agent": "true", "do_not_contact": "false",
        "loan_account_number": "LN202401002", "loan_type": "GOLD",
        "bank_name": "State Bank of India", "branch_code": "DEL005",
        "sanctioned_amount": "150000", "disbursed_amount": "150000",
        "outstanding_principal": "0", "total_outstanding": "0",
        "overdue_amount": "0", "emi_amount": "6200",
        "disbursement_date": "2023-01-15", "maturity_date": "2026-01-15",
        "last_payment_date": "2024-10-21", "next_due_date": "2024-11-20",
        "dpd": "0", "interest_rate": "11.0", "penal_charges": "0",
        "case_number": "CASE0000002", "target_amount": "0",
        "bank_action": "PAID_DIRECT", "recall_reason": "", "settlement_amount": "", "bank_remark": "Full payment received via NEFT on 21-Oct",
    },
    # Row 3 — High DPD NPA account (120 days overdue)
    {
        "customer_ref": "CUST000003", "customer_name": "Mohammed Iqbal",
        "dob": "1972-11-05", "gender": "MALE",
        "pan_masked": "KLMNO9012P", "aadhaar_masked": "XXXXXXXX9012",
        "phone_primary": "8899001122", "phone_alternate": "8899001133",
        "email": "m.iqbal@work.com",
        "address_line1": "Shop 7, Commercial Complex, Banjara Hills", "address_line2": "",
        "city": "Hyderabad", "state": "Telangana", "pincode": "500034",
        "latitude": "17.4100", "longitude": "78.4500",
        "cibil_score": "480", "language_preference": "TELUGU",
        "preferred_contact_start": "9", "preferred_contact_end": "18",
        "is_hostile": "false", "requires_female_agent": "false", "do_not_contact": "false",
        "loan_account_number": "LN202401003", "loan_type": "BUSINESS",
        "bank_name": "ICICI Bank", "branch_code": "HYD012",
        "sanctioned_amount": "1000000", "disbursed_amount": "980000",
        "outstanding_principal": "750000", "total_outstanding": "815000",
        "overdue_amount": "183000", "emi_amount": "22000",
        "disbursement_date": "2021-09-01", "maturity_date": "2026-09-01",
        "last_payment_date": "2024-06-05", "next_due_date": "2024-10-05",
        "dpd": "121", "interest_rate": "18.5", "penal_charges": "5500",
        "case_number": "CASE0000003", "target_amount": "183000",
        "bank_action": "ACTIVE", "recall_reason": "", "settlement_amount": "", "bank_remark": "",
    },
    # Row 4 — Bank recalled: customer filed a complaint with RBI ombudsman
    {
        "customer_ref": "CUST000004", "customer_name": "Sunita Devi",
        "dob": "1968-04-10", "gender": "FEMALE",
        "pan_masked": "PQRST3456U", "aadhaar_masked": "XXXXXXXX3456",
        "phone_primary": "9988776655", "phone_alternate": "",
        "email": "", "address_line1": "House 22, Sector 8, RK Puram", "address_line2": "",
        "city": "Delhi", "state": "Delhi", "pincode": "110022",
        "latitude": "28.5700", "longitude": "77.1800",
        "cibil_score": "510", "language_preference": "HINDI",
        "preferred_contact_start": "10", "preferred_contact_end": "17",
        "is_hostile": "false", "requires_female_agent": "true", "do_not_contact": "false",
        "loan_account_number": "LN202401004", "loan_type": "HOME",
        "bank_name": "Axis Bank", "branch_code": "DEL022",
        "sanctioned_amount": "2500000", "disbursed_amount": "2400000",
        "outstanding_principal": "1900000", "total_outstanding": "1960000",
        "overdue_amount": "95000", "emi_amount": "28000",
        "disbursement_date": "2019-03-01", "maturity_date": "2034-03-01",
        "last_payment_date": "2024-07-01", "next_due_date": "2024-10-01",
        "dpd": "112", "interest_rate": "9.5", "penal_charges": "3000",
        "case_number": "CASE0000004", "target_amount": "95000",
        "bank_action": "RECALL", "recall_reason": "CUSTOMER_COMPLAINT",
        "settlement_amount": "", "bank_remark": "RBI ombudsman complaint filed. Cease all field visits immediately.",
    },
    # Row 5 — Bank-approved one-time settlement (customer negotiated reduced amount)
    {
        "customer_ref": "CUST000005", "customer_name": "Vikram Malhotra",
        "dob": "1975-08-25", "gender": "MALE",
        "pan_masked": "UVWXY6789Z", "aadhaar_masked": "XXXXXXXX6789",
        "phone_primary": "9871234560", "phone_alternate": "9871234561",
        "email": "v.malhotra@biz.com",
        "address_line1": "B-12, Industrial Area Phase 2", "address_line2": "",
        "city": "Pune", "state": "Maharashtra", "pincode": "411018",
        "latitude": "18.5800", "longitude": "73.9200",
        "cibil_score": "430", "language_preference": "MARATHI",
        "preferred_contact_start": "9", "preferred_contact_end": "18",
        "is_hostile": "true", "requires_female_agent": "false", "do_not_contact": "false",
        "loan_account_number": "LN202401005", "loan_type": "BUSINESS",
        "bank_name": "Kotak Mahindra Bank", "branch_code": "PUN018",
        "sanctioned_amount": "800000", "disbursed_amount": "780000",
        "outstanding_principal": "600000", "total_outstanding": "680000",
        "overdue_amount": "220000", "emi_amount": "18500",
        "disbursement_date": "2020-11-01", "maturity_date": "2025-11-01",
        "last_payment_date": "2023-12-01", "next_due_date": "2024-10-01",
        "dpd": "305", "interest_rate": "21.0", "penal_charges": "12000",
        "case_number": "CASE0000005", "target_amount": "220000",
        "bank_action": "SETTLED", "recall_reason": "",
        "settlement_amount": "145000", "bank_remark": "OTS approved. Collect ₹1,45,000 as full and final settlement.",
    },
    # Row 6 — Customer deceased, stop all visits
    {
        "customer_ref": "CUST000006", "customer_name": "Harish Chandra Gupta",
        "dob": "1955-12-01", "gender": "MALE",
        "pan_masked": "ABCDF1111G", "aadhaar_masked": "XXXXXXXX1111",
        "phone_primary": "9900112233", "phone_alternate": "",
        "email": "", "address_line1": "Old Quarter, Near Temple Road", "address_line2": "",
        "city": "Lucknow", "state": "Uttar Pradesh", "pincode": "226001",
        "latitude": "26.8467", "longitude": "80.9462",
        "cibil_score": "520", "language_preference": "HINDI",
        "preferred_contact_start": "9", "preferred_contact_end": "18",
        "is_hostile": "false", "requires_female_agent": "false", "do_not_contact": "false",
        "loan_account_number": "LN202401006", "loan_type": "PERSONAL",
        "bank_name": "Punjab National Bank", "branch_code": "LKO001",
        "sanctioned_amount": "200000", "disbursed_amount": "200000",
        "outstanding_principal": "140000", "total_outstanding": "155000",
        "overdue_amount": "42000", "emi_amount": "7200",
        "disbursement_date": "2022-01-01", "maturity_date": "2027-01-01",
        "last_payment_date": "2024-05-10", "next_due_date": "2024-10-10",
        "dpd": "163", "interest_rate": "13.0", "penal_charges": "2100",
        "case_number": "CASE0000006", "target_amount": "42000",
        "bank_action": "DECEASED", "recall_reason": "",
        "settlement_amount": "", "bank_remark": "Death certificate submitted by family on 18-Oct-2024.",
    },
    # Row 7 — Bucket improved: was 90+ DPD, partial payment brought it to 45 DPD (re-prioritise)
    {
        "customer_ref": "CUST000007", "customer_name": "Kavitha Rajan",
        "dob": "1982-06-18", "gender": "FEMALE",
        "pan_masked": "MNOPQ2222R", "aadhaar_masked": "XXXXXXXX2222",
        "phone_primary": "9444556677", "phone_alternate": "",
        "email": "kavitha.r@mail.com",
        "address_line1": "42, Anna Salai Apartments", "address_line2": "T Nagar",
        "city": "Chennai", "state": "Tamil Nadu", "pincode": "600017",
        "latitude": "13.0400", "longitude": "80.2300",
        "cibil_score": "590", "language_preference": "TAMIL",
        "preferred_contact_start": "9", "preferred_contact_end": "18",
        "is_hostile": "false", "requires_female_agent": "false", "do_not_contact": "false",
        "loan_account_number": "LN202401007", "loan_type": "EDUCATION",
        "bank_name": "Indian Bank", "branch_code": "CHN017",
        "sanctioned_amount": "500000", "disbursed_amount": "500000",
        "outstanding_principal": "320000", "total_outstanding": "341000",
        "overdue_amount": "28000", "emi_amount": "11000",
        "disbursement_date": "2021-07-01", "maturity_date": "2028-07-01",
        "last_payment_date": "2024-10-05", "next_due_date": "2024-11-05",
        "dpd": "45", "interest_rate": "9.0", "penal_charges": "500",
        "case_number": "CASE0000007", "target_amount": "28000",
        "bank_action": "ACTIVE", "recall_reason": "", "settlement_amount": "",
        "bank_remark": "Partial payment of ₹55,000 received. DPD reduced from 105 to 45.",
    },
]


def generate_sample(output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(SAMPLE_ROWS)
    print(f"\nSample CSV written to: {output_path}")
    print(f"\n{len(FIELDNAMES)} columns:")
    sections = [
        ("Customer (23)", FIELDNAMES[:23]),
        ("Loan (17)",     FIELDNAMES[23:40]),
        ("Case (2)",      FIELDNAMES[40:42]),
        ("Bank action (4)", FIELDNAMES[42:]),
    ]
    for label, cols in sections:
        print(f"\n  [{label}]")
        for c in cols:
            print(f"    {c}")
    print(f"\n{len(SAMPLE_ROWS)} sample rows covering:")
    print("  Row 1 - ACTIVE:      normal daily refresh (most rows will be like this)")
    print("  Row 2 - PAID_DIRECT: customer paid bank overnight, auto-close case")
    print("  Row 3 - ACTIVE NPA:  120+ DPD, high priority")
    print("  Row 4 - RECALL:      RBI complaint, cease all visits")
    print("  Row 5 - SETTLED:     bank OTS, collect reduced settlement amount")
    print("  Row 6 - DECEASED:    stop all visits, mark do_not_contact")
    print("  Row 7 - ACTIVE:      DPD bucket improved (partial payment), re-prioritise")


# ─── Entry point ──────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="TIQCollect daily case ingestion")
    parser.add_argument("--file", type=Path, default=None, help="Path to CSV file")
    parser.add_argument("--dry-run", action="store_true", help="No DB writes — preview only")
    parser.add_argument("--generate-sample", action="store_true", help="Write sample CSV and exit")
    args = parser.parse_args()

    if args.generate_sample:
        generate_sample(Path(__file__).parent / "sample_daily_feed.csv")
        return

    file_path = args.file
    if file_path is None:
        file_path = find_todays_file()
        if file_path is None:
            log.error(
                "No file found in %s\n"
                "Expected: cases_YYYYMMDD.csv / latest.csv / cases.csv\n"
                "Or pass --file <path>",
                DEFAULT_INCOMING_DIR,
            )
            sys.exit(1)

    run_ingestion(file_path, dry_run=args.dry_run)


def find_todays_file() -> Path | None:
    today_str = date.today().strftime("%Y%m%d")
    for name in [f"cases_{today_str}.csv", "latest.csv", "cases.csv"]:
        p = DEFAULT_INCOMING_DIR / name
        if p.exists():
            return p
    return None


if __name__ == "__main__":
    main()
