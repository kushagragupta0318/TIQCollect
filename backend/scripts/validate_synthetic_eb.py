"""Did Empirical Bayes recover the agent skill the simulator planted?

The synthetic world gives every agent a hidden ability per (loan_type,
dpd_bucket) segment, drawn from N(0, 0.55) on the logit scale. Nothing writes
that number to the database — it exists only in data/synthetic/ground_truth.json
and in the payment events it produced.

So this is a closed-loop test of the estimator. If EB is working, then across
(agent, segment) cells:

    1. SHRINKAGE BEHAVES. Cells with little evidence sit ON the segment prior,
       because that is what shrinkage is for. Cells with a lot of evidence pull
       away from it toward the agent's own measured rate. The gap from the prior
       should grow with n, monotonically.

    2. THE ESTIMATE TRACKS THE TRUTH, and does so BETTER as n grows. A rank
       correlation against planted ability that is near zero when n is small and
       rises with n is the signature of the estimator working. One that is high
       everywhere would mean the shrinkage is doing nothing; one that is flat
       near zero would mean the estimator is not recovering anything.

This is the check that separates "the code runs" from "the method works".

USAGE
    DATABASE_URL=...fieldops_synth python scripts/validate_synthetic_eb.py
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

_URL = os.environ.get("DATABASE_URL", "")
if "synth" not in _URL:
    raise SystemExit(
        "refusing to run: DATABASE_URL does not name a synthetic database.\n"
        f"  got: {_URL or '(unset)'}"
    )
os.environ.setdefault("SECRET_KEY", "eb-validate")
os.environ.setdefault("COMMAND_CENTRE_API_KEY", "eb-validate")

from sqlalchemy import func                                     # noqa: E402

from app.core.database import SessionLocal                      # noqa: E402
from app.ml.empirical_bayes import EmpiricalBayesAgentAdjuster  # noqa: E402
from app.models.repayment_snapshot import RepaymentSnapshot     # noqa: E402

DEFAULT_TRUTH = (pathlib.Path(__file__).parent.parent / "data" / "synthetic"
                 / "ground_truth.json")


def spearman(xs: list[float], ys: list[float]) -> float | None:
    """Rank correlation, without pulling in scipy for one number."""
    n = len(xs)
    if n < 4:
        return None

    def ranks(v: list[float]) -> list[float]:
        order = sorted(range(n), key=lambda i: v[i])
        r = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1.0
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r

    rx, ry = ranks(xs), ranks(ys)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    dx = sum((a - mx) ** 2 for a in rx) ** 0.5
    dy = sum((b - my) ** 2 for b in ry) ** 0.5
    return round(num / (dx * dy), 4) if dx and dy else None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--truth", default=str(DEFAULT_TRUTH))
    ap.add_argument("--lookback-days", type=int,
                    default=EmpiricalBayesAgentAdjuster.DEFAULT_LOOKBACK_DAYS)
    args = ap.parse_args()

    truth = json.loads(pathlib.Path(args.truth).read_text(encoding="utf-8"))
    ability = {tuple(k.split("|")): v
               for k, v in truth["agent_segment_ability"].items()}

    db = SessionLocal()
    as_of = db.query(func.max(RepaymentSnapshot.as_of_date)).scalar()
    if as_of is None:
        raise SystemExit("no snapshots; run the generator first")

    eb = EmpiricalBayesAgentAdjuster().fit_from_db(
        db, as_of=as_of, lookback_days=args.lookback_days)

    rows = []
    for (agent_id, lt, bucket), obs in eb.agent_observations.items():
        planted = ability.get((agent_id, lt, bucket))
        if planted is None:
            continue
        shrunk, prior, mult = eb.get_segment_multiplier(agent_id, lt, bucket)
        rows.append({
            "n": int(obs["n"]),
            "planted": planted,
            "shrunk": shrunk,
            "prior": prior,
            "raw": obs["recovered"] / obs["target"] if obs["target"] else 0.0,
            "gap_from_prior": abs(shrunk - prior),
            "multiplier": mult,
        })

    if not rows:
        raise SystemExit("no (agent, segment) cells found")

    print("\n" + "=" * 78)
    print("  EB VALIDATION AGAINST SIMULATOR TRUTH — SYNTHETIC")
    print("=" * 78)
    print(f"  fitted as of      : {as_of}   (lookback {args.lookback_days}d)")
    print(f"  (agent, segment)  : {len(rows)} cells")
    print(f"  EB global prior   : {eb.global_prior:.4f}")
    print(f"  min_sample_thresh : {eb.min_sample_threshold}   "
          f"smoothing k = {eb.smoothing_k}")
    print("-" * 78)

    bands = [(1, 2), (3, 4), (5, 9), (10, 19), (20, 49), (50, 10 ** 9)]
    print(f"  {'evidence n':<14}{'cells':>7}{'|shrunk-prior|':>16}"
          f"{'rank corr vs planted':>24}")
    for lo, hi in bands:
        band = [r for r in rows if lo <= r["n"] <= hi]
        if not band:
            continue
        gap = sum(r["gap_from_prior"] for r in band) / len(band)
        rho = spearman([r["planted"] for r in band], [r["shrunk"] for r in band])
        label = f"{lo}-{hi}" if hi < 10 ** 9 else f"{lo}+"
        rho_s = f"{rho:+.4f}" if rho is not None else "  (too few)"
        print(f"  {label:<14}{len(band):>7}{gap:>16.4f}{rho_s:>24}")

    print("-" * 78)
    mature = [r for r in rows if r["n"] >= eb.min_sample_threshold]
    sparse = [r for r in rows if r["n"] < eb.min_sample_threshold]
    print(f"  below threshold (n < {eb.min_sample_threshold}): {len(sparse)} cells, "
          f"max |shrunk - prior| = "
          f"{max((r['gap_from_prior'] for r in sparse), default=0.0):.6f}")
    print("    ^ must be 0.000000 — under the threshold the adjuster returns the")
    print("      segment prior unchanged, which is the whole point of shrinkage.")
    if mature:
        rho_all = spearman([r["planted"] for r in mature],
                           [r["shrunk"] for r in mature])
        rho_raw = spearman([r["planted"] for r in mature], [r["raw"] for r in mature])
        print(f"  at or above threshold : {len(mature)} cells")
        print(f"    rank corr, shrunk estimate vs planted ability : {rho_all}")
        print(f"    rank corr, raw   estimate vs planted ability : {rho_raw}")
        print(f"    multiplier range : "
              f"{min(r['multiplier'] for r in mature):.3f} .. "
              f"{max(r['multiplier'] for r in mature):.3f}  (clamped to 0.75-1.25)")
    print("=" * 78 + "\n")
    db.close()


if __name__ == "__main__":
    main()
