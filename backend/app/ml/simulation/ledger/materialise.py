# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-09-09 — NEW. Phase 3: the event ledger, loaded into the REAL schema, and
#   rewound to any past date.
#
#   WHY THIS EXISTS. `panel.py` derives features from events with pandas.
#   `services/ml_scoring_service.py` derives them from ORM queries against
#   mutable current-state columns. Those are two independent implementations of
#   one definition, and until now nothing could compare them — which is exactly
#   how four silent ML failures survived a full cycle in September, and why
#   `ptp_kept_ratio` was absent from every served vector while coverage stayed
#   above its floor.
#
#   THE REWIND IS THE HARD PART. `Loan.dpd`, `Loan.overdue_amount`,
#   `Customer.cibil_score` and `PTP.status` are all overwritten in place with no
#   history — that is the schema's central limitation and the reason a
#   retrospective backtest against the live database is impossible. Here the
#   ledger CAN answer "what was this on day T", so `rewind_to()` writes those
#   columns back to their value at T and the adapter, which only ever reads
#   current state, sees the past exactly as it saw the present.
#
#   IT MUST NOT BECOME A SECOND FEATURE IMPLEMENTATION. Nothing in this file
#   computes a model feature. It writes rows; the adapter reads them.
#
# 2026-09-15 — Loads the v2 channels: `Visit.outcome` from the ledger's visit
#   outcome, one `CallLog` row per call event, `Customer.fraud_flag` at load
#   (static, bank-reported), and `Customer.is_hostile` on REWIND — it is a
#   flag event with a day, overwritten in place in the live schema like `dpd`,
#   so "was it raised before as_of" is a rewind question.
# ───────────────────────────────────────────────────────────────────────────
"""Load a `Ledger` into the production schema and rewind it to a past date.

    mat = Materialiser(ledger, cfg)
    mat.load(db)                 # static rows + all events, once
    mat.rewind_to(db, day=180)   # mutable state as it stood on day 180
    feats = MLScoringService(db).build_features(loan, as_of=...)

`load` is idempotent per session and writes every event with its true historical
timestamp. `rewind_to` is the only thing that changes between as_of dates, and
it touches ONLY the columns the real schema overwrites in place.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta, timezone

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from app.ml.simulation.ledger import billing
from app.ml.simulation.ledger.config import LedgerConfig
from app.ml.simulation.ledger.simulator import Ledger
from app.models.agent import Agent, AgentSpecialization, AgentStatus, AgentTier
from app.models.call_log import CallLog, CallOutcome
from app.models.case import Case, CasePriority, CaseStatus
from app.models.customer import CUSTOMER_TAG_DECEASED, Customer
from app.models.loan import DPDBucket, Loan, LoanStatus, LoanType, dpd_bucket_for
from app.models.payment import Payment, PaymentMode, PaymentStatus
from app.models.call_log import BorrowerDisposition
from app.models.ptp import PTP, PTPStatus
from app.models.user import User, UserRole
from app.models.visit import DefaultReason, Visit, VisitOutcome

logger = logging.getLogger(__name__)

#: Ledger status strings -> the product's enums. Written out rather than
#: inferred so an unmapped value fails loudly instead of silently defaulting.
PAYMENT_STATUS = {
    "VERIFIED": PaymentStatus.VERIFIED,
    "PENDING_VERIFICATION": PaymentStatus.PENDING_VERIFICATION,
    "REJECTED": PaymentStatus.REJECTED,
    "REVERSED": PaymentStatus.REVERSED,
}
PTP_STATUS = {
    "HONORED": PTPStatus.HONORED,
    "BROKEN": PTPStatus.BROKEN,
    "RESCHEDULED": PTPStatus.RESCHEDULED,
}


def _dt(start: date, day: int) -> datetime:
    """Midnight UTC on a day index.

    *(This used to return MIDDAY, on the reasoning that a same-day event should
    fall clearly on one side of an end-of-day `as_of`. It does — but it also put
    every day-difference out by one: the adapter computes
    `(as_of_dt - event).days`, and 23:59 on day t minus 12:00 on day t-21 is
    20 days and 11:59, which floors to 20 against the panel's 21. Measured:
    130 of 202 loans disagreed on `days_since_last_contact`, every one of them
    by exactly 1. Midnight makes the difference a whole number of days, and a
    same-day event still sorts before an end-of-day as_of.)*
    """
    return datetime.combine(start + timedelta(days=int(day)), time(0, 0),
                            tzinfo=timezone.utc)


class Materialiser:
    """Writes a ledger into the real schema, and rewinds mutable state."""

    def __init__(self, ledger: Ledger, cfg: LedgerConfig):
        self.ledger = ledger
        self.cfg = cfg
        self.start = cfg.start_date
        self._loan_ids: list[str] = []

    # ── one-time load ───────────────────────────────────────────────────────
    def load(self, db: Session, *, limit_loans: int | None = None) -> list[str]:
        """Static rows and every event, with true historical timestamps."""
        led, cfg = self.ledger, self.cfg
        loans = led.loans if limit_loans is None else led.loans.head(limit_loans)
        keep = set(loans.loan_id)
        self._loan_ids = list(loans.loan_id)

        mgr = User(id="u-mgr", email="m@ledger.test", phone="9800000001",
                   full_name="M", hashed_password="h",
                   role=UserRole.AGENCY_MANAGER, is_active=True, is_verified=True)
        db.add(mgr)
        db.flush()

        agent_ids = {}
        for i, row in led.agents.iterrows():
            usr = User(id=f"u-{row.agent_id}", email=f"{row.agent_id}@ledger.test",
                       phone=f"97{i:08d}", full_name=row.agent_id,
                       hashed_password="h", role=UserRole.FIELD_AGENT,
                       is_active=True, is_verified=True)
            db.add(usr)
            db.flush()
            ag = Agent(id=row.agent_id, user_id=usr.id, employee_code=row.agent_id,
                       id_card_number=f"IC{i:04d}", agency_id="AG",
                       manager_user_id=mgr.id, gender="M",
                       base_latitude=28.4, base_longitude=77.0,
                       territory="Gurugram", languages_spoken=["HINDI"],
                       specialization=AgentSpecialization.BOTH,
                       max_cases_per_day=10, status=AgentStatus.ON_DUTY,
                       tier=AgentTier.TIER_1, ranking_score=50.0,
                       lifetime_collection_rate=0.5)
            db.add(ag)
            agent_ids[row.agent_id] = row.agent_id
        db.flush()

        borrowers = led.borrowers.set_index("borrower_id")
        seen_cust = set()
        for r in loans.itertuples():
            b = borrowers.loc[r.borrower_id]
            if r.borrower_id not in seen_cust:
                db.add(Customer(
                    id=r.borrower_id, customer_ref=r.borrower_id,
                    full_name=r.borrower_id,
                    # A REAL date of birth, so the adapter's age arithmetic has
                    # something to work from. `dob_day` is a ledger fact.
                    date_of_birth=(self.start + timedelta(days=int(b.dob_day))
                                   ).strftime("%Y-%m-%d"),
                    gender="M", pan_masked="A1234B", aadhaar_masked="1111",
                    phone_primary="9900000001", address_line1="x",
                    city=b.city, state="HR", pincode="122001",
                    latitude=28.4, longitude=77.0, language_preference="HINDI",
                    customer_segment=b.employment_type,
                    cibil_score=None,          # set by rewind_to
                    # Static from origination; `is_hostile` is set by rewind_to.
                    fraud_flag=bool(getattr(b, "fraud_flag", 0)),
                ))
                seen_cust.add(r.borrower_id)

            db.add(Loan(
                id=r.loan_id, customer_id=r.borrower_id,
                loan_account_number=r.loan_id, loan_type=LoanType(r.loan_type),
                bank_name="HDFC", branch_code=r.branch_code,
                sanctioned_amount=float(r.sanction_amount),
                disbursed_amount=float(r.sanction_amount),
                outstanding_principal=0.0, total_outstanding=0.0,
                overdue_amount=0.0, emi_amount=float(r.emi_amount),
                interest_rate=float(r.interest_rate),
                tenure_months=int(r.tenure_months),
                # ONE origination fact, shared with the panel's months_on_book.
                disbursement_date=(self.start + timedelta(days=int(r.origination_day))
                                   ).strftime("%Y-%m-%d"),
                maturity_date=(self.start + timedelta(
                    days=int(r.origination_day) + cfg.cycle_days * int(r.tenure_months))
                    ).strftime("%Y-%m-%d"),
                dpd=0, dpd_bucket=DPDBucket.CURRENT, status=LoanStatus.ACTIVE,
            ))
            # ONE case per loan. The adapter aggregates over every case of a
            # loan, so a second case would change no feature here — but it also
            # would not be a ledger fact, and inventing one to exercise the
            # union would be testing the fixture, not the adapter.
            db.add(Case(
                id=f"C-{r.loan_id}", case_number=f"C-{r.loan_id}",
                customer_id=r.borrower_id, loan_id=r.loan_id,
                status=CaseStatus.ASSIGNED, priority=CasePriority.MEDIUM,
                target_amount=float(r.emi_amount), collected_amount=0.0,
            ))
        db.flush()

        for p in led.payments.itertuples():
            if p.loan_id not in keep:
                continue
            db.add(Payment(
                id=p.payment_id, case_id=f"C-{p.loan_id}",
                agent_id=led.agents.agent_id.iloc[0],
                amount=float(p.amount), mode=PaymentMode.CASH,
                status=PaymentStatus.VERIFIED,      # rewound below
                receipt_number=p.receipt_number,
                payment_date=_dt(self.start, p.payment_day),
            ))
        has_outcome = "outcome" in led.visits.columns
        # 2026-09-16 — the structured disposition, where the ledger recorded
        # one (observe_disposition on). The ledger's vocabulary IS the
        # product's enum; a value the enum does not know raises here.
        has_disp_v = "disposition" in led.visits.columns
        for v in led.visits.itertuples():
            if v.loan_id not in keep:
                continue
            vdisp = getattr(v, "disposition", None) if has_disp_v else None
            vdisp = (BorrowerDisposition(vdisp)
                     if isinstance(vdisp, str) and vdisp else None)
            # The ledger's outcome vocabulary IS the product's enum; a value
            # the enum does not know raises here rather than defaulting.
            outcome = (VisitOutcome(v.outcome) if has_outcome else
                       (VisitOutcome.PTP if v.met else VisitOutcome.NOT_AVAILABLE))
            reason = getattr(v, "default_reason", None)
            db.add(Visit(
                id=v.visit_id, case_id=f"C-{v.loan_id}", agent_id=v.agent_id,
                check_in_latitude=28.4, check_in_longitude=77.0,
                check_in_time=_dt(self.start, v.day),
                distance_from_customer_metres=50.0,
                customer_met=bool(v.met),
                outcome=outcome,
                default_reason=(DefaultReason(reason)
                                if isinstance(reason, str) else None),
                borrower_disposition=vdisp,
            ))
        calls = getattr(led, "calls", None)
        if calls is not None and len(calls):
            loan_borrower = dict(zip(loans.loan_id, loans.borrower_id))
            has_disp_c = "disposition" in calls.columns
            for k in calls.itertuples():
                if k.loan_id not in keep:
                    continue
                intent = getattr(k, "payment_intent", None)
                cdisp = getattr(k, "disposition", None) if has_disp_c else None
                cdisp = (BorrowerDisposition(cdisp)
                         if isinstance(cdisp, str) and cdisp else None)
                db.add(CallLog(
                    id=k.call_id, case_id=f"C-{k.loan_id}", agent_id=k.agent_id,
                    customer_id=loan_borrower[k.loan_id],
                    called_at=_dt(self.start, k.day),
                    outcome=CallOutcome(k.outcome),
                    # 2026-09-15 (later): the ledger's own duration where the
                    # channel is on; the old placeholder 90 where it is not.
                    duration_seconds=(
                        (int(k.duration_seconds)
                         if getattr(k, "duration_seconds", None) is not None
                         and not pd.isna(k.duration_seconds) else 90)
                        if k.answered else None),
                    # None where the call was not answered — nobody said
                    # anything — exactly as the column is nullable for.
                    payment_intent_signalled=(bool(intent) if k.answered else None),
                    verbal_payment_date=(
                        self.start + timedelta(days=int(k.verbal_due_day))
                        if getattr(k, "verbal_due_day", -1) is not None
                        and int(getattr(k, "verbal_due_day", -1)) >= 0 else None),
                    borrower_disposition=cdisp,
                ))
        for t in led.ptps.itertuples():
            if t.loan_id not in keep:
                continue
            row = PTP(id=t.ptp_id, case_id=f"C-{t.loan_id}",
                      agent_id=led.agents.agent_id.iloc[0],
                      committed_amount=float(t.committed_amount),
                      committed_date=self.start + timedelta(days=int(t.committed_day)),
                      status=PTPStatus.ACTIVE)
            db.add(row)
            db.flush()
            # `created_at` carries a server default, so it has to be forced
            # AFTER the insert. The adapter windows PTPs on exactly this column,
            # and in the live demo database it holds the seed run's wall clock
            # rather than the promise's real date — which is why ptp_kept_ratio
            # cannot be reconstructed historically there at all.
            row.created_at = _dt(self.start, t.created_day)
        db.flush()
        db.commit()
        logger.info("ledger.materialised loans=%d", len(loans))
        return self._loan_ids

    # ── the rewind ──────────────────────────────────────────────────────────
    def rewind_to(self, db: Session, day: int) -> None:
        """Set every overwritten-in-place column to its value on `day`.

        `day` means THE START OF DAY `day`, matching the adapter exactly.
        `build_features` does `datetime.combine(as_of, datetime.min.time())`, so
        it discards the time of day and filters history on `< midnight(day)`.
        State rewound to include day `day` itself would hand the adapter a
        balance built from money its own history filter cannot see — measured,
        that put `last_payment_date` on the wrong side of the boundary and made
        `days_since_last_payment` read 0.0 where the panel had never-paid.

        This is what the bank's nightly file does to a real book, run backwards.
        Only mutable state is touched: events keep their own timestamps and are
        never edited, so the append-only half of the schema stays append-only.
        """
        led, cfg = self.ledger, self.cfg
        keep = set(self._loan_ids)

        # ── payment status as at `day` ──────────────────────────────────────
        pays = led.payments[led.payments.loan_id.isin(keep)]
        status_now = np.where(pays.status_effective_day < day,
                              pays.final_status, pays.initial_status)
        for pid, st in zip(pays.payment_id, status_now):
            db.query(Payment).filter(Payment.id == pid).update(
                {"status": PAYMENT_STATUS[st]}, synchronize_session=False)

        # ── PTP status as at `day` ──────────────────────────────────────────
        ptps = led.ptps[led.ptps.loan_id.isin(keep)]
        for t in ptps.itertuples():
            resolved = t.resolved_day is not None and 0 <= t.resolved_day < day
            st = PTP_STATUS[t.resolved_status] if resolved else PTPStatus.ACTIVE
            db.query(PTP).filter(PTP.id == t.ptp_id).update(
                {"status": st}, synchronize_session=False)

        # ── loan state as at `day` ──────────────────────────────────────────
        loans = led.loans[led.loans.loan_id.isin(keep)].set_index("loan_id")
        verified = pays[(pays.payment_day < day) & (status_now == "VERIFIED")]
        paid = (loans.opening_paid.astype(float)
                + verified.groupby("loan_id").amount.sum()
                .reindex(loans.index).fillna(0.0))
        sched = billing.Schedule(loans.first_due_day.to_numpy(),
                                 loans.emi_amount.to_numpy(dtype=float),
                                 loans.tenure_months.to_numpy(), cfg.cycle_days)
        dpd = billing.dpd_at(day, sched, paid.to_numpy(), cfg.grace_days)
        overdue = billing.overdue_at(day, sched, paid.to_numpy())
        penal = billing.penal_at(overdue, dpd, cfg.penal_rate_monthly, cfg.cycle_days)
        settled = billing.settled_count(paid.to_numpy(),
                                        loans.emi_amount.to_numpy(dtype=float),
                                        sched.billed_count(day))
        principal = np.maximum(
            loans.sanction_amount.to_numpy() *
            (1 - settled / np.maximum(loans.tenure_months.to_numpy(), 1)), 0.0)

        last_pay = verified.groupby("loan_id").payment_day.max().reindex(loans.index)
        for i, lid in enumerate(loans.index):
            lp = last_pay.iloc[i]
            db.query(Loan).filter(Loan.id == lid).update({
                "dpd": int(dpd[i]),
                "dpd_bucket": dpd_bucket_for(int(dpd[i])),
                "overdue_amount": round(float(overdue[i]), 2),
                "penal_charges": round(float(penal[i]), 2),
                "outstanding_principal": round(float(principal[i]), 2),
                "total_outstanding": round(
                    float(principal[i] + overdue[i] + penal[i]), 2),
                "last_payment_date": (
                    None if pd.isna(lp)
                    else (self.start + timedelta(days=int(lp))).strftime("%Y-%m-%d")),
            }, synchronize_session=False)

        # ── lifecycle as at `day` ───────────────────────────────────────────
        # Censoring is read off these columns by `outcomes.censoring_status`,
        # and each one lands somewhere different: a write-off on the case AND
        # the loan, a settlement on the loan, a RECALL only as free text in
        # `resolution_notes` because the schema records the bank action nowhere
        # else, and a death only as a customer tag. Without this rewind every
        # account looks ACTIVE forever and no prediction can ever be censored —
        # the labeller's whole censoring branch would go untested.
        life = led.lifecycle
        life = life[life.loan_id.isin(keep) & (life.event != "OPENED")
                    & (life.day < day)]
        for ev in life.itertuples():
            cid = f"C-{ev.loan_id}"
            if ev.event == "WRITTEN_OFF":
                db.query(Case).filter(Case.id == cid).update(
                    {"status": CaseStatus.WRITTEN_OFF}, synchronize_session=False)
                db.query(Loan).filter(Loan.id == ev.loan_id).update(
                    {"status": LoanStatus.WRITTEN_OFF}, synchronize_session=False)
            elif ev.event == "SETTLED":
                db.query(Loan).filter(Loan.id == ev.loan_id).update(
                    {"status": LoanStatus.SETTLED}, synchronize_session=False)
            elif ev.event == "CLOSED":
                db.query(Case).filter(Case.id == cid).update(
                    {"status": CaseStatus.CLOSED}, synchronize_session=False)
                db.query(Loan).filter(Loan.id == ev.loan_id).update(
                    {"status": LoanStatus.CLOSED}, synchronize_session=False)
            elif ev.event == "RECALLED":
                db.query(Case).filter(Case.id == cid).update(
                    {"resolution_notes": "RECALLED by bank on "
                     f"{self.start + timedelta(days=int(ev.day))}"},
                    synchronize_session=False)
            elif ev.event == "DECEASED":
                loan = db.query(Loan).filter(Loan.id == ev.loan_id).first()
                if loan is not None:
                    db.query(Customer).filter(
                        Customer.id == loan.customer_id).update(
                        {"tags": [CUSTOMER_TAG_DECEASED]}, synchronize_session=False)

        # ── hostility flag as at `day` ──────────────────────────────────────
        # Raised by an event, never lowered — so its value at `day` is "was an
        # event raised strictly before day". Written explicitly both ways, so a
        # rewind BACKWARDS clears a flag that had not yet been raised.
        flags = getattr(led, "flags", None)
        raised: set[str] = set()
        if flags is not None and len(flags):
            fl = flags[flags.loan_id.isin(keep) & (flags.flag == "HOSTILE")
                       & (flags.day < day)]
            raised = set(fl.loan_id)
        for lid in loans.index:
            db.query(Customer).filter(
                Customer.id == loans.loc[lid, "borrower_id"]).update(
                {"is_hostile": lid in raised}, synchronize_session=False)

        # ── bureau score as at `day` ────────────────────────────────────────
        pulls = led.bureau_pulls
        pulls = pulls[pulls.loan_id.isin(keep) & (pulls.day < day)] if len(pulls) \
            else pulls
        latest = (pulls.sort_values("day").groupby("loan_id").cibil_score.last()
                  if len(pulls) else pd.Series(dtype=float))
        for lid in loans.index:
            score = latest.get(lid, loans.loc[lid, "opening_cibil"])
            db.query(Customer).filter(
                Customer.id == loans.loc[lid, "borrower_id"]).update(
                {"cibil_score": int(round(float(score)))}, synchronize_session=False)
        db.commit()
