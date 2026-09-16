# recovery_risk 2.2.0 — production-readiness closure, 2026-09-16

**Status: (B) PRODUCTION READY WITH DOCUMENTED LIMITATION.** Every implementation blocker the
2026-09-16 audit raised against the reference GAM is closed and proved by executing it; the
remaining limitations are model-performance and data-availability facts, listed in §14, not
defects. **Nothing is promoted: `champion.txt` still reads 1.1.0.** Nothing is committed.

The audit (`../2.1.0/observability/PRODUCTION_READINESS_AUDIT.md`) classified the reference
15-feature GAM as (C) on four implementation grounds: four inputs the adapter did not produce,
an engine that could not load the model type, no calibrator / band table / reason codes, and a
monitor blind to missingness. It also found the reference fit's one interaction on the wrong
pair. This document records what was built to close each, and what the corrected model measures.

## A. Corrected GAM artifact / version

| | |
|---|---|
| artifact | `app/ml/artifacts/recovery_risk/2.2.0/` — **new version, the research artifact untouched** |
| model type | `gam`: `HistGradientBoostingClassifier`, singleton `interaction_cst` + one declared pair, monotone where the sign is declared, 32 knots, min-leaf 300, L2 1, lr 0.05, **629 trees chosen on validation KS** (probe 1,500) |
| spec | `config.RECOVERY_RISK_GAM` — 15 features fixed from the ladder's EXP5, `interactions=(("latest_disposition","arrears_ratio"),)`, `model_form="gam"` |
| interaction | **declared `latest_disposition x arrears_ratio`, fitted `latest_disposition x arrears_ratio`** — `interaction_cst` expressed in sklearn's remapped index space (`gam.interaction_cst_for`); the fitted trees are read back (`GamModel.tree_groups`) and the only pair group is the declared one. The research fit's pair (`latest_disposition x last_commit_status`) is absent. |
| artifact sha256 | `b6682096…` (metadata `artifact_sha256`, verified on every load; an earlier draft of this report quoted `c0fe3ede…`, the build before the pair-marginal split was added to `fit_background`) |
| trainer | `scripts/train_recovery_risk_gam.py` — the frozen protocol, no search; deterministic (three runs, identical figures) |
| libraries | Python 3.12.10, scikit-learn 1.6.0, numpy 2.2.0, pandas 2.2.3 |

## B. Corrected OOT metrics (frozen wd10 world; train months 0–13 n=54,144 · valid 14–17 n=14,945 · OOT 18–23 n=23,780)

| | Gini | AUC | KS | Brier | cal gap | breaks | top-decile lift |
|---|---|---|---|---|---|---|---|
| train (raw) | 0.5135 | 0.7568 | 38.13 | 0.18113 | −0.000 | 0 | 1.406 |
| valid (raw) | 0.5347 | 0.7674 | 40.50 | 0.18134 | +0.007 | 0 | 1.444 |
| **OOT, raw** | **0.5126** | **0.7563** | **38.66** | **0.17978** | −0.014 | 0 | 1.403 |
| **OOT, served (calibrated)** | **0.5122** | **0.7561** | **38.66** | **0.18021** | −0.020 | 0 | 1.402 |
| reference research fit (wrong pair, for the record) | 0.5118 | 0.7559 | 38.84 | 0.17984 | −0.014 | 0 | 1.40 |

Gini ≥ 0.50: **yes**. **KS ≥ 39: no (38.66).** All 12 spec gates PASS (`evaluation/gates.csv`), train→OOT
gap 0.0013. The corrected model is 0.18 KS below the research figure and 0.01 above the audit's
as-declared refit (38.65): the reference's extra 0.2 KS came from the pair it was not supposed to have.

## C. PSI / CSI / VIF / IV

Score PSI train→OOT **0.0103** (raw 0.0106). CSI, every input < 0.10 — max **0.070**
(`disposition_recency_class`), then `latest_disposition` 0.069, `last_commit_status` 0.066,
`recent_ptp_status` 0.064, `calls_3m` 0.062; everything else ≤ 0.034. VIF over the ten numerics,
max **2.14** (`calls_3m`), `arrears_ratio` 2.02, rest ≤ 1.54. IV (train, the pipeline's binner):
`arrears_ratio` 0.520, `overdue_amount` 0.347, `disposition_recency_class` 0.339,
`latest_disposition` 0.312, `calls_3m` 0.204, `paid_ratio_3m` 0.204, `cibil_score` 0.185,
`no_answer_streak` 0.091, `last_commit_status` 0.090, `recent_ptp_status` 0.076,
`intent_calls_3m` 0.023, `days_since_last_contact` 0.019, `employment_type` 0.010,
`interest_rate` 0.009, `ptp_amount_to_emi` 0.007. Segment Gini (OOT, gated): min **0.353**
(`dpd_bucket` BUCKET_1 / BUCKET_3), every loan type 0.46–0.53, every city 0.50–0.53.
Monotonicity 10/10 numerics as declared, checked on the fitted shape functions
(`evaluation/monotonicity.csv`).

## D. Calibration

`SegmentCalibrator` on `overdue_amount`, five quantile segments, **fitted on the validation months
only** (asserted in the trainer: the calibration sample's months ⊂ {14–17}, ∩ OOT = ∅); method
chosen by 3-fold Brier inside that sample — Platt 0.18145 vs isotonic 0.18285 → **Platt**;
validation Brier 0.18134 → 0.18112. Deterministic (`random_state=0`), versioned
(`recovery-calibration-2.2.0+c33dd365d1edd493`), no OOT row seen.

| pre / post on OOT | Gini | KS | Brier | gap |
|---|---|---|---|---|
| raw | 0.5126 | 38.66 | 0.17978 | −0.0141 |
| calibrated | 0.5122 | 38.66 | 0.18021 | −0.0204 |

Per segment (OOT, raw → calibrated over-prediction): 1: −4.9% → −8.9% · 2: −4.0% → −3.5% ·
3: −2.4% → −2.4% · 4: −1.0% → −2.3% · 5: 0.0% → −0.7%. **The calibrator does not improve the OOT
level** — it moves the smallest-balance segment the wrong way by four points — because the world's
macro shock sits at month 17 (`shock_month_index`), the last validation month, so the level the
calibrator learned is not the OOT level. Both figures are inside the 0.10 calibration gate; the
served number is the calibrated one, as for 1.1.0; and a row with no `overdue_amount` is served
uncalibrated rather than put in a default segment (pinned). Recorded as limitation §14.2 rather than
tuned away: fitting the calibrator on OOT would be the leak the rule exists to prevent.

Final probability reproduction is exact: served probability == round(reference calibrated
probability, 6) on **all 23,780 OOT rows and all 2,000 background rows, 0 disagreements**.

## E. Feature parity — 15 of 15

The audit found 11/15 produced. The four:

| feature | source | what was done |
|---|---|---|
| `last_commit_status` | `CallLog.verbal_payment_date` + VERIFIED `Payment` ledger — existed, unread | derived in `ml_scoring_service._commitment_features`: KEPT / BROKEN / OPEN as known at as_of from money between the call and the named date + `COMMITMENT_GRACE_DAYS` (2), threshold `COMMITMENT_KEPT_RATIO` (0.5) × EMI; NONE if no date was ever named |
| `recent_ptp_status` | `PTP.status` — existed, unread | newest promise created before as_of; ACTIVE→OPEN, HONORED / PARTIALLY_HONORED→HONORED, BROKEN / EXPIRED→BROKEN, RESCHEDULED→RESCHEDULED (`_PTP_STATUS_LEVEL`, total over the enum, pinned); NONE if none |
| `latest_disposition` | **no column existed** | **schema change**: enum `borrower_disposition_enum` (WILL_PAY / MAY_PAY / NO_COMMITMENT / HARDSHIP / DISPUTE / REFUSES — the ledger's vocabulary, held equal by test) and nullable `borrower_disposition` on `call_logs` and `visits`, migration **`c9a3d5e7f102`**; newest reading on an answered call or met visit before as_of, call after visit on the same day; NONE if never read |
| `disposition_recency_class` | as above | reading + `_FRESH` (≤ `DISPOSITION_FRESH_DAYS` = 7 days) / `_STALE`; NONE if never read |

No value is defaulted: NONE is the panel's own level for "nothing in the record", the level the
model was fitted on. The three constants are declared once in `pipeline/config.py` and held equal to
`LedgerConfig`'s defaults and the panel's by test.

**Training definition == production definition**, executed: the wd10 recipe materialised into the
real schema (300 borrowers, 12 months, seed 17), rewound to days 90/150/210/270/300, every
(loan, as_of) pair (≥ 1,000) through `build_features` against the panel — **all 15 inputs agree,
the five categoricals and every count exactly, the three ratios within 0.002** (the rounding both
sides already accept); every level the adapter emits is in the artifact's vocabulary; each status
takes ≥ 3 distinct values on the world (a parity that passed on all-NONE would prove nothing).
Also run on the audit's own 237-row day-300 harness: **15/15 emitted, max |diff| 0.0**.

One defect found and fixed on the way: three day-gap features (`days_since_last_contact`,
`days_since_last_call`, `days_since_last_answered_call`) read `(as_of − event).days`, which floors
elapsed time — a contact at 18:00 the day before a midnight as_of read 0 where the panel says 1.
Invisible to the equality harness because the materialiser writes events at midnight; caught by the
microsecond boundary test. All day gaps are now calendar-day arithmetic (`_calendar_days`), the
panel's definition. None of 1.1.0's four inputs is affected.

## F. Production-vs-reference scoring parity

`tests/test_gam_serving.py`, through `DecisionEngine` (`score` and `score_batch_detailed`):

| | rows | result |
|---|---|---|
| raw probability, engine object vs reference | 23,780 OOT + 2,000 background | max \|Δp\| **≤ 1e-12** (measured 0.0) |
| logit | same | max \|Δ\| ≤ 1e-10 (measured < 1e-14) |
| served probability vs reference calibrated (6 dp) | same | **0 disagreements** |
| band, points | same | 0 disagreements |
| single-row vs batch path | 40 | identical probability, band, points, reason codes, contributions |
| determinism (re-run, reversed order) | 200 | identical |
| Gini / KS recomputed from served scores | 23,780 | 0.5122 / 38.66, **0 rank-order breaks** |

The engine reads the artifact's `model_type`; a `gam` artifact must be a `GamModel` (raises
otherwise), and `tree_groups()` is run at load so a model whose trees use an undeclared pair is
refused. The 1.1.0 path is unchanged — same `predict_proba`, same calibrator call, same
`ScoreCard.explain` — and pinned (`test_the_1_1_0_path_is_untouched`).

## G. PIT / leakage

Previous audit: 25 panel-level and adapter-level tests, every channel, midnight boundary to the
microsecond — all still pass. Added for the four features (`tests/test_recovery_risk_gam_features.py`):
a disposition on a call / on a visit at midnight−1µs is inside, at midnight and +1µs outside;
a reading 8 days old is `_STALE`, 7 days `_FRESH`; a commitment walks OPEN → KEPT → OPEN (a rupee
short) → OPEN (money before the call does not count) → BROKEN (grace expired) → OPEN (grace edge);
a keeping payment at the boundary; a commitment named at the boundary; a promise created at the
boundary; a promise resolved after as_of reads OPEN on the rewound book. The four features read
no outcome or forbidden column (spec check in the trainer).

## H. Explainability reconciliation

Exact per-tree decomposition (`GamModel.contributions`): `intercept + Σ feature + Σ pair == logit`,
max error **8.9e-15 over all 23,780 OOT rows**; `sigmoid(logit) == predict_proba` to **0.0**.
The intercept is the background's mean logit (2,000 train rows, seed 0, hash-versioned). A pair
tree's main effects are attributed to the two features through the pair's background marginals on
each feature's fitted grid, and only the residual is reported as the interaction — without that,
`latest_disposition`'s whole effect (its singleton trees are empty; boosting put it in the pair
trees) would have been called an interaction. Stored on every served row (`contributions`,
6 dp) so the row reconciles within 1e-5. Deterministic.

## I. Risk bands

`ProbabilityBands`, version **`recovery-bands-2.2.0`**, edges hash `37426125b719460f`. Direction:
**higher calibrated probability = higher risk; A safest, E riskiest.** Cut at the scorecard's shares
(15/20/25/25/15) of the validation months' calibrated probability; upper edge exclusive; assignment
is a `searchsorted` (deterministic, pinned at both sides of every edge).

| band | upper edge (excl.) | dev share | dev bad rate | OOT n | OOT bad rate |
|---|---|---|---|---|---|
| A | 0.40434 | 0.15 | 0.314 | 3,523 | 0.359 |
| B | 0.58206 | 0.20 | 0.479 | 4,749 | 0.521 |
| C | 0.74370 | 0.25 | 0.661 | 5,821 | 0.683 |
| D | 0.88985 | 0.25 | 0.826 | 5,932 | 0.830 |
| E | — | 0.15 | 0.931 | 3,755 | 0.934 |

`points` is the scorecard scaling of the raw logit (PDO 20, base 600 at odds 50; higher = safer),
for display; documented as not a scorecard.

## J. Reason codes

Version **`recovery-reasons-1.0.0`** + hash of the label map. Rule: the up-to-4 largest centred
contributions with |c| ≥ 0.02 logits, either direction, ordered by magnitude, ties by name; each
carries feature, kind (feature / interaction), the value, the contribution, `increases_risk` /
`decreases_risk`, rank, and a sentence in plain words. Two rows from the background:

```
p 0.983  band E  points 364          p 0.081  band A  points 551
1 instalments in arrears (7.9) raises the chance of no payment (+1.36 on the log-odds)
2 call attempts (3 months) (18) raises … (+0.43)
3 overdue amount (85,498) raises … (+0.40)
4 latest disposition and its freshness (REFUSES_STALE) raises … (+0.35)
                                     1 instalments in arrears (0) lowers … (−0.95)
                                     2 overdue amount (0) lowers … (−0.68)
                                     3 bureau score (883) lowers … (−0.39)
                                     4 latest disposition and its freshness (WILL_PAY_FRESH) lowers … (−0.32)
```

The interaction, when it ranks, is one term with both values ("… together with …"). Pinned:
ordering, direction, plain words, determinism, and that each code's contribution equals the stored
one.

## K. Missingness monitoring

`app/ml/pipeline/missingness.py`, wired into `monitor_model`'s stability block **beside** PSI;
`evaluate.psi` unchanged. Per feature: n on each side, baseline and current **null rate** and
**absent rate** (null OR the "NONE" level — so a product that has not started capturing
dispositions reads 100% absent, 0% null), absolute delta, relative delta, both thresholds, breached,
and the action. Thresholds: |Δabs| > 0.10, or |Δrel| > 0.25 with |Δabs| > 0.02. A breach sets
`retrain_recommended` with a reason naming the feature and the move; the action text says what to
distrust (PSI for that feature), what to find (source / schema / process), and when to retrain.
The audited case is the regression: identical non-missing distribution, NaN share 49.2% → 37.2%
— PSI 0.0216 ("stable"), the monitor breaches; the same shape through `monitor_model` on logged
predictions fires; a stable 45% → 45% stays quiet. 13 tests.

## L. Versioning

Every served score carries `ScoreResult.versions`, written to the new `model_predictions.scoring_versions`
(migration **`d0b4e6f8a213`**, with `contributions`):

```
model_artifact      2.2.0                 artifact_sha256   b6682096…
feature_definition  recovery-features-2.2.0+f2f533e34496d455   (features, levels, abstaining, constants, PIT rule)
calibration         recovery-calibration-2.2.0+c33dd365d1edd493
risk_bands          recovery-bands-2.2.0+37426125b719460f
reason_codes        recovery-reasons-1.0.0+49a2704c0307e9da
background          recovery-background-2.2.0+ce37fb98a59672a1   (2,000 train rows, seed 0)
training_data       e9d6e21e6c43e0eb
```

Each hash is of the thing itself (the band edges, the calibrator's dict, the encoded background
frame, the label map), so a change to any one of them changes the stamp without touching the model.
`GET /manager/ml/health` reports `model_type` and `versions`. The 1.x path stamps version and sha.

## M. Tests

| suite | count | result |
|---|---|---|
| all backend tests before this work | 1,266 | pass (re-run after the changes: **1,266 passed**, 636 s) |
| `test_recovery_risk_gam_features.py` — parity ×5 days, PIT, vocab, constants | 46 | pass |
| `test_gam_serving.py` — load, parity, constraint, calibration, bands, reasons, versions | 38 | pass |
| `test_missingness_monitor.py` | 13 | pass |
| **total** | **1,363** | **all pass** — see the final run recorded at the end |
| `python -m compileall app scripts` | — | clean |

## Final table

| CHECK | RESULT | PASS/FAIL | EVIDENCE | ACTION |
|---|---|---|---|---|
| Corrected artifact / version | 2.2.0, new, pair where declared, sha verified | PASS | `metadata.json` interaction_constraint; `test_the_fitted_model_uses_exactly_the_declared_pair` | — |
| Interaction constraint remapped; monotone mapping preserved | cst `[0,5]` = remapped indices; 10/10 monotone | PASS | `test_the_constraint_was_expressed_in_the_remapped_order`, `monotonicity.csv` | — |
| Undeclared pair refused | raises on load | PASS | `test_a_model_with_an_undeclared_pair_is_refused` | — |
| OOT Gini ≥ 0.50 | 0.5126 raw / 0.5122 served | PASS | `metrics.oot` | — |
| **OOT KS ≥ 39** | **38.66** | **FAIL** | `metrics.oot`, `business_target` | none — model-performance limitation of the frozen world (§14.1) |
| Spec gates (12) | all PASS | PASS | `evaluation/gates.csv` | — |
| Score PSI / CSI / VIF | 0.010 / 0.070 / 2.14 | PASS | `psi.csv`, `vif.csv` | — |
| Calibration | fitted on validation only; gap −0.020 (raw −0.014); exact reproduction | PASS (with note) | §D, `calibration_by_segment.csv` | re-fit on the first matured production cohort; see §14.2 |
| Feature parity 15/15 | emitted and equal on ≥1,000 rewound pairs | PASS | `test_every_gam_input_is_emitted_and_agrees` | — |
| Schema for disposition | migration `c9a3d5e7f102`, nullable, no default | PASS | migration, `test_the_disposition_enum_is_the_simulators_vocabulary` | field process must capture the stance (§14.3) |
| PIT / leakage | boundary to the µs on every new channel; 25 prior tests still pass | PASS | `test_recovery_risk_gam_features.py`, `test_recovery_risk_pit_audit.py` | — |
| Scoring parity prod vs reference | \|Δp\| 0.0 raw, 0 disagreements served, 0 breaks | PASS | `test_every_oot_row_reproduces…`, `test_background_rows_reproduce…` | — |
| Explanation reconciliation | 8.9e-15 over 23,780 rows; sigmoid 0.0 | PASS | `explanation_reconciliation` | — |
| Risk bands | versioned, deterministic, direction documented | PASS | `risk_bands`, `test_bands_are_deterministic…` | — |
| Reason codes | contribution-based, directed, readable, reconciled | PASS | `test_reason_codes_are_the_largest_contributions…` | — |
| Missingness monitor | 49.2→37.2 case fires; PSI stays 0.02 | PASS | `test_missingness_monitor.py` | — |
| Versioning on every score | 7 stamps + sha, stored per row | PASS | `test_every_served_score_carries_the_versions` | — |
| 1.1.0 untouched; champion untouched | same path, `champion.txt` = 1.1.0 | PASS | `test_the_1_1_0_path_is_untouched`, `test_champion_is_still_1_1_0` | — |
| Full regression | 1,363 passed, compileall clean | PASS | final run | — |

## 14. Documented limitations (why B, not A)

1. **KS 38.66 < 39.** A model-performance limitation of the frozen world, not an implementation
   defect: the observability programme's ladder and final search (`FINAL_SEARCH_REPORT.md`)
   established the demonstrated ceiling of a stable, interpretable model on this data at KS ≈ 38.8;
   the configurations above 39 are GBMs leaning on seasoning features with CSI 0.46–0.52. The
   corrected artifact sits where the audit's as-declared refit said it would (38.65).
2. **Calibration does not transfer across the month-17 shock.** Fitted on validation as the rule
   requires, it slightly over-corrects the lowest-balance segment on OOT (−8.9% vs −4.9% raw).
   Inside the gate; the served number is calibrated as for 1.1.0. The right fix is a re-fit on real
   matured outcomes, which the retraining lifecycle already schedules — not a fit on OOT.
3. **Disposition capture is a product process that does not exist yet.** The column now exists;
   until agents record the stance, every production borrower reads `latest_disposition = NONE`
   (0% null, 100% absent) and the model runs without its strongest behavioural input — the
   missingness monitor reports that share on day one and its action says what it means. The
   offline figures assume 75% of the pool read at reliability ~0.85 (the wd10 world).
4. **Synthetic throughout.** Every number above describes the ledger simulator's borrowers
   (`SYNTHETIC_WARNING` on the artifact).
5. **Not promoted.** `DecisionEngine.get("recovery_risk")` still resolves to 1.1.0; 2.2.0 is
   loaded only when asked for by version. Promotion is `registry.promote`, four eyes, a person.

## What changed

`app/ml/pipeline/gam.py` (new) · `missingness.py` (new) · `engine.py` (GAM path, `versions`,
`contributions`) · `monitor.py` (missingness beside PSI) · `config.py` (`RECOVERY_RISK_GAM`,
`model_form`, `interactions`, three constants, `LOGGED_FEATURES`) · `models/call_log.py`,
`models/visit.py` (`BorrowerDisposition`) · `models/model_prediction.py` (two JSON columns) ·
`alembic/versions/c9a3d5e7f102_*`, `d0b4e6f8a213_*` (new) · `services/ml_scoring_service.py`
(four features, calendar-day gaps, version/contribution logging) · `ml/simulation/ledger/materialise.py`
(writes the disposition it already had; the DGP untouched) · `scripts/train_recovery_risk_gam.py`
(new) · `scripts/research/recovery_risk_obs/gam_common.py` (re-export) · three new test files.
