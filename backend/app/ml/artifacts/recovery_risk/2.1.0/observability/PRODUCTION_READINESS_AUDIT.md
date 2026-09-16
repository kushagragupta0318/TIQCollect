# Production-readiness audit — the reference recovery-risk GAM, 2026-09-16

> **Closed, 2026-09-16 (later).** Every implementation blocker below is closed as
> **recovery_risk 2.2.0** — a NEW artifact fitted with the interaction where it was declared
> (OOT Gini 0.5126 / KS 38.66 raw; 0.5122 / 38.66 served), the four missing features built
> (two through migration `c9a3d5e7f102`), GAM serving in the engine, a validation-fitted
> calibrator, versioned bands and reason codes, a missingness monitor beside PSI, and version
> stamps on every served score. Reclassified **(B) PRODUCTION READY WITH DOCUMENTED
> LIMITATION**; the KS shortfall remains a model-performance limitation. The verdict below is
> left as it was written, because it was right when it was written. Closure report:
> `app/ml/artifacts/recovery_risk/2.2.0/PRODUCTION_READINESS_CLOSURE.md`. `champion.txt` still
> reads 1.1.0.

**Reference model.** The strongest admissible model established by the model search: the
15-feature additive GAM on the frozen `wd10` world, **OOT Gini 0.5118, KS 38.84, AUC 0.7559,
Brier 0.17984**, n = 23,780 (months 18–23), bad rate 0.6788.

**Disposition: (C) NOT PRODUCTION READY** — on implementation grounds, not on model performance.
Four of its fifteen features cannot be computed by the product at all, and the serving engine
cannot load a model of this type. The KS shortfall is a separate, documented model-performance
limitation.

`champion.txt` is untouched (1.1.0). Nothing promoted, committed or pushed. The simulator, DGP,
label, split, seed and every gate are unchanged. One implementation defect was found and fixed;
one model-behaviour change was deliberately **not** made (section 6).

Evidence: `gam/audit/` beside this file (`audit.log`, `audit.json`, `gam_exp5.joblib`,
`gam_exp5_metadata.json`, `shape_functions.json`, `explainability.json`, `adapter_parity.json`).
Scripts: `scripts/research/recovery_risk_obs/audit_production_readiness.py`,
`audit_adapter_parity.py`, `audit_explainability.py`, `gam_common.py`.

---

## The defect this audit found, and fixed

**`interaction_cst` was applied to the wrong pair of features.** `HistGradientBoostingClassifier`
inserts a ColumnTransformer whenever `categorical_features` is used, and its output puts every
categorical first. sklearn remaps `monotonic_cst` into that order
(`gradient_boosting.py:566`) and passes **`interaction_cst` through unremapped** (line 579 → 887).
Every GAM fitted by `gam_ladder.py` / `gam_search.py` therefore declared one pair and allowed a
different one:

```
declared   latest_disposition x arrears_ratio        (caller indices 1, 0)
applied    latest_disposition x last_commit_status   (remapped indices 0, 1)
```

Found by measuring, not by reading: the exact score decomposition did not sum to the logit, and
a brute-force scan over all 105 feature pairs showed exactly one non-additive pair — and it was
not the declared one.

| | interaction actually allowed | valid KS | OOT Gini | OOT KS |
|---|---|---|---|---|
| **as fitted** — every figure reported before today | `latest_disposition × last_commit_status` | 40.66 | 0.5118 | **38.84** |
| **as declared** — the intended pair | `latest_disposition × arrears_ratio` | 40.41 | 0.5121 | **38.65** |

**What it does not change.** Additivity: the constraint list is fifteen singletons plus one pair,
and a set of singletons is invariant under any permutation of columns, so "every tree splits on
one feature" held throughout — the model is a GAM, as claimed. Monotonicity: sklearn *does* remap
`monotonic_cst`, so every declared sign landed on its own feature (verified empirically, 8/8).
Metrics: unchanged, because the measured model is the model that was measured. **KS < 39 either
way**, so no conclusion of the model search moves.

**Fixed** in `scripts/research/recovery_risk_obs/gam_common.py` (one definition, used by all three
GAM scripts), pinned by four tests in `tests/test_gam_interaction_constraint.py`, including one
that fails if sklearn ever stops reordering and one that preserves the defect itself. The audited
artifact and its metadata now name the interaction that was actually fitted, beside the one that
was declared.

---

## 1. ARTIFACT

| check | result | verdict |
|---|---|---|
| recoverable | `exp5` loads from `gam/models.joblib`; re-emitted as `gam/audit/gam_exp5.joblib`, sha256 `f139ae6d50e63232…` | PASS |
| reproducible | re-fit from the recorded recipe: **max \|p_refit − p_stored\| = 0.000e+00** over 23,780 OOT rows | PASS |
| metadata complete | features · model type · train/valid/OOT periods and row counts · target definition and outcome-definition version · preprocessing · monotonic declarations · interaction (fitted **and** declared) · missing-value handling · hyperparameters · seed · category level map · unknown-category behaviour · library versions · artifact sha256 · training-data hash | PASS |
| clean-process load | a subprocess importing nothing from `app/` reproduced all 23,780 scores, max \|diff\| < 1e-12 | PASS |

Model type: `logit = b0 + Σ_j f_j(x_j) + h(latest_disposition, last_commit_status)`, 438 boosting
iterations, learning rate 0.05, 4 leaves / depth 2, min 300 rows per leaf, L2 = 1, 32 histogram
knots, seed 0, iterations chosen on validation KS. Periods: train 0–13 (54,144), valid 14–17
(14,945), OOT 18–23 (23,780).

## 2. FEATURE PARITY — executed against the production adapter

`MLScoringService.build_features` run against a database materialised from the frozen recipe and
rewound to a past day, compared feature by feature with the panel (237 rewound rows).

| | features | max \|diff\| vs panel |
|---|---|---|
| **emitted and exactly equal** (11) | `arrears_ratio`, `cibil_score`, `no_answer_streak`, `overdue_amount`, `intent_calls_3m`, `calls_3m`, `paid_ratio_3m`, `ptp_amount_to_emi`, `employment_type`, `days_since_last_contact`, `interest_rate` | **0.0** on every one |
| **not produced at all** (4) | `latest_disposition`, `disposition_recency_class`, `last_commit_status`, `recent_ptp_status` | — |

The four split into two kinds, and the distinction decides the work:

- **No product column exists.** `latest_disposition` and `disposition_recency_class` need a
  structured borrower disposition on `CallLog` and `Visit`. The schema has neither
  (`CallLog.disposition` = absent, `Visit.disposition` = absent). A migration, adapter code and
  a real field process are prerequisites — this is the product decision the disposition report
  already flagged.
- **The column exists, the derivation does not.** `last_commit_status` is derivable from
  `CallLog.verbal_payment_date` + the payment ledger (the panel does exactly this);
  `recent_ptp_status` is derivable from `PTP.status` + `created_at`, both of which the adapter
  already queries. Both are implementable today.

Other parity checks: window boundaries written once and strict at `as_of` (PASS); unknown
categorical level deterministic and finite — code −1, scored without error (PASS, with the caveat
that −1 is an untrained bin, so an explicit `OTHER` level is the safer serving contract); feature
order is **positional**, so any serving path must build the frame in `metadata["features"]` order
(PASS, recorded as a serving requirement). The panel/adapter equality harness covers 11 of 15 —
the same four are absent (FAIL until they exist).

## 3. SERVING PARITY

| check | result | verdict |
|---|---|---|
| row-dict serving shim vs offline frame | max \|Δp\| **0.000e+00** over 23,780 rows | PASS |
| **the real `DecisionEngine` can serve it** | **no** — `registry.load` expects `<model>/<version>/model.joblib` with a spec+scorecard metadata block, and `score_batch_detailed` calls `predict_proba` on a frame of **raw** values; a bare HistGradientBoosting cannot take string categoricals | **FAIL** |
| shape functions served identically | evaluated on a frozen 2,000-row background (seed 0) and stored in `shape_functions.json` | PASS |
| knots / bin boundaries | `max_bins = 32`; per-feature bin counts recorded | PASS |
| declared-sign monotonicity | 8 sign-constrained features, **0 violations** | PASS |
| missing routing | 2 features carry a NaN branch (`ptp_amount_to_emi` +0.022, `days_since_last_contact` +0.023); explicit and reproducible | PASS |
| intercept + contributions = logit | **exact**, see section 9 | PASS |
| probability transform | `max |sigmoid(logit) − predict_proba| = 0.0` | PASS |

Serving this model needs: a Pipeline whose first step applies the stored category map, a
`model_type` the registry understands, and a band table (section 9). None exists.

## 4. POINT-IN-TIME / LEAKAGE — `tests/test_recovery_risk_pit_audit.py`, 25 tests, all passing

**Panel (day-grained).** For every channel the model reads, events at or after `as_of` cannot move
any feature: payments appended after `as_of`; a payment **status change** that takes effect after
`as_of`; duplicated visits, calls, bureau pulls, flags and lifecycle rows moved past `as_of`;
promises created or resolved after `as_of`; dispositions and hardship reasons recorded after
`as_of`. Plus: paying the entire book inside the outcome window moves `y` and **nothing else** —
the feature and label windows are disjoint by construction.

Boundaries: day t−1 counts, day t does not, day t+1 does not; the inclusive lower edge is exact
(a call at exactly t−90 is inside `calls_3m`, at t−91 outside).

**Adapter (timestamp-grained).** The contract is not the time passed in — `build_features` does
`datetime.combine(as_of, datetime.min.time())`, so the cut is **midnight of the as_of date**.
Tested to the microsecond for calls, payments, visits and PTPs: an event at
23:59:59.999999 the previous day is **inside**; at 00:00:00.000000 on the day itself, **outside**;
one microsecond later, outside.

One defect was found here too — in the test, not the model: the first draft of the
status-change test rewrote `status_effective_day` for *all* historical payments, which moved
PENDING→VERIFIED transitions across `as_of` and changed the live population. The harness caught
it; the mutation was narrowed to what it claimed to be.

## 5. SCORE REPRODUCTION — frozen OOT population

```
rows                       23,780
unique (loan_id, as_of)    23,780     (1:1, no duplicate keys)
max |Δ probability|        0.000e+00
mean |Δ probability|       0.000e+00
max |Δ logit|              0.000e+00
disagreements > 1e-9       0
rank-order breaks          0
```

**End-to-end reproduction against the production service is not executable** and that is the
finding, not an omission: four features have no adapter implementation and the engine cannot load
the artifact, so there is no production prediction to compare against. Blocked on 2.1 and 3.2.

## 6. CALIBRATION — measured, not adjusted

Brier **0.17984**; observed bad rate 0.6788 vs predicted 0.6644, **gap −0.0144** (inside \|gap\| < 0.03);
worst decile gap +0.0341 (decile 6). By DPD bucket:

| segment | n | observed | predicted | gap | Gini |
|---|---|---|---|---|---|
| CURRENT | 1,109 | 0.5194 | 0.5122 | −0.0072 | 0.3703 |
| BUCKET_1 | 6,610 | 0.5215 | 0.4953 | **−0.0261** | 0.3542 |
| BUCKET_2 | 5,138 | 0.6107 | 0.5996 | −0.0112 | 0.3735 |
| BUCKET_3 | 3,830 | 0.7345 | 0.7281 | −0.0064 | 0.3513 |
| NPA | 7,093 | 0.8696 | 0.8582 | −0.0114 | 0.4179 |

The model is **uncalibrated by construction** — unlike the 1.x artifacts it carries no Platt /
segment calibration layer. It passes the gate anyway. **No recalibration was performed**: it was
not requested, and fitting one would change model behaviour. Adding the pipeline's
`SegmentCalibrator` is a prerequisite for serving, because the allocator multiplies
`collectable_balance × probability` and so consumes the level, not just the ranking.

## 7. STABILITY

| metric | value | threshold | verdict |
|---|---|---|---|
| score PSI, train → OOT | 0.0106 | < 0.10 | PASS |
| max feature CSI | 0.0700 (`disposition_recency_class`), then `latest_disposition` 0.0694, `last_commit_status` 0.0660, `recent_ptp_status` 0.0640, `calls_3m` 0.0620 | < 0.10 | PASS |
| **missingness drift** | `days_since_last_contact` **−0.1197** (49.2% → 37.2% missing), `ptp_amount_to_emi` −0.0878 | — | **FAIL** |

**And the instrument itself has a blind spot, which this audit is the first to state.**
`evaluate.psi` drops NaN on both sides before binning, so a feature whose *missingness* moves while
its observed values do not reports PSI ≈ 0 and a "stable" verdict — which is exactly why
`days_since_last_contact` shows CSI 0.0216 against a 12-point missingness shift. The drift is
benign here (a maturing book has fewer never-contacted accounts) but it is invisible to the gate
that exists to catch it, and the same `psi` feeds `monitor.py` in production.

**Not fixed**, deliberately: changing `psi` would silently move every PSI figure in every committed
artifact and every monitoring row — a behaviour change, not a defect fix. Pinned instead by
`test_psi_cannot_see_missingness_drift_so_the_monitor_needs_its_own_check` in
`tests/test_model_monitoring.py`, and specified as its own monitor below.

## 8. MINIMUM PRODUCTION MONITORING SPECIFICATION

Thresholds reuse the repo's existing constants wherever one exists; no gate is weakened. Every
alert names the action, because an alert without one trains people to close it.

| # | monitor | computed | WARN | ALERT | action on alert |
|---|---|---|---|---|---|
| 1 | **score distribution** (PSI of served probability vs development) | nightly, ≥ 500 matured account-days | ≥ 0.10 | ≥ 0.25 | open a retrain candidate; do not auto-promote |
| 2 | **feature PSI / CSI**, per selected feature | nightly | ≥ 0.10 | ≥ 0.25 | identify the feature; if it is a policy-shaped one (`calls_3m`, `days_since_last_contact`) check for a collections-process change before treating it as borrower drift |
| 3 | **missingness rate**, per feature (the gap PSI cannot see) | nightly | ±5 pts vs development | ±10 pts | inspect the adapter and the upstream channel: a jump to 100% is a broken join, not drift — page, do not queue |
| 4 | **feature presence** — any selected feature absent from the served vector | every batch | any | any | `retrain_recommended` in its own right; this is exactly how `ptp_kept_ratio` vanished for a cycle while coverage stayed above its floor |
| 5 | **calibration** (observed − predicted, overall and per DPD bucket) | on matured cohorts | \|gap\| ≥ 0.03 | ≥ 0.10 | recalibrate the segment layer; the allocator prices in rupees, so level errors cost money directly |
| 6 | **bad-rate drift** (live vs development prevalence) | on matured cohorts | ±5 pts | ±10 pts | informational alone; combined with (5) it is a population change, with (7) a model-decay signal |
| 7 | **discrimination** — live Gini and KS vs the artifact's OOT | ≥ 500 matured, ≥ 2 classes | Gini −10% rel. | Gini −20% / KS −30% rel. | existing retrain trigger |
| 8 | **rank-order stability** — decile monotonicity | on matured cohorts | any break outside the top 5 | any break in the top 5 | retrain candidate |
| 9 | **segment performance** — Gini per DPD bucket, loan type, city | monthly | any gated segment < 0.20 | < 0.15 | investigate the segment before the model |
| 10 | **data quality** — coverage floor declines, unknown categorical levels, out-of-range values, duplicate (entity, as_of) keys | every batch | > 1% declined, any unknown level | > 5% declined | unknown levels are the sharp one for this model: −1 is an untrained bin, so a new disposition value would score silently |

The existing `monitor.py` already implements 1, 2, 4, 5, 6, 7, 8 on the same thresholds. **3, 9 and
10 do not exist** and are the additions this audit requires before any GAM is served.

## 9. EXPLAINABILITY

For an additive model plus declared pairs the decomposition is exact against any fixed background:
`F(x) = E_B[F] + Σ_j g_j(x_j) + Σ_pairs r_ab`, with the pair residual attributed explicitly.
Measured over 60 OOT borrowers against a frozen 2,000-row background (seed 0):

- **max \|intercept + contributions − logit\| = 3.997e-15**, mean 1.06e-15 (earlier write-ups left
  the interaction unattributed and so summed only approximately)
- probability transform: `max |sigmoid(logit) − predict_proba| = 0.0`
- determinism: same row explained twice, max \|diff\| **0.0**

A worked example, exactly as a production explanation would read:

```
loan L0011179, as_of 2026-05-24   P(no material payment) 0.2134   logit -1.3043
  intercept (background mean logit)                    +0.8951
  arrears_ratio                   = 0.0                -1.0304
  overdue_amount                  = 0.0                -0.5896
  cibil_score                     = 776                -0.2197
  paid_ratio_3m                   = 0.991              -0.1722
  calls_3m                        = 4                  -0.1267
  ...
  latest_disposition x last_commit_status               -0.0060
  TOTAL                                                -1.3043   (error 1.1e-15)
```

**Missing for production explainability** (all three FAIL):

1. **No risk-band table.** The WOE pipeline fits bands from the development score distribution;
   this model has none, so a served row could carry a probability but no band.
2. **No reason codes.** The engine derives them from scorecard points per feature; the equivalent
   here is the top-k contributions above, which nothing computes at serve time.
3. **The background sample is part of the explanation, not the model.** The 2,000 train rows
   (seed 0) must be versioned with the artifact or explanations will drift while scores do not.

## 10. REGRESSION TESTS

`python -m compileall app scripts` clean. Full backend suite green (count in the session record).
Added by this audit:

- `tests/test_recovery_risk_pit_audit.py` — 25 tests, section 4.
- `tests/test_gam_interaction_constraint.py` — 4 tests pinning the `interaction_cst` defect, its
  fix, sklearn's reordering premise, and why additivity survived it.
- `tests/test_model_monitoring.py` — 1 test pinning the PSI missingness blind spot.

## 11. DOCUMENTATION

Corrected visibly rather than rewritten: `GAM_REPORT.md` and `FRESHNESS_REPORT.md` attribute the
interaction to `latest_disposition × arrears_ratio`; both now carry the correction to
`latest_disposition × last_commit_status` with the measured effect of the difference. The KS
shortfall, the admissible ceiling, why the ≥39 GBMs are inadmissible, why the DGP is frozen and why
the search stopped are restated in section 12.

---

## 12. FINAL DISPOSITION

| CHECK | RESULT | PASS/FAIL | EVIDENCE | REQUIRED ACTION |
|---|---|---|---|---|
| 1.1 artifact exists / recoverable | loaded, re-emitted with sha256 | **PASS** | `gam_exp5.joblib`, `audit.log` §1 | — |
| 1.2 reproducible from the recipe | max \|Δp\| 0.000e+00 over 23,780 rows | **PASS** | `audit.log` §1 | — |
| 1.3 metadata complete | all 12 required keys | **PASS** | `gam_exp5_metadata.json` | — |
| 1.4 clean-process load | subprocess reproduced all scores, <1e-12 | **PASS** | `clean_load_probe.py` | — |
| 2.1 every feature produced by the adapter | 11/15 emitted, all 11 exact; **4 missing** | **FAIL** | `adapter_parity.json` | implement `last_commit_status`, `recent_ptp_status` (columns exist); `latest_disposition`, `disposition_recency_class` need a schema migration |
| 2.2 schema backs the missing features | disposition column absent on `CallLog`/`Visit` | **FAIL** | `audit.log` §2 | product decision + migration before serving |
| 2.3 panel/adapter equality coverage | 11/15 in the harness | **FAIL** | `test_ledger_phase3_adapter_equality.py` | extend once the four exist |
| 2.4 window boundaries / no post-as_of data | strict `< as_of`, written once | **PASS** | §4 tests | — |
| 2.5 unknown-category behaviour | deterministic, code −1, finite | **PASS** | `audit.log` §2 | map unseen levels to an explicit `OTHER` at serve time |
| 2.6 deterministic feature ordering | positional; order is part of the contract | **PASS** | `audit.log` §2 | serving must use `metadata["features"]` order |
| 3.1 offline vs serving-shaped scores | max \|Δp\| 0.000e+00 | **PASS** | `audit.json` | — |
| 3.2 the engine can serve this model type | it cannot | **FAIL** | `audit.log` §3 | Pipeline wrapper + registry support for `model_type="gam"` |
| 3.3 monotonicity preserved | 8/8, 0 violations | **PASS** | `shape_functions.json` | — |
| 3.4 missing routing identical | 2 NaN branches, reproducible | **PASS** | `shape_functions.json` | — |
| 3.5 knots / bins recorded | 32 knots, per-feature counts | **PASS** | `shape_functions.json` | — |
| 4 point-in-time / leakage, all channels | 25 tests, incl. microsecond boundaries | **PASS** | `test_recovery_risk_pit_audit.py` | — |
| 5 score reproduction (offline ↔ serving-shaped) | 23,780 rows, 0 disagreements, 0 breaks | **PASS** | `audit.json` | — |
| 5b end-to-end through the production service | not executable | **BLOCKED** | §5 | blocked on 2.1 and 3.2 |
| 6.1 overall calibration | Brier 0.17984, gap −0.0144 | **PASS** | `audit.log` §6 | add a segment calibrator before serving |
| 6.2 segment calibration | worst bucket gap −0.0261 | **PASS** | `audit.log` §6 | — |
| 7.1 score PSI | 0.0106 | **PASS** | `audit.json` | — |
| 7.2 feature CSI | max 0.0700 | **PASS** | `audit.json` | — |
| 7.3 missingness drift | −0.1197 on `days_since_last_contact`; PSI is blind to it | **FAIL** | `audit.json`, monitoring test | add monitor 3; do not change `psi` |
| 8 monitoring specification | 10 monitors defined; 3 absent today | **FAIL** | §8 | implement monitors 3, 9, 10 |
| 9 explainability | exact to 4e-15, deterministic | **PASS** | `explainability.json` | — |
| 9b bands / reason codes / background versioning | none exist | **FAIL** | §9 | required before serving |
| 10 regression tests + static checks | suite green, 30 tests added, compileall clean | **PASS** | session record | — |
| 11 documentation | audit report + visible corrections | **PASS** | this file | — |
| **KS ≥ 39 hard target** | **38.84** (38.65 with the declared pair) | **FAIL** | model search | none — see below |
| Gini ≥ 0.50 | 0.5118 | **PASS** | §1 | — |

### Classification: **(C) NOT PRODUCTION READY**

Not because the model is unsound — on every property that could be tested it is: reproducible
bit-for-bit, point-in-time safe across every channel to the microsecond, exactly self-explaining,
monotone where declared, stable on PSI and CSI, calibrated inside the gate, and scoring identically
through a row-at-a-time serving path. It is not ready because **it cannot be served**: four of its
fifteen features do not exist in the product (two need a schema migration and a field process), the
engine cannot load a model of this type, and it has no calibrator, band table or reason codes.

**The KS shortfall is a documented model-performance limitation, not an implementation defect.**
Best admissible OOT KS **38.84**, Gini **~0.51**, against a target of KS ≥ 39. The gap is 0.16 KS
and it was searched for exhaustively: no unused admissible candidate is worth more than +0.09
validation KS, the additive form already matches an unrestricted GBM on the same inputs, and the
only configurations above 39 are GBMs over pools containing `months_on_book` (feature PSI 0.52)
and `outstanding_to_sanction` (0.30) — features that read the calendar rather than the borrower and
fail the CSI < 0.10 gate before their KS is read. The DGP was frozen because the target is a
property of the data-generating process, and tuning it to reach a metric would make the metric
meaningless; the search was stopped because it had demonstrably converged.

**If the four features are implemented and the serving gaps closed**, the honest expectation is the
number measured here: Gini ≈ 0.51, KS ≈ 38.8 — a model that beats the deployed 1.1.0 substantially
on identical rows while still missing the KS target.
