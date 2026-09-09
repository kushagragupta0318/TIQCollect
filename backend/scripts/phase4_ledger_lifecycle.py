"""Phase 4: the whole ML lifecycle, replayed on the ledger.

    python -m scripts.phase4_ledger_lifecycle            # healthy book
    python -m scripts.phase4_ledger_lifecycle --stress   # concept drift on

    predict at as_of  ->  advance 30 days  ->  label  ->  compare  ->  monitor

Every stage runs the PRODUCTION code unchanged: `MLScoringService.score_cases_and_log`
writes the predictions, `outcomes.attach_outcomes` labels them,
`label_comparison.compare_all` measures the disagreement, `monitor.readiness`
gates and `monitor.monitor_model` judges. Nothing here reimplements any of them
— the point is to exercise them, not to model them a second time.

WHY IT SCORES WITH 1.2.0-ledger, NOT THE CHAMPION. `recovery_risk` 1.1.0 was
fitted on `book_simulator`, so its development metrics describe a different
world; monitoring live performance against them would compare two books rather
than one model against itself. The version is selected through
`settings.ML_MODEL_VERSION`, which is a rollout gate that only started working
on 2026-09-09 — before that every `DecisionEngine.get()` ignored it and served
the champion regardless. `champion.txt` is never written.

THE STRESS RUN IS THE POINT OF 8 AND 9. A monitor that never fires is not a
monitor, and one that fires on a healthy book is worse. Both are asserted, on
the SAME thresholds — nothing in `monitor.py` is relaxed to make either pass.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from sqlalchemy import create_engine                       # noqa: E402
from sqlalchemy.orm import sessionmaker                    # noqa: E402
from sqlalchemy.pool import StaticPool                     # noqa: E402

from app.core.config import settings                       # noqa: E402
from app.ml.pipeline import monitor as mon                 # noqa: E402
from app.ml.pipeline.label_comparison import compare_all   # noqa: E402
from app.ml.pipeline.outcomes import (                     # noqa: E402
    OUTCOME_DEFINITION_VERSION, attach_outcomes,
)
from app.ml.simulation.ledger import LedgerConfig, LedgerSimulator  # noqa: E402
from app.ml.simulation.ledger.materialise import Materialiser       # noqa: E402
from app.models.base import Base                           # noqa: E402
from app.models.case import Case                           # noqa: E402
from app.models.model_prediction import ModelPrediction    # noqa: E402
from app.services.ml_scoring_service import MLScoringService  # noqa: E402

LEDGER_VERSION = "1.2.0-ledger"
HORIZON = 30

BASE = LedgerConfig(n_borrowers=1_400, months=30, seed=31)

#: BURN-IN. No snapshot before this day, because the longest lookback window a
#: feature uses is 365 days and one that reaches past the start of the world is
#: truncated by the simulation rather than by the borrower.
#:
#: Measured, this is not a nicety. With snapshots from day 120, PSI on
#: `days_since_last_contact` read 0.76 and fired the retrain trigger on a
#: PERFECTLY HEALTHY book — its maximum is 60 at day 60, 120 at day 120 and 174
#: at day 180, settling only around day 300, so the reference third simply could
#: not express a long no-contact gap. The distribution was not drifting; the
#: world was still starting. Production has the same rule for the same reason:
#: a loan with no history is not scored as though its history were empty.
BURN_IN_DAYS = 365

#: Score here, label 30 days later. Spread so the monitor's stability split has
#: an early third and a late two-thirds to compare.
AS_OF_DAYS = [400, 460, 520, 580, 640, 700, 760, 820]
assert min(AS_OF_DAYS) >= BURN_IN_DAYS
assert max(AS_OF_DAYS) + HORIZON < BASE.months * BASE.cycle_days


def live_loans(ledger, day: int) -> list[str]:
    """Loans in the book on `day`: disbursed by then, not yet terminated."""
    term = ledger.lifecycle[ledger.lifecycle.event != "OPENED"]
    closed = term.groupby("loan_id").day.min().to_dict()
    return [r.loan_id for r in ledger.loans.itertuples()
            if r.opened_day <= day < closed.get(r.loan_id, 10 ** 9)]


def _session():
    engine = create_engine("sqlite://",
                           connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    return sessionmaker(autocommit=False, autoflush=False, bind=engine)()


def replay(cfg: LedgerConfig) -> dict:
    ledger = LedgerSimulator(cfg).run(intercept=-4.1562)
    db = _session()
    mat = Materialiser(ledger, cfg)
    mat.load(db)

    svc = MLScoringService(db)
    n_pred = 0
    labelled = {"labelled": 0, "by_status": {}}
    # ONE FORWARD PASS. Score a cohort, let its own 30 days elapse, label it,
    # then move on — which is what the nightly tasks do. Scoring every cohort
    # first and labelling them all at the end let a write-off on day 800 censor
    # a prediction made on day 400: measured, that pushed censored rows to 5,981
    # of 11,200 and dropped the comparable bad rate from 0.70 to 0.51, which
    # fired the retrain trigger on a book with nothing wrong with it.
    # AS_OF_DAYS are spaced wider than the horizon, so the clock never rewinds.
    for day in AS_OF_DAYS:
        # 1. PREDICT at as_of, with the database rewound to that moment.
        mat.rewind_to(db, day)
        as_of = cfg.start_date + timedelta(days=day)
        # ONLY THE LIVE POOL, exactly as production scores it: a loan that has
        # not been disbursed yet, or that closed before this date, is not in the
        # book. Scoring every case row regardless put 13,882 of 39,800
        # predictions (34.9%) at NO_BASELINE — accounts with nothing billed, so
        # no overdue, so no threshold to measure an outcome against. That was
        # the harness inventing a population, not the labeller failing.
        cases = [db.query(Case).filter(Case.id == f"C-{lid}").first()
                 for lid in live_loans(ledger, day)]
        cases = [c for c in cases if c is not None]
        probs, rows = svc.score_cases_and_log(cases, as_of=as_of)
        db.commit()
        n_pred += len(rows)

        # 2. ADVANCE 30 days.  3. LABEL this cohort, at its own maturity.
        mature_day = day + HORIZON + 1
        mat.rewind_to(db, mature_day)
        got = attach_outcomes(db, "recovery_risk",
                              as_of=cfg.start_date + timedelta(days=mature_day))
        labelled["labelled"] += got["labelled"]
        for k, v in got["by_status"].items():
            labelled["by_status"][k] = labelled["by_status"].get(k, 0) + v

    label_as_of = cfg.start_date + timedelta(days=max(AS_OF_DAYS) + HORIZON + 1)

    # 5. COMPARE, without touching the model's own label.
    comparison = compare_all(db, "recovery_risk", as_of=label_as_of)
    comparison.pop("examples", None)

    # 6/7. MONITOR, on the serving version and the current outcome definition.
    gate = mon.readiness(db, "recovery_risk")
    report = mon.monitor_model(db, "recovery_risk",
                               version=gate.model_version,
                               outcome_definition_version=gate.outcome_definition_version,
                               lookback_days=10_000)
    digest = report.to_dict()
    for heavy in ("decile_table", "calibration_table"):
        digest.pop(heavy, None)

    outcome_mix = dict(
        db.query(ModelPrediction.outcome_status,
                 __import__("sqlalchemy").func.count())
        .group_by(ModelPrediction.outcome_status).all())
    db.close()
    return {
        "predictions": n_pred,
        "labelling": labelled,
        "outcome_status_mix": {str(k): v for k, v in outcome_mix.items()},
        "comparison": comparison,
        "readiness": gate.to_dict(),
        "monitor": digest,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stress", action="store_true",
                    help="turn concept drift on; monitoring must detect it")
    a = ap.parse_args()

    # The rollout gate, exercised. Before 2026-09-09 this line did nothing.
    settings.ML_MODEL_VERSION = LEDGER_VERSION
    from app.ml.pipeline.engine import DecisionEngine
    DecisionEngine.clear_cache()
    if DecisionEngine.get("recovery_risk") is None:
        raise SystemExit(f"no {LEDGER_VERSION} artifact — run phase 2 first")

    cfg = replace(BASE, concept_drift=a.stress)
    label = "STRESS (concept drift ON)" if a.stress else "HEALTHY"
    print(f"\n{'='*78}\n  PHASE 4 — {label}\n{'='*78}")

    out = replay(cfg)
    m, c, g = out["monitor"], out["comparison"], out["readiness"]
    perf, stab = m.get("performance", {}), m.get("stability", {})

    print(f"\n  predictions written      {out['predictions']:,}")
    print(f"  labelling                {out['labelling']}")
    print(f"  outcome status mix       {out['outcome_status_mix']}")
    print(f"\n  LABEL COMPARISON")
    print(f"    matured                {c['total_matured']}")
    print(f"    model  rec/not/cens    {c['model_definition']['recovered']}"
          f"/{c['model_definition']['not_recovered']}"
          f"/{c['model_definition']['censored']}")
    print(f"    repay  rec/not/cens    {c['repayment_definition']['recovered']}"
          f"/{c['repayment_definition']['not_recovered']}"
          f"/{c['repayment_definition']['censored']}")
    print(f"    comparable / disagree  {c['comparable_rows']} / {c['disagree']}"
          f"  ({c['disagreement_pct']}%)")
    print(f"    causes                 {c['cause_breakdown']}")

    print(f"\n  READINESS                ready={g['ready']} "
          f"matured={g['n_matured']} required={g['required']}")
    print(f"    excluded other model   {g['n_excluded_other_model_version']}")
    print(f"    excluded other outcome {g['n_excluded_other_outcome_version']}")

    print(f"\n  MONITOR  version {m.get('version')}  "
          f"outcome_def {m.get('outcome_definition_version')}")
    print(f"    verdict                {m.get('verdict')}")
    print(f"    retrain_recommended    {m.get('retrain_recommended')}")
    for k in ("n", "auc_live", "gini_live", "gini_at_development",
              "gini_relative_drop", "ks_live", "brier_live", "calibration_gap",
              "bad_rate_live", "recovery_rate_live", "rank_order_breaks"):
        if k in perf:
            print(f"    {k:<22} {perf[k]}")
    print(f"    score_psi              {stab.get('score_psi')}")
    print(f"    max_feature_psi        {stab.get('max_feature_psi')}")
    print(f"    features missing       {stab.get('features_missing_from_predictions')}")
    for row in (stab.get("per_feature") or [])[:8]:
        print(f"      psi {row['feature']:<22} {round(row['psi'], 4)}")
    print(f"    reasons                {m.get('reasons')}")

    Path(BACKEND / "data" / "ledger").mkdir(parents=True, exist_ok=True)
    name = "PHASE4_STRESS.json" if a.stress else "PHASE4_HEALTHY.json"
    (BACKEND / "data" / "ledger" / name).write_text(
        json.dumps(out, indent=2, default=str), encoding="utf-8")
    print(f"\n  written to data/ledger/{name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
