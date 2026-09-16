# recovery_risk 2.1.0 — fresh development on the observable ledger world

**Date** 2026-09-15 · **World** `data/ledger/full`, fingerprint `6588454cc7f0e259`,
94,149 account-months, 24 months, realism 17/17 PASS · **Split** months 0–13 train /
14–17 valid / 18–23 OOT (24,161 rows, bad rate 0.7003) — the same chronological
split every recovery_risk version uses · **Not promoted** — `champion.txt` reads 1.1.0.

Every figure below is read from this directory (`metadata.json`, `V21_REPORT.json`,
`evaluation/*.csv`, `discovery/*`), not recomputed for the report.

---

## 0. Verdict in one paragraph

The best legitimate model on this world is a nine-feature WOE scorecard at
**OOT Gini 0.4698 (95% CI 0.456–0.481), KS 35.22, AUC 0.7349**, calibrated to
|gap| 0.012, VIF ≤ 4.1, every feature PSI/CSI ≤ 0.03, zero rank-order breaks,
better than the deployed 1.1.0 on identical rows in every DPD bucket and every
OOT month. **It does not reach the business target of Gini 0.50–0.55 / KS 39–42.**
No model family does: a gradient-boosted model over every one of the ~100
candidates tops out at **0.492 / 36.3**, which is the observable-information ceiling
of this world. The gap is not a modelling gap. The missing information is the
borrower's *current willingness*: given the observables, perfect knowledge of
that one latent alone lifts Gini to 0.551 / KS 42.1 — inside the target band — while
capacity, reachability and the shock state together add less than 0.02. Every
observable channel through which willingness reaches the record (refusals, promises
and their endings, stated intent, payment regularity) is already simulated and
already in the candidate set. Section 22 quantifies this.

---

## 1–4. Best model, family, features, count

| | |
|---|---|
| **Best model** | `recovery_risk` **2.1.0** (this artifact, `artifact_sha256` in `metadata.json`) |
| **Family** | WOE binning + logistic regression scorecard, PDO-scaled, segment-calibrated on `overdue_amount` (Platt, 5 segments) — same form as 1.1.0 and 2.0.0 |
| **Feature count** | **9** of 59 candidates (25 survived IV/correlation/VIF; SFS reached 10; the sign check removed 1) |

Selected features, in SFS order, with IV, drop-one Gini loss and where the adapter reads them:

| # | feature | family | IV | drop-one ΔGini | source (production) |
|---|---|---|---|---|---|
| 1 | `arrears_ratio` | delinquency | 0.498 | −0.0146 | `Loan.overdue_amount / emi_amount` |
| 2 | `cibil_score` | bureau | 0.178 | −0.0205 | `Customer.cibil_score` |
| 3 | `paid_ratio_3m` | payment | 0.189 | −0.0062 | VERIFIED `Payment` rows in [as_of−90d, as_of) / 3×EMI |
| 4 | `overdue_amount` | delinquency | 0.342 | −0.0060 | `Loan.overdue_amount` |
| 5 | `last_visit_outcome` ★ | visit | 0.083 | −0.0022 | `Visit.outcome` of the newest visit before as_of |
| 6 | `intent_rate_6m` ★ | call | 0.104 | −0.0039 | `CallLog.payment_intent_signalled` / answered calls, 6m |
| 7 | `call_answer_rate_3m` ★ | call | 0.098 | −0.0020 | answered / attempted `CallLog`, 3m |
| 8 | `pay_amount_cv_6m` ★ | payment | 0.089 | −0.0008 | population CV of VERIFIED `Payment.amount`, 6m |
| 9 | `visits_3m` | visit | 0.122 | −0.0006 | `Visit` count, 3m |

★ = new in 2.1.0. Full bins, WoE and points: `scorecard.csv`; selection log: `evaluation/selection_log.csv`, `sfs_path.csv`, `sign_dropped.csv`.

**Two directions the business would not guess, and why they are kept.**
`visits_3m` reads "more visits → more risk" and `pay_amount_cv_6m` reads "more
varied payment sizes → *less* risk". The first is collections policy in the record:
attempts follow delinquency, so a heavily visited account is a delinquent one. The
second is a property of active payers: a borrower with two or more payments of
different sizes in six months is one who sometimes clears arrears, and the 63% of
rows with fewer than two payments sit in the Missing bin at the highest event rate
(0.739). Both are declared with sign 0 in the spec, both bins are monotone, and both
coefficients passed the WOE sign check. `days_since_last_contact` — the same policy
shape — entered at SFS step 10 and was **removed by the sign check** (coefficient
+0.77): recorded in `sign_dropped.csv`, not overridden.

---

## 5–13. Out-of-time metrics

| metric | 2.1.0 (OOT, n=24,161) | acceptance band | result |
|---|---|---|---|
| **AUC** | **0.7349** | ~0.75–0.775 | below |
| **Gini** | **0.4698** · bootstrap 300× mean 0.4697, sd 0.0067, **CI 0.4564–0.4808** | 0.50–0.55 | **below** (CI upper < 0.50) |
| Gini, uncalibrated | 0.4707 | | |
| **KS** | **35.22** (cutoff decile 5) | 39–42 | **below** |
| Brier | 0.18004 | | |
| Calibration gap (mean pred − observed) | **−0.0123**; worst band −0.028 (`calibration_oot.csv`) | \|gap\| < 0.03 | pass |
| Train / valid / OOT Gini | 0.4554 / 0.4563 / 0.4698 (OOT higher: no overfit) | gap < 0.10 | pass |
| Rank-order breaks | 0 (top-5: 0); top-decile bad rate 0.933, bottom 0.387, lift 1.33× of max 1.43× | ≤1 / 0 | pass |
| **IV** of selected | 0.083 – 0.498; 6 of 9 in the preferred 0.10–0.50; 3 in 0.08–0.10 (above the 0.02 floor); none above the 0.50 review line | 0.10–0.50 preferred | pass (3 below preferred, noted) |
| **VIF** (kept pool, 25) | max **4.097** (`paid_ratio_3m`); selected max 4.097 | < 5 | pass |
| **PSI** score, train→OOT | **0.0085** | < 0.10 | pass |
| **CSI** per selected feature | max **0.0414** (`last_visit_outcome`, categorical PSI); numeric max 0.0297 (`visits_3m`) | < 0.10 | pass |
| WOE | every selected feature monotone in its bins (`eda/information_value.csv: monotonic=True` ×9) | sensible | pass |

Adversarial validation dev-vs-OOT AUC 0.728 ("different populations"): the book's
origination mix walks by design (covariate drift, always on) and behavioural history
accumulates; the selected features are nonetheless stable (all CSI ≤ 0.04) and OOT
Gini exceeds train.

---

## 14. Feature-by-feature and family-by-family contribution

**Forward selection on validation** (`evaluation/sfs_path.csv`):

| step | added | valid Gini | gain |
|---|---|---|---|
| 1 | arrears_ratio | 0.3835 | — |
| 2 | cibil_score | 0.4298 | +0.0463 |
| 3 | paid_ratio_3m | 0.4402 | +0.0104 |
| 4 | overdue_amount | 0.4449 | +0.0046 |
| 5 | last_visit_outcome | 0.4490 | +0.0041 |
| 6 | intent_rate_6m | 0.4507 | +0.0017 |
| 7 | call_answer_rate_3m | 0.4529 | +0.0022 |
| 8 | pay_amount_cv_6m | 0.4543 | +0.0014 |
| 9 | visits_3m | 0.4553 | +0.0010 |
| 10 | days_since_last_contact | 0.4561 | +0.0008 → removed by sign check |
| — | adverse_visit_ratio_6m | 0.4569 | +0.0008 < stop rule |

**Family contribution** (`discovery/families.json`, WOE+LR re-selected without each
family on the ~100-candidate pool; valid / OOT Gini):

| family | candidates | strongest IV | Gini without the family (valid / OOT) | Δ vs all (valid) | adds new information? |
|---|---|---|---|---|---|
| all families | 99 | — | 0.4542 / 0.4655 | — | |
| delinquency | 9 | 0.512 (`arrears_ratio`) | 0.4385 / 0.4564 | −0.016 | yes — the spine |
| static / bureau | 11 | 0.183 (`cibil_score`) | 0.4291 / 0.4439 | −0.025 | yes — the only static signal that matters |
| payment | 22 | 0.228 (`paid_to_overdue_6m`) | 0.4453 / 0.4554 | −0.009 | yes |
| visit | 18 | 0.130 (`visits_3m`) | 0.4508 / 0.4649 | −0.003 | marginal |
| call | 15 | 0.168 (`calls_3m`) | 0.4535 / 0.4623 | −0.001 | marginal (intensity is a delinquency proxy; answer/intent rates carry the new part) |
| PTP | 18 | 0.122 (`ptp_amount_to_overdue`) | 0.4542 / 0.4655 | 0.000 | no — absorbed by payment + visit outcome |
| hardship | 4 | 0.021 | 0.4542 / 0.4655 | 0.000 | no (IV ≤ 0.02) |
| flags | 2 | 0.057 (`is_hostile`) | 0.4542 / 0.4655 | 0.000 | no — `fraud_flag` IV 0.000 (it drives disputes, not payment) |
| cross-channel | 4 | 0.151 (`attempts_6m`) | 0.4542 / 0.4655 | 0.000 | no |

Per-candidate IV, WOE-Gini, missingness and monthly stability for all 99:
`discovery/univariate_dev.csv`. Probes that found nothing: a live promise at as_of
(4.4% of rows, bad rate 0.59 vs 0.70 — real but too rare to bin), calendar month
(+0.001 on the GBM; seasonality is an intercept shift), 12-month PTP kept ratio,
PTP-to-payment conversion (identical to kept rate by construction).

---

## 15. Model-family comparison

All on the same rows; selection on train/valid only; OOT scored once each
(`discovery/families.log`).

| family | features | valid Gini / KS | **OOT Gini / KS** | Brier | cal. gap | interpretable | production-feasible |
|---|---|---|---|---|---|---|---|
| **A. WOE + LR scorecard — this artifact** | 9 | 0.4563 / 34.58 | **0.4698 / 35.22** | 0.1800 | −0.012 | yes (points per bin) | yes, served today |
| A. WOE + LR, research SFS set | 10 | 0.4542 / 34.54 | 0.4655 / 34.79 | 0.1807 | −0.008 | yes | yes |
| B. L1-regularised LR on WOE (C by valid) | 10 | 0.4542 / 34.53 | 0.4655 / 34.78 | 0.1807 | −0.008 | yes | yes — no shrinkage chosen: identical to A |
| B′. L1-LR over the whole VIF pool | 29 non-zero | 0.4541 / 34.47 | 0.4718 / 35.61 | 0.1797 | −0.011 | weak (29 terms) | yes |
| C. GBM (HistGB), pipeline challenger | 9 | — | 0.4732 / 35.38 | 0.1793 | −0.013 | no | yes |
| C. GBM, research set | 10 | 0.4609 / 34.69 | 0.4761 / 35.62 | 0.1783 | −0.011 | no | yes |
| C′. GBM over the VIF pool | 36 | 0.4640 / 35.14 | 0.4823 / 36.04 | 0.1775 | −0.014 | no | costly (36 inputs) |
| **C″. GBM over every candidate — the ceiling** | 99 | 0.4732 / — | **0.4920 / 36.29** | — | — | no | no (99 inputs, half unserved) |
| D. Monotone-constrained GBM | 10 | 0.4628 / 35.03 | 0.4750 / 35.36 | 0.1786 | −0.011 | partial | yes |

**Choice: the scorecard.** The GBM on the same nine inputs adds +0.0034 Gini —
under the repo's own 0.05 challenger margin and under the comparison module's
tolerance (0.018 at this n) — for a model that cannot print its points per bin.
The ceiling model (C″) adds +0.022 for 99 inputs, and *still* misses the target by
0.008 Gini and 2.7 KS points. No family is within reach of 0.50 / 39; the choice
between them is about interpretability, not about the target.

---

## 16. PIT / leakage audit

- **Boundary.** Every feature reads events with `day < as_of` and window
  `[as_of − W, as_of)`; the label reads `(as_of, as_of + 30]`. Written once in
  `panel._before` / `_window`, never restated. The adapter floors `as_of` to
  midnight so "before as_of" means "before the day began" on both sides.
- **Executable, per feature** (`tests/test_recovery_risk_v21_pit.py`, 23 tests): for
  each of the six new features an event at `t−1` is counted, at `t` excluded, at `t+1`
  excluded; a reversal effective after `as_of` is invisible; the six-month and
  thirty-day window edges are exact; an outcome-window payment moves nothing; same
  input → same output. The 2.0.0 and 1.x features carry the same tests from earlier
  sessions.
- **Whole-panel probes** (`V21_REPORT.json → leakage`): shuffled labels Gini
  **−0.010** (structure reaches the model only through features); features from
  month m+1 Gini **0.958** (+0.469) — the as_of boundary is doing all of the work.
- **Latents never reach the panel** (`test_no_latent_reaches_the_panel`); forbidden
  columns absent (`test_every_forbidden_column_the_spec_names_is_absent`).
- **Definitions in words** for the six: `app/ml/pipeline/config.py` at
  `RECOVERY_RISK_V21` (formula, source table and timestamp column, window,
  numerator, denominator, zero-denominator and missing behaviour).

## 17. Production parity audit

- `MLScoringService.build_features` computes all 59 candidates from the live schema
  (`Loan`, `Customer`, `Visit`, `CallLog`, `PTP`, `Payment`); the six new ones were
  added in `_history_features` with the same arithmetic as `panel.py`.
- **Phase 3 equality harness** (`tests/test_ledger_phase3_adapter_equality.py`, 78
  tests): the ledger is materialised into the real schema, rewound to five snapshot
  days, and the adapter's vector compared with the panel's on 1,180+ (loan, as_of)
  pairs — exact for counts/categoricals, 0.01 on money, 0.002 on ratios. All 59
  candidates are held equal; `test_every_v21_candidate_is_covered_by_one_of_the_groups`
  fails if one drops out. Abstention agrees on both sides (NaN ↔ None; "NONE" is a
  category, not a missing value; `paid_ratio_1m` is 0.0 when nothing was paid).
- Every selected feature is a key the adapter always emits (`LOGGED_FEATURES`), so a
  missing feature is a coverage-floor decline, not a silent Missing bin.
- One product-only case documented: a live `Visit.outcome` the ledger never emits
  (PAID_FULL, PART_PAID, …) maps to the binner's neutral unknown bin (WoE 0);
  `paid_ratio_1m`/`_3m` carry the payment itself.
- Reproducibility: the retrain reproduced the artifact bit-for-bit (same selection,
  same 0.4698 / 35.22).

## 18. Stability

Identical OOT rows, production scoring path (`V21_REPORT.json → stability`):

| OOT month | n | bad rate | 1.1.0 | 2.0.0 | **2.1.0** |
|---|---|---|---|---|---|
| 18 | 4,136 | 0.669 | 0.3889 | 0.4444 | **0.4504** |
| 19 | 3,982 | 0.698 | 0.4028 | 0.4701 | **0.4776** |
| 20 | 3,938 | 0.717 | 0.4226 | 0.4721 | **0.4727** |
| 21 | 4,034 | 0.717 | 0.4015 | 0.4728 | **0.4742** |
| 22 | 4,085 | 0.708 | 0.4232 | 0.4842 | **0.4804** |
| 23 | 3,986 | 0.694 | 0.3989 | 0.4580 | **0.4635** |
| sd | | | 0.0125 | 0.0126 | **0.0101** |

| DPD bucket | n | bad rate | 1.1.0 | 2.0.0 | **2.1.0** | KS 2.1.0 |
|---|---|---|---|---|---|---|
| CURRENT | 1,163 | 0.525 | 0.2023 | 0.2695 | **0.2596** | 19.1 |
| BUCKET_1 | 6,415 | 0.538 | 0.2141 | 0.3059 | **0.3004** | 21.9 |
| BUCKET_2 | 5,113 | 0.640 | 0.1628 | 0.2703 | **0.2870** | 21.6 |
| BUCKET_3 | 3,951 | 0.756 | 0.1085 | 0.2660 | **0.2750** | 21.3 |
| NPA | 7,519 | 0.878 | 0.1857 | 0.3102 | **0.3219** | 24.9 |

Within-bucket Gini 0.26–0.32 everywhere (gate ≥ 0.20): the model is not DPD wearing
a scorecard. Check 8 diagnostic Gini(dpd)/Gini(full) = 0.84 — above the 0.75
guideline for the structural reason recorded in Phase 2: `arrears_ratio` and `dpd`
are the same FIFO position (r = 0.88), so the ratio is not dominance. Loan type
0.41–0.50, city 0.46–0.49 (`evaluation/segment_performance.csv`). Score-band table:
`evaluation/decile_oot.csv` (monotone, bad rate 0.933 → 0.387).

## 19. Comparison with 1.1.0 and 2.0.0 — identical 24,161 OOT rows

| | 1.1.0 (deployed) | 2.0.0 | **2.1.0** |
|---|---|---|---|
| features | 4 | 9 | **9** |
| Gini | 0.4066 | 0.4669 | **0.4698** |
| KS | 29.93 | 34.60 | **35.22** |
| AUC | 0.7033 | 0.7334 | **0.7349** |
| Brier | 0.1899 | 0.1804 | **0.1800** |
| calibration gap | +0.0475 | −0.0126 | **−0.0123** |
| rank-order breaks | 0 | 0 | 0 |
| verdict vs 2.1.0 (`comparison.py`) | uplift +0.0633 > tol 0.0193 → **challenger_better**, all five gates PASS | uplift +0.0030 ≤ tol 0.0184 → **equivalent_keep_incumbent** | |

2.1.0 beats 1.1.0 in every bucket (+0.057 to +0.167) and every month. Against 2.0.0
it is inside sampling error — recorded as such, not hidden. What the fresh
development changed relative to 2.0.0 is the *composition*: a cleaner nine (recent
intent and answer rate, the last visit's outcome, payment-size regularity) instead of
`payment_gap_cv_12m` (71% missing), `adverse_visit_ratio_6m` and `contact_rate_6m`,
with the policy-shaped recency feature removed by the sign check rather than
carried. The 1.x spec re-fitted on this same panel (`2.0.0-ref-v1spec`) reaches
0.4626 / 35.18 with 7 features, so the new channels are worth ≈ +0.007 Gini to a
scorecard on this world. The no-degrade table (`V21_REPORT.json → no_degrade`) has
no FAIL row.

Reference points, unchanged: 1.1.0 on its own book_simulator world 0.5136 / 39.72;
1.1.0 on this ledger 0.4066 / 29.93; 2.0.0 on this ledger 0.4669 / 34.60.

## 20–21. Was the target reached?

| | target | achieved | gap |
|---|---|---|---|
| Gini | ≥ 0.50 (0.50–0.55) | 0.4698 (CI 0.456–0.481) | **−0.030** (CI upper −0.019) |
| KS | ≥ 39 (39–42) | 35.22 | **−3.78** |
| AUC | 0.75–0.775 | 0.7349 | −0.015 |

**No.** Neither, by any family. The target was not lowered and the evaluation
was not moved.

## 22. Information-gap analysis

Measured on this world with the DGP frozen (`discovery/gap.log`,
`V21_REPORT.json → oracle`). Every model below is a GBM fitted on train,
early-stopped on valid, scored on the same OOT rows.

| information set | OOT Gini | KS |
|---|---|---|
| 2.1.0 scorecard (9 features) | 0.4698 | 35.22 |
| GBM, all 99 observable candidates — **observable ceiling** | **0.4920** | 36.29 |
| observables + calendar month | 0.4933 | 36.51 |
| observables + true **shock state** | 0.4933 | 36.61 |
| observables + true **reachability** | 0.4992 | 36.97 |
| observables + true **capacity** | 0.5031 | 37.33 |
| observables + true **willingness** | **0.5506** | **42.09** |
| observables + willingness + capacity | 0.5663 | 42.88 |
| observables + all four latents (joint oracle) | 0.5777 | 43.61 |
| latents alone (LR, no observables) | 0.5219 | 38.96 |

**What is missing, precisely.** The gap from the best legitimate model (0.470) to the
target floor (0.500) is 0.030; the gap to the observable ceiling is 0.022; the gap
from the ceiling to the joint oracle is 0.085. Of that 0.085, **willingness alone
accounts for 0.059 (69%)** and on its own carries the model to 0.551 / 42.1 — the
target band. Capacity adds 0.011, reachability 0.007, the shock state 0.001. So the
information that would reach the target is the borrower's *current disposition to
pay*, and the observables see it only through sparse, noisy channels: a refusal at
the door (5% of visits), a stated intent on an answered call (answer rate 45%,
intent share 40%), a promise made or broken (22k promises over 18k loans), and the
regularity of the payment ledger. Willingness has 0.635 monthly persistence in
this world (daily AR(1) ρ = 0.985), so its past readings decay; the channel the
product does not have is a *fresh* reading at scoring time.

**Why it is not closed here.** Every product column that carries willingness is
already an event channel in the ledger and a candidate in the spec. The columns that
remain unsimulated (`Visit.business_running`, `not_met_reason`, `person_met`,
`CallLog.duration_seconds`, `verbal_payment_date`, `PTP.actual_paid_amount`,
`Customer.complaints_raised`) read reachability or capacity — the two latents worth
< 0.02 together — or would be another reading of willingness whose *strength* would
be an assumption chosen after seeing this table. Adding a channel and setting its
signal to whatever closes 0.03 Gini is the DGP change the brief forbids, and would
be unfalsifiable. It was not done.

**Contribution of each new observable channel** (scorecard, same panel): 1.x spec
0.4626 → 2.0.0 channels 0.4669 (+0.004) → 2.1.0 channels 0.4698 (+0.003). Observable
ceiling: 0.457 on the pre-channel ledger (Phase 2, logistic on every 1.x candidate)
→ 0.489 on this world with the same form (`V21_REPORT.json → oracle.gini_observable`)
→ 0.492 with a GBM over all 99 candidates (+0.035 for the channels in total). The
channels are real and they are exhausted.

---

## Final checklist

| | criterion | result |
|---|---|---|
| **PASS** | ≥ 7 features | 9 |
| **FAIL** | Gini ≥ 0.50 | 0.4698 (CI 0.456–0.481) |
| **FAIL** | Gini within preferred 0.50–0.55 | 0.4698 |
| **FAIL** | KS ≥ 39 | 35.22 |
| **FAIL** | KS within preferred 39–42 | 35.22 |
| **PASS** | IV acceptable | 0.083–0.498; all above the 0.02 floor, none above the 0.50 review line; 3 of 9 below the preferred 0.10 |
| **PASS** | VIF < 5 | max 4.097 |
| **PASS** | PSI < 0.10 | score 0.0085 |
| **PASS** | CSI < 0.10 | max 0.0414 (categorical), 0.0297 (numeric) |
| **PASS** | WOE acceptable | monotone bins on all 9; two data-determined directions documented |
| **PASS** | calibration acceptable | gap −0.012, worst band −0.028 |
| **PASS** | PIT | strict `< as_of`, 23 boundary tests, future-feature probe +0.469 |
| **PASS** | leakage | shuffled-label Gini −0.010; latents absent; forbidden columns absent |
| **PASS** | production parity | 78 equality tests, 59/59 candidates held equal, retrain bit-identical |
| **PASS** | tests | full backend suite: 1,213 passed (2026-09-15), including 23 PIT boundary tests, 78 adapter-equality tests and 17 artifact tests for 2.1.0 |
| — | promotion | **not performed**; `champion.txt` = 1.1.0; no commit, no push |

**Bottom line.** 2.1.0 is the strongest legitimate scorecard this world supports and
is measurably better than the deployed model on identical rows — but the business
target sits 0.03 Gini / 3.8 KS above what the product's observable record can
support, and the difference is one specific unobserved quantity: current
willingness. Reaching 0.50 / 39 on this world requires a fresh willingness reading at
scoring time, not a different model.
