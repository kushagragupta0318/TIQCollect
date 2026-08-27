"""
READ-ONLY. Measures whether the demo book behaves like a real collections book.

  python -m scripts.measure_demo_realism
  python -m scripts.measure_demo_realism --months 6
  python -m scripts.measure_demo_realism --label "baseline"

Writes nothing, to any database, ever — the connection is opened with
default_transaction_read_only=on, so Postgres itself refuses.

WHY THIS EXISTS. Calibrating seeded field activity takes many passes, and each
pass has to be judged against the SAME definitions the product uses. Eyeballing
ad-hoc SQL between runs is how a number gets believed because it was convenient.
Every figure below is computed the way the manager endpoints compute it —
notably the collection rate, which is a RUPEE-WEIGHTED ratio over DISTINCT cases
visited, not an average of per-case rates.

THE ONE THAT IS EASY TO GET WRONG. The DPD section reports case counts AND the
rupee weight, because target_amount scales with DPD through
`overdue = emi * (dpd // 30 + 1) * U(0.85, 1.15)` — that multiplier averages
around 5.7 in the 90+ band against 2 in the 30-60 band. So the money sits in the
deepest bucket even when the case COUNT looks evenly spread, and any attempt to
reason about the collection rate from case counts is wrong before it starts.

IT DOES NOT JUDGE. It prints what is there next to what a real book looks like,
and leaves the conclusion to the reader. No metric here is a target to be hit.
"""
from __future__ import annotations

import argparse

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

RULE = "─" * 78

# Believable ranges for Indian field collections, for context in the margin.
# These are REFERENCE POINTS, not thresholds — nothing here passes or fails.
REFERENCE = {
    "visits_per_day": "8-12 per agent per working day",
    "contact_rate": "~45% of visits meet the borrower",
    "ptp_capture": "25-35% of visits that needed a promise secured one",
    "ptp_kept": "50-70% of matured promises honoured",
    "collection_rate": "60-70% of the monthly target collected",
    "payment_vs_target": "a paying case settles most of its cycle target",
}


def _session(url: str | None):
    from app.core.config import settings
    engine = create_engine(
        url or settings.DATABASE_URL,
        connect_args={"options": "-c default_transaction_read_only=on"},
    )
    return sessionmaker(bind=engine)()


def _q(db, sql: str, **params):
    return db.execute(text(sql), params).mappings().all()


def _pct(v, nd=1):
    return "—" if v is None else f"{float(v):.{nd}f}%"


def _num(v, nd=1):
    return "—" if v is None else f"{float(v):.{nd}f}"


# ── Sections ─────────────────────────────────────────────────────────────────
def book_shape(db, since: str) -> None:
    print(RULE)
    print("BOOK SHAPE")
    print(RULE)
    rows = _q(db, """
        SELECT 'customers' AS t, count(*) AS n FROM customers
        UNION ALL SELECT 'loans',    count(*) FROM loans
        UNION ALL SELECT 'cases',    count(*) FROM cases
        UNION ALL SELECT 'visits',   count(*) FROM visits WHERE check_in_time >= :s
        UNION ALL SELECT 'payments (VERIFIED)', count(*) FROM payments
                   WHERE status = 'VERIFIED' AND payment_date >= :s
        UNION ALL SELECT 'ptps',     count(*) FROM ptps
        UNION ALL SELECT 'agents',   count(*) FROM agents
    """, s=since)
    for r in rows:
        print(f"  {r['t']:22s}{r['n']:>8d}")

    print("\n  Per manager (scoping must stay separate):")
    for r in _q(db, """
        SELECT u.full_name AS manager, count(*) AS agents
        FROM agents a JOIN users u ON u.id = a.manager_user_id
        GROUP BY 1 ORDER BY 2 DESC
    """):
        print(f"    {r['manager']:28s}{r['agents']:>4d} agents")

    print("\n  Cases per loan (a monthly collection cycle would make this ~1 per month):")
    r = _q(db, """
        SELECT round(avg(n)::numeric, 2) AS avg_cases, max(n) AS max_cases
        FROM (SELECT loan_id, count(*) n FROM cases GROUP BY 1) x
    """)[0]
    print(f"    avg {r['avg_cases']} cases/loan, max {r['max_cases']}")


def visit_volume(db, since: str) -> None:
    print()
    print(RULE)
    print(f"VISIT VOLUME   (reference: {REFERENCE['visits_per_day']})")
    print(RULE)
    print(f"  {'month':9s}{'agents':>8s}{'visits':>9s}{'per agent':>11s}"
          f"{'per day*':>10s}{'min':>6s}{'max':>6s}")
    for r in _q(db, """
        WITH v AS (
          SELECT agent_id, to_char(check_in_time,'YYYY-MM') m, count(*) n
          FROM visits WHERE check_in_time >= :s GROUP BY 1,2)
        SELECT m, count(*) agents, sum(n) total,
               round(avg(n)::numeric,1) per_agent, min(n) lo, max(n) hi
        FROM v GROUP BY m ORDER BY m
    """, s=since):
        # *24.7 = Mon-Sat working days less the ~5% seeded leave rate.
        # sum() comes back as Decimal from Postgres and ':d' rejects it.
        print(f"  {r['m']:9s}{r['agents']:>8d}{int(r['total']):>9d}"
              f"{_num(r['per_agent']):>11s}{_num(float(r['per_agent'])/24.7,1):>10s}"
              f"{r['lo']:>6d}{r['hi']:>6d}")
    print("  * per agent per working day, assuming 24.7 worked days/month")


def contact_and_outcomes(db, since: str) -> None:
    print()
    print(RULE)
    print(f"CONTACT RATE   (reference: {REFERENCE['contact_rate']})")
    print(RULE)
    print("  Contact is the ceiling on money: a visit that meets nobody cannot collect.")
    print(f"\n  {'month':9s}{'visits':>8s}{'met':>8s}{'contact':>10s}")
    for r in _q(db, """
        SELECT to_char(check_in_time,'YYYY-MM') m, count(*) n,
               count(*) FILTER (WHERE customer_met) met
        FROM visits WHERE check_in_time >= :s GROUP BY 1 ORDER BY 1
    """, s=since):
        share = 100.0 * r["met"] / r["n"] if r["n"] else None
        print(f"  {r['m']:9s}{r['n']:>8d}{r['met']:>8d}{_pct(share):>10s}")

    print("\n  Visit outcome mix (all months pooled):")
    rows = _q(db, """
        SELECT outcome::text o, count(*) n FROM visits
        WHERE check_in_time >= :s GROUP BY 1 ORDER BY 2 DESC
    """, s=since)
    total = sum(r["n"] for r in rows) or 1
    for r in rows:
        bar = "█" * max(1, round(40 * r["n"] / total))
        print(f"    {r['o']:16s}{r['n']:>7d}{_pct(100.0*r['n']/total):>8s}  {bar}")


def ptp_metrics(db, since: str) -> None:
    print()
    print(RULE)
    print("PTP BEHAVIOUR")
    print(RULE)
    print("  CAPTURE and KEPT are different questions and fail independently.")
    print(f"    capture — {REFERENCE['ptp_capture']}")
    print(f"    kept    — {REFERENCE['ptp_kept']}")
    print("\n  Capture is per VISIT, excluding PAID_FULL (nothing left to promise),")
    print("  exactly as manager.py computes it.")
    print(f"\n  {'month':9s}{'eligible':>10s}{'captured':>10s}{'capture':>10s}")
    for r in _q(db, """
        SELECT to_char(check_in_time,'YYYY-MM') m,
               count(*) FILTER (WHERE outcome <> 'PAID_FULL') elig,
               count(*) FILTER (WHERE outcome IN ('PTP','PART_PAID_PTP')) cap
        FROM visits WHERE check_in_time >= :s GROUP BY 1 ORDER BY 1
    """, s=since):
        share = 100.0 * r["cap"] / r["elig"] if r["elig"] else None
        print(f"  {r['m']:9s}{r['elig']:>10d}{r['cap']:>10d}{_pct(share):>10s}")

    print("\n  Kept — of promises whose committed_date has passed:")
    print(f"  {'month':9s}{'due':>8s}{'kept':>8s}{'kept %':>9s}{'still ACTIVE':>14s}")
    for r in _q(db, """
        SELECT to_char(committed_date,'YYYY-MM') m,
               count(*) due,
               count(*) FILTER (WHERE status IN ('HONORED','PARTIALLY_HONORED')) kept,
               count(*) FILTER (WHERE status = 'ACTIVE') still_active
        FROM ptps WHERE committed_date <= CURRENT_DATE AND committed_date >= :s
        GROUP BY 1 ORDER BY 1
    """, s=since):
        share = 100.0 * r["kept"] / r["due"] if r["due"] else None
        print(f"  {r['m']:9s}{r['due']:>8d}{r['kept']:>8d}{_pct(share):>9s}"
              f"{r['still_active']:>14d}")

    # The channel that currently contributes nothing.
    r = _q(db, """
        SELECT count(*) n,
               count(*) FILTER (WHERE actual_paid_amount > 0) claims_paid,
               count(*) FILTER (WHERE status IN ('HONORED','PARTIALLY_HONORED')) kept
        FROM ptps
    """)[0]
    linked = _q(db, """
        SELECT count(DISTINCT p.id) n FROM payments p
        WHERE p.visit_id IS NULL AND p.status = 'VERIFIED'
    """)[0]["n"]
    print(f"\n  PTPs claiming actual_paid_amount > 0 : {r['claims_paid']} of {r['n']}")
    print(f"  Payments NOT tied to a visit          : {linked}")
    print("  If the first number is large and the second is ~0, honoured promises are")
    print("  asserting money that no Payment row records — invisible to every rate below.")

    print("\n  Payment channel split (doorstep vs remote-against-a-promise):")
    print(f"    {'channel':22s}{'count':>8s}{'value Rs L':>12s}{'avg Rs':>10s}{'share':>8s}")
    rows = _q(db, """
        SELECT CASE WHEN visit_id IS NULL THEN 'visit-less (remote)'
                    ELSE 'visit-linked (door)' END channel,
               count(*) n, sum(amount)::numeric val, round(avg(amount)::numeric) avg_amt
        FROM payments WHERE status='VERIFIED' AND payment_date >= :s
        GROUP BY 1 ORDER BY 2 DESC
    """, s=since)
    tot = sum(float(x["val"] or 0) for x in rows) or 1.0
    for x in rows:
        print(f"    {x['channel']:22s}{int(x['n']):>8d}"
              f"{_num(float(x['val'] or 0) / 1e5, 1):>12s}{int(x['avg_amt']):>10d}"
              f"{_pct(100.0 * float(x['val'] or 0) / tot):>8s}")


def collection_rate(db, since: str) -> None:
    print()
    print(RULE)
    print(f"COLLECTION RATE   (reference: {REFERENCE['collection_rate']})")
    print(RULE)
    print("  Computed exactly as _live_monthly_metrics does: VERIFIED payments in the")
    print("  month, over the summed target of the DISTINCT cases visited that month.")
    print("  Rupee-weighted, so a big-target case dominates a small one.")
    print(f"\n  {'month':9s}{'cases':>8s}{'target ₹L':>11s}{'pays':>7s}"
          f"{'collected ₹L':>14s}{'rate':>9s}")
    for r in _q(db, """
        WITH vis AS (
          SELECT DISTINCT to_char(v.check_in_time,'YYYY-MM') m, v.case_id,
                 c.target_amount
          FROM visits v JOIN cases c ON c.id = v.case_id
          WHERE v.check_in_time >= :s),
        tgt AS (SELECT m, count(*) cases, sum(target_amount)::numeric target
                FROM vis GROUP BY 1),
        col AS (SELECT to_char(payment_date,'YYYY-MM') m, count(*) pays,
                       sum(amount)::numeric collected
                FROM payments WHERE status='VERIFIED' AND payment_date >= :s
                GROUP BY 1)
        SELECT t.m, t.cases, t.target, coalesce(c.pays,0) pays,
               coalesce(c.collected,0) collected
        FROM tgt t LEFT JOIN col c ON c.m = t.m ORDER BY t.m
    """, s=since):
        rate = 100.0 * float(r["collected"]) / float(r["target"]) if r["target"] else None
        print(f"  {r['m']:9s}{int(r['cases']):>8d}{_num(float(r['target'])/1e5,1):>11s}"
              f"{int(r['pays']):>7d}{_num(float(r['collected'])/1e5,1):>14s}{_pct(rate):>9s}")


def payment_vs_target(db, since: str) -> None:
    print()
    print(RULE)
    print(f"PAYMENT SIZE vs TARGET   (reference: {REFERENCE['payment_vs_target']})")
    print(RULE)
    print("  A case can never over-collect (payment is drawn from target − collected),")
    print("  so this shows how completely a paying case is settled.")
    r = _q(db, """
        SELECT round(avg(amount)::numeric) avg_pay,
               round(percentile_cont(0.5) WITHIN GROUP (ORDER BY amount)::numeric) med_pay
        FROM payments WHERE status='VERIFIED' AND payment_date >= :s
    """, s=since)[0]
    t = _q(db, "SELECT round(avg(target_amount)::numeric) avg_target FROM cases")[0]
    print(f"  avg payment ₹{r['avg_pay']}   median ₹{r['med_pay']}   "
          f"avg case target ₹{t['avg_target']}")

    print("\n  Settlement completeness, cases with any money:")
    for r in _q(db, """
        SELECT CASE
                 WHEN collected_amount >= target_amount - 1 THEN 'settled in full'
                 WHEN collected_amount >= target_amount*0.5 THEN 'half or more'
                 WHEN collected_amount > 0                  THEN 'part paid'
               END band, count(*) n
        FROM cases WHERE collected_amount > 0 GROUP BY 1 ORDER BY 2 DESC
    """):
        print(f"    {r['band']:18s}{r['n']:>7d}")
    r = _q(db, "SELECT count(*) n FROM cases WHERE coalesce(collected_amount,0) = 0")[0]
    print(f"    {'no money at all':18s}{r['n']:>7d}")


def dpd_distribution(db) -> None:
    print()
    print(RULE)
    print("DPD DISTRIBUTION — case count AND rupee weight")
    print(RULE)
    print("  The rupee column is the one that matters. target_amount scales with DPD,")
    print("  so the money concentrates in the deepest bucket even when the case count")
    print("  looks even — and the collection rate is rupee-weighted.")
    print(f"\n  {'bucket':12s}{'cases':>8s}{'case %':>9s}{'target ₹Cr':>13s}{'rupee %':>10s}")
    rows = _q(db, """
        SELECT l.dpd_bucket::text bucket, count(*) cases,
               sum(c.target_amount)::numeric target
        FROM cases c JOIN loans l ON l.id = c.loan_id
        GROUP BY 1 ORDER BY 1
    """)
    tc = sum(r["cases"] for r in rows) or 1
    tt = sum(float(r["target"] or 0) for r in rows) or 1.0
    for r in rows:
        print(f"  {r['bucket']:12s}{int(r['cases']):>8d}{_pct(100.0*r['cases']/tc):>9s}"
              f"{_num(float(r['target'] or 0)/1e7, 2):>13s}"
              f"{_pct(100.0*float(r['target'] or 0)/tt):>10s}")


def structural_integrity(db, since: str) -> None:
    """The mechanics that make the rates trustworthy, rather than the rates."""
    print()
    print(RULE)
    print("STRUCTURAL INTEGRITY")
    print(RULE)
    print("  Visits per case within its month — the `k` that drives the collection")
    print("  rate. One visit per case is what made the old pool exhaust on day one.")
    rows = _q(db, """
        SELECT n, count(*) cases FROM (
            SELECT v.case_id, to_char(v.check_in_time,'YYYY-MM') m, count(*) n
            FROM visits v WHERE v.check_in_time >= :s GROUP BY 1,2
        ) x GROUP BY n ORDER BY n
    """, s=since)
    tot = sum(int(r["cases"]) for r in rows) or 1
    weighted = sum(int(r["n"]) * int(r["cases"]) for r in rows)
    for r in rows:
        bar = "#" * max(1, round(40 * int(r["cases"]) / tot))
        print(f"    {int(r['n'])} visit(s){int(r['cases']):>7d}"
              f"{_pct(100.0 * int(r['cases']) / tot):>8s}  {bar}")
    print(f"    mean k = {weighted / tot:.2f} visits per case-month")

    print("\n  Recovery progress:")
    for r in _q(db, """
        SELECT CASE
                 WHEN coalesce(collected_amount,0) >= coalesce(target_amount,0) - 0.01
                      AND coalesce(target_amount,0) > 0 THEN 'fully recovered'
                 WHEN coalesce(collected_amount,0) > 0 THEN 'partially recovered'
                 ELSE 'nothing collected' END band,
               count(*) n FROM cases GROUP BY 1 ORDER BY 2 DESC
    """):
        print(f"    {r['band']:22s}{int(r['n']):>7d}")
    r = _q(db, """
        SELECT count(*) FILTER (WHERE coalesce(overdue_amount,0) <= 0.01) cleared,
               count(*) FILTER (WHERE status = 'CLOSED') closed,
               count(*) FILTER (WHERE overdue_amount < 0 OR total_outstanding < 0
                                  OR dpd < 0) negative
        FROM loans
    """)[0]
    print(f"    loans with arrears cleared : {int(r['cleared'])}")
    print(f"    loans CLOSED               : {int(r['closed'])}")
    print(f"    NEGATIVE balances          : {int(r['negative'])}  (must be 0)")

    print("\n  Invariants — each must read 0:")
    checks = _q(db, """
        SELECT 'cases over the visit cap' k, count(*) n FROM (
            SELECT v.case_id, to_char(v.check_in_time,'YYYY-MM') m, count(*) n,
                   min(c.max_visits_allowed) allowed
            FROM visits v JOIN cases c ON c.id = v.case_id GROUP BY 1,2
        ) x WHERE x.n > least(x.allowed, 3)
        UNION ALL
        SELECT 'simulated cases spanning months', count(*) FROM (
            SELECT v.case_id FROM visits v JOIN cases c ON c.id = v.case_id
            WHERE c.case_number NOT LIKE 'DEMO%'
            GROUP BY v.case_id
            HAVING count(DISTINCT to_char(v.check_in_time,'YYYY-MM')) > 1) y
        UNION ALL
        SELECT 'cross-manager reattribution', count(*) FROM (
            SELECT v.case_id FROM visits v
            JOIN agents va ON va.id = v.agent_id
            JOIN cases c ON c.id = v.case_id
            JOIN agents ca ON ca.id = c.agent_id
            WHERE va.manager_user_id <> ca.manager_user_id
            GROUP BY v.case_id) z
        UNION ALL
        SELECT 'duplicated denominator rows', count(*) FROM (
            SELECT d.agent_id, d.m, d.case_id FROM (
                SELECT DISTINCT v.agent_id, to_char(v.check_in_time,'YYYY-MM') m,
                       v.case_id, c.target_amount
                FROM visits v JOIN cases c ON c.id = v.case_id) d
            GROUP BY 1,2,3 HAVING count(*) > 1) w
        UNION ALL
        SELECT 'cases collected beyond target', count(*) FROM cases
            WHERE coalesce(collected_amount,0) > coalesce(target_amount,0) + 0.01
    """)
    for r in checks:
        flag = "OK" if int(r["n"]) == 0 else "*** VIOLATION ***"
        print(f"    {r['k']:32s}{int(r['n']):>6d}   {flag}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--months", type=int, default=6,
                    help="how far back to measure (default 6)")
    ap.add_argument("--label", default="", help="tag this run in the header")
    ap.add_argument("--database-url", default=None)
    args = ap.parse_args()

    db = _session(args.database_url)
    try:
        since = db.execute(text(
            "SELECT (date_trunc('month', CURRENT_DATE) "
            "        - make_interval(months => :m))::date"
        ), {"m": args.months - 1}).scalar()
        print("=" * 78)
        print(f"  DEMO REALISM MEASUREMENT{'  —  ' + args.label if args.label else ''}")
        print(f"  READ-ONLY · window from {since} · {args.months} months")
        print("=" * 78)

        book_shape(db, str(since))
        visit_volume(db, str(since))
        contact_and_outcomes(db, str(since))
        ptp_metrics(db, str(since))
        collection_rate(db, str(since))
        payment_vs_target(db, str(since))
        dpd_distribution(db)
        structural_integrity(db, str(since))

        print()
        print("=" * 78)
        print("  Nothing was written. The reference ranges are context, not targets.")
        print("=" * 78)
        return 0
    finally:
        db.rollback()
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
