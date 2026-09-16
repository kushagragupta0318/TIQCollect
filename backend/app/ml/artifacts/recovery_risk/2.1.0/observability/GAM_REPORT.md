# The GAM ladder — an interpretable nonlinear form on the frozen data, 2026-09-15

> **Correction, 2026-09-16 (production-readiness audit).** Every reference below to the
> interaction `latest_disposition x arrears_ratio` names the pair that was *declared*, not the
> one that was *fitted*. `HistGradientBoostingClassifier` reorders columns (categoricals first)
> when `categorical_features` is used, remaps `monotonic_cst` into that order and passes
> `interaction_cst` through unremapped — so the pair actually allowed was
> **`latest_disposition x last_commit_status`**. The model remained an exact GAM (a set of
> singleton constraints is permutation-invariant) and every metric here stands as measured;
> only the label was wrong. Re-fitted with the pair where it was declared: OOT Gini 0.5121,
> **KS 38.65** against the 38.84 reported here — the conclusion is unchanged. Fixed in
> `scripts/research/recovery_risk_obs/gam_common.py`, pinned by
> `tests/test_gam_interaction_constraint.py`. Full detail: `PRODUCTION_READINESS_AUDIT.md`.


**Brief.** Data and evaluation frozen (world `wd10`, its split, its OOT rows, the target, the DGP).
Replace the WOE + linear card with an interpretable additive model; test up to three justified
interactions; benchmark against a GBM ceiling; select on train + validation only; run OOT once per row.
Hard target: OOT KS ≥ 39 **and** Gini ≥ 0.50.

**Result.** The GAM recovers most of the card-to-GBM gap — **OOT KS 37.81 → 38.84, Gini 0.5014 → 0.5118**
(validation-selected EXP5, 15 features, one interaction) — and stays fully explainable (per-feature shape
tables, monotone where declared, exact per-borrower decomposition). **KS ≥ 39 is not reached**: the
GBM ceiling on the same frozen data is **39.01 (43-feature pool) / 39.28 (all 115)** under the same
validation-chosen stopping, and every one of those GBMs carries a CSI of 0.46 (the pool contains
`months_on_book` and `outstanding_to_sanction`, both shifted between train and OOT) that fails the
stability gate on its own. Gini ≥ 0.50: yes, on every GAM row. Nothing promoted; `champion.txt` reads 1.1.0.

Reproduce: `scripts/research/recovery_risk_obs/gam_ladder.py OUT` (ladder + benchmarks, ~50 min),
`gam_explain.py OUT exp5` (shapes, interaction table, decomposition). `gam/ladder.json`,
`gam/exp2_explain.log`, `gam/exp5_explain.log` beside this file.

## 1. The model form

Boosted shape functions: scikit-learn `HistGradientBoostingClassifier` with `interaction_cst` set
to singletons, so every tree splits on exactly one feature and the model is **exactly additive on the
logit** — `logit = b0 + Σ f_j(x_j)`; interactions are explicit allowed pairs (depth-2 trees on that
pair only). Monotone constraints from the spec's declared signs; 32 knots per feature (`max_bins`);
≥ 300 rows per leaf; L2 = 1; learning rate 0.05; the number of trees chosen on **validation KS**.
Missing values are routed by each shape function to a learned side and that value is reported.
Categoricals are native. No neural network, no unrestricted trees in the candidate model.

## 2. The ladder (train / validation for selection; OOT once per row)

| Model | Features | Interactions | train G / KS | **valid G / KS** | **OOT Gini** | AUC | **OOT KS** | Brier | cal gap | IV range | max VIF | PSI | max CSI | lift | breaks | min seg |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| EXP0 WOE-LR baseline | 10 | 0 | 0.498 / 36.96 | 0.523 / 39.04 | 0.5014 | 0.7507 | 37.81 | 0.1817 | −0.017 | 0.023–0.519 | 4.30 | 0.014 | 0.069 | 1.39 | 0 | 0.333 |
| EXP1 GAM, 10 features, additive | 10 | 0 | 0.505 / 37.49 | 0.530 / 39.51 | 0.5088 | 0.7544 | 38.40 | 0.1803 | −0.015 | 0.023–0.519 | 4.30 | 0.011 | 0.069 | 1.40 | 0 | 0.344 |
| EXP2 GAM + `latest_disposition × arrears_ratio` | 10 | 1 | 0.506 / 37.57 | 0.531 / 39.72 | 0.5095 | 0.7547 | 38.46 | 0.1803 | −0.015 | 0.023–0.519 | 4.30 | 0.011 | 0.069 | 1.40 | 0 | 0.345 |
| EXP3 GAM + up to 3 (none more qualified) | 10 | 1 | = EXP2 | = EXP2 | 0.5095 | 0.7547 | 38.46 | 0.1803 | −0.015 | 0.023–0.519 | 4.30 | 0.011 | 0.069 | 1.40 | 0 | 0.345 |
| EXP4 GAM, 43-feature VIF pool | 43 | 0 | 0.506 / 37.41 | 0.529 / 39.78 | 0.5094 | 0.7547 | 38.64 | 0.1803 | −0.014 | 0.023–0.519 | 4.30 | 0.011 | **0.457** | 1.40 | 0 | 0.348 |
| EXP4b GAM, pool + interaction | 44 | 1 | 0.508 / 37.69 | 0.530 / 40.01 | 0.5104 | 0.7552 | 38.80 | 0.1801 | −0.015 | 0.023–0.519 | 4.30 | 0.011 | **0.457** | 1.40 | 0 | 0.346 |
| **EXP5 Best GAM (validation-selected)** | **15** | **1** | 0.512 / 37.92 | **0.535 / 40.66** | **0.5118** | 0.7559 | **38.84** | 0.1798 | −0.014 | 0.007–0.519 | 4.30 | 0.011 | 0.069 | 1.40 | 0 | 0.351 |
| GBM benchmark, 10 features | 10 | free | 0.522 / 38.55 | 0.534 / 39.93 | 0.5125 | 0.7563 | 38.39 | 0.1793 | −0.014 | 0.023–0.519 | 4.30 | 0.009 | 0.069 | 1.40 | 0 | 0.346 |
| GBM benchmark, VIF pool | 43 | free | 0.545 / 40.23 | 0.537 / 40.18 | 0.5180 | 0.7590 | 39.01 | 0.1786 | −0.015 | 0.023–0.519 | 4.30 | 0.008 | **0.457** | 1.40 | 0 | 0.361 |
| GBM benchmark, all candidates | 115 | free | 0.557 / 41.24 | 0.541 / 40.85 | 0.5246 | 0.7623 | 39.28 | 0.1775 | −0.018 | 0.000–0.519 | 4.30 | 0.007 | **0.457** | 1.40 | 0 | 0.375 |

VIF is the artifact's WOE-space figure for the same features (not defined for a GAM's shape functions;
shown "where applicable"). IV is the artifact's univariate IV of the inputs. PSI is the score PSI
train → OOT; CSI the maximum feature PSI among the inputs. The 0.457 rows are the pool's own
`months_on_book` (0.52) and `outstanding_to_sanction` (0.35) — a young book seasoning — and disqualify
those rows under the CSI < 0.10 gate regardless of KS.

**Interaction trials (validation, from EXP1):** `latest_disposition × arrears_ratio` +0.21 KS,
`× dpd` +0.17, `× days_since_disposition` −0.06. Only the first cleared the +0.05 admission rule; adding
either of the others to it did not improve validation KS (39.68, 39.67 vs 39.72). One interaction.

**EXP5 selection (validation KS, greedy from EXP2 over the 105 remaining candidates):**
`ptp_amount_to_emi` +0.47 → `employment_type` +0.09 → `days_since_last_contact` +0.07 →
`disposition_recency_class` +0.17 → `interest_rate` +0.13; validation 39.72 → 40.66. Next candidates
(`days_since_last_payment`, `days_since_commit_kept`, `disposition_7d`, `disposition_count_30d`) did not
add ≥ 0.05 once the earlier ones were in.

## 3. The decomposition the brief asked for (Section 9)

Where the missing KS comes from, on the identical OOT rows:

| stage | OOT KS | Δ | OOT Gini | Δ |
|---|---|---|---|---|
| WOE-LR, 10 features | 37.81 | — | 0.5014 | — |
| → GAM, same 10 features, additive | 38.40 | **+0.59** | 0.5088 | +0.0074 |
| → + one interaction | 38.46 | +0.06 | 0.5095 | +0.0007 |
| → broader pool, validation-selected (15) | 38.84 | +0.38 | 0.5118 | +0.0023 |
| → GBM, same 10 inputs | 38.39 | (−0.45 vs EXP5; +0.58 vs card) | 0.5125 | |
| → GBM, 43-feature pool | 39.01 | +0.17 vs EXP5 | 0.5180 | +0.006 |
| → GBM, all 115 | 39.28 | +0.44 vs EXP5 | 0.5246 | +0.013 |

- **Functional form (shape functions) is the largest single recoverable piece: +0.6 KS.** The card's
  six-bin WOE flattens `arrears_ratio` (range 2.3 logits in the GAM, a smooth ramp from −1.04 to +1.28)
  and `overdue_amount` (1.1 logits); the GAM keeps the curvature. On the same ten inputs the GAM
  (38.40) already matches the unrestricted GBM (38.39) — **there is no interaction information left in
  those ten that a GBM finds and the GAM does not.**
- **Interactions are worth almost nothing: +0.06 KS.** The disposition × arrears table (`exp2_explain.log`)
  is close to parallel: WILL_PAY sits ~1.0 logit below REFUSES at every arrears level. The stance
  shifts risk by about the same amount whether the account is one or five instalments behind.
- **Omitted features: +0.4 KS** (EXP5), of which `ptp_amount_to_emi` alone is +0.47 on validation, and the
  freshness form `disposition_recency_class` +0.17 — the GAM uses the class × age form the card could
  not (WILL_PAY_FRESH −0.51 vs WILL_PAY_STALE −0.15: a fresh positive stance is worth three stale ones).
- **The remaining 0.2–0.4 KS to the GBM ceiling lives in features that fail the stability gate** (the
  pool GBM's extra over EXP5 comes with CSI 0.46) and in unrestricted 15-leaf trees over 115 inputs.
  It is not recoverable by an interpretable, stable model on this data.

The validation → OOT drop (~1.7 KS on every row, GBM included) is a property of the frozen world's
OOT months, not of any model: it is the same on the WOE card, the GAM and the GBM.

## 4. Interpretability (Section 7) — EXP5, `gam/exp5_explain.log`

Exact additive decomposition: `logit = +0.875 (train mean) + Σ f_j(x_j) + g(latest_disposition, arrears_ratio)`.

| feature | declared sign | shape | contribution range (logits) | monotone | missing → |
|---|---|---|---|---|---|
| arrears_ratio | + | ascending, −1.04 at 0.34 → +1.28 at 7.4 | 2.32 | ✓ | never missing |
| overdue_amount | + | ascending, −0.59 → +0.52 | 1.11 | ✓ | never |
| cibil_score | − | descending, +0.44 at 315 → −0.37 at 863 | 0.81 | ✓ | never |
| disposition_recency_class | cat | WILL_PAY_FRESH −0.51 · MAY_PAY_FRESH −0.13 · NONE −0.01 · NO_COMMITMENT_FRESH +0.10 · HARDSHIP_STALE +0.27 · REFUSES_FRESH +0.44 · REFUSES_STALE +0.46 | 0.97 | — | "NONE" is a level |
| calls_3m | 0 | ascending, −0.34 at 0 → +0.38 at 18 (collections intensity follows delinquency) | 0.73 | ✓ | never |
| ptp_amount_to_emi | − | descending, +0.12 below 0.9 × EMI → −0.45 above 1.3 × EMI | 0.58 | ✓ | +0.02 (no promise) |
| paid_ratio_3m | − | descending, +0.10 at 0 → −0.34 at 1.33 | 0.44 | ✓ | never |
| recent_ptp_status | cat | OPEN −0.20 · HONORED 0.00 · NONE 0.00 · BROKEN +0.10 · RESCHEDULED +0.10 | 0.30 | — | level |
| days_since_last_contact | 0 | −0.21 at 1 day → +0.07 at 149 (no constraint declared) | 0.28 | unconstrained | +0.02 (never met) |
| intent_calls_3m | − | descending, +0.10 at 0 → −0.12 at 3+ | 0.21 | ✓ | never |
| last_commit_status | cat | OPEN −0.14 · KEPT −0.01 · BROKEN +0.03 · NONE +0.05 | 0.19 | — | level |
| latest_disposition | cat | WILL_PAY −0.07 … REFUSES +0.09 (main effect small — the recency class carries it) | 0.16 | — | level |
| no_answer_streak | + | ascending, −0.02 → +0.09 | 0.11 | ✓ | never |
| employment_type | cat | SALARIED −0.05 · BUSINESS_OWNER +0.04 · SELF_EMPLOYED +0.05 | 0.10 | — | level |
| interest_rate | + | ascending, −0.03 → +0.04 | 0.06 | ✓ | never |

Every declared-sign feature is monotone in its declared direction. The interaction table
(`latest_disposition × arrears_ratio`) is printed as a 7 × 5 grid of joint logit contributions. A
borrower's score is the intercept plus fifteen readable numbers — e.g. train row 47077: logit +0.79,
P(no material payment) 0.69 = +0.875 − 0.22 (arrears 2.0) + 0.18 (CIBIL 410) − 0.13 (promised 1.18 × EMI)
− 0.12 (4 calls) + 0.11 (DISPUTE, stale) + 0.10 (no intent) … — "why this score" is answerable line by line.

Three inputs are below the preferred IV band: `intent_calls_3m` 0.023, `interest_rate` 0.008,
`employment_type` 0.013 (univariate IV; each earned its place on validation KS, +0.13 to +0.47).

## 5. Answers

1. **KS ≥ 39?** **No.** Best interpretable: **38.84** (EXP5); best stable GBM: 38.39 (10 inputs); the
   only rows above 39 are GBMs over pools whose CSI (0.46) fails the stability gate.
2. **Gini ≥ 0.50?** Yes — every GAM row (0.509–0.512), the card (0.5014), every GBM.
3. **Smallest change with the largest effect:** replacing the six-bin WOE + linear form with additive
   shape functions on the *same ten inputs*: +0.59 KS, +0.007 Gini, nothing else changed. It closes
   half the card-to-ceiling KS gap and all of the same-input gap (GAM 38.40 vs GBM 38.39).
4. **What recovered the KS:** shape curvature on `arrears_ratio` / `overdue_amount` / `cibil_score`
   (+0.6); then `ptp_amount_to_emi` and the freshness form of the disposition (+0.4 across EXP5's five
   additions); the one interaction +0.06.
5. **Still interpretable?** Yes: exactly additive on the logit plus one two-way term, monotone where
   declared, per-feature shape tables, explicit missing routing, exact per-borrower decomposition.
6. **Quality gates:** EXP5 — AUC 0.756 ✓, VIF 4.30 ✓, score PSI 0.011 ✓, CSI 0.069 ✓, 0 rank-order
   breaks ✓, min segment Gini 0.35 ✓, calibration gap −0.014 (uncalibrated model; the pipeline's segment
   calibrator would sit on top) ✓, train 0.512 ≤ OOT 0.512 ✓, 15 features (≤ 12 preferred: **over**),
   IV: 3 of 15 below 0.10 (**noted**), **KS 38.84 < 39: FAIL**.
7. **Recommended production model:** none is promotable against the stated hard target. If the
   business accepts Gini 0.51 / KS 38.8, the recommendation is **EXP2** (10 features, one interaction,
   0.5095 / 38.46) or **EXP5** (15 features, 0.5118 / 38.84) as a GAM — a new production form
   (shape-function serving, a new artifact type, a new explanation surface) rather than a points card.
   The WOE card 2.1.0-class model remains the only form the product can serve today.
8. **Exact remaining gap to the GBM ceiling:** EXP5 → GBM-pool 0.17 KS / 0.006 Gini; → GBM-all 0.44 KS /
   0.013 Gini. Same-input gap: 0 KS (38.40 vs 38.39). The residual sits in unstable features (CSI 0.46)
   and unrestricted depth over 115 inputs — neither admissible.

**Bottom line.** On this frozen data the KS 39 line is above what any stable, interpretable model
reaches: 38.84 with a GAM, against a ceiling of 39.0–39.3 that only unstable, opaque GBMs touch. The
functional-form hypothesis was right and is now measured — it was worth 0.6 of the 1.5 KS gap — and
what it does not buy is not available without changing either the data (forbidden) or the stability
bar (not proposed).
