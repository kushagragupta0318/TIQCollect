# ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
# 2026-08-25 — New file. PROTOTYPE / SIMULATION ONLY.
#
#   Renders the synthetic validation run as a stakeholder-readable markdown
#   report. Separate from validate_recovery.py on purpose: that script is the
#   production instrument and gained one additive flag (--synthetic); the
#   demo-only formatting lives here so a production validation run never depends
#   on synthetic code being present.
#
#   IT COMPUTES NOTHING OF ITS OWN. Every number comes from
#   app/ml/recovery_validation.py — the same functions the production run will
#   call on 2026-11-22 — so the report cannot flatter the fixture through a
#   second, friendlier implementation of a metric.
#
#   THE PASS/FAIL TABLE USES THE BARS THAT ALREADY EXISTED. Every threshold is
#   read from the framework or quoted from validate_recovery.py's own printed
#   guidance (>=2.0 lift, >=0.30 rho, <15% overlap, +/-5pp bias, >=1.75 capture
#   lift, >=100 per band). None was chosen here, and none was chosen after
#   seeing a result — which is the only thing that makes a PASS mean anything.
# ───────────────────────────────────────────────────────────────────────────
"""Write docs/validation/synthetic/validation_{30,60,90}d.md from a fixture."""
from __future__ import annotations

import argparse
from collections import Counter

from app.ml.recovery_validation import (
    ADMISSIBLE, BANDS, EXPECTED_VERSION, MIN_ADMISSIBLE_PER_BAND,
    calibration_metrics, classify, diagnose, factor_residual_correlation,
    marginal_lift_by_security, observation_from, ranking_metrics,
    summarise_admissibility, top_k_capture,
)
from scripts.synthetic_fixture import (
    BANNER, DISCLAIMER, FIXTURE_DIR, REAL_CHECKPOINTS, SCENARIOS, load_fixture,
)

HORIZONS = (30, 60, 90)

# The bars, and where each one comes from. Kept as data so the table below and
# the prose cannot drift apart.
BIAS_TOLERANCE = 0.05        # diagnose(bias_tolerance=...)
SPEARMAN_FLOOR = 0.30        # diagnose(spearman_floor=...)
LIFT_FLOOR = 2.0             # validate_recovery.py: ">=2.0 is meaningful separation"
OVERLAP_CEILING = 0.15       # validate_recovery.py: "<15% is healthy"
CAPTURE_LIFT_FLOOR = 1.75    # validate_recovery.py: "<1.75 means it barely changes the work"

# Every admissibility reason, listed explicitly so a count of zero is PRINTED
# rather than omitted. An exclusion table that silently drops its empty rows
# reads as "this cannot happen" when it means "it did not happen this time".
ALL_REASONS = ("ADMISSIBLE", "IMMATURE", "UNOBSERVABLE", "CENSORED",
               "VERSION_MISMATCH", "NO_DENOMINATOR", "BACKFILL")


def _pct(v, nd=1):
    return "—" if v is None else f"{v * 100:.{nd}f}%"


def _num(v, nd=3):
    return "—" if v is None else f"{v:.{nd}f}"


def _verdict_row(name: str, threshold: str, actual: str, ok: bool | None) -> str:
    mark = "—" if ok is None else ("**PASS**" if ok else "**FAIL**")
    return f"| {name} | {threshold} | {actual} | {mark} |"


def render(scenario: str, horizon: int) -> str:
    meta, rows = load_fixture(scenario)
    reasons = Counter(classify(r, horizon=horizon) for r in rows)
    summary = summarise_admissibility(reasons)
    admissible = [observation_from(r, horizon=horizon) for r in rows
                  if classify(r, horizon=horizon) == ADMISSIBLE]

    L: list[str] = []
    add = L.append

    # ── Header ───────────────────────────────────────────────────────────────
    add(f"# {BANNER}")
    add("")
    add(f"> **{DISCLAIMER}**")
    add(">")
    add("> The synthetic dataset exists only to demonstrate and test the validation")
    add("> framework before the real cohort matures. When the real 30/60/90-day")
    add("> outcomes arrive, the same framework consumes them with no change to the")
    add("> validation methodology.")
    add("")
    add(f"## Recovery scorecard validation — {horizon}-day horizon (SIMULATED)")
    add("")
    add("| | |")
    add("|---|---|")
    add(f"| Cohort (predictions) | {meta['cohort']} — **real**, not re-scored |")
    add(f"| Outcomes | **synthetic**, scenario `{meta['scenario']}` |")
    add(f"| Scenario intent | {meta['headline']} |")
    add(f"| Random seed | `{meta['seed']}` (fixed — same input, same dataset) |")
    add(f"| Simulation version | `{meta['simulation_version']}` |")
    add(f"| Scorecard version | `{meta['scorecard_version']}` (unchanged by this run) |")
    add(f"| Loans in cohort | {meta['n']} |")
    add(f"| Bands as scored | HIGH {meta['bands'].get('HIGH')} · "
        f"MEDIUM {meta['bands'].get('MEDIUM')} · LOW {meta['bands'].get('LOW')} |")
    add(f"| Real labels due | {REAL_CHECKPOINTS.get(horizon, '?')} |")
    add("")

    # ── Data quality first ───────────────────────────────────────────────────
    add("## Data quality — exclusions before findings")
    add("")
    add("Every scored loan is accounted for. A loan excluded here contributes to no")
    add("number below it.")
    add("")
    add("| Reason | Loans | Meaning |")
    add("|---|---:|---|")
    why = {
        "ADMISSIBLE": "enters every metric below",
        "IMMATURE": "the labelling checkpoint has not reached this horizon yet",
        "UNOBSERVABLE": "no case for the whole window — the ledger cannot see a payment",
        "CENSORED": "bank action (write-off / settlement / recall / death), not borrower conduct",
        "VERSION_MISMATCH": "scored by a different scorecard version — pooling would average two scorecards",
        "NO_DENOMINATOR": "total_outstanding <= 0, so no rate is definable",
        "BACKFILL": "features leak the future; never admissible",
    }
    for reason in ALL_REASONS:
        add(f"| `{reason}` | {summary['counts'].get(reason, 0)} | {why[reason]} |")
    add(f"| **Admissible** | **{summary['admissible']}** | "
        f"{_pct(summary['admissible_share'])} of {summary['total']} |")
    add("")
    add("`UNOBSERVABLE` is the load-bearing one. A caseless loan is **not** recorded as")
    add("₹0 recovered: `Payment.case_id` is `NOT NULL`, so money reaches the ledger only")
    add("through a case. Recording zero would manufacture the very correlation this")
    add("validation tests for — caselessness also depresses the *score*, because")
    add("`PAYMENT_MOMENTUM` abstains with no payment path — and it would bias LOW hardest")
    add("(50 of the 115 caseless loans are LOW, against 26 HIGH).")
    add("")

    if not admissible:
        add("**Nothing admissible at this horizon — no metrics computed.**")
        return "\n".join(L) + "\n"

    per_band = Counter(o.band for o in admissible)
    thin = [b for b in BANDS if per_band[b] < MIN_ADMISSIBLE_PER_BAND]

    # ── Section 1 ────────────────────────────────────────────────────────────
    add("## Section 1 — Outcome coverage")
    add("")
    add("| Horizon | Synthetic observations available | Real labels due |")
    add("|---|---:|---|")
    for h in HORIZONS:
        n = sum(1 for r in rows if classify(r, horizon=h) == ADMISSIBLE)
        mark = " ← this report" if h == horizon else ""
        add(f"| {h} days | {n}{mark} | {REAL_CHECKPOINTS[h]} |")
    add("")
    if thin:
        add(f"⚠ **Insufficient sample:** {', '.join(thin)} is below the "
            f"{MIN_ADMISSIBLE_PER_BAND}-loan bar "
            f"({', '.join(f'{b}={per_band[b]}' for b in thin)}). Its band mean is")
        add("reported but must not be used to calibrate a level. Ordering evidence is")
        add("still usable. This is a **real** structural finding, not a simulation")
        add("artefact: the LOW band loses 50 of its 141 loans to caselessness before")
        add("any outcome exists.")
        add("")

    # ── Section 2 ────────────────────────────────────────────────────────────
    rk = ranking_metrics(admissible)
    add("## Section 2 — Ranking: does the order hold?")
    add("")
    add("Ranking is invariant to any monotone rescaling of the predictions. It asks")
    add("only whether loans rated higher actually recovered more — which is what the")
    add("HIGH/MEDIUM/LOW **label** needs to be right, because the label orders field work.")
    add("")
    add("| Band | n | Realised recovery rate | 95% CI |")
    add("|---|---:|---:|---|")
    for b in BANDS:
        s = rk["by_band"][b]
        ci = f"[{_pct(s['lo'])}, {_pct(s['hi'])}]" if s["mean"] is not None else "—"
        flag = " ⚠ thin" if s["n"] < MIN_ADMISSIBLE_PER_BAND else ""
        add(f"| {b}{flag} | {s['n']} | {_pct(s['mean'])} | {ci} |")
    add("")
    add(f"- Monotonic HIGH ≥ MEDIUM ≥ LOW: **{rk['monotonic']}**")
    add(f"- HIGH/LOW lift: **{_num(rk['high_low_lift'], 2)}** (≥ {LIFT_FLOOR} is meaningful separation)")
    add(f"- HIGH and LOW confidence intervals disjoint: **{rk['bands_separated']}**")
    add(f"- LOW loans above the HIGH median: **{_pct(rk['low_above_high_median'])}** "
        f"(< {_pct(OVERLAP_CEILING, 0)} is healthy)")
    add(f"- Spearman ρ: **{_num(rk['spearman'])}** (≥ {SPEARMAN_FLOOR} is a usable ordering)")
    add("")

    # ── Section 3 ────────────────────────────────────────────────────────────
    cal = calibration_metrics(admissible)
    add("## Section 3 — Calibration: are the levels right?")
    add("")
    add("**A different question from Section 2, and it fails independently.** Ranking")
    add("asks whether the *order* is right; calibration asks whether *38.8% means 38.8%*.")
    add("A scorecard can rank perfectly and be uniformly miscalibrated (the lever is the")
    add("intercept, `BASE_RATE`), or be calibrated on average and rank poorly (the lever")
    add("is the weights). The label needs ranking; the rupee figure needs calibration.")
    add("")
    add("| Segment | n | Predicted | Realised | Difference |")
    add("|---|---:|---:|---:|---:|")
    add(f"| **Overall** | {cal['n']} | {_pct(cal['mean_predicted'])} | "
        f"{_pct(cal['mean_realised'])} | {_pct(cal['bias'])} |")
    for b in BANDS:
        s = cal["by_band"].get(b, {})
        if not s.get("n"):
            add(f"| {b} | 0 | — | — | — |")
            continue
        add(f"| {b} | {s['n']} | {_pct(s['predicted'])} | {_pct(s['realised'])} | "
            f"{_pct(s['bias'])} |")
    add("")
    add(f"- Calibration-in-the-large (overall bias): **{_pct(cal['bias'])}** "
        f"(tolerance ±{_pct(BIAS_TOLERANCE, 0)})")
    add(f"- Calibration slope: **{_num(cal['slope'], 2)}** "
        "(1.00 perfect; < 1 = predictions over-spread)")
    add(f"- Bias spread across bands: **{_pct(cal['bias_spread'])}** — a roughly *constant*")
    add("  bias is an intercept problem; a band-dependent one is a spread problem.")
    add("")

    # ── Section 4 ────────────────────────────────────────────────────────────
    cap = top_k_capture(admissible)
    add("## Section 4 — Operational lift")
    add("")
    add("The metric that maps to actually reallocating agents. Random targeting captures")
    add("20% of the money in 20% of the loans; anything near that means the label does")
    add("not change where the team should go, whatever the correlations say.")
    add("")
    add("| | |")
    add("|---|---:|")
    add(f"| Top 20% by predicted money holds | **{_pct(cap['captured'])}** of realised recovery |")
    add(f"| Random 20% baseline | {_pct(cap['baseline'])} |")
    add(f"| Capture lift | **{_num(cap['lift'], 2)}×** (< {CAPTURE_LIFT_FLOOR} barely changes the work) |")
    add(f"| Loans in the top 20% | {cap.get('loans_in_top_k')} of {cap['n']} |")
    add("")

    # ── Section 5 ────────────────────────────────────────────────────────────
    sec = marginal_lift_by_security(admissible)
    add("## Section 5 — Collateral segmentation")
    add("")
    add("`SECURITY` is the largest single weight (±0.16) and the only binary factor, so")
    add("it dominates the score by construction. The question this section answers is")
    add("whether the label still orders loans **within** a collateral class — if the")
    add("ordering vanishes inside each group, the label is an expensive proxy for")
    add("`loan_type` and the honest thing is to say so.")
    add("")
    add("| Group | n | HIGH/LOW lift | Spearman ρ |")
    add("|---|---:|---:|---:|")
    for g, s in sec.items():
        add(f"| {g} | {s['n']} | {_num(s['high_low_lift'], 2)} | {_num(s['spearman'])} |")
    add("")

    # ── Section 6 ────────────────────────────────────────────────────────────
    fac = factor_residual_correlation(admissible)
    add("## Section 6 — Factor diagnostics")
    add("")
    add("**Diagnostics only.** A factor whose contribution tracks the scorecard's own")
    add("error is a candidate for investigation — with eight factors, some correlation")
    add("is expected by chance, and correlation is not causation. Nothing here licenses")
    add("a re-weighting.")
    add("")
    add("| Factor | n | Correlation with error | Mean points |")
    add("|---|---:|---:|---:|")
    for code, s in fac.items():
        add(f"| `{code}` | {s['n']} | {_num(s.get('correlation'))} | "
            f"{_num(s.get('mean_contribution'), 4)} |")
    add("")
    params = SCENARIOS.get(meta["scenario"], {})
    add(f"On synthetic data the strongest of these is **planted**: this scenario adds a")
    add(f"`security_effect` of {params.get('security_effect')} to the true recovery rate on")
    add("top of the ±0.16 the scorecard already scores, precisely so this diagnostic has")
    add("a known signal to surface. That `SECURITY` shows up is evidence the **diagnostic")
    add("works**, not a discovery about the book.")
    add("")

    # ── Section 7 ────────────────────────────────────────────────────────────
    d = diagnose(rk, cal, bias_tolerance=BIAS_TOLERANCE, spearman_floor=SPEARMAN_FLOOR)
    add("## Section 7 — Verdict")
    add("")
    add("| Metric | Threshold | Actual | Result |")
    add("|---|---|---:|---|")
    add(_verdict_row("Monotonic HIGH ≥ MEDIUM ≥ LOW", "True",
                     str(rk["monotonic"]), rk["monotonic"] is True))
    add(_verdict_row("HIGH/LOW lift", f"≥ {LIFT_FLOOR}",
                     _num(rk["high_low_lift"], 2),
                     None if rk["high_low_lift"] is None else rk["high_low_lift"] >= LIFT_FLOOR))
    add(_verdict_row("Spearman ρ", f"≥ {SPEARMAN_FLOOR}", _num(rk["spearman"]),
                     None if rk["spearman"] is None else rk["spearman"] >= SPEARMAN_FLOOR))
    # bool() rather than `is True`: bands_separated comes out of a numpy
    # comparison, so it is np.bool_ and `np.True_ is True` is False. That read as
    # a FAIL next to a printed "True", which is the worst kind of wrong in a
    # report someone signs off on.
    add(_verdict_row("HIGH/LOW CIs disjoint", "True", str(rk["bands_separated"]),
                     None if rk["bands_separated"] is None else bool(rk["bands_separated"])))
    add(_verdict_row("LOW above HIGH median", f"< {_pct(OVERLAP_CEILING, 0)}",
                     _pct(rk["low_above_high_median"]),
                     None if rk["low_above_high_median"] is None
                     else rk["low_above_high_median"] < OVERLAP_CEILING))
    add(_verdict_row("Calibration bias", f"within ±{_pct(BIAS_TOLERANCE, 0)}",
                     _pct(cal["bias"]),
                     None if cal["bias"] is None else abs(cal["bias"]) <= BIAS_TOLERANCE))
    add(_verdict_row("Bias spread across bands", f"≤ {_pct(BIAS_TOLERANCE, 0)}",
                     _pct(cal["bias_spread"]),
                     None if cal["bias_spread"] is None
                     else cal["bias_spread"] <= BIAS_TOLERANCE))
    add(_verdict_row("Top-20% capture lift", f"≥ {CAPTURE_LIFT_FLOOR}",
                     _num(cap["lift"], 2),
                     None if cap["lift"] is None else cap["lift"] >= CAPTURE_LIFT_FLOOR))
    add(_verdict_row("Admissible per band", f"≥ {MIN_ADMISSIBLE_PER_BAND}",
                     " · ".join(f"{b}={per_band[b]}" for b in BANDS), not thin))
    add("")
    add(f"- Framework verdict: **`{d['verdict']}`**")
    add(f"- Candidate lever: **{d['candidate_lever'] or '— none —'}**")
    add(f"- Action: **{d['action']}**")
    add("")
    add(f"### {BANNER}")
    add("")
    add("Synthetic validation demonstrates that the validation pipeline can distinguish")
    add("recovery ranking and calibration behaviour. **This is not evidence of production")
    add("scorecard performance.** The scorecard is not validated; the *validator* is.")
    add("")

    # ── Section 8 ────────────────────────────────────────────────────────────
    add("## Section 8 — Limitations")
    add("")
    add(f"1. **The real outcomes have not matured.** The cohort was scored "
        f"{meta['cohort']}; real labels for this horizon are due "
        f"{REAL_CHECKPOINTS.get(horizon, '?')}.")
    add("2. **The outcomes above are generated from assumptions**, not observed. The")
    add("   assumption model is documented in `assumptions.md` and fixed by seed")
    add(f"   `{meta['seed']}`. Change an assumption and these numbers change.")
    add("3. **A synthetic run cannot establish production accuracy.** It establishes that")
    add("   the pipeline computes, excludes, ranks, calibrates and concludes correctly.")
    add(f"4. **The `beta` assumption is doing visible work.** This scenario sets it to "
        f"{params.get('beta')} — the degree to which the true recovery rate tracks the")
    add("   scorecard's own prediction. At 1.0 the scorecard's spread is *assumed* correct,")
    add("   so a strong Section 2 result is that assumption made visible rather than a")
    add("   finding. `synthetic_weak_signal` is the contrasting case and it fails.")
    add("5. **Spread is squeezed at the extremes.** Non-zero rates are drawn from a Beta,")
    add("   whose unimodality floor overrides `sigma` as the mean approaches 0 or 1 — so")
    add("   the strongest and weakest loans are less dispersed than the parameter asks.")
    add("6. **The production gate remains closed.** `RECOVERY_WRITE_LABEL = False`.")
    add("7. **The production scorecard is unchanged** — no weight, band edge, `BASE_RATE`")
    add(f"   or version moved. `{EXPECTED_VERSION}` throughout.")
    add("8. **No synthetic row was written to any database.** The fixture is a file; this")
    add("   report opened no database connection.")
    add("")
    return "\n".join(L) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--scenario", default="synthetic_baseline", choices=sorted(SCENARIOS))
    ap.add_argument("--horizon", type=int, choices=HORIZONS, action="append")
    ap.add_argument("--suffix", default="", help="appended to the filename, for "
                                                "non-default scenarios")
    args = ap.parse_args()

    print(BANNER)
    for horizon in (args.horizon or list(HORIZONS)):
        text = render(args.scenario, horizon)
        path = FIXTURE_DIR / f"validation_{horizon}d{args.suffix}.md"
        path.write_text(text, encoding="utf-8")
        print(f"  wrote {path.name}  ({len(text.splitlines())} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
