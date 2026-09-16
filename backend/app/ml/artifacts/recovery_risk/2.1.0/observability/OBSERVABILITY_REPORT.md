# Improving the observable evidence — the experiment ladder, 2026-09-15

**Question.** Can the ledger world be made to produce better *legitimate* observable evidence of
borrower behaviour so that a ≥7-feature interpretable `recovery_risk` reaches OOT Gini ≥ 0.50 / KS ≥ 39 —
without touching the target, the split, the seed, `signal_scale`, `observation_noise`, latent persistence,
or the metric definitions?

**Answer.** No, not with the observation-process changes tried here, and the reason is now measured rather
than argued. The best interpretable model reached **OOT Gini 0.486 / KS 36.4** (EXP6, 13 features); the
best observable ceiling of any world reached **0.509 / 38.0**. What moves the number is not more history,
more channels or more contact — it is a reading of willingness taken *at* scoring time, and the product
has no column that records one at the precision required (Section 6).

Everything below is reproducible from `scripts/research/recovery_risk_obs/` and the JSON beside this
file. Nothing is promoted; `champion.txt` reads 1.1.0; the new simulator channels are **off by default**
and the flags-off book is bit-identical to the one every committed artifact was measured on
(`tests/test_ledger_observability.py`).

---

## 1. Diagnosis on the current world (Section 2 of the brief)

Same OOT rows as 2.1.0 (n = 24,161, bad rate 0.700). `observability/diagnose.log`.

| | Gini | KS |
|---|---|---|
| A. observable ceiling — GBM on all 99 legitimate candidates | 0.4920 | 36.29 |
| B. oracle, willingness + capacity + reachability only (LR / GBM) | 0.4981 / 0.4926 | 37.4 / 37.1 |
| B′. the same three latents **plus** the observables (GBM) | 0.5777 | 43.61 |
| C. 2.1.0 scorecard, production path | 0.4699 | 35.16 |
| D. scorecard → observable ceiling | +0.022 | +1.1 |
| D. observable ceiling → joint oracle | +0.086 | +7.3 |

Which latent is least observed (observables + one true latent, GBM): **willingness +0.059** (→ 0.551 / 42.1,
the target on its own); capacity +0.011; reachability +0.007; shock state +0.001. The observables recover
willingness at as_of with R² 0.41–0.46 (residual sd 0.18), capacity 0.45, reachability 0.35.
Coverage per delinquent account-month: 1.3 call attempts, 0.5 answered, 0.8 visits, 0.4 met,
0.23 intent flags, 0.13 promises.

## 2. What was built

All in `app/ml/simulation/ledger/`, config-gated, documented at each field of `LedgerConfig`:

| change | what it is | where the product stores it | outcome process touched? |
|---|---|---|---|
| `observe_declines` | a picked-up-and-cut call is `DECLINED`, drawn from low willingness (logit −2.0 − 3.5·(w−0.5) − 0.8·hostile + N(0, 0.4)); the flat 10% draw folds into NO_ANSWER | `CallOutcome.DECLINED` | no |
| `observe_call_duration` | log-normal seconds on answered calls, 4.2 + 0.9·(w−0.5) + 0.30·intent − 0.5·hostile + N(0, 0.55) | `CallLog.duration_seconds` | no |
| `observe_verbal_commitments` | a date named on 35% of answered calls (p from willingness + capacity, N(0, 0.45)), 2–10 days out; **kept/broken derived from the payment ledger** (≥ 0.5 × EMI between call and due + 2), never stored | `CallLog.verbal_payment_date` | **yes** — a live commitment adds +0.60 to the daily payment logit, half the +1.20 the hazard already gives a doorstep PTP. Without it 16% of phone promises were kept against a 30–70% band: a promise with no commitment effect is not a promise. Set once, not revisited. |
| `pre_scoring_call_days` / `_attempts` | a tele-calling sweep of the live delinquent pool in the last 3 days before each snapshot — 1 attempt (EXP5) or one on each day (EXP6). Same draws as any call. | `CallLog` rows | indirectly — an answered call already carried +0.30 for 7 days |
| `channel_noise_scale` | multiplies every channel-noise sd; 1.0 = identity; used only for a what-if | — | no |

Realism: four new bands (`declined_share_of_reached` 0.03–0.25, `commitment_share_of_answered`
0.15–0.60, `commitment_kept_rate`, `median_call_duration_s` 30–240), gated only when the channel is on.
**One band was corrected visibly**: `commitment_kept_rate` was written 0.30–0.70 by copying the PTP band,
measured 0.23, and moved to 0.15–0.70 with the reasoning in `config.BANDS` (shorter horizon, tighter grace,
no agent present, and the PTP figure includes its +1.20). The effect size was not raised to meet the band.
Every experiment world passes 21/21.

The intercept was held at the committed −4.23438 rather than re-bisected, so prevalence is a *consequence*:
material-payment rate 0.306 (current) → 0.318 (channels) → 0.324 (coverage) → 0.328 (1-attempt sweep) →
0.340 (3-attempt sweep). Contact prompts payment in this world; more of it means more payments.

Panel features added (`ledger/panel.py`, all `day < t`, windows `[t−W, t)`): channel aggregates
(`declined_rate_*`, `mean_call_duration_*`, `last_call_duration`, `commitments_*`, `commit_kept_ratio_*`,
`commit_broken_*`, `commit_live_at_asof`, `last_commit_status`, `days_since_commit_kept/broken`), recency
(`intent_rate_30d/90d`, `declined_rate_30d/90d`, `answered_rate_30d`, `calls_30d`, `answered_calls_30d`,
`days_since_positive/negative_intent`, `days_since_last_successful_contact`, `visits_30d`, `met_visits_30d`,
`recent_visit_outcome_30d`, `recent_ptp_status`, `ptp_conversion_90d`, `mean_call_duration_30d`),
payment/capacity (`payment_momentum_30_vs_90`, `payment_count_30d`, `partial_rate_90d`,
`payment_amount_trend`, `payment_gap_mean_12m`, `hardship_flag_90d`, `days_since_hardship`) and the
3-day reading (`calls_3d`, `answered_3d`, `declined_3d`, `intent_3d`, `duration_3d`, `reached_3d`).
Boundary tests: `tests/test_ledger_observability.py` (14). **None of these is in a production spec or the
adapter** — they were candidates for the ladder, and no experiment reached the bar that would justify
carrying them into `ml_scoring_service`.

## 3. The ladder (Section 10)

Every row is the **production trainer** (`ModelTrainer`: WOE with forced trends → IV → correlation → VIF →
SFS on validation → sign check → LR → segment calibration → 13 gates → GBM challenger) on a scratch spec;
OOT is the last six months of each world and is touched once per row. *Worlds differ in realisation*
(same seed and constants, different random stream once a channel draws), so cross-row differences carry
about ±0.007 Gini of book-to-book noise; the within-row `ceiling`, `+w` and `oracle` columns are the
like-for-like reference. `observability/summary.txt`, `observability/exp*.json`.

| experiment | world | bad rate | **Gini** | AUC | **KS** | Brier | cal gap | IV (selected) | max VIF | PSI | max CSI | k | GBM chall. | **observable ceiling** | + true w | joint oracle |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **EXP0 — 2.1.0 baseline** | current | 0.693 | **0.4698** | 0.7349 | **35.22** | 0.1800 | −0.012 | 0.083–0.498 | 4.10 | 0.0085 | 0.041 | 9 | 0.4732 | 0.492 / 36.3 | 0.551 | 0.578 |
| EXP1 — willingness channels | w1 | 0.683 | 0.4652 | 0.7326 | 34.82 | 0.1839 | −0.010 | 0.069–0.521 | 4.11 | 0.0081 | 0.045 | 10 | 0.4643 | 0.481 / 35.9 | 0.544 | 0.571 |
| EXP2 — + coverage (calls ×1.45) | w2 | 0.676 | 0.4462 | 0.7231 | 33.08 | 0.1896 | −0.009 | 0.043–0.511 | 4.19 | 0.0060 | 0.063 | 12 | 0.4500 | 0.474 / 35.2 | 0.527 | 0.554 |
| EXP3 — + 30d/90d recency | w2 | 0.676 | 0.4446 | 0.7223 | 32.99 | 0.1898 | −0.009 | 0.043–0.511 | 4.33 | 0.0065 | 0.063 | 12 | 0.4479 | 0.474 / 35.2 | 0.527 | 0.554 |
| EXP4 — + payment/capacity | w2 | 0.676 | 0.4441 | 0.7221 | 32.93 | 0.1899 | −0.009 | 0.043–0.511 | 4.84 | 0.0057 | 0.077 | 12 | 0.4461 | 0.474 / 35.2 | 0.527 | 0.554 |
| EXP3b — recency on w1 | w1 | 0.683 | 0.4661 | 0.7331 | 34.75 | 0.1838 | −0.010 | 0.069–0.521 | 4.31 | 0.0081 | 0.055 | 11 | 0.4669 | 0.481 / 35.9 | 0.544 | 0.571 |
| EXP4b — + payment/capacity on w1 | w1 | 0.683 | 0.4661 | 0.7331 | 34.75 | 0.1838 | −0.010 | 0.069–0.521 | 4.69 | 0.0081 | 0.055 | 11 | 0.4669 | 0.481 / 35.9 | 0.544 | 0.571 |
| EXP1z — channels, no commitment effect | w1z | 0.696 | 0.4593 | 0.7296 | 34.20 | 0.1811 | −0.010 | 0.081–0.519 | 4.03 | 0.0054 | 0.054 | 9 | 0.4616 | 0.484 / 35.9 | 0.545 | 0.571 |
| EXP5 — + pre-scoring sweep, 1 attempt | w3 | 0.672 | 0.4783 | 0.7391 | 35.38 | 0.1851 | −0.024 | 0.026–0.508 | 4.04 | 0.0125 | 0.070 | 15 | 0.4819 | 0.496 / 36.6 | 0.550 | 0.575 |
| **EXP6 — sweep, 3 attempts (best)** | w4 | 0.661 | **0.4860** | 0.7430 | **36.41** | 0.1870 | −0.016 | 0.037–0.502 | 4.47 | 0.0130 | 0.094 | 13 | 0.4915 | **0.509 / 38.0** | 0.551 | 0.579 |
| what-if: channel noise ×0.6 *(not adopted)* | wi_chan | 0.683 | 0.4608 | 0.7304 | 33.82 | 0.1845 | −0.011 | 0.030–0.504 | 4.80 | 0.0091 | 0.084 | 12 | 0.4626 | 0.483 / 36.1 | 0.543 | 0.569 |
| what-if: latent ρ 0.985→0.992 *(forbidden, measured)* | wi_rho | 0.685 | 0.4539 | 0.7269 | 34.00 | 0.1839 | −0.010 | 0.042–0.495 | 4.13 | 0.0043 | 0.082 | 10 | 0.4553 | 0.483 / 36.0 | 0.550 | 0.579 |
| what-if: observation_noise 1.0→0.85 *(forbidden, measured)* | wi_obs | 0.692 | 0.4663 | 0.7331 | 34.52 | 0.1815 | −0.011 | 0.062–0.477 | 4.66 | 0.0082 | 0.071 | 9 | 0.4703 | 0.489 / 36.7 | 0.551 | 0.577 |

All rows: rank-order breaks 0, gates PASS except EXP1z (min-segment Gini 0.16 in CURRENT). Per-row
SFS paths, sign drops, segment tables and bootstrap CIs are in the JSON files.

Selected features of the best row (EXP6): `arrears_ratio, intent_rate_6m, call_answer_rate_3m,
cibil_score, overdue_amount, mean_call_duration_6m, recent_ptp_status, no_answer_streak,
commitments_90d, calls_3m, contact_rate_6m, paid_ratio_3m, declined_rate_30d`.

## 4. What the ladder found

1. **The three channels carry information and add no discrimination.** `mean_call_duration_6m` IV 0.12 and
   selected in every world; `declined_rate_6m` IV 0.075; a kept commitment vs a broken one separates bad
   rates 0.56 vs 0.74. Yet the ceiling did not rise (0.492 → 0.481–0.484) and the scorecard did not either.
   They read the same willingness the existing channels (intent flag, refusal, promises, payment shape)
   already read, at the same staleness.
2. **More contact made the world *less* predictable.** EXP2 (calls ×1.45) lowered not only the scorecard
   (−0.019) but the joint oracle itself (0.571 → 0.554). An answered call adds +0.30 to the hazard for a
   week; calls that land *inside* the outcome window are random with respect to as_of, so they add outcome
   variance nothing at as_of can see. "Improve coverage" as a lever backfires in a world where contact
   causes payment.
3. **Recency and payment-shape features add nothing** (EXP3/3b/4/4b: identical selections, ±0.001). A
   30-day intent rate is still an average over readings with monthly persistence 0.635.
4. **Freshness is the lever, and it is quantified.** `observability/read_precision.log`: a single reading of
   willingness taken at as_of, on top of every observable, moves the ceiling to 0.500 at read sd 0.30
   (reliability 0.40), 0.512 at 0.20, 0.531 / KS 39.5 at 0.10, 0.544 at 0. The observables already recover
   willingness to residual sd 0.18 from *stale* events — a fresh, worse reading beats them.
5. **A pre-scoring sweep is the one process change that helped**, and it did so by delivering fresh readings:
   +0.012 Gini with one attempt (39% of the pool reached), +0.020 with three (ceiling 0.509 / 38.0 —
   the first above 0.50). It also doubles call volume and lifts the material-payment rate to 0.34, and the
   interpretable model on it stops at 0.486 / 36.4 with IV down to 0.037 and CSI up to 0.094.
6. **The forbidden single constants, at moderate size, would not have closed it either** (what-ifs, measured
   only): channel noise ×0.6 → 0.461, latent ρ 0.992 → 0.454, observation_noise 0.85 → 0.466; ceilings
   0.483–0.489. The target is not one dial away.

## 5. The final questions (Section 13)

| | |
|---|---|
| Did we reach Gini ≥ 0.50? | **No.** Best interpretable 0.486 (EXP6); best ceiling 0.509 (GBM, 101 inputs, not a scorecard). |
| Did we reach KS ≥ 39? | **No.** Best 36.4; best ceiling 38.0. |
| Which DATA change contributed most? | The pre-scoring tele-calling sweep (fresh readings at as_of): +0.02 Gini, +1.2 KS over the same world without it. The three CallLog channels alone: 0. Coverage: negative. Recency / payment features: 0. |
| Which features carry the improvement? | On EXP6 vs EXP1: `declined_rate_30d`, `commitments_90d`, `recent_ptp_status`, `call_answer_rate_3m` enter; `mean_call_duration_6m` and `intent_rate_6m` are in every world. The 3-day features themselves (`intent_3d` IV 0.038, `duration_3d` 0.058, `reached_3d` 0.033) were correlation-pruned into `days_since_positive_intent` / `days_since_last_answered_call`. |
| Are all quality metrics in range? | VIF < 5, PSI < 0.10, CSI < 0.10 (0.094 on EXP6 — at the edge), WOE monotone, calibration |gap| ≤ 0.024, ≥ 7 features: yes on every row. IV: EXP6 carries three features below the preferred 0.10 (min 0.037), as 2.1.0 carries three below (min 0.083). |
| Is it legitimate and point-in-time safe? | Yes for the observation side: every new event is a noisy draw from borrower state, every feature reads `day < t`, boundary tests pass, flags-off is bit-identical. Two things a reviewer must weigh: the +0.60 commitment effect is an *outcome-process* addition (argued from the existing PTP mechanism, set once); and the sweep raises contact volume and prevalence — it is a business-process assumption, not a data-quality fix. |

## 6. Section 12 — the honest close

1. Best achievable observable-model Gini (interpretable, ≥7 features): **0.486** (EXP6), 0.470 on the current world.
2. Best KS: **36.4** (EXP6), 35.2 on the current world.
3. Observable ceiling: **0.509 / 38.0** (EXP6 world); 0.492 / 36.3 (current world).
4. Oracle ceiling (w + cap + reach + observables): 0.575–0.579 / 43–44 in every world; latents alone 0.50 / 37.
5. Remaining gap to the target floor: **0.014 Gini / 2.6 KS from the best scorecard; 0.030 / 3.8 from the deployed candidate 2.1.0**; the ceiling of the best world clears Gini by 0.009 and misses KS by 1.0.
6. Responsible latent / channel: **current willingness**, and specifically its *freshness* — the channels that read it are all stale by the time of scoring; the only channel that can be fresh is a contact made just before scoring, and one contact is one noisy bit.
7. Smallest legitimate change that could close it: a **structured disposition reading recorded on every pre-scoring contact** — a field like `CallLog.borrower_disposition` (cooperative / evasive / refusing, or a 1–5 rating) captured by the caller — reaching ≥ 75% of the pool with reliability ≈ 0.85 (read sd ≈ 0.10 on this world's willingness scale). Measured on w1 that reading alone gives ceiling 0.531 / KS 39.5 with the observables, i.e. both targets, and it is an *observation* the product could actually store. It does not exist in the schema today, and its reliability would be an assumption until a real field team produced it.

**What is NOT concluded:** that the target is unreachable on this DGP family. It is unreachable with the
observation channels the product's schema can record today, at the coverage a collections floor can
realistically run, without a disposition reading at scoring time.
