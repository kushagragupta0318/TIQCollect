"""Compare candidate value transforms for the allocator's expected-recovery term.

    python -m scripts.value_transform_study
    python -m scripts.value_transform_study --cases 1200 --seeds 7,42,101,202,303

READ-ONLY. Nothing is promoted, no weight is retuned, and production keeps
`log_current`. The nominal objective weights are untouched throughout — the whole
question here is whether the value TERM can carry the influence its stated 0.45
weight implies, which is a property of the transform, not of the weight.

THE PROBLEM BEING SOLVED. `_value_score` was calibrated against target amounts
(Rs 7,031 / 25,901 / 211,701 / 500,000, per its own 2026-09-02 note) but is fed
expected values — a target times a probability below 1. Measured on a 1,200-case
book, 100% of expected values now fall below its 25,000 knee, so it runs in its
near-linear region and then divides by log1p(12) = 2.565. The term ends up using
3.9% of its available range, and proximity (range 0.40) outvotes it ~26:1.

WHAT WOULD COUNT AS A GOOD ANSWER, stated before the numbers so it cannot be
fitted to them:

  1. realised recovered INR at least matching the current baseline;
  2. the value term's weighted range comparable to proximity's, so 0.45 means
     something;
  3. balance must NOT dominate — the 2026-09-02 failure was an unbounded linear
     term letting a large case score 12.0 against a proximity maximum of 1.0.
     Every candidate here is bounded to [0, 1], so the test is behavioural: the
     mix of balance segments in the plan must not collapse onto the largest;
  4. BLOCKED invariant across every transform and seed.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

import numpy as np  # noqa: E402

from app.ml.pipeline.engine import DecisionEngine  # noqa: E402
from app.models.allocation_decision import AllocationOutcome  # noqa: E402
from app.services.global_allocator import GlobalAllocator  # noqa: E402
from scripts.shadow_allocation_ml import build_book  # noqa: E402

CANDIDATES = ["log_current", "log_rescaled", "sqrt", "power_035", "linear_capped"]


def _alloc(cases, agents, prio, **kw):
    incumbent = {c.id: c.agent_id for c in cases}
    try:
        return GlobalAllocator(**kw).allocate(
            cases=cases, agents=agents, history_matrix={}, prio_scores=prio)
    finally:
        for c in cases:
            c.agent_id = incumbent[c.id]


def _split(decisions):
    a, b, d = {}, set(), set()
    for x in decisions:
        if x.outcome == AllocationOutcome.ALLOCATED.value:
            a[x.case_id] = x.allocated_agent_id
        elif x.outcome == AllocationOutcome.BLOCKED.value:
            b.add(x.case_id)
        else:
            d.add(x.case_id)
    return a, b, d


def evaluate_seed(n_cases: int, n_agents: int, seed: int, objective: str) -> dict:
    engine = DecisionEngine.get("recovery_risk")
    if engine is None:
        raise SystemExit("no champion recovery_risk artifact; train one first")

    cases, agents, feats, truth = build_book(n_cases, n_agents, seed)
    prio = {c.id: 50.0 for c in cases}
    collectable = {c.id: max(0.0, float(c.target_amount or 0.0)
                             - float(c.collected_amount or 0.0)) for c in cases}
    p_rec = {c.id: 1.0 - p for c, p in
             zip(cases, engine.score_batch([feats[c.id] for c in cases]))
             if p is not None}

    bal = np.array([collectable[c.id] for c in cases])
    edges = np.percentile(bal, [20, 40, 60, 80])
    seg_of = {c.id: int(np.searchsorted(edges, collectable[c.id], side="right"))
              for c in cases}

    # Baseline: production exactly as it runs — current formula, current transform.
    base_alloc, base_blocked, base_def = _split(
        _alloc(cases, agents, prio)[1])
    base_inr = sum(collectable[c] * truth[c] for c in base_alloc if c in truth)

    out = {"seed": seed, "n_cases": len(cases), "n_agents": len(agents),
           "baseline": {
               "allocated": len(base_alloc), "blocked": len(base_blocked),
               "deferred": len(base_def),
               "realised_inr": round(base_inr, 0),
               "realised_rate": round(float(np.mean(
                   [truth[c] for c in base_alloc if c in truth])), 4),
               "segment_mix": [round(float(np.mean(
                   [seg_of[c] == s for c in base_alloc])), 4) for s in range(5)],
           },
           "transforms": {}}

    for name in CANDIDATES:
        by_agent, decisions, expected = _alloc(
            cases, agents, prio, ml_recovery_probability=p_rec,
            use_ml_affinity=True, value_transform=name)
        alloc, blocked, deferred = _split(decisions)

        chosen = [d for d in decisions
                  if d.outcome == AllocationOutcome.ALLOCATED.value]
        inr_scores = np.array([d.score_breakdown.get("inr_score", 0.0)
                               for d in chosen])
        prox = np.array([d.score_breakdown.get("proximity_score", 0.0)
                         for d in chosen])

        common = set(alloc) & set(base_alloc)
        moved = sum(1 for cid in common if alloc[cid] != base_alloc[cid])
        realised = [truth[c] for c in alloc if c in truth]
        realised_inr = sum(collectable[c] * truth[c] for c in alloc if c in truth)

        def rng(a):
            return float(np.percentile(a, 90) - np.percentile(a, 10)) if len(a) else 0.0

        out["transforms"][name] = {
            "allocated": len(alloc), "blocked": len(blocked),
            "deferred": len(deferred),
            "blocked_identical": blocked == base_blocked,
            "deferred_diff": len(deferred ^ base_def),
            "assignments_moved": moved,
            "assignments_compared": len(common),
            "pct_moved": round(100.0 * moved / max(len(common), 1), 2),
            "expected_recovery_forecast": round(expected, 0),
            "realised_inr": round(realised_inr, 0),
            "realised_inr_vs_baseline_pct": round(
                100.0 * (realised_inr - base_inr) / max(base_inr, 1), 2),
            "realised_rate": round(float(np.mean(realised)), 4) if realised else None,
            "value_p10": round(float(np.percentile(inr_scores, 10)), 4),
            "value_p50": round(float(np.percentile(inr_scores, 50)), 4),
            "value_p90": round(float(np.percentile(inr_scores, 90)), 4),
            # The comparison that matters: each term's SPREAD after its weight,
            # because a weighted sum is decided by ranges, not by levels.
            "value_weighted_range": round(rng(inr_scores) * 0.45, 4),
            "proximity_weighted_range": round(rng(prox) * 0.40, 4),
            "value_vs_proximity_ratio": round(
                (rng(inr_scores) * 0.45) / max(rng(prox) * 0.40, 1e-9), 3),
            "segment_mix": [round(float(np.mean([seg_of[c] == s for c in alloc])), 4)
                            for s in range(5)],
        }
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", type=int, default=1200)
    ap.add_argument("--agents", type=int, default=40)
    ap.add_argument("--seeds", default="7,42,101,202,303")
    ap.add_argument("--objective", default="BALANCED")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    seeds = [int(x) for x in a.seeds.split(",")]
    runs = [evaluate_seed(a.cases, a.agents, s, a.objective) for s in seeds]

    if a.json:
        print(json.dumps(runs, indent=2))
        return 0

    print("=" * 100)
    print(f"  VALUE TRANSFORM STUDY — {a.cases} cases x {a.agents} agents, "
          f"{len(seeds)} seeds, weights UNCHANGED")
    print("=" * 100)

    base_inr = np.mean([r["baseline"]["realised_inr"] for r in runs])
    base_rate = np.mean([r["baseline"]["realised_rate"] for r in runs])
    print(f"\n  baseline (production: current formula, log_current)")
    print(f"    realised INR {base_inr:>12,.0f}    realised rate {base_rate:.4f}")

    print(f"\n  {'transform':<15}{'INR %':>8}{'rate':>8}{'val range':>11}"
          f"{'prox range':>12}{'val/prox':>10}{'moved%':>8}{'defd':>7}{'BLOCKED':>9}")
    print("  " + "-" * 96)
    summary = {}
    for name in CANDIDATES:
        rows = [r["transforms"][name] for r in runs]
        inr = np.mean([x["realised_inr_vs_baseline_pct"] for x in rows])
        rate = np.mean([x["realised_rate"] for x in rows])
        vr = np.mean([x["value_weighted_range"] for x in rows])
        pr = np.mean([x["proximity_weighted_range"] for x in rows])
        ratio = np.mean([x["value_vs_proximity_ratio"] for x in rows])
        mv = np.mean([x["pct_moved"] for x in rows])
        dd = np.mean([x["deferred_diff"] for x in rows])
        ok = all(x["blocked_identical"] for x in rows)
        wins = sum(1 for x in rows if x["realised_inr_vs_baseline_pct"] > 0)
        summary[name] = dict(inr=inr, ratio=ratio, ok=ok, wins=wins, n=len(rows))
        print(f"  {name:<15}{inr:>+8.1f}{rate:>8.4f}{vr:>11.4f}{pr:>12.4f}"
              f"{ratio:>10.3f}{mv:>8.2f}{dd:>7.0f}"
              f"{('same' if ok else 'MOVED'):>9}")

    print(f"\n  per-seed realised INR vs baseline (%)")
    print(f"  {'transform':<15}" + "".join(f"{s:>10}" for s in seeds) + f"{'wins':>8}")
    print("  " + "-" * (15 + 10 * len(seeds) + 8))
    for name in CANDIDATES:
        rows = [r["transforms"][name] for r in runs]
        cells = "".join(f"{x['realised_inr_vs_baseline_pct']:>+10.1f}" for x in rows)
        print(f"  {name:<15}{cells}{summary[name]['wins']:>6}/{len(rows)}")

    print(f"\n  BALANCE SEGMENT MIX of the allocated plan "
          f"(share of plan per quintile, 1=smallest)")
    print(f"  {'plan':<15}" + "".join(f"{'Q'+str(i+1):>9}" for i in range(5)))
    print("  " + "-" * 60)
    bm = np.mean([r["baseline"]["segment_mix"] for r in runs], axis=0)
    print(f"  {'baseline':<15}" + "".join(f"{v:>9.3f}" for v in bm))
    for name in CANDIDATES:
        mix = np.mean([r["transforms"][name]["segment_mix"] for r in runs], axis=0)
        print(f"  {name:<15}" + "".join(f"{v:>9.3f}" for v in mix))
    print("\n  A transform that pushes the plan onto Q5 has let balance take over "
          "again —\n  the 2026-09-02 failure, in a bounded disguise.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
