# Freshness-aware willingness, model form, and one coverage experiment — 2026-09-15

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


**Brief.** On the frozen `wd10` world (structured disposition, read noise 0.10, sweep until reached;
nothing in the DGP touched), make the disposition more useful to an interpretable scorecard through
freshness-aware features; benchmark WOE-LR against regularised LR and a GBM; run one coverage
experiment. Target: Gini ≥ 0.50 **and** KS ≥ 39.

**Result in one line.** Freshness features change nothing (F1 = F2 = F3, identical selection and
0.5007 / 38.01), the richest representation is slightly worse (F4 0.4997 / 37.58), wider coverage buys
Gini and not KS (F4C 0.5045 / 37.61), and **KS ≥ 39 is reached only by a GBM over the 43-feature VIF pool
(39.03) or over all 115 candidates (39.71) — never by a scorecard.** Gini ≥ 0.50: yes (0.5007–0.5045,
CIs straddling 0.50). KS ≥ 39: no. Nothing promoted; `champion.txt` reads 1.1.0.

All rows below: the production trainer, selection on train/validation only, OOT scored once after the
specification was frozen. JSON per experiment beside this file (`f1.json` … `f4c.json`,
`f1_model_form.json`, `f4_model_form.json`).

## 1. Features added (all `event_day < as_of`, tests in `tests/test_ledger_observability.py`)

`disposition_7d`, `disposition_30d` (latest reading inside the window, "NONE" otherwise),
`fresh_positive_disposition` / `fresh_negative_disposition` (a WILL/MAY or REFUSES/DISPUTE reading in
the last 7 days, 0 when none — an observation, not a gap), beside the existing `latest_disposition`,
`days_since_disposition`, `disposition_3d`, the 30-day positive/negative rates, `disposition_trend_90d`,
`disposition_recency_class` (class × FRESH ≤ 7 d / STALE) and `disposition_score_decayed` (score halved
every 14 days). The wd10 panel was rebuilt from its stored ledger — every pre-existing column
identical (`rebuild_panel.py` refuses otherwise).

## 2. The freshness ladder

| Experiment | disposition candidates offered | Gini [95% CI] | AUC | KS | Brier | cal gap | IV range | max VIF | PSI | max CSI | k | Features |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| EXP0 = d10 (baseline) | latest, score, days_since, 3d, rates 30d, count, trend | 0.5007 [.487–.515] | 0.7504 | 38.01 | 0.1820 | −0.020 | 0.023–0.520 | 4.30 | 0.014 | 0.069 | 10 | arrears_ratio, latest_disposition, cibil_score, no_answer_streak, overdue_amount, intent_calls_3m, last_commit_status, recent_ptp_status, calls_3m, paid_ratio_3m |
| EXP1 latest + days_since | those two only | 0.5007 [.487–.515] | 0.7504 | 38.01 | 0.1820 | −0.020 | 0.023–0.520 | 4.30 | 0.014 | 0.069 | 10 | identical to EXP0 |
| EXP2 + 3d / 7d / 30d | + three windowed classes | 0.5007 | 0.7504 | 38.01 | 0.1820 | −0.020 | 0.023–0.520 | 4.30 | 0.014 | 0.069 | 10 | identical — all three correlation-pruned against `latest_disposition` (\|r\| 0.80–0.93) |
| EXP3 + 30d pos/neg rates | + two rates | 0.5007 | 0.7504 | 38.01 | 0.1820 | −0.020 | 0.023–0.520 | 4.30 | 0.014 | 0.069 | 10 | identical — both pruned (\|r\| 0.73 / 0.71) |
| EXP4 + disposition × freshness | + fresh_pos/neg, score, decayed score, trend, recency class | 0.4997 [.487–.514] | 0.7498 | 37.58 | 0.1823 | −0.022 | 0.023–0.520 | 4.30 | 0.011 | 0.067 | 11 | arrears_ratio, **disposition_recency_class**, cibil_score, overdue_amount, intent_calls_3m, last_commit_status, call_answer_rate_6m, payment_gap_mean_12m, mean_call_duration_6m, recent_ptp_status, calls_3m |
| **Coverage: EXP4 on a 5-day sweep** (world wd10c) | as EXP4 | **0.5045** [.491–.518] | 0.7523 | 37.61 | 0.1827 | −0.024 | 0.027–0.506 | 4.28 | 0.012 | 0.066 | 10 | arrears_ratio, latest_disposition, overdue_amount, call_answer_rate_6m, paid_ratio_3m, intent_rate_6m, cibil_score, contact_rate_6m, commit_live_at_asof, mean_call_duration_6m |

Every row: 0 rank-order breaks, top-decile lift 1.39×, min segment Gini 0.30–0.33, WOE monotone, 13 gates
PASS. Observable ceiling of wd10 with the full 115-candidate universe: **0.526 / KS 39.71**; +true
willingness 0.551 / 41.2; joint oracle 0.576 / 42.9.

**Why freshness does nothing in a scorecard.** The freshness variants are all near-copies of
`latest_disposition` at the correlation gate (0.70): on this world the median reading is three days old,
so "the latest reading" and "the reading in the last 3 / 7 / 30 days" are the same thing for two-thirds of
the pool, and the IV-ordered correlation prune keeps the one with the highest IV. When the class × age
form is *forced* to stand in for it (EXP4), it costs 0.4 KS: the STALE levels are thin and their WOE noisy.
`days_since_disposition` (IV 0.055) is pruned against `no_answer_streak` (|r| 0.77) — both measure the
same thing, how long since anyone reached this borrower.

## 3. Model form (same inputs, same split; KS everywhere)

`model_form.py f1` — the EXP0/EXP1 selection, 10 inputs:

| form | k | OOT Gini | OOT KS | Brier | valid Gini |
|---|---|---|---|---|---|
| A  WOE + LR (the artifact's ranking) | 10 | 0.5014 | 37.81 | 0.1817 | 0.5241 |
| B  L1 LR on the same WOE columns, C by validation (3.0) | 10 | 0.5014 | 37.81 | 0.1818 | 0.5228 |
| B  L2 LR on the same WOE columns, C by validation (0.3) | 10 | 0.5014 | 37.83 | 0.1817 | 0.5228 |
| C  GBM on the same 10 raw inputs, early-stopped on validation | 10 | 0.5130 | 38.27 | 0.1792 | 0.5332 |
| C  GBM on the 40-feature VIF pool | 40 | 0.5178 | 38.82 | 0.1785 | 0.5349 |

On the EXP4 selection (11 inputs): A 0.5003 / 37.50 · B 0.5014 / 37.55 · C same inputs 0.5170 / 38.55 ·
C on the 43-feature pool **0.5194 / 39.03**.

- **Regularisation is worth nothing** (+0.000 Gini, +0.02 KS): the WOE-LR is not over-fitted, it is
  under-expressive.
- **Functional-form gap, same inputs: +0.012–0.017 Gini, +0.5–1.05 KS.** What the GBM uses that a
  linear-in-WOE card cannot: the reading's weight depending on its age and on the delinquency state
  (a REFUSES from a current borrower is not the same evidence as a REFUSES at 150 DPD).
- **Information gap, pool vs selection (GBM): +0.002–0.005 Gini, +0.5 KS** — the 30-odd pruned features
  carry a little that the ten do not.
- KS 39 is crossed only by the GBM over the pool (39.03) or the whole universe (39.71): the ceiling, not a
  production form. The repo's own challenger rule (GBM must beat the card by 0.05) does not promote it,
  and neither would the interpretability requirement of the brief.

## 4. Coverage (one experiment, then stop)

5-day sweep window, retries until reached, read noise 0.10, everything else as wd10:

| | wd10 (3-day) | wd10c (5-day) |
|---|---|---|
| reading within 7 d / 30 d | 0.70 / 0.82 | **0.76** / 0.85 |
| calls per delinquent account-month | 3.26 | 3.54 |
| material-payment rate | 0.331 | 0.331 |
| scorecard Gini / KS | 0.5007 / 38.01 | 0.5045 / **37.61** |
| GBM challenger | 0.508 | 0.510 |
| observable ceiling | 0.526 / 39.7 | 0.524 / 39.3 |

Six points more of the pool read within a week: +0.004 Gini on the card (inside the CI), **KS −0.4**, the
ceiling unmoved. Coverage does not improve KS; not pursued further.

## 5. The report items

1. **Best scorecard Gini:** 0.5045 (wd10c, 10 features); 0.5007 on the frozen wd10.
2. **Best scorecard KS:** 38.01 (EXP0/EXP1, wd10).
3. **Best GBM ceiling:** 0.526 / 39.71 (all 115 candidates); 0.519 / 39.03 (43-feature VIF pool); 0.513 / 38.27 on the card's own 10 inputs.
4. **Scorecard → GBM gap:** +0.012 Gini / +0.5 KS on the same inputs; +0.017 / +1.2 to the pool GBM; +0.025 / +1.7 to the full ceiling.
5. **Does freshness improve KS?** No. F1 = F2 = F3 exactly; the forced class × age form loses 0.4 KS.
6. **Does coverage improve KS?** No (−0.4 KS, +0.004 Gini).
7. **Top features, validation SFS (EXP0/EXP1):** arrears_ratio 0.414 → **latest_disposition +0.078** → cibil_score +0.010 → no_answer_streak +0.007 → overdue_amount +0.006 → intent_calls_3m +0.003 → last_commit_status +0.002 → recent_ptp_status +0.002 → calls_3m +0.001 → paid_ratio_3m +0.001 (validation 0.523; `ptp_kept_ratio` entered eleventh and was removed by the sign check).
8. **Both targets?** **Gini ≥ 0.50 yes (marginal), KS ≥ 39 no.** No scorecard configuration reached 39 without violating a constraint; the GBM that does is a ceiling measurement.

## 6. The remaining gap, quantified and attributed

KS 38.0 (card) → 39.7 (ceiling) = 1.7 points:

| component | KS | Gini | what it is |
|---|---|---|---|
| functional form, same 10 inputs | ~0.5 | 0.012 | a card cannot weight a reading by its age or by delinquency state |
| information dropped by selection | ~0.5–0.7 | 0.005 | pruned features carry a little more (the 43-feature GBM) |
| universe beyond the pool | ~0.7 | 0.007 | the remaining correlated variants |
| beyond the ceiling | — | 0.025 to +true w; 0.050 to oracle | a third of the pool unread in the window; one categorical reading; capacity and reachability |

None of these is a DGP question. Closing KS 39 legitimately means either a model form that can
express class × age × DPD interactions while staying explainable (a GAM / monotone small tree ensemble
with per-feature shape functions — a different production commitment than a points card), or a
disposition capture that reads willingness more finely than six classes (a 1–5 stance rating), which
would be another product assumption. With the card form and the six-class reading as they are, this
world's honest number is **Gini ≈ 0.50, KS ≈ 38**.
