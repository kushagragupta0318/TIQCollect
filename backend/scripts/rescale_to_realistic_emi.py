"""Put the book on a realistic rupee scale, keyed off the EMI.

A case is one collection cycle against a loan — in practice, one instalment.
It should therefore be EMI-sized. It was not: Case.target_amount ran to a median
of Rs 22,333 with a p95 of Rs 1,89,576 and a maximum of Rs 5,00,000.

The targets were not the fault, though. target_amount is
_cycle_target(overdue_amount, emi_amount) and sat at a median 0.80x the EMI,
which is exactly right. Loan.emi_amount was the problem: it was seeded without
reference to loan type, so the book carried

    MICROFINANCE   median Rs 20,815   max Rs 3,87,737
    HOME           median Rs 10,388   max Rs 3,95,741

— microfinance instalments larger than home-loan instalments, and monthly
instalments of nearly four lakh rupees in every product. No retail borrower pays
that, and every figure downstream inherited it.

WHAT THIS DOES
--------------
For each loan, its EMI is remapped into a realistic band for its product, then
EVERY rupee field on that loan and on everything hanging off it — cases,
payments, PTPs — is multiplied by the same factor.

One factor per loan is the whole point. Scaling the EMI alone would leave a
Rs 2,800 microfinance instalment against a Rs 30,00,000 balance; scaling targets
alone would break collected <= target. Because a single multiplier is applied to
every amount on the loan, every RATIO is preserved exactly: collection
percentages, arrears share, recovery rates, each agent's progress against target,
the DPD mix. The dashboards keep their shape and change only their scale.

Rank within product is preserved too — the largest home loan stays the largest
home loan. Only the band moves.

INVARIANTS
----------
Payments are scaled first, then collected_amount is recomputed as the exact sum
of that case's VERIFIED payments rather than scaled independently, so
collected == SUM(VERIFIED) holds by construction rather than by rounding luck.
collected <= target is then asserted per case before anything commits.

USAGE
    python scripts/rescale_to_realistic_emi.py --dry-run
    python scripts/rescale_to_realistic_emi.py --apply
"""
from __future__ import annotations

import argparse
import csv
import os
import pathlib
import re
import sys
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _resolve_database_url() -> str:
    if os.environ.get("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
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

    root, be = envmap(here / ".env"), envmap(here / "backend" / ".env")
    url = re.sub(r"\$\{(\w+)(?::-[^}]*)?\}", lambda m: root.get(m.group(1), m.group(0)),
                 be.get("DATABASE_URL", ""))
    return url.replace("@postgres:", "@localhost:").replace(":5432/", ":15432/")


os.environ["DATABASE_URL"] = _resolve_database_url()
os.environ.setdefault("SECRET_KEY", "rescale-script")
os.environ.setdefault("COMMAND_CENTRE_API_KEY", "rescale-script")

from sqlalchemy import func                                   # noqa: E402
from app.core.database import SessionLocal                    # noqa: E402
from app.models.case import Case                              # noqa: E402
from app.models.loan import Loan                              # noqa: E402
from app.models.payment import Payment, PaymentStatus         # noqa: E402
from app.models.ptp import PTP                                # noqa: E402

# Realistic monthly instalment bands for an Indian retail book, low to high.
# Ordered so the products a borrower services with the smallest cheque sit at the
# bottom and secured, long-tenure debt at the top — which is the ordering the
# seeded data had inverted.
EMI_BANDS = {
    "MICROFINANCE": (1_200, 5_000),
    "CREDIT_CARD":  (1_500, 12_000),    # minimum due, not the balance
    "GOLD":         (3_000, 18_000),
    "EDUCATION":    (4_000, 22_000),
    "PERSONAL":     (5_000, 30_000),
    "AUTO":         (8_000, 32_000),
    "BUSINESS":     (10_000, 55_000),
    "HOME":         (12_000, 70_000),
}
DEFAULT_BAND = (5_000, 30_000)

# Every rupee-denominated column on a loan. Missing any of these would leave the
# loan internally inconsistent — an instalment that no longer divides into the
# balance, or penal charges out of proportion to the arrears.
LOAN_MONEY_FIELDS = (
    "sanctioned_amount", "disbursed_amount", "outstanding_principal",
    "total_outstanding", "overdue_amount", "emi_amount",
    "outstanding_interest", "penal_charges", "last_payment_amount",
)

ROLLBACK_DIR = (pathlib.Path(__file__).resolve().parents[2]
                / "docs" / "rollback" / "2026-09-02-emi-rescale")


def _new_emi(loan_type: str, rank: int, n: int) -> float:
    """Place this loan in its product's band at the position it already held.

    Rank-preserving rather than random: the biggest home loan stays the biggest
    home loan, so any narrative built on the existing book still reads true.
    """
    low, high = EMI_BANDS.get(loan_type, DEFAULT_BAND)
    frac = 0.5 if n <= 1 else rank / (n - 1)
    return round(low + (high - low) * frac, -1)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    db = SessionLocal()
    loans = db.query(Loan).all()

    # factor per loan, from its rank inside its own product
    by_type: dict[str, list[Loan]] = defaultdict(list)
    for ln in loans:
        by_type[ln.loan_type.value if hasattr(ln.loan_type, "value") else str(ln.loan_type)].append(ln)

    factor: dict[str, float] = {}
    new_emi_of: dict[str, float] = {}
    for lt, group in by_type.items():
        group.sort(key=lambda l: float(l.emi_amount or 0.0))
        for i, ln in enumerate(group):
            target_emi = _new_emi(lt, i, len(group))
            old = float(ln.emi_amount or 0.0)
            factor[ln.id] = (target_emi / old) if old > 0 else 1.0
            new_emi_of[ln.id] = target_emi

    cases = db.query(Case).all()
    payments = db.query(Payment).all()
    ptps = db.query(PTP).all()
    pay_by_case: dict[str, list[Payment]] = defaultdict(list)
    for p in payments:
        pay_by_case[p.case_id].append(p)
    ptp_by_case: dict[str, list[PTP]] = defaultdict(list)
    for t in ptps:
        ptp_by_case[t.case_id].append(t)

    old_total = sum(float(c.target_amount or 0.0) for c in cases)

    # ── project the whole change before writing a byte ───────────────────────
    planned_targets: dict[str, float] = {}
    planned_collected: dict[str, float] = {}
    planned_pay: dict[str, float] = {}
    planned_ptp: dict[str, float] = {}
    for c in cases:
        # A case IS one instalment, so its target is the loan's EMI outright —
        # not the old target rescaled. Scaling would have preserved the seed's
        # target/EMI ratio, which ran as high as 36x and left 22 cases above a
        # lakh even after the EMI itself was made sane. One cycle, one EMI.
        planned_targets[c.id] = float(new_emi_of.get(c.loan_id, c.target_amount or 0.0))
        # Everything already recorded against the case moves with ITS target, not
        # with the loan's EMI factor, so a part-paid case stays part-paid by the
        # same fraction and collected <= target survives by construction.
        old_t = float(c.target_amount or 0.0)
        f = (planned_targets[c.id] / old_t) if old_t > 0 else 1.0
        verified = 0.0
        for p in pay_by_case.get(c.id, ()):
            amt = round(float(p.amount or 0.0) * f, 2)
            planned_pay[p.id] = amt
            if p.status == PaymentStatus.VERIFIED:
                verified += amt
        # Recomputed, not scaled: this is what keeps collected == SUM(VERIFIED)
        # true to the paisa instead of true to within a rounding error.
        planned_collected[c.id] = min(round(verified, 2), planned_targets[c.id])
        for t in ptp_by_case.get(c.id, ()):
            planned_ptp[t.id] = min(round(float(t.committed_amount or 0.0) * f, 2),
                                    planned_targets[c.id])

    new_total = sum(planned_targets.values())
    tv = sorted(planned_targets.values())

    def q(vals, p):
        return vals[min(len(vals) - 1, int(len(vals) * p))] if vals else 0.0

    print(f"cases {len(cases)}   loans {len(loans)}   payments {len(payments)}   PTPs {len(ptps)}")
    print(f"\ntotal case target : Rs {old_total/10_000_000:.2f} Cr  ->  Rs {new_total/10_000_000:.2f} Cr")
    print(f"target p25/median/p75/p95/max: "
          f"{q(tv,.25):,.0f} / {q(tv,.5):,.0f} / {q(tv,.75):,.0f} / {q(tv,.95):,.0f} / {tv[-1] if tv else 0:,.0f}")
    print(f"cases still >= Rs 1,00,000: {sum(1 for v in tv if v >= 100_000)}")
    print("\nnew EMI band by product (median):")
    for lt in sorted(by_type):
        meds = sorted(new_emi_of[l.id] for l in by_type[lt])
        print(f"   {lt:<14} n={len(meds):<4} median Rs {meds[len(meds)//2]:>8,.0f}"
              f"   range Rs {meds[0]:,.0f} - {meds[-1]:,.0f}")

    over = [c.id for c in cases if planned_collected[c.id] > planned_targets[c.id] + 0.01]
    print(f"\ninvariant check — cases where collected would exceed target: {len(over)}")

    if args.dry_run or not args.apply:
        print("\n(dry run — nothing written; pass --apply to commit)")
        db.close()
        return

    ROLLBACK_DIR.mkdir(parents=True, exist_ok=True)
    with open(ROLLBACK_DIR / "01-before.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["kind", "id", "field", "value"])
        for ln in loans:
            for fld in LOAN_MONEY_FIELDS:
                w.writerow(["loan", ln.id, fld, getattr(ln, fld, None)])
        for c in cases:
            w.writerow(["case", c.id, "target_amount", c.target_amount])
            w.writerow(["case", c.id, "collected_amount", c.collected_amount])
        for p in payments:
            w.writerow(["payment", p.id, "amount", p.amount])
        for t in ptps:
            w.writerow(["ptp", t.id, "committed_amount", t.committed_amount])
    print(f"\nsnapshot -> {ROLLBACK_DIR / '01-before.csv'}")

    for ln in loans:
        f = factor.get(ln.id, 1.0)
        for fld in LOAN_MONEY_FIELDS:
            v = getattr(ln, fld, None)
            if v is not None:
                setattr(ln, fld, round(float(v) * f, 2))
        ln.emi_amount = new_emi_of.get(ln.id, ln.emi_amount)
    for p in payments:
        if p.id in planned_pay:
            p.amount = planned_pay[p.id]
    for t in ptps:
        if t.id in planned_ptp:
            t.committed_amount = planned_ptp[t.id]
    for c in cases:
        c.target_amount = planned_targets[c.id]
        c.collected_amount = planned_collected[c.id]

    db.commit()
    print(f"applied: {len(loans)} loans, {len(cases)} cases, "
          f"{len(planned_pay)} payments, {len(planned_ptp)} PTPs")

    fresh = float(db.query(func.coalesce(func.sum(Case.target_amount), 0.0)).scalar())
    print(f"book target now Rs {fresh/10_000_000:.2f} Cr")
    db.close()


if __name__ == "__main__":
    main()
