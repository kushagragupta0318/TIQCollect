"""A synthetic collections book with a real past, for validating the pipeline.

WHY THIS EXISTS
---------------
The repayment snapshots in the demo database cannot be trained on: the oldest is
7 days old and REPAYMENT_OUTCOME_HORIZON_DAYS is 30, so nothing has matured.
Waiting does not help either — this is a demo environment, so no real borrower
activity will arrive.

The tempting shortcut is to backfill: take today's loans and write snapshots
dated three months ago. That cannot be made honest. Loan.dpd, PTP.status and
Loan.last_payment_date are all overwritten in place with no history, so a
"historical" row rebuilt from them reports a DPD that reflects whether the
borrower has since paid. It would leak the label into the features and the
model would look excellent right up until it was deployed.

So this does the opposite. It simulates a book FORWARD through time, one day at
a time, and writes each snapshot at the moment the state it describes is
genuinely the current state:

    for each simulated day D:
        age the loans, open cases, assign agents, run visits,
        record promises, take payments          <- the book moves to D
        if D is a scoring date:
            RepaymentService.rescore(as_of=D)   <- snapshot what is true NOW

Nothing is reconstructed. At the instant of every rescore, `loan.dpd` IS the DPD
on that date, because the simulation has not yet reached tomorrow. The features
are frozen by the real service, the label is attached later by the real
labeller, and EB is fitted by the real estimator over the real payment ledger.

WHAT IS SIMULATED, AND WHAT MUST BE LEARNED
-------------------------------------------
Two hidden variables drive the world and are NEVER written to the database:

    borrower  willingness x capacity   -> a repayment propensity
    agent     ability, per (loan_type, dpd_bucket) segment

Observable features are generated as NOISY functions of the borrower latents —
a CIBIL score with a 55-point error, a DPD that only loosely tracks
willingness — so the model sees a blurred view of the truth and has a real
inference problem. Payment events are Bernoulli draws on a logistic combination
of borrower propensity, agent segment ability, DPD, arrears ratio and promise
history, plus per-event noise. A willing borrower sometimes fails; a difficult
one sometimes pays.

The generator's formula is deliberately NOT the model's formula. The model gets
observables; the simulator uses latents. Recovering the planted relationships
from the observables is the thing being tested.

The latents are written to a sidecar JSON file, never to the database, so there
is no path by which they can reach the feature table.

ISOLATION
---------
Everything lands in a SEPARATE DATABASE (default `fieldops_synth`). The demo
database is never opened. `--reset` drops and recreates it, so removing the
whole experiment is one flag.

USAGE
    python scripts/generate_synthetic_repayment_history.py --reset --seed 42
    python scripts/generate_synthetic_repayment_history.py --reset --seed 42 \
        --borrowers 1500 --start 2025-09-01 --end 2026-08-31

Or, for the whole pipeline end to end, use scripts/run_synthetic_experiment.py.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import pathlib
import random
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

DEFAULT_SYNTH_DB = "fieldops_synth"


def _demo_url() -> str:
    """The demo database URL, used ONLY to derive the server address."""
    if os.environ.get("DEMO_DATABASE_URL"):
        return os.environ["DEMO_DATABASE_URL"]
    here = pathlib.Path(__file__).resolve().parents[2]

    def envmap(p: pathlib.Path) -> dict[str, str]:
        d: dict[str, str] = {}
        if not p.exists():
            return d
        for ln in p.read_text(encoding="utf-8", errors="ignore").splitlines():
            ln = ln.strip()
            if ln and not ln.startswith("#") and "=" in ln:
                k, v = ln.split("=", 1)
                d[k.strip()] = v.strip().strip('"').strip("'")
        return d

    import re
    root, be = envmap(here / ".env"), envmap(here / "backend" / ".env")
    url = re.sub(r"\$\{(\w+)(?::-[^}]*)?\}", lambda m: root.get(m.group(1), m.group(0)),
                 be.get("DATABASE_URL", ""))
    return url.replace("@postgres:", "@localhost:").replace(":5432/", ":15432/")


def synth_url(db_name: str) -> str:
    base = _demo_url()
    return base.rsplit("/", 1)[0] + "/" + db_name


# ── Reference distributions ──────────────────────────────────────────────────
# Measured from the demo book on 2026-09-03 so the synthetic portfolio looks
# like this application's domain rather than arbitrary Faker output. The MIX is
# deliberately more concentrated than the demo's near-uniform spread: EB is
# keyed on (loan_type, dpd_bucket), and a portfolio spread evenly over 8 types
# gives every cell 3 observations and none the 20+ needed to see shrinkage
# actually release toward an agent's own rate. Concentrating gives BOTH regimes
# in one book, which is the point.
LOAN_TYPE_WEIGHTS = {
    "PERSONAL": 0.26, "AUTO": 0.22, "CREDIT_CARD": 0.18, "GOLD": 0.14,
    "MICROFINANCE": 0.08, "BUSINESS": 0.06, "EDUCATION": 0.04, "HOME": 0.02,
}
# (emi_low, emi_high, tenure_months) — the realistic instalment bands settled on
# in scripts/rescale_to_realistic_emi.py.
EMI_BANDS = {
    "MICROFINANCE": (1_200, 5_000, 24), "CREDIT_CARD": (1_500, 12_000, 12),
    "GOLD": (3_000, 18_000, 24), "EDUCATION": (4_000, 22_000, 60),
    "PERSONAL": (5_000, 30_000, 48), "AUTO": (8_000, 32_000, 60),
    "BUSINESS": (10_000, 55_000, 60), "HOME": (12_000, 70_000, 180),
}
SECURED = {"HOME", "AUTO", "GOLD"}
CUSTOMER_SEGMENTS = ["SALARIED"] * 5 + ["BUSINESS_OWNER", "HOMEMAKER",
                                        "SELF_EMPLOYED", "RETIRED"]
# Delhi NCR, matching the demo book's spread.
LAT_RANGE, LON_RANGE = (28.36, 28.79), (76.93, 77.59)


def dpd_to_bucket(dpd: int) -> str:
    if dpd == 0:
        return "CURRENT"
    if dpd <= 30:
        return "BUCKET_1"
    if dpd <= 60:
        return "BUCKET_2"
    if dpd <= 90:
        return "BUCKET_3"
    return "NPA"


def logistic(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-max(-40.0, min(40.0, x))))


# ── The hidden world ─────────────────────────────────────────────────────────
class Truth:
    """Latent variables. NEVER written to the database.

    Held in a plain object and dumped to a sidecar JSON so there is no field on
    any model that could accidentally be picked up by build_features. If these
    ever needed to be persisted, the right place would still not be `features`.
    """

    def __init__(self, rng: random.Random, agent_ids: list[str]):
        self.rng = rng
        # Borrower: two independent drivers. Willingness moves contact and
        # promise behaviour; capacity moves how much actually arrives.
        self.willingness: dict[str, float] = {}
        self.capacity: dict[str, float] = {}
        # Agent skill per (loan_type, dpd_bucket), on the logit scale. sd=0.55
        # is a deliberate choice: large enough that a good agent in their best
        # segment is worth roughly +12 percentage points of conversion, small
        # enough that it never dominates borrower quality.
        self.agent_ability: dict[tuple[str, str, str], float] = {}
        for aid in agent_ids:
            for lt in LOAN_TYPE_WEIGHTS:
                for bucket in ("BUCKET_1", "BUCKET_2", "BUCKET_3", "NPA"):
                    self.agent_ability[(aid, lt, bucket)] = rng.gauss(0.0, 0.55)

    def propensity(self, customer_id: str) -> float:
        return 0.55 * self.willingness[customer_id] + 0.45 * self.capacity[customer_id]

    def to_dict(self) -> dict:
        return {
            "_README": (
                "SIMULATOR GROUND TRUTH. These are the hidden variables the "
                "synthetic world was generated from. They are NOT in the "
                "database and MUST NOT be used as model features — they exist "
                "only to check whether EB and the borrower model recover the "
                "relationships that were planted."
            ),
            "borrower_willingness": self.willingness,
            "borrower_capacity": self.capacity,
            "agent_segment_ability": {
                "|".join(k): round(v, 6) for k, v in self.agent_ability.items()
            },
        }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--borrowers", type=int, default=1400)
    ap.add_argument("--agents", type=int, default=6)
    ap.add_argument("--start", default="2025-09-01", help="first simulated day")
    ap.add_argument("--end", default="2026-08-31", help="last simulated day")
    ap.add_argument("--snapshot-every", type=int, default=21,
                    help="days between scoring runs")
    ap.add_argument("--snapshot-from", default=None,
                    help="first scoring date (default: start + 60d, so the "
                         "180-day behaviour window has something in it)")
    ap.add_argument("--snapshot-until", default=None,
                    help="last scoring date (default: end - 31d, so every "
                         "snapshot's 30-day outcome window is complete)")
    ap.add_argument("--db-name", default=DEFAULT_SYNTH_DB)
    ap.add_argument("--reset", action="store_true",
                    help="DROP and recreate the synthetic database first")
    ap.add_argument("--truth-out", default=None)
    args = ap.parse_args()

    url = synth_url(args.db_name)
    if args.reset:
        _reset_database(args.db_name)

    # Set BEFORE importing anything that builds an engine.
    os.environ["DATABASE_URL"] = url
    os.environ.setdefault("SECRET_KEY", "synthetic-generator")
    os.environ.setdefault("COMMAND_CENTRE_API_KEY", "synthetic-generator")

    from app.core.database import SessionLocal, engine
    from app.models.base import Base
    from app.models.agent import Agent, AgentSpecialization, AgentStatus, AgentTier
    from app.models.case import Case, CaseStatus
    from app.models.customer import Customer
    from app.models.loan import DPDBucket, Loan, LoanStatus, LoanType
    from app.models.payment import Payment, PaymentMode, PaymentStatus
    from app.models.ptp import PTP, PTPStatus
    from app.models.user import User, UserRole
    from app.models.visit import Visit, VisitOutcome
    from app.services.repayment_service import RepaymentService

    print(f"synthetic database : {url}")
    Base.metadata.create_all(engine)
    db = SessionLocal()

    rng = random.Random(args.seed)
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    snap_from = (date.fromisoformat(args.snapshot_from) if args.snapshot_from
                 else start + timedelta(days=60))
    snap_until = (date.fromisoformat(args.snapshot_until) if args.snapshot_until
                  else end - timedelta(days=31))

    def dt(d: date, hour: int = 11) -> datetime:
        return datetime.combine(d, time(hour, 0)).replace(tzinfo=timezone.utc)

    # ── Staff ────────────────────────────────────────────────────────────────
    mgr_user = User(id="synth-mgr", email="synthetic.manager@example.invalid",
                    phone="+910000000000", full_name="Synthetic Manager",
                    hashed_password="!", role=UserRole.AGENCY_MANAGER,
                    is_active=True, is_verified=True)
    db.add(mgr_user)

    agents: list[Agent] = []
    for i in range(args.agents):
        u = User(id=f"synth-user-{i}", email=f"synthetic.agent{i}@example.invalid",
                 phone=f"+9190000000{i:02d}", full_name=f"Synthetic Agent {i:02d}",
                 hashed_password="!", role=UserRole.FIELD_AGENT,
                 is_active=True, is_verified=True)
        db.add(u)
        a = Agent(
            id=f"synth-agent-{i}", user_id=u.id, employee_code=f"SYN{i:04d}",
            id_card_number=f"SYNID{i:05d}", agency_id="SYNTH",
            base_latitude=rng.uniform(*LAT_RANGE), base_longitude=rng.uniform(*LON_RANGE),
            territory=f"SYNTH-ZONE-{i % 5}", languages_spoken=["HINDI", "ENGLISH"],
            specialization=rng.choice(list(AgentSpecialization)),
            max_cases_per_day=rng.randint(10, 16), status=AgentStatus.ON_DUTY,
            tier=rng.choice(list(AgentTier)), manager_user_id=mgr_user.id,
        )
        db.add(a)
        agents.append(a)
    db.flush()

    truth = Truth(rng, [a.id for a in agents])

    # ── Borrowers and loans ──────────────────────────────────────────────────
    types = list(LOAN_TYPE_WEIGHTS)
    weights = [LOAN_TYPE_WEIGHTS[t] for t in types]
    loans: list[Loan] = []
    for i in range(args.borrowers):
        willing = rng.betavariate(2.0, 2.0)
        capacity = rng.betavariate(2.0, 2.0)
        cid = f"synth-cust-{i}"
        truth.willingness[cid] = willing
        truth.capacity[cid] = capacity

        # OBSERVABLES ARE NOISY VIEWS OF THE LATENTS. The 55-point error on
        # CIBIL is what stops the model reading capacity straight off the
        # bureau score; without it the problem is trivial.
        cibil = int(max(300, min(820, 330 + 400 * capacity + rng.gauss(0, 55))))
        lt = rng.choices(types, weights=weights, k=1)[0]
        lo, hi, tenure = EMI_BANDS[lt]
        emi = round(rng.uniform(lo, hi), -1)
        months_left = rng.randint(6, tenure)
        principal = emi * months_left
        # Arrears track willingness, again noisily.
        overdue_cycles = max(1, int(round(1 + rng.expovariate(1.0 / (1.4 + 2.2 * (1 - willing))))))
        overdue_cycles = min(overdue_cycles, 8)
        dpd0 = max(1, int(round(overdue_cycles * 30 - rng.uniform(0, 28))))

        cust = Customer(
            id=cid, customer_ref=f"SYNC{i:06d}", full_name=f"Synthetic Borrower {i:05d}",
            date_of_birth="1985-01-01", gender=rng.choice(["MALE", "FEMALE"]),
            pan_masked="XXXXX0000X", aadhaar_masked="XXXXXXXX0000",
            phone_primary=f"+9199{i:08d}",
            address_line1=f"{i} Synthetic Road", city="Delhi", state="Delhi",
            pincode=f"1{rng.randint(10000, 99999)}",
            latitude=rng.uniform(*LAT_RANGE), longitude=rng.uniform(*LON_RANGE),
            # risk_category/risk_score are deliberately NOT set. They belong to
            # the repayment scorer alone (tests/test_repayment_task.py enforces
            # this), and the column default is MEDIUM regardless — a synthetic
            # book that minted its own risk labels would be the fourth
            # independent scorer this codebase has had to delete.
            cibil_score=cibil,
            customer_segment=rng.choice(CUSTOMER_SEGMENTS),
            language_preference="HINDI",
            is_hostile=rng.random() < 0.03,
        )
        db.add(cust)

        loan = Loan(
            id=f"synth-loan-{i}", loan_account_number=f"SYNL{i:08d}", customer_id=cid,
            loan_type=LoanType(lt), bank_name="Synthetic Bank", branch_code="SYN01",
            sanctioned_amount=principal * 1.2, disbursed_amount=principal * 1.2,
            outstanding_principal=principal, total_outstanding=principal * 1.12,
            overdue_amount=emi * overdue_cycles, emi_amount=emi,
            disbursement_date=str(start - timedelta(days=rng.randint(400, 1500))),
            maturity_date=str(start + timedelta(days=30 * months_left)),
            dpd=dpd0, dpd_bucket=DPDBucket(dpd_to_bucket(dpd0)),
            status=LoanStatus.ACTIVE, interest_rate=round(rng.uniform(11.0, 22.0), 1),
            outstanding_interest=principal * 0.08, penal_charges=emi * 0.05 * overdue_cycles,
            last_payment_amount=emi if rng.random() < 0.6 else 0.0,
            last_payment_date=str(start - timedelta(days=rng.randint(20, 200))),
            npa_flag=dpd0 > 90,
        )
        db.add(loan)
        loans.append(loan)
    db.commit()
    print(f"created {len(agents)} agents, {len(loans)} borrowers/loans")

    # ── Mutable simulation state, held in Python not in the DB ───────────────
    loan_state = {
        ln.id: {
            "dpd": ln.dpd, "emi": float(ln.emi_amount),
            "overdue": float(ln.overdue_amount),
            "outstanding": float(ln.total_outstanding),
            "principal": float(ln.outstanding_principal),
            "penal": float(ln.penal_charges),
            "customer_id": ln.customer_id, "loan_type": ln.loan_type.value,
            "case": None,          # the open case, if any
            "next_visit": None,    # simulated date of the next visit
            "cycles": 0,           # how many collection cycles this loan has had
            "closed": False,
        } for ln in loans
    }
    loan_by_id = {ln.id: ln for ln in loans}
    case_by_id: dict[str, Case] = {}
    open_ptp: dict[str, PTP] = {}       # case_id -> active promise
    agent_load: Counter = Counter()

    snapshot_dates = []
    d = snap_from
    while d <= snap_until:
        snapshot_dates.append(d)
        d += timedelta(days=args.snapshot_every)
    snapshot_set = set(snapshot_dates)

    svc = RepaymentService(db)
    seq = {"case": 0, "visit": 0, "pay": 0, "ptp": 0}
    stats: Counter = Counter()
    changed_since_last_run: set[str] = set()
    total_snapshots = 0

    print(f"simulating {start} .. {end}  "
          f"({len(snapshot_dates)} scoring dates, every {args.snapshot_every}d)")

    # ── The clock ────────────────────────────────────────────────────────────
    day = start
    while day <= end:
        weekday = day.weekday() < 6          # agents do not work Sundays

        for lid, st in loan_state.items():
            if st["closed"]:
                continue
            ln = loan_by_id[lid]

            # 1. The loan ages. One more day past due, and a new instalment
            #    falls due every 30 days.
            st["dpd"] += 1
            if st["dpd"] % 30 == 0:
                st["overdue"] += st["emi"]
                st["penal"] += st["emi"] * 0.02

            # 2. A loan with no open case enters collections once it is far
            #    enough past due. The threshold is per-loan and random so cases
            #    do not all arrive on the same day.
            if st["case"] is None and st["dpd"] >= 25 and rng.random() < 0.06:
                seq["case"] += 1
                agent = min(agents, key=lambda a: agent_load[a.id] + rng.random())
                agent_load[agent.id] += 1
                case = Case(
                    id=f"synth-case-{seq['case']}",
                    case_number=f"SYNCASE{seq['case']:07d}",
                    customer_id=st["customer_id"], loan_id=lid,
                    agent_id=agent.id, status=CaseStatus.ASSIGNED,
                    target_amount=round(st["emi"], 2), collected_amount=0.0,
                    allocation_date=str(day), visit_count=0,
                )
                case.created_at = dt(day, 9)
                db.add(case)
                case_by_id[case.id] = case
                st["case"] = case.id
                st["cycles"] += 1
                st["next_visit"] = day + timedelta(days=rng.randint(1, 5))
                changed_since_last_run.add(lid)
                stats["cases_opened"] += 1

            # 3. Visits happen on the scheduled day.
            cid = st["case"]
            if not cid or st["next_visit"] is None or st["next_visit"] > day:
                continue
            if not weekday:
                # A visit scheduled on a Sunday SLIPS to Monday. It used to be
                # skipped by a `continue` that never rescheduled, which froze
                # the case permanently — and since every visit reschedules, one
                # seventh of cases died at each step and compounded. The book
                # ended up with 1,397 open cases sharing 25 visits a day, a 4.8%
                # positive rate, and 44 paid cases out of 1,825.
                st["next_visit"] = day + timedelta(days=1)
                continue
            case = case_by_id[cid]
            agent = next(a for a in agents if a.id == case.agent_id)
            bucket = dpd_to_bucket(st["dpd"])
            prop = truth.propensity(st["customer_id"])
            ability = truth.agent_ability.get((agent.id, st["loan_type"], bucket), 0.0)

            # ── THE HIDDEN GENERATING MODEL ──────────────────────────────────
            # Borrower propensity and agent ability both matter, DPD and arrears
            # push against them, and a kept promise is genuine evidence. The
            # per-event gaussian is what makes a willing borrower sometimes fail
            # and a difficult one sometimes pay. None of these coefficients is
            # visible to the model, which sees only the noisy observables.
            arrears_ratio = st["overdue"] / max(1.0, st["outstanding"])
            kept_before = stats.get(f"kept:{cid}", 0)
            z = (
                0.55
                + 2.30 * (prop - 0.5)
                + 1.00 * ability
                # Bounded at 1.6. An unbounded DPD term sends p_pay to zero on
                # every aged account, the book stops paying entirely, and the
                # label collapses to a single class — which is exactly what the
                # first run of this generator did.
                - 0.55 * min(1.6, st["dpd"] / 120.0)
                - 1.20 * min(1.0, arrears_ratio * 4.0)
                + 0.55 * min(2, kept_before)
                + rng.gauss(0.0, 0.85)
            )
            p_contact = logistic(0.9 + 1.6 * (truth.willingness[st["customer_id"]] - 0.5))
            met = rng.random() < p_contact
            p_pay = logistic(z) if met else 0.0

            seq["visit"] += 1
            paid_now = 0.0
            outcome = VisitOutcome.NOT_AVAILABLE
            promise = None

            if not met:
                outcome = rng.choices(
                    [VisitOutcome.NOT_AVAILABLE, VisitOutcome.ADDRESS_ISSUE,
                     VisitOutcome.REVISIT],
                    weights=[0.62, 0.20, 0.18], k=1)[0]
            elif rng.random() < p_pay:
                remaining = float(case.target_amount) - float(case.collected_amount)
                cap = truth.capacity[st["customer_id"]]
                # How much arrives is capacity-driven and separate from whether
                # anything arrives at all.
                #
                # BIMODAL, deliberately. A pure Beta share of the REMAINING
                # balance decays geometrically and never actually reaches zero:
                # at a typical 64% share it takes eight payments to clear a
                # cycle, while cases close after five to ten visits, so nothing
                # ever completed. The first run produced 1 PAID case in 1,304.
                # Real borrowers clear the last instalment in one go, and the
                # completions are what let a loan re-delinquent and give agents
                # repeated observations in the same segment.
                if rng.random() < 0.30 + 0.40 * cap:
                    paid_now = round(remaining, 2)
                else:
                    share = min(1.0, max(0.12, rng.betavariate(1.4 + 3.2 * cap, 1.7)))
                    paid_now = round(min(remaining, remaining * share), 2)
                if paid_now >= remaining - 1.0:
                    paid_now = round(remaining, 2)
                    outcome = VisitOutcome.PAID_FULL
                elif rng.random() < 0.35:
                    outcome = VisitOutcome.PART_PAID_PTP
                    promise = remaining - paid_now
                else:
                    outcome = VisitOutcome.PART_PAID
            else:
                roll = rng.random()
                if roll < 0.42:
                    outcome = VisitOutcome.PTP
                    promise = float(case.target_amount) - float(case.collected_amount)
                elif roll < 0.62:
                    outcome = VisitOutcome.RTP
                elif roll < 0.75:
                    outcome = VisitOutcome.DISPUTE
                else:
                    outcome = VisitOutcome.REVISIT

            cust_lat = rng.uniform(*LAT_RANGE)
            visit = Visit(
                id=f"synth-visit-{seq['visit']}", case_id=cid, agent_id=agent.id,
                check_in_latitude=cust_lat, check_in_longitude=rng.uniform(*LON_RANGE),
                check_in_time=dt(day, rng.randint(9, 17)),
                distance_from_customer_metres=rng.uniform(5, 120),
                geo_verified=True, customer_met=met, outcome=outcome,
                visit_number=(case.visit_count or 0) + 1,
            )
            db.add(visit)
            case.visit_count = (case.visit_count or 0) + 1
            stats[f"outcome:{outcome.value}"] += 1

            # A promise made today resolves later, on its own merits.
            if promise and promise > 0 and cid not in open_ptp:
                seq["ptp"] += 1
                ptp = PTP(
                    id=f"synth-ptp-{seq['ptp']}", case_id=cid, visit_id=visit.id,
                    agent_id=agent.id, committed_amount=round(promise, 2),
                    committed_date=day + timedelta(days=rng.randint(3, 14)),
                    status=PTPStatus.ACTIVE,
                )
                db.add(ptp)
                open_ptp[cid] = ptp
                if case.status == CaseStatus.ASSIGNED:
                    case.status = CaseStatus.PTP_SET
                stats["ptps_created"] += 1

            if paid_now > 0:
                seq["pay"] += 1
                db.add(Payment(
                    id=f"synth-pay-{seq['pay']}", case_id=cid, visit_id=visit.id,
                    agent_id=agent.id, amount=paid_now, mode=PaymentMode.CASH,
                    status=PaymentStatus.VERIFIED,
                    receipt_number=f"SYNRCP{seq['pay']:09d}",
                    payment_date=dt(day, 15), verified_at=dt(day, 18),
                ))
                case.collected_amount = round(float(case.collected_amount) + paid_now, 2)
                st["overdue"] = max(0.0, st["overdue"] - paid_now)
                st["outstanding"] = max(0.0, st["outstanding"] - paid_now)
                # Arrears paid down cure DPD proportionally. A borrower who
                # clears half an instalment is not as far behind as they were.
                st["dpd"] = max(0, st["dpd"] - int(30 * paid_now / max(1.0, st["emi"])))
                st["principal"] = max(0.0, st["principal"] - paid_now * 0.8)
                ln.last_payment_amount = paid_now
                ln.last_payment_date = str(day)
                changed_since_last_run.add(lid)
                stats["payments"] += 1

                # A promise the borrower kept.
                held = open_ptp.get(cid)
                if held is not None and held.committed_date >= day:
                    held.status = (PTPStatus.HONORED
                                   if paid_now >= float(held.committed_amount) - 1.0
                                   else PTPStatus.PARTIALLY_HONORED)
                    held.actual_paid_amount = paid_now
                    stats[f"kept:{cid}"] = stats.get(f"kept:{cid}", 0) + 1
                    open_ptp.pop(cid, None)

            # 4. Where the case goes next.
            if float(case.collected_amount) >= float(case.target_amount) - 0.01:
                case.status = CaseStatus.PAID
                # One instalment cleared: the loan steps back out of arrears and
                # will re-delinquent later, which is what gives agents repeated
                # observations in the same segment.
                st["dpd"] = max(0, st["dpd"] - 30)
                st["case"] = None
                st["next_visit"] = None
                if open_ptp.pop(cid, None) is not None:
                    pass
                changed_since_last_run.add(lid)
                stats["cases_paid"] += 1
            elif case.visit_count >= rng.randint(5, 10):
                # Worked and not recovered. Some are given up on, which is what
                # produces the censored class — a bank decision, not the
                # borrower's failure.
                if outcome == VisitOutcome.DISPUTE or rng.random() < 0.16:
                    case.status = (CaseStatus.ESCALATED if rng.random() < 0.55
                                   else CaseStatus.CLOSED)
                    st["case"] = None
                    st["next_visit"] = None
                    open_ptp.pop(cid, None)
                    changed_since_last_run.add(lid)
                    stats[f"closed:{case.status.value}"] += 1
                else:
                    if float(case.collected_amount) > 0:
                        case.status = CaseStatus.PARTIALLY_PAID
                    st["next_visit"] = day + timedelta(days=rng.randint(7, 15))
            else:
                if float(case.collected_amount) > 0:
                    case.status = CaseStatus.PARTIALLY_PAID
                elif case.status == CaseStatus.ASSIGNED:
                    case.status = CaseStatus.IN_PROGRESS
                st["next_visit"] = day + timedelta(days=rng.randint(5, 12))

        # 5. Promises that came and went.
        for cid, ptp in list(open_ptp.items()):
            if ptp.committed_date < day and ptp.status == PTPStatus.ACTIVE:
                ptp.status = PTPStatus.BROKEN
                open_ptp.pop(cid, None)
                stats["ptps_broken"] += 1

        # 6. Push the day's state onto the loan rows, THEN score. This ordering
        #    is the whole point of the file: when rescore reads loan.dpd it is
        #    reading today's DPD, because tomorrow has not been simulated.
        for lid, st in loan_state.items():
            ln = loan_by_id[lid]
            ln.dpd = st["dpd"]
            ln.dpd_bucket = DPDBucket(dpd_to_bucket(st["dpd"]))
            ln.overdue_amount = round(st["overdue"], 2)
            ln.total_outstanding = round(st["outstanding"], 2)
            ln.outstanding_principal = round(st["principal"], 2)
            ln.penal_charges = round(st["penal"], 2)
            ln.npa_flag = st["dpd"] > 90

        if day in snapshot_set:
            db.flush()
            # Score only loans actually in collections on this date. A loan with
            # no case cannot receive money — Payment.case_id is NOT NULL — so
            # labelling it NO_PAYMENT would record a borrower failure that the
            # ledger made impossible.
            active = [lid for lid, st in loan_state.items() if st["case"] is not None]
            if active:
                res = svc.rescore(
                    as_of=day, loan_ids=active, trigger="SEED",
                    changed_loan_ids=changed_since_last_run & set(active),
                )
                total_snapshots += res["snapshots_written"]
                print(f"   {day}  active={len(active):>5}  "
                      f"written={res['snapshots_written']:>5}  "
                      f"skipped={res['snapshots_skipped_no_change']:>5}  "
                      f"total={total_snapshots}")
            changed_since_last_run.clear()
            db.commit()
        elif day.day % 5 == 0:
            db.commit()

        day += timedelta(days=1)

    db.commit()

    truth_path = pathlib.Path(args.truth_out or
                              pathlib.Path(__file__).parent.parent / "data" /
                              "synthetic" / "ground_truth.json")
    truth_path.parent.mkdir(parents=True, exist_ok=True)
    truth_path.write_text(json.dumps(truth.to_dict(), indent=1), encoding="utf-8")

    print(f"\nsnapshots written      : {total_snapshots}")
    print(f"cases opened / paid    : {stats['cases_opened']} / {stats['cases_paid']}")
    print(f"payments / PTPs        : {stats['payments']} / {stats['ptps_created']}")
    print(f"escalated / closed     : {stats.get('closed:ESCALATED', 0)} / "
          f"{stats.get('closed:CLOSED', 0)}")
    print(f"visits                 : {seq['visit']}")
    print(f"simulator truth (NOT features) -> {truth_path}")
    db.close()


def _reset_database(db_name: str) -> None:
    """DROP and recreate, so the whole experiment is one flag to remove."""
    import psycopg2
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

    admin = _demo_url().rsplit("/", 1)[0] + "/postgres"
    dsn = admin.replace("postgresql+psycopg2://", "postgresql://")
    conn = psycopg2.connect(dsn)
    conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = %s AND pid <> pg_backend_pid()", (db_name,))
        cur.execute(f'DROP DATABASE IF EXISTS "{db_name}"')
        cur.execute(f'CREATE DATABASE "{db_name}"')
    conn.close()
    print(f"reset database: {db_name}")


if __name__ == "__main__":
    main()
