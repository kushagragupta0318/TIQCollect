"""Generate the synthetic modelling panel and write it to backend/data/modelling/.

    python -m scripts.build_modelling_dataset                  # full tier
    python -m scripts.build_modelling_dataset --tier dev
    python -m scripts.build_modelling_dataset --sweep          # difficulty sweep

WHY THIS IS NOT scripts/generate_synthetic_repayment_history.py. That one drives
the REAL ORM through a simulated past to prove the production scoring pipeline is
point-in-time honest, and it needs Postgres. This one produces a modelling
dataset: files, from a seed, no infrastructure, in about a second. Both are
wanted; neither replaces the other.

The panel is written to backend/data/modelling/<tier>/, which .gitignore covers
(backend/data/*.csv and the synthetic block). That is deliberate — the dataset is
regenerable from the seed recorded in its own metadata, and a parquet of 120k
synthetic borrowers does not belong in git. The ARTIFACTS built from it
(app/ml/artifacts/) are committed, because those are the deliverable.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.ml.simulation.book_simulator import BookSimulator, SimConfig  # noqa: E402

DATA_ROOT = BACKEND / "data" / "modelling"

TIERS = {
    # Fast loop while iterating on the pipeline.
    "dev": SimConfig(n_borrowers=1_000, months=12, seed=42),
    # Model development: 24 months so the last 6 can be a genuine out-of-time
    # holdout, and enough rows that ~50 candidate features can be selected from
    # without the selection itself becoming the overfit.
    "full": SimConfig(n_borrowers=5_000, months=24, seed=42),
}


def build(tier: str, *, calibrate: bool = True) -> dict:
    cfg = TIERS[tier]
    out = DATA_ROOT / tier

    offset = 0.0
    if calibrate:
        print(f"[1/3] calibrating intercept for target bad rate {cfg.target_bad_rate} ...")
        offset = BookSimulator(cfg).calibrate()
        print(f"      offset = {offset:+.4f}")

    print(f"[2/3] simulating {cfg.n_borrowers} accounts x {cfg.months} months ...")
    sim = BookSimulator(cfg)
    panel = sim.run(intercept_offset=offset)
    sim.truth["intercept_offset"] = offset

    print(f"[3/3] writing to {out} ...")
    meta = sim.save(out, panel)
    meta["intercept_offset"] = offset
    (out / "dataset_metadata.json").write_text(json.dumps(meta, indent=2, default=str))

    print(f"\n  rows              {meta['rows']:,}")
    print(f"  distinct loans    {meta['distinct_loans']:,}")
    print(f"  realised bad rate {meta['realised_bad_rate']}  (target {cfg.target_bad_rate})")
    print(f"  bucket mix        {meta['dpd_bucket_mix']}")
    print(f"  exits             {meta['exits']}")
    return meta


def sweep() -> None:
    """Difficulty sweep: what Gini does each noise setting actually yield?

    The point of recording this is that a target Gini is not something you set,
    it is something a data-generating process produces. Anyone can claim a book
    is 'realistic'; this prints the table that shows what it is.
    """
    import warnings
    warnings.filterwarnings("ignore")
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.metrics import roc_auc_score

    print(f"{'signal':>7} {'noise':>7} {'drift':>7} {'bad rate':>9} "
          f"{'Gini':>7} {'DPD only':>9} {'lift':>7}")
    print("-" * 60)
    for sig, noi, dr in [(1.0, 1.0, 0.075), (0.9, 1.2, 0.075), (0.8, 1.4, 0.09),
                         (0.7, 1.6, 0.11), (0.6, 1.9, 0.13)]:
        cfg = SimConfig(n_borrowers=2_000, months=24, seed=42,
                        signal_scale=sig, observation_noise=noi, latent_drift=dr)
        off = BookSimulator(cfg).calibrate()
        p = BookSimulator(cfg).run(intercept_offset=off)
        num = [c for c in p.columns
               if p[c].dtype.kind in "if"
               and c not in ("y", "month_index", "recovered_amount", "visit_made",
                             "customer_met", "ptp_set", "ptp_kept")]
        split = int(cfg.months * 0.75)
        tr, te = p[p.month_index < split], p[p.month_index >= split]
        m = HistGradientBoostingClassifier(max_iter=220, max_depth=5,
                                           random_state=0).fit(tr[num], tr.y)
        g = 2 * roc_auc_score(te.y, m.predict_proba(te[num])[:, 1]) - 1
        d = 2 * roc_auc_score(te.y, te.dpd) - 1
        print(f"{sig:>7.2f} {noi:>7.2f} {dr:>7.3f} {p.y.mean():>9.3f} "
              f"{g:>7.3f} {d:>9.3f} {g - d:>+7.3f}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--tier", choices=list(TIERS), default="full")
    ap.add_argument("--sweep", action="store_true",
                    help="print the difficulty sweep instead of building")
    ap.add_argument("--no-calibrate", action="store_true")
    a = ap.parse_args()
    if a.sweep:
        sweep()
    else:
        build(a.tier, calibrate=not a.no_calibrate)
