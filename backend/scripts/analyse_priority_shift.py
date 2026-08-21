"""
READ-ONLY. Shows how replacing the risk calculation with Scorecard v1 changes
Case.priority and nightly allocation. Writes nothing, to any database, ever.

Run this BEFORE the scorer is wired up. The whole point is that the
distribution shift is reviewed first, not discovered afterwards.

  python -m scripts.analyse_priority_shift                # auto: DB if reachable
  python -m scripts.analyse_priority_shift --source=simulate
  python -m scripts.analyse_priority_shift --source=db

`simulate` reproduces seed_data.py's own draws (same distributions, cited by
line) so the comparison is reproducible without a running database. `db` reads
the live rows. Neither opens a write transaction.

WHAT IS BEING COMPARED
----------------------
Only ONE input changes. The priority formula itself is left exactly as it is:

    priority_score = min(100, dpd/90*40 + outstanding_principal/500000*30
                              + risk_score * 0.3)          # unchanged
    priority       = _priority_from_score(priority_score)  # unchanged

OLD risk_score:  dpd/90*60 + (750-cibil)/750*40 + uniform(-8,8), floored at 30
                 ...against a `dpd_hint` belonging to NO loan of that customer
                 (seed_data.py:1327-1328)
NEW risk_score:  100 - scorecard likelihood, computed on the loan's OWN dpd
"""
from __future__ import annotations

import argparse
import os
import random
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Windows consoles default to cp1252 and the bars below are box-drawing
# characters. Without this the script dies mid-report on the first histogram.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.ml.repayment_scorecard import (  # noqa: E402
    SCORECARD_VERSION, TOTAL_WEIGHT, risk_category_for, score,
)
from app.models.loan import LoanType  # noqa: E402

PRIORITIES = ["CRITICAL", "HIGH", "MEDIUM", "LOW"]
CATEGORIES = ["CRITICAL", "HIGH", "MEDIUM", "LOW"]

# ── The seed's own constants, cited so the simulation can be checked ─────────
DPD_CHOICES = [35, 42, 50, 58, 65, 72, 80, 88, 95, 105, 120, 150, 180, 210, 270]
DPD_WEIGHTS = [10, 10, 8, 7, 9, 9, 8, 7, 6, 6, 5, 5, 4, 4, 2]     # :92-93
CUSTOMER_SEGMENTS = ["SALARIED", "SELF_EMPLOYED", "BUSINESS_OWNER",
                     "RETIRED", "HOMEMAKER"]                       # :89
LOAN_TYPES = list(LoanType)
N_LOANS = 500                                                       # :71
N_AGENTS = 18                                                       # :70
MAX_CASES_PER_DAY = 15                     # models/agent.py default


# ── The two priority functions, copied verbatim from the scripts ────────────
def _priority_from_score(s: float) -> str:
    """ingest_daily.py:110-117 / seed_data.py:465-469 — identical."""
    if s >= 85:
        return "CRITICAL"
    if s >= 60:
        return "HIGH"
    if s >= 35:
        return "MEDIUM"
    return "LOW"


def _priority_score(dpd: float, outstanding_principal: float, risk: float) -> float:
    """seed_data.py:1399 / ingest_daily.py:429 — UNCHANGED by this work."""
    return min(100.0, dpd / 90 * 40 + outstanding_principal / 500000 * 30 + risk * 0.3)


def _old_risk(dpd_hint: int, cibil: int, jitter: float) -> float:
    """seed_data.py:458-464. `dpd_hint` is deliberately NOT the loan's dpd —
    reproducing the decorrelation bug is the point of the comparison."""
    base = min(dpd_hint / 90 * 60 + (750 - cibil) / 750 * 40, 100)
    return round(min(100, max(30, base + jitter)), 1)


# ── Row assembly ────────────────────────────────────────────────────────────
def simulate(seed: int = 42) -> list[dict]:
    """Draw N_LOANS loans from seed_data.py's own distributions."""
    rng = random.Random(seed)
    rows = []
    for _ in range(N_LOANS):
        cibil = rng.randint(300, 680)                                  # :1326
        dpd_hint = rng.choices(DPD_CHOICES, DPD_WEIGHTS)[0]            # :1327
        dpd = rng.choices(DPD_CHOICES, DPD_WEIGHTS)[0]                 # :1375
        loan_type = rng.choice(LOAN_TYPES)                             # :1363
        sanctioned = round(rng.choice([
            rng.uniform(50000, 300000), rng.uniform(300000, 1500000),
            rng.uniform(1500000, 5000000)]), -3)                       # :1364-1368
        disbursed = sanctioned * rng.uniform(0.90, 1.00)
        rate = rng.uniform(10.0, 24.0)
        tenure = rng.choice([12, 24, 36, 48, 60, 84, 120])
        r = rate / 1200
        emi = round((disbursed * r * (1 + r) ** tenure) / ((1 + r) ** tenure - 1), 2)
        principal = round(disbursed * rng.uniform(0.35, 0.95), 2)      # :1379-1380
        overdue = round(emi * (dpd // 30 + 1) * rng.uniform(0.85, 1.15), 2)  # :1382

        legal = "NONE"
        if dpd >= 90:                                                  # :1387-1392
            legal = rng.choices(["NONE", "NOTICE_SENT", "SARFAESI", "SUIT_FILED"],
                                weights=[60, 25, 10, 5])[0]
        settle = "NONE"
        if dpd >= 120:                                                 # :1393-1397
            settle = rng.choices(["NONE", "OFFERED", "NEGOTIATING"],
                                 weights=[70, 20, 10])[0]

        # Behavioural history. The historical block conditions visit outcomes on
        # DPD (seed_data.py:517-549); PTP status does NOT condition on anything
        # (:1644-1650, weights=[30,35,25,10]) — reproduced faithfully, including
        # the fact that it carries no signal.
        met_prob = 0.60 if dpd <= 60 else 0.45 if dpd <= 90 else 0.30
        visits = rng.choices([0, 1, 2, 3, 4], weights=[18, 26, 26, 18, 12])[0]
        visits_met = sum(1 for _ in range(visits) if rng.random() < met_prob)
        ptps_resolved = rng.choices([0, 1, 2, 3], weights=[40, 30, 20, 10])[0]
        ptps_honored = sum(1 for _ in range(ptps_resolved) if rng.random() < 0.58)
        target = round(min(overdue * rng.uniform(0.18, 0.35), 50000.0), 2)   # :564-567
        paid = round(target * rng.uniform(0, 1), 2) if (
            visits_met and rng.random() < 0.45) else 0.0

        rows.append({
            "dpd": dpd, "dpd_hint": dpd_hint, "cibil_score": cibil,
            "jitter": rng.uniform(-8, 8),
            "loan_type": loan_type, "emi_amount": emi,
            "outstanding_principal": principal, "overdue_amount": overdue,
            "last_payment_amount": round(emi * rng.uniform(0.4, 1.0), 2),   # :1418
            "legal_status": legal, "settlement_status": settle,
            "is_hostile": rng.random() < 0.06, "fraud_flag": rng.random() < 0.02,
            "customer_segment": rng.choice(CUSTOMER_SEGMENTS),
            "visits": visits, "visits_met": visits_met,
            "ptps_resolved": ptps_resolved, "ptps_honored": ptps_honored,
            "adverse_visit_outcomes": max(0, visits - visits_met - 1),
            "case_target_amount": target, "amount_paid_in_window": paid,
        })
    return rows


def from_db() -> list[dict]:
    """Read live rows. SELECT only — no session.commit() anywhere below.

    Features come from RepaymentService.build_features, not from a re-derivation
    here, so this exercises the real code path including its point-in-time
    filters. A second implementation would be a second thing to drift.
    """
    from datetime import date as _date

    from sqlalchemy.orm import joinedload

    from app.core.database import SessionLocal
    from app.models.case import Case
    from app.models.loan import Loan
    from app.models.payment import Payment
    from app.models.ptp import PTP
    from app.models.visit import Visit
    from app.services.repayment_service import RepaymentService

    db = SessionLocal()
    try:
        svc = RepaymentService(db=None)      # pure helpers only; no writes
        as_of = _date.today()

        loans = db.query(Loan).options(joinedload(Loan.customer)).all()

        cases_by_loan: dict[str, list] = defaultdict(list)
        case_to_loan: dict[str, str] = {}
        for case in db.query(Case).all():
            if case.loan_id:
                cases_by_loan[case.loan_id].append(case)
                case_to_loan[case.id] = case.loan_id

        # Behavioural history, bulk-loaded once and bucketed by loan through
        # the case that owns it. Per-loan queries over 525 loans would be 2,100
        # round trips for a read-only report.
        def bucket(rows):
            out: dict[str, list] = defaultdict(list)
            for row in rows:
                loan_id = case_to_loan.get(row.case_id)
                if loan_id:
                    out[loan_id].append(row)
            return out

        visits_by_loan = bucket(db.query(Visit).all())
        ptps_by_loan = bucket(db.query(PTP).all())
        payments_by_loan = bucket(db.query(Payment).all())

        rows = []
        for ln in loans:
            features = svc.build_features(
                ln, ln.customer, as_of,
                cases=cases_by_loan.get(ln.id, ()),
                visits=visits_by_loan.get(ln.id, ()),
                ptps=ptps_by_loan.get(ln.id, ()),
                payments=payments_by_loan.get(ln.id, ()),
            )
            # The "before" side is the REAL column as it stands today, not a
            # re-derivation of the formula that produced it.
            features["_live_risk"] = ln.customer.risk_score if ln.customer else 50.0
            features["dpd_hint"] = ln.dpd
            features["jitter"] = 0.0
            rows.append(features)
        return rows
    finally:
        db.close()


# ── Reporting ───────────────────────────────────────────────────────────────
def _bar(n: int, total: int, width: int = 28) -> str:
    filled = 0 if not total else round(n / total * width)
    return "█" * filled + "·" * (width - filled)


def _dist(title: str, counts: Counter, order: list[str], total: int) -> None:
    print(f"\n  {title}")
    for key in order:
        n = counts.get(key, 0)
        print(f"    {key:<9} {n:>4}  {n / total * 100:>5.1f}%  {_bar(n, total)}")


def _crosstab(title: str, pairs: list[tuple[str, str]], order: list[str]) -> None:
    grid: dict[tuple[str, str], int] = Counter(pairs)
    print(f"\n  {title}")
    print("    old \\ new  " + "".join(f"{k:>10}" for k in order) + "      total")
    moved = 0
    for o in order:
        row = [grid.get((o, n), 0) for n in order]
        moved += sum(v for n, v in zip(order, row) if n != o)
        cells = "".join(
            (f"{v:>10}" if v else f"{'·':>10}") for n, v in zip(order, row)
        )
        print(f"    {o:<10}" + cells + f"{sum(row):>11}")
    print("    " + "-" * 62)
    print("    total     " + "".join(
        f"{sum(1 for _, n in pairs if n == k):>10}" for k in order))
    pct = moved / len(pairs) * 100 if pairs else 0
    print(f"\n    MOVED: {moved} of {len(pairs)} ({pct:.1f}%) change band")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", choices=["auto", "db", "simulate"], default="auto")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    source = args.source
    if source in ("auto", "db"):
        try:
            rows = from_db()
            source = "db"
        except Exception as exc:            # noqa: BLE001 — any failure falls back
            if args.source == "db":
                print(f"Could not read the database: {exc}")
                return 1
            print(f"  (database unreachable — {type(exc).__name__}; simulating)")
            rows, source = simulate(args.seed), "simulate"
    else:
        rows = simulate(args.seed)

    print("=" * 74)
    print("  PRIORITY / ALLOCATION IMPACT — Scorecard v1 vs the current formula")
    print("=" * 74)
    print(f"  source={source}   loans={len(rows)}   scorecard={SCORECARD_VERSION}")
    print("  READ-ONLY: this script opens no write transaction.")

    old_cats, fix_cats, new_cats = Counter(), Counter(), Counter()
    old_pris, fix_pris, new_pris = Counter(), Counter(), Counter()
    cat_pairs_bug: list[tuple[str, str]] = []      # old -> bug fixed only
    cat_pairs_card: list[tuple[str, str]] = []     # bug fixed -> scorecard
    pri_pairs: list[tuple[str, str]] = []
    coverage: list[float] = []
    old_risks: list[float] = []
    fix_risks: list[float] = []
    new_risks: list[float] = []
    ranked = []

    for row in rows:
        cibil = row["cibil_score"] or 650
        old_r = row.get("_live_risk")
        if old_r is None:
            old_r = _old_risk(row["dpd_hint"], cibil, row["jitter"])
        # The SAME old formula, but fed the loan's real dpd and no jitter. This
        # isolates the decorrelation bug from the scorecard: everything between
        # `old` and `fixed` is the bug, everything between `fixed` and `new` is
        # the new rules. Conflating the two would overstate the scorecard's churn.
        fix_r = _old_risk(row["dpd"], cibil, 0.0)
        result = score(row)
        new_r = result["risk_score"]

        pri = {}
        for key, risk in (("old", old_r), ("fix", fix_r), ("new", new_r)):
            pri[key] = _priority_from_score(
                _priority_score(row["dpd"], row["outstanding_principal"], risk))

        old_cats[risk_category_for(old_r)] += 1
        fix_cats[risk_category_for(fix_r)] += 1
        new_cats[result["risk_category"]] += 1
        old_pris[pri["old"]] += 1
        fix_pris[pri["fix"]] += 1
        new_pris[pri["new"]] += 1
        cat_pairs_bug.append((risk_category_for(old_r), risk_category_for(fix_r)))
        cat_pairs_card.append((risk_category_for(fix_r), result["risk_category"]))
        pri_pairs.append((pri["old"], pri["new"]))
        coverage.append(result["evidence_coverage"])
        old_risks.append(old_r)
        fix_risks.append(fix_r)
        new_risks.append(new_r)
        ranked.append((pri["old"], pri["new"], row["case_target_amount"] or 0.0))

    n = len(rows)
    print("\n" + "-" * 74)
    print("  1 · Customer.risk_score itself")
    print("-" * 74)
    for label, vals in (("OLD  ", old_risks), ("FIXED", fix_risks),
                        ("NEW  ", new_risks)):
        vals_sorted = sorted(vals)
        print(f"    {label}  min={vals_sorted[0]:>5.1f}  "
              f"p25={vals_sorted[n // 4]:>5.1f}  median={vals_sorted[n // 2]:>5.1f}  "
              f"p75={vals_sorted[3 * n // 4]:>5.1f}  max={vals_sorted[-1]:>5.1f}  "
              f"mean={sum(vals) / n:>5.1f}")

    print("\n" + "-" * 74)
    print("  2 · RiskCategory  (drives RiskBadge, and manager filters)")
    print("-" * 74)
    _dist("BEFORE (today)", old_cats, CATEGORIES, n)
    _dist("AFTER  (Scorecard v1)", new_cats, CATEGORIES, n)
    _crosstab("PART A — churn caused by FIXING THE dpd_hint BUG alone "
              "(old formula, real dpd)", cat_pairs_bug, CATEGORIES)
    _crosstab("PART B — churn caused by THE SCORECARD itself "
              "(bug already fixed)", cat_pairs_card, CATEGORIES)

    print("\n" + "-" * 74)
    print("  3 · Case.priority  (drives allocation order — formula UNCHANGED)")
    print("-" * 74)
    _dist("BEFORE", old_pris, PRIORITIES, n)
    _dist("AFTER", new_pris, PRIORITIES, n)
    _crosstab("MIGRATION", pri_pairs, PRIORITIES)

    print("\n" + "-" * 74)
    print("  4 · Allocation impact")
    print("-" * 74)
    capacity = N_AGENTS * MAX_CASES_PER_DAY
    print(f"    Nightly capacity: {N_AGENTS} agents x {MAX_CASES_PER_DAY} = "
          f"{capacity} cases/day")
    order = {p: i for i, p in enumerate(PRIORITIES)}
    old_rank = sorted(range(n), key=lambda i: (order[ranked[i][0]], -ranked[i][2]))
    new_rank = sorted(range(n), key=lambda i: (order[ranked[i][1]], -ranked[i][2]))
    pos_old = {idx: r for r, idx in enumerate(old_rank)}
    pos_new = {idx: r for r, idx in enumerate(new_rank)}
    moves = [abs(pos_old[i] - pos_new[i]) for i in range(n)]
    print(f"    Allocator sorts by (priority, -target_amount)  [allocator.py:155]")
    print(f"    Mean |rank change|: {sum(moves) / n:.1f} places of {n}")
    print(f"    Cases moving >50 places: {sum(1 for m in moves if m > 50)}")
    if n <= capacity:
        print(f"    Cases ({n}) <= capacity ({capacity}): every case is still")
        print(f"    allocated. Order changes WHICH agent, never WHETHER.")
    else:
        cut_old = {old_rank[i] for i in range(capacity)}
        cut_new = {new_rank[i] for i in range(capacity)}
        print(f"    Cases ({n}) > capacity ({capacity}) — order decides who waits.")
        print(f"    Cases crossing the cutoff: {len(cut_old ^ cut_new)}")

    print("\n" + "-" * 74)
    print("  5 · Evidence coverage  (how much of the 124 pts had any evidence)")
    print("-" * 74)
    cov_sorted = sorted(coverage)
    thin = sum(1 for c in coverage if c < 0.5)
    print(f"    min={cov_sorted[0]:.2f}  median={cov_sorted[n // 2]:.2f}  "
          f"max={cov_sorted[-1]:.2f}   (denominator = {TOTAL_WEIGHT:.0f} pts)")
    print(f"    Below 0.5 — UI shows a band, not a number: {thin} ({thin / n * 100:.1f}%)")

    print("\n" + "=" * 74)
    print("  Nothing was written. Re-run with --source=db once Docker is up to")
    print("  compare against the live rows instead of the simulation.")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
