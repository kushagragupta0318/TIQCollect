"""How sensitive is `log_rescaled` to the knee and reference it was given?

    python -u -m scripts.value_transform_sensitivity

The two constants (knee Rs 1,000, reference Rs 20,000) were chosen from a
measured expected-value distribution, which makes them fitted to data — and a
result that only holds at exactly those values is not a fix, it is a coincidence
with good documentation. This sweeps a grid around them and reports whether the
gain survives.

Leaner than value_transform_study on purpose: it evaluates ONE transform against
the production baseline, rather than all five, so the grid finishes in minutes.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

import numpy as np  # noqa: E402

from app.ml.pipeline.engine import DecisionEngine  # noqa: E402
from app.models.allocation_decision import AllocationOutcome  # noqa: E402
from app.services.global_allocator import GlobalAllocator  # noqa: E402
from scripts.shadow_allocation_ml import build_book  # noqa: E402

GRID = [(500, 10_000), (500, 20_000), (1_000, 10_000), (1_000, 20_000),
        (1_000, 40_000), (2_000, 20_000), (2_000, 40_000), (4_000, 40_000)]


def _run(cases, agents, prio, **kw):
    incumbent = {c.id: c.agent_id for c in cases}
    try:
        return GlobalAllocator(**kw).allocate(
            cases=cases, agents=agents, history_matrix={}, prio_scores=prio)[1]
    finally:
        for c in cases:
            c.agent_id = incumbent[c.id]


def _parts(decisions):
    a, b = {}, set()
    for d in decisions:
        if d.outcome == AllocationOutcome.ALLOCATED.value:
            a[d.case_id] = d.allocated_agent_id
        elif d.outcome == AllocationOutcome.BLOCKED.value:
            b.add(d.case_id)
    return a, b


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", type=int, default=1200)
    ap.add_argument("--agents", type=int, default=40)
    ap.add_argument("--seeds", default="7,42,101")
    a = ap.parse_args()
    seeds = [int(x) for x in a.seeds.split(",")]

    engine = DecisionEngine.get("recovery_risk")
    if engine is None:
        raise SystemExit("no champion recovery_risk artifact")

    # Build each book once and reuse it across the whole grid — the constants
    # change the transform, not the world.
    books = {}
    for s in seeds:
        cases, agents, feats, truth = build_book(a.cases, a.agents, s)
        prio = {c.id: 50.0 for c in cases}
        collectable = {c.id: max(0.0, float(c.target_amount or 0.0)
                                 - float(c.collected_amount or 0.0)) for c in cases}
        p_rec = {c.id: 1.0 - p for c, p in
                 zip(cases, engine.score_batch([feats[c.id] for c in cases]))
                 if p is not None}
        base_alloc, base_blocked = _parts(_run(cases, agents, prio))
        base_inr = sum(collectable[c] * truth[c] for c in base_alloc if c in truth)
        bal = np.array([collectable[c.id] for c in cases])
        edges = np.percentile(bal, [20, 40, 60, 80])
        seg = {c.id: int(np.searchsorted(edges, collectable[c.id], side="right"))
               for c in cases}
        books[s] = (cases, agents, prio, collectable, p_rec, truth,
                    base_alloc, base_blocked, base_inr, seg)
        print(f"  built seed {s}: baseline INR {base_inr:,.0f}", flush=True)

    k0 = GlobalAllocator.VALUE_EV_KNEE_INR
    r0 = GlobalAllocator.VALUE_EV_REFERENCE_INR
    print(f"\n{'knee':>7}{'ref':>8}{'ratio':>8}{'INR% mean':>11}{'min':>8}{'max':>8}"
          f"{'wins':>7}{'val/prox':>10}{'Q5':>8}{'BLOCKED':>9}")
    print("-" * 82)
    try:
        for knee, ref in GRID:
            GlobalAllocator.VALUE_EV_KNEE_INR = float(knee)
            GlobalAllocator.VALUE_EV_REFERENCE_INR = float(ref)
            deltas, ratios, q5s, ok = [], [], [], True
            for s in seeds:
                (cases, agents, prio, collectable, p_rec, truth,
                 base_alloc, base_blocked, base_inr, seg) = books[s]
                dec = _run(cases, agents, prio, ml_recovery_probability=p_rec,
                           use_ml_affinity=True, value_transform="log_rescaled")
                alloc, blocked = _parts(dec)
                inr = sum(collectable[c] * truth[c] for c in alloc if c in truth)
                deltas.append(100.0 * (inr - base_inr) / max(base_inr, 1))
                chosen = [d for d in dec
                          if d.outcome == AllocationOutcome.ALLOCATED.value]
                v = np.array([d.score_breakdown.get("inr_score", 0.0) for d in chosen])
                px = np.array([d.score_breakdown.get("proximity_score", 0.0)
                               for d in chosen])

                def rg(x):
                    return float(np.percentile(x, 90) - np.percentile(x, 10))

                ratios.append((rg(v) * 0.45) / max(rg(px) * 0.40, 1e-9))
                q5s.append(float(np.mean([seg[c] == 4 for c in alloc])))
                ok = ok and (blocked == base_blocked)
            print(f"{knee:>7}{ref:>8}{ref/knee:>8.0f}{np.mean(deltas):>+11.1f}"
                  f"{min(deltas):>+8.1f}{max(deltas):>+8.1f}"
                  f"{sum(1 for d in deltas if d > 0):>5}/{len(deltas)}"
                  f"{np.mean(ratios):>10.3f}{np.mean(q5s):>8.3f}"
                  f"{('same' if ok else 'MOVED'):>9}", flush=True)
    finally:
        GlobalAllocator.VALUE_EV_KNEE_INR = k0
        GlobalAllocator.VALUE_EV_REFERENCE_INR = r0

    print("\n  Baseline Q5 share is ~0.261. A configuration pushing far above that")
    print("  has let balance dominate again — the 2026-09-02 failure, bounded.")
    print("  `ratio` is reference/knee: it, not either constant alone, sets how")
    print("  much of the 0-1 range the curve spends on the realistic EV band.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
