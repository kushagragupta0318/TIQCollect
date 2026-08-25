"""
READ-ONLY. Measures whether the recovery scorecard worked. Writes nothing, to
any database, ever — the connection is opened with
default_transaction_read_only=on, so Postgres itself refuses.

  python -m scripts.validate_recovery                      # all three horizons
  python -m scripts.validate_recovery --horizon 30
  python -m scripts.validate_recovery --cohort 2026-08-24
  python -m scripts.validate_recovery --caseless           # the data finding only

SYNTHETIC MODE (2026-08-25, prototype). The real outcomes do not exist yet, so
the framework can be exercised against a simulated fixture instead:

  python -m scripts.validate_recovery --synthetic synthetic_baseline --horizon 30

In that mode NO DATABASE IS OPENED AT ALL — the rows come from a JSON fixture in
docs/validation/synthetic/, the metrics are computed by exactly the same
functions, and every heading says so. The commands above are unchanged and still
validate real outcomes when they land; --synthetic is additive.

BUILT BEFORE THE LABELS EXIST, deliberately. The 2026-08-24 cohort's outcomes
land on 2026-09-23 (30-day), 2026-10-23 (60-day) and 2026-11-22 (90-day). Fixing
the questions in advance is the only way to stop them being chosen after seeing
the answers. Until labelling runs this reports "nothing admissible yet" and says
why for every row — which is itself the check that the plumbing works.

WHAT IT WILL NOT DO
-------------------
It does not tune anything. Ranking and calibration are reported separately, a
candidate lever is named, and that is where it stops: any recalibration is a
separate, versioned, approved decision. The scorecard is not imported for
anything except its version string.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.ml.recovery_validation import (
    ADMISSIBLE, BANDS, EXPECTED_VERSION, MIN_ADMISSIBLE_PER_BAND, UNOBSERVABLE,
    calibration_metrics, classify, diagnose, factor_residual_correlation,
    marginal_lift_by_security, observation_from, ranking_metrics,
    summarise_admissibility, top_k_capture,
)

HORIZONS = (30, 60, 90)
CHECKPOINTS = {30: "2026-09-23", 60: "2026-10-23", 90: "2026-11-22"}
RULE = "─" * 78


def _session(url: str | None):
    from app.core.config import settings
    engine = create_engine(
        url or settings.DATABASE_URL,
        # The hard guarantee. Any write attempted through this connection errors
        # rather than quietly succeeding.
        connect_args={"options": "-c default_transaction_read_only=on"},
    )
    return sessionmaker(bind=engine)()


def _pct(v, nd=1):
    return "—" if v is None else f"{v * 100:.{nd}f}%"


def _num(v, nd=3):
    return "—" if v is None else f"{v:.{nd}f}"


def _load_cohort(db, cohort: date):
    from app.models.repayment_snapshot import RepaymentSnapshot
    return (db.query(RepaymentSnapshot)
            .filter(RepaymentSnapshot.as_of_date == cohort)
            .filter(RepaymentSnapshot.recovery_rate_90.isnot(None))
            .all())


# ── The caseless finding ─────────────────────────────────────────────────────
def report_caseless(db, cohort: date) -> None:
    """Why 115 scored loans can never show a recovery — reported, not corrected.

    Payment.case_id is NOT NULL, so money reaches the ledger only through a case.
    A loan with no case is invisible to the outcome, whatever the borrower paid.
    That is an operational fact about allocation, not a scorecard defect, and the
    validation excludes those loans rather than recording a false zero.
    """
    print(RULE)
    print("CASELESS LOANS — an operational finding, not a scorecard defect")
    print(RULE)
    rows = db.execute(text("""
        SELECT s.recovery_potential AS band,
               count(*) AS scored,
               count(*) FILTER (WHERE NOT has_case) AS caseless,
               round(avg(l.dpd) FILTER (WHERE NOT has_case)::numeric, 0) AS caseless_dpd,
               round(avg(l.dpd) FILTER (WHERE has_case)::numeric, 0) AS cased_dpd,
               round((sum(l.total_outstanding) FILTER (WHERE NOT has_case)
                      / 10000000)::numeric, 2) AS caseless_cr
        FROM (SELECT s.*, EXISTS (SELECT 1 FROM cases c WHERE c.loan_id = s.loan_id) AS has_case
              FROM repayment_score_snapshots s
              WHERE s.as_of_date = :d AND s.recovery_rate_90 IS NOT NULL) s
        JOIN loans l ON l.id = s.loan_id
        GROUP BY 1 ORDER BY 1
    """), {"d": cohort}).fetchall()

    print(f"  {'band':8s}{'scored':>8s}{'caseless':>10s}{'% lost':>9s}"
          f"{'dpd (caseless)':>16s}{'dpd (cased)':>13s}{'outstanding':>14s}")
    for r in rows:
        share = r.caseless / r.scored if r.scored else 0
        print(f"  {r.band:8s}{r.scored:8d}{r.caseless:10d}{share * 100:8.1f}%"
              f"{str(r.caseless_dpd):>16s}{str(r.cased_dpd):>13s}"
              f"{'₹' + str(r.caseless_cr) + ' Cr':>14s}")

    extra = db.execute(text("""
        SELECT
          count(*) FILTER (WHERE NOT has_case) AS caseless,
          count(*) FILTER (WHERE NOT has_case AND l.status = 'CLOSED')  AS closed,
          count(*) FILTER (WHERE NOT has_case AND l.status = 'SETTLED') AS settled,
          count(*) FILTER (WHERE NOT has_case AND l.status = 'WRITTEN_OFF') AS written_off,
          count(*) FILTER (WHERE NOT has_case AND l.status = 'ACTIVE')  AS active,
          count(*) FILTER (WHERE NOT has_case AND l.status = 'NPA')     AS npa,
          count(*) FILTER (WHERE NOT has_case AND l.dpd < 90)  AS under_90,
          count(*) FILTER (WHERE NOT has_case AND l.dpd >= 90) AS over_90
        FROM (SELECT s.*, EXISTS (SELECT 1 FROM cases c WHERE c.loan_id = s.loan_id) AS has_case
              FROM repayment_score_snapshots s
              WHERE s.as_of_date = :d AND s.recovery_rate_90 IS NOT NULL) s
        JOIN loans l ON l.id = s.loan_id
    """), {"d": cohort}).one()
    print(f"\n  caseless loans by LOAN STATUS: CLOSED {extra.closed} · "
          f"SETTLED {extra.settled} · WRITTEN_OFF {extra.written_off} · "
          f"ACTIVE {extra.active} · NPA {extra.npa}")
    print(f"  caseless loans by delinquency: <90 DPD {extra.under_90} · "
          f">=90 DPD {extra.over_90}")
    # ── The circularity, which is why exclusion is not merely tidiness ───────
    conf = db.execute(text("""
        SELECT has_case,
               count(*) AS n,
               round(avg(recovery_rate_90)::numeric * 100, 1) AS mean_rate,
               round(avg(recovery_evidence_coverage)::numeric, 3) AS mean_cov,
               -- Queried through jsonb, not by matching the serialised text:
               -- jsonb reorders keys on storage, so a LIKE on '"code": ...,
               -- "abstained": true' silently matches nothing and reports a
               -- confident 0%.
               round(100.0 * count(*) FILTER (WHERE EXISTS (
                   SELECT 1 FROM jsonb_array_elements(
                       recovery_contributions -> 'factors') f
                   WHERE f ->> 'code' = 'PAYMENT_MOMENTUM'
                     AND coalesce((f ->> 'abstained')::boolean, false)))
                     / count(*), 1) AS momentum_abstained_pct
        FROM (SELECT s.*, EXISTS (SELECT 1 FROM cases c WHERE c.loan_id = s.loan_id) AS has_case
              FROM repayment_score_snapshots s
              WHERE s.as_of_date = :d AND s.recovery_rate_90 IS NOT NULL) s
        GROUP BY 1 ORDER BY 1 DESC
    """), {"d": cohort}).fetchall()
    print("\n  THE CIRCULARITY — caselessness does not just hide the OUTCOME,")
    print("  it also depresses the SCORE:")
    print(f"    {'group':10s}{'n':>6s}{'mean rate_90':>14s}{'coverage':>11s}"
          f"{'MOMENTUM abstained':>21s}")
    for r in conf:
        label = "cased" if r.has_case else "caseless"
        print(f"    {label:10s}{r.n:6d}{str(r.mean_rate) + '%':>14s}"
              f"{str(r.mean_cov):>11s}{str(r.momentum_abstained_pct) + '%':>21s}")
    print("\n    PAYMENT_MOMENTUM reads amount_paid_in_window, and payments are")
    print("    reachable only through a case. So a caseless loan can never earn")
    print("    that upside-only factor: it abstains 100% of the time, coverage")
    print("    falls, and the loan scores lower — which is why caseless loans")
    print("    concentrate in LOW rather than LOW loans being denied a case.")
    print("    DPD is near-identical between the two groups within each band,")
    print("    which rules out 'the worst loans are not allocated'.")
    print("\n    Left uncorrected this would have manufactured the very result")
    print("    the validation is testing for: no case -> low score AND no case ->")
    print("    zero observed recovery, a perfect correlation created entirely by")
    print("    the ledger's shape. Excluding these loans removes a CONFOUND, not")
    print("    just an inconvenience.")

    print("\n  READ THIS AS: a caseless loan is UNOBSERVABLE, never ₹0 recovered.")
    print("  If LOW-band loans are systematically not allocated a case, the")
    print("  validation inherits that operational choice as missing data — and")
    print("  the scorecard's apparent separation is flattered by exactly the")
    print("  loans it rated worst. Reported for a decision; not corrected here.")


# ── The caseless finding, from a synthetic fixture ───────────────────────────
def report_caseless_fixture(rows) -> None:
    """The same finding as report_caseless, computed without a database.

    Synthetic mode has no `cases` table to query, but the fixture carries the
    real has-a-case fact for every loan (that is a property of the production
    cohort, not something simulated), so the band skew is still reportable — and
    it is the reason the synthetic generator refuses to write ₹0 for these loans.

    Caselessness is read through classify(), NOT through a synthetic-only field.
    An earlier draft tested the fixture's `_simulation` provenance block, which
    put simulation vocabulary inside a production script and would have made this
    function unusable against a real cohort. classify() answers the same question
    from the real contract — amounts NULL with the marker at or past the horizon
    is exactly what UNOBSERVABLE means — so this reports identically whether the
    rows came from a fixture or from Postgres.
    """
    print(RULE)
    print("CASELESS LOANS — an operational finding, not a scorecard defect")
    print(RULE)
    print("  Counts are REAL (from the production cohort). Only the outcomes are")
    print("  simulated, and for these loans there is deliberately no outcome.")
    print(f"\n  {'band':8s}{'scored':>8s}{'caseless':>10s}{'% lost':>9s}"
          f"{'outstanding':>16s}")
    for band in BANDS:
        scored = [r for r in rows if getattr(r, "recovery_potential", None) == band]
        caseless = [r for r in scored
                    if classify(r, horizon=90) == UNOBSERVABLE]
        if not scored:
            continue
        cr = sum(r.features.get("total_outstanding", 0.0) for r in caseless) / 1e7
        print(f"  {band:8s}{len(scored):8d}{len(caseless):10d}"
              f"{len(caseless) / len(scored) * 100:8.1f}%{'₹' + f'{cr:.2f}' + ' Cr':>16s}")
    print("\n  READ THIS AS: a caseless loan is UNOBSERVABLE, never ₹0 recovered.")
    print("  Payment.case_id is NOT NULL, so money reaches the ledger only through")
    print("  a case. Recording ₹0 for these would manufacture the very result the")
    print("  validation is testing for — caselessness also depresses the SCORE,")
    print("  because PAYMENT_MOMENTUM abstains without a payment path — and it")
    print("  would bias LOW hardest. The generator leaves them NULL for that reason.")


# ── The cohort report ────────────────────────────────────────────────────────
def report_horizon(rows, horizon: int, *, synthetic: str | None = None) -> None:
    print()
    print(RULE)
    if synthetic:
        print(f"HORIZON {horizon} DAYS   —   SIMULATED RESULT, scenario {synthetic}")
        print(f"  (real labels are not due until {CHECKPOINTS.get(horizon, '?')})")
    else:
        print(f"HORIZON {horizon} DAYS   (labels due {CHECKPOINTS.get(horizon, '?')})")
    print(RULE)

    reasons = Counter(classify(r, horizon=horizon) for r in rows)
    summary = summarise_admissibility(reasons)
    print("  ADMISSIBILITY — exclusions before findings")
    for reason, n in sorted(summary["counts"].items(), key=lambda kv: -kv[1]):
        print(f"    {reason:18s}{n:5d}")
    print(f"    {'-> admissible':18s}{summary['admissible']:5d}"
          f"   ({_pct(summary['admissible_share'])} of {summary['total']})")

    admissible = [observation_from(r, horizon=horizon) for r in rows
                  if classify(r, horizon=horizon) == ADMISSIBLE]
    if not admissible:
        print("\n  Nothing admissible yet — no metrics computed.")
        print("  This is the expected state until the checkpoint above has passed")
        print("  and the labelling run has been executed manually.")
        return

    per_band = Counter(o.band for o in admissible)
    thin = [b for b in BANDS if per_band[b] < MIN_ADMISSIBLE_PER_BAND]
    if thin:
        print(f"\n  ⚠ INSUFFICIENT SAMPLE: {', '.join(thin)} below the "
              f"{MIN_ADMISSIBLE_PER_BAND}-loan bar "
              f"({', '.join(f'{b}={per_band[b]}' for b in thin)}).")
        print("    Their band means are reported but must not be used to")
        print("    calibrate a level. Ordering evidence is still usable.")

    # ── Ranking ──────────────────────────────────────────────────────────────
    rk = ranking_metrics(admissible)
    print("\n  RANKING — does the order hold? (independent of the levels)")
    print(f"    {'band':8s}{'n':>6s}{'realised':>11s}{'95% CI':>22s}")
    for b in BANDS:
        s = rk["by_band"][b]
        ci = f"[{_pct(s['lo'])}, {_pct(s['hi'])}]" if s["mean"] is not None else "—"
        flag = "  ⚠ thin" if s["n"] < MIN_ADMISSIBLE_PER_BAND else ""
        print(f"    {b:8s}{s['n']:6d}{_pct(s['mean']):>11s}{ci:>22s}{flag}")
    print(f"    monotonic HIGH>=MEDIUM>=LOW : {rk['monotonic']}")
    print(f"    HIGH/LOW lift               : {_num(rk['high_low_lift'], 2)}"
          f"   (>=2.0 is meaningful separation)")
    print(f"    HIGH and LOW CIs disjoint   : {rk['bands_separated']}")
    print(f"    LOW loans above HIGH median : {_pct(rk['low_above_high_median'])}"
          f"   (<15% is healthy)")
    print(f"    Spearman rho                : {_num(rk['spearman'])}"
          f"   (>=0.30 is a usable ordering)")

    cap = top_k_capture(admissible)
    print("\n  OPERATIONAL LIFT — the metric that maps to reallocating agents")
    print(f"    top {int(cap['k'] * 100)}% by predicted money holds "
          f"{_pct(cap['captured'])} of realised recovery")
    print(f"    random baseline {_pct(cap['baseline'])}  ->  lift "
          f"{_num(cap['lift'], 2)}   (<1.75 means it barely changes the work)")

    # ── Calibration ──────────────────────────────────────────────────────────
    cal = calibration_metrics(admissible)
    print("\n  CALIBRATION — are the levels right? (a DIFFERENT question)")
    print(f"    mean predicted {_pct(cal['mean_predicted'])}   "
          f"mean realised {_pct(cal['mean_realised'])}")
    print(f"    bias (realised - predicted) : {_pct(cal['bias'])}")
    print(f"    calibration slope           : {_num(cal['slope'], 2)}"
          f"   (1.00 perfect; <1 = predictions over-spread)")
    print(f"    bias spread across bands    : {_pct(cal['bias_spread'])}")
    print(f"    {'band':8s}{'n':>6s}{'predicted':>11s}{'realised':>11s}{'bias':>10s}")
    for b in BANDS:
        s = cal["by_band"].get(b, {})
        if not s.get("n"):
            print(f"    {b:8s}{0:6d}{'—':>11s}{'—':>11s}{'—':>10s}")
            continue
        print(f"    {b:8s}{s['n']:6d}{_pct(s['predicted']):>11s}"
              f"{_pct(s['realised']):>11s}{_pct(s['bias']):>10s}")

    # ── Marginal value over collateral ───────────────────────────────────────
    sec = marginal_lift_by_security(admissible)
    print("\n  MARGINAL VALUE OVER COLLATERAL — does the label add anything")
    print("  WITHIN a collateral class, or is it a proxy for loan_type?")
    print(f"    {'group':14s}{'n':>6s}{'HIGH/LOW lift':>15s}{'spearman':>11s}")
    for g, s in sec.items():
        print(f"    {g:14s}{s['n']:6d}{_num(s['high_low_lift'], 2):>15s}"
              f"{_num(s['spearman']):>11s}")

    # ── Factor diagnostics ───────────────────────────────────────────────────
    fac = factor_residual_correlation(admissible)
    print("\n  FACTOR vs RESIDUAL — candidates for investigation, not a mandate")
    print(f"    {'factor':20s}{'n':>6s}{'corr with error':>17s}{'mean pts':>11s}")
    for code, s in fac.items():
        print(f"    {code:20s}{s['n']:6d}{_num(s.get('correlation')):>17s}"
              f"{_num(s.get('mean_contribution'), 4):>11s}")

    # ── Verdict ──────────────────────────────────────────────────────────────
    d = diagnose(rk, cal)
    print("\n  VERDICT")
    print(f"    ranks well            : {d['ranks_well']}")
    print(f"    calibrated            : {d['calibrated']}")
    print(f"    bias uniform by band  : {d['bias_uniform_across_bands']}")
    print(f"    verdict               : {d['verdict']}")
    print(f"    candidate lever       : {d['candidate_lever'] or '— none —'}")
    print(f"    action                : {d['action']}")
    if synthetic:
        # The verdict logic is untouched; what changes is what it is a verdict
        # ABOUT. On a synthetic fixture it says the pipeline can tell these
        # states apart — never that the scorecard works.
        print("\n    SIMULATED RESULT. This demonstrates that the validation")
        print("    pipeline can distinguish recovery ranking and calibration")
        print("    behaviour. It is not evidence of production scorecard")
        print(f"    performance — real labels are due {CHECKPOINTS.get(horizon, '?')}.")


def _main_synthetic(args) -> int:
    """PROTOTYPE PATH. Same metrics, same verdict logic, a fixture instead of a DB.

    Imported here rather than at module scope so that the production path does
    not depend on the synthetic machinery existing at all — delete
    docs/validation/synthetic/ and everything above still runs.
    """
    from scripts.synthetic_fixture import BANNER, DISCLAIMER, load_fixture

    meta, rows = load_fixture(args.synthetic)
    print("=" * 78)
    print(f"  {BANNER}")
    print(f"  {DISCLAIMER}")
    print("=" * 78)
    print(f"  scenario                   : {meta['scenario']} — {meta['headline']}")
    print(f"  seed                       : {meta['seed']}")
    print(f"  simulation version         : {meta['simulation_version']}")
    print(f"  cohort (predictions)       : {meta['cohort']}  ({meta['predictions']})")
    print(f"  outcomes                   : {meta['outcomes']}")
    print(f"  expected scorecard version : {EXPECTED_VERSION}")
    print(f"  scored rows in fixture     : {len(rows)}")
    print(f"  bands                      : {meta['bands']}")
    print(f"  admissibility bar per band : {MIN_ADMISSIBLE_PER_BAND} loans")
    print("  database                   : NOT OPENED — this run reads a file")
    print(f"  real labels due            : {meta['real_checkpoints']}")

    if args.caseless:
        print()
        report_caseless_fixture(rows)
        return 0

    for horizon in (args.horizon or list(HORIZONS)):
        report_horizon(rows, horizon, synthetic=meta["scenario"])

    print()
    report_caseless_fixture(rows)
    print()
    print("=" * 78)
    print(f"  {BANNER}")
    print(f"  {DISCLAIMER}")
    print("=" * 78)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cohort", default="2026-08-24", help="as_of_date to validate")
    ap.add_argument("--horizon", type=int, choices=HORIZONS, action="append")
    ap.add_argument("--caseless", action="store_true",
                    help="only the caseless-loans data finding")
    ap.add_argument("--database-url", default=None)
    ap.add_argument("--synthetic", default=None, metavar="SCENARIO",
                    help="PROTOTYPE: validate a synthetic fixture from "
                         "docs/validation/synthetic/ instead of the database. "
                         "No database connection is opened.")
    args = ap.parse_args()

    if args.synthetic:
        return _main_synthetic(args)

    cohort = date.fromisoformat(args.cohort)
    db = _session(args.database_url)
    try:
        print(RULE)
        print(f"RECOVERY VALIDATION — cohort {cohort} — READ-ONLY")
        print(RULE)
        print(f"  expected scorecard version : {EXPECTED_VERSION}")
        print(f"  admissibility bar per band : {MIN_ADMISSIBLE_PER_BAND} loans")
        print("  writes                     : none (read-only transaction)")

        rows = _load_cohort(db, cohort)
        print(f"  scored rows in cohort      : {len(rows)}")
        versions = Counter(r.recovery_model_version for r in rows)
        print(f"  versions present           : {dict(versions)}")
        off = sum(n for v, n in versions.items() if v != EXPECTED_VERSION)
        if off:
            print(f"  ⚠ {off} rows are a different version and are excluded — "
                  "pooling versions would average two scorecards")

        if args.caseless:
            report_caseless(db, cohort)
            return 0

        for horizon in (args.horizon or list(HORIZONS)):
            report_horizon(rows, horizon)

        print()
        report_caseless(db, cohort)
        return 0
    finally:
        db.rollback()
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
