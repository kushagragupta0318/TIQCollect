# The structured-disposition ladder — current-willingness observability, 2026-09-15

**Brief.** Add a structured borrower disposition to pre-scoring contacts, generated from *current*
willingness with realistic read noise, at ~75% coverage, and run a reliability ladder (read noise
0.30 / 0.20 / 0.10) — everything else frozen (target, split, seed, `signal_scale`,
`observation_noise`, latent persistence and weights, metrics). Success: OOT Gini ≥ 0.50 **and**
KS ≥ 39 with ≥ 7 features.

**Result.** The reading is the single strongest feature this world has produced (IV 0.21–0.31,
second into every forward selection after `arrears_ratio`) and it moves the interpretable model from
0.477 to **0.501 / KS 38.0** at the most reliable level, with the observable ceiling at **0.525 / 39.3**.
**Gini ≥ 0.50 is reached at read noise 0.10 only, and only marginally (bootstrap CI 0.487–0.515); KS ≥ 39
is not reached at any level** — the ceiling itself only touches 39 at read noise 0.10. Nothing promoted;
`champion.txt` reads 1.1.0; every flag is off by default and off draws nothing.

Reproduce: `scripts/research/recovery_risk_obs/` (`build_worlds.py wd_off wd30 wd20 wd10`,
`run_ladder.py OUT d_off d30 d20 d10 d30b d20b d10b`, `disposition_audit.py wd30 wd20 wd10`).
Per-experiment JSON and the audit are beside this file.

---

## 1. What was built

| piece | where | what |
|---|---|---|
| `observe_disposition`, `disposition_read_noise`, three cut-points | `ledger/config.py` | On every **answered call** and every **met visit** the contact records one of `WILL_PAY · MAY_PAY · NO_COMMITMENT · REFUSES · HARDSHIP · DISPUTE`. Generated as `r = w_t + N(0, read_noise)` cut at 0.62 / 0.45 / 0.30 on the willingness scale (sd of w ≈ 0.25), then **HARDSHIP** overrides at the existing hardship-reporting rates (55% when in an income shock, 4% otherwise) and **DISPUTE** at the existing dispute rate (3%, +12 pts for fraud-flagged). Each reading is an independent draw. Vocabulary mirrors the schema's `RTP` / `DISPUTE` / `DefaultReason` families. |
| `pre_scoring_until_reached` | `ledger/config.py`, `simulator.py` | The pre-scoring sweep now retries an account on each of the 3 days before a snapshot **until it is reached** (answered or declined), then stops — tele-calling retries, not blanket dialling. Only a contact *inside* the window retires the account (the first build let any call in the cycle do it and reached 44%). |
| panel features | `ledger/panel.py: _disposition_history` | `latest_disposition`, `latest_disposition_score` (ordinal +2 … −2), `days_since_disposition`, `disposition_3d`, `positive/negative_disposition_rate_30d`, `disposition_count_30d`, `disposition_trend_90d`, and two freshness-aware forms — `disposition_recency_class` (class × FRESH ≤ 7 d / STALE) and `disposition_score_decayed` (score halved every 14 days). All `day < t`. |
| realism bands | `config.BANDS` | `disposition_positive_share` 0.30–0.70, `disposition_refuse_share` 0.08–0.35, gated only when the channel is on. Every world passes 23/23. |
| tests | `tests/test_ledger_observability.py` (+9) | recorded only where reached; a noisy read, not the latent (nearby readings disagree 5–80% of the time); off emits nothing; features at t−1 / t / t+1; windows, rates and trend; a future reading cannot overwrite the latest; the sweep retires only on a sweep contact. |

**Not built, deliberately:** a product column. The schema has no `borrower_disposition` on `CallLog` or
`Visit`; serving any of this needs a migration, an adapter implementation and the Phase 3 equality
harness widened — the brief is about the data, and no experiment reached the bar that would justify
the product change (Section 5).

## 2. Is the signal a realistic observation? (Section 7)

`disposition_audit.json` per world. Same world realisation for the three noise levels (the noise draw
is the last in the call block, so accounts, calls and payments are identical across wd30/wd20/wd10).

| | read noise 0.30 | 0.20 | 0.10 |
|---|---|---|---|
| readings per account-month | 2.33 | 2.33 | 2.33 |
| distribution WILL / MAY / NO_COMMIT / REFUSES / HARDSHIP / DISPUTE | .33 / .15 / .13 / .29 / .07 / .03 | .31 / .18 / .16 / .26 / .07 / .03 | .28 / .21 / .18 / .23 / .07 / .03 |
| **coverage**: reading in last 3 d / 30 d / ever | **0.66** / 0.82 / 0.90 | same | same |
| reached in last 3 d (answered or declined) | 0.75 | same | same |
| median age of the latest reading | 3 days | | |
| CSI train → OOT, `latest_disposition` / `disposition_3d` | 0.069 / 0.022 | 0.069 / 0.022 | 0.069 / 0.022 |
| mean true willingness, WILL_PAY → REFUSES | 0.645 → 0.320 | 0.691 → 0.267 | 0.741 → 0.206 |
| Spearman(score, willingness at as_of) | 0.53 | 0.67 | 0.81 |
| exact agreement with the noiseless class | **0.46** | **0.53** | **0.68** |
| FP / FN rate on "positive stance" (WILL or MAY) | 0.29 / 0.26 | 0.23 / 0.20 | 0.14 / 0.12 |
| bad rate, WILL_PAY vs REFUSES (3-day reading) | 0.53 vs 0.75 | 0.50 vs 0.79 | 0.46 vs 0.83 |
| **Gini added by the reading GIVEN true willingness** | **0.0000** | 0.0001 | 0.0002 |
| future events in any feature | none — `day < t`, boundary tests | | |

The last row is the leakage check that matters: conditional on the latent it reads, the reading carries
nothing about the outcome. It is a noisy channel to willingness and only that. Bad rates by class stay
between 0.46 and 0.84 at every level — no class is a label. Coverage is not universal: a third of the
pool has no reading in the window (unreachable), and 10% never had one.

**Plausibility judgement.** 0.30 (46% exact agreement, 29% of non-positive borrowers recorded as
positive) is a poor recorder; 0.20 (53%, 23%/20%) is a plausible trained tele-caller logging a stance
from one conversation; **0.10 (68%, 14%/12%) is the optimistic edge** — a reliable, consistently
applied disposition capture, still with one reading in seven wrong on stance. None is an oracle.

## 3. The ladder (Sections 6 and Final Output)

Production trainer on every row (WOE with forced trends → IV → corr → VIF → SFS on validation → sign
→ LR → segment calibration → 13 gates → GBM challenger); OOT = last six months, touched once per row.
`d_off` is the identical sweep world without the reading; `*b` rows add the freshness-aware forms.

| Experiment | Coverage (3d / 30d) | Reliability (read sd · class agreement) | **Observable ceiling** Gini / KS | **Scorecard Gini** [95% CI] | AUC | **KS** | Brier | cal gap | IV (selected) | max VIF | PSI | max CSI | k | GBM chall. | Selected features |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| EXP0 — 2.1.0, current world | — | — | 0.492 / 36.3 | 0.4698 [0.456–0.481] | 0.735 | 35.22 | 0.180 | −0.012 | 0.083–0.498 | 4.10 | 0.009 | 0.041 | 9 | 0.473 | arrears_ratio, cibil_score, paid_ratio_3m, overdue_amount, last_visit_outcome, intent_rate_6m, call_answer_rate_3m, pay_amount_cv_6m, visits_3m |
| d_off — sweep-until-reached, no reading | 0.75 reached | — | 0.502 / 37.8 | 0.4771 [0.464–0.490] | 0.739 | 35.93 | 0.186 | −0.017 | 0.023–0.528 | 4.39 | 0.009 | 0.057 | 12 | 0.480 | arrears_ratio, cibil_score, intent_calls_3m, overdue_amount, last_commit_status, mean_call_duration_6m, contact_rate_6m, paid_ratio_3m, no_answer_streak, commit_live_at_asof, last_call_duration, declined_rate_30d |
| **EXP1 — disposition, read sd 0.30** | 0.66 / 0.82 | 0.30 · 46% | 0.512 / 38.2 | 0.4862 [0.473–0.500] | 0.743 | 36.40 | 0.184 | −0.022 | 0.023–0.520 | 4.30 | 0.010 | 0.069 | 11 | 0.495 | arrears_ratio, cibil_score, intent_calls_3m, latest_disposition, overdue_amount, no_answer_streak, mean_call_duration_6m, last_commit_status, recent_ptp_status, calls_3m, payment_gap_mean_12m |
| **EXP2 — read sd 0.20** | 0.66 / 0.82 | 0.20 · 53% | 0.517 / 38.7 | 0.4920 [0.478–0.506] | 0.746 | 36.98 | 0.183 | −0.022 | 0.023–0.520 | 4.30 | 0.011 | 0.069 | 11 | 0.501 | arrears_ratio, latest_disposition, cibil_score, intent_calls_3m, overdue_amount, no_answer_streak, mean_call_duration_6m, last_commit_status, recent_ptp_status, calls_3m, payment_gap_mean_12m |
| **EXP3 — read sd 0.10** | 0.66 / 0.82 | 0.10 · 68% | **0.525 / 39.3** | **0.5007 [0.487–0.515]** | **0.750** | **38.01** | 0.182 | −0.020 | 0.023–0.520 | 4.30 | 0.014 | 0.069 | 10 | 0.508 | arrears_ratio, latest_disposition, cibil_score, no_answer_streak, overdue_amount, intent_calls_3m, last_commit_status, recent_ptp_status, calls_3m, paid_ratio_3m |
| EXP1b — + freshness forms | 0.66 / 0.82 | 0.30 | 0.512 / 38.2 | 0.4865 [0.474–0.500] | 0.743 | 36.47 | 0.184 | −0.021 | 0.023–0.520 | 4.31 | 0.009 | 0.067 | 12 | 0.495 | … disposition_recency_class, positive_disposition_rate_30d, days_since_negative_intent … |
| EXP2b — + freshness forms | 0.66 / 0.82 | 0.20 | 0.518 / 38.9 | 0.4916 [0.479–0.506] | 0.746 | 37.11 | 0.184 | −0.022 | 0.023–0.520 | 4.30 | 0.009 | 0.067 | 13 | 0.500 | … disposition_recency_class, positive_disposition_rate_30d … |
| EXP3b — + freshness forms | 0.66 / 0.82 | 0.10 | 0.525 / 39.5 | 0.4997 [0.487–0.514] | 0.750 | 37.58 | 0.182 | −0.022 | 0.023–0.520 | 4.30 | 0.011 | 0.067 | 11 | 0.508 | arrears_ratio, disposition_recency_class, cibil_score, overdue_amount, intent_calls_3m, last_commit_status, call_answer_rate_6m, payment_gap_mean_12m, mean_call_duration_6m, recent_ptp_status, calls_3m |

Every row: rank-order breaks 0 (top-5: 0), top-decile lift 1.37–1.39×, minimum segment Gini
0.30–0.33 (all five DPD buckets, up from 0.26 on 2.1.0), all 13 gates PASS, WOE monotone on every
selected feature, train ≤ OOT. EXP3 SFS path: arrears_ratio 0.414 → **+ latest_disposition 0.491
(+0.078)** → cibil 0.501 → no_answer_streak 0.508 → overdue 0.514 → intent_calls_3m 0.517 →
last_commit_status 0.519 → recent_ptp_status 0.521 → calls_3m 0.522 → paid_ratio_3m 0.523;
`ptp_kept_ratio` entered and was removed by the sign check.

WOE of `latest_disposition` at read sd 0.10 (train): WILL_PAY +0.76 (bad 0.49), MAY_PAY +0.13,
NONE +0.08, HARDSHIP −0.14, NO_COMMITMENT −0.34, REFUSES −0.96 (bad 0.84) — ordered as a stance
should be, with HARDSHIP between neutral and negative.

## 4. The final questions

1. **Gini ≥ 0.50?** Only at read sd 0.10: **0.5007**, and the bootstrap interval (0.487–0.515) straddles
   the line — a marginal pass, not a clear one. 0.20 → 0.492; 0.30 → 0.486.
2. **KS ≥ 39?** **No, at any level.** Best 38.0 (sd 0.10); the observable ceiling reaches 39.3–39.5 only
   at sd 0.10, so KS 39 would need the scorecard to capture essentially all observable information.
3. **Smallest legitimate intervention that reaches the target?** For Gini alone: a structured disposition
   at read sd 0.10 (68% class agreement, 14%/12% FP/FN) on the sweep-until-reached process (66% of the pool
   read within 3 days, 82% within 30). For Gini **and** KS: none tried. Each step of reliability buys
   ~+0.005 Gini / +0.6 KS on the scorecard; the reading's own ceiling (perfect read, this coverage) is
   0.550 / 41.2, so coverage and the categorical form, not reliability, bound what is left.
4. **Is the data generation realistic?** The channel is (Section 2): a noisy ordinal read with overrides,
   errorful at every level, adding nothing beyond the latent, PIT-safe, stable (CSI ≤ 0.07). Two things to
   weigh: sd 0.10 is the optimistic end of what a field process records, and the sweep raises call volume
   (calls per delinquent account-month ≈ 2.9 vs 1.3) and the material-payment rate (0.331 vs 0.306, contact
   prompts payment in this world; the intercept was not re-bisected).
5. **Strictly PIT-safe?** Yes: readings with `day < t` only; a reading on or after as_of is invisible
   (tests); commitment status from payments before t; leakage probes on every row (shuffled-label Gini
   ≈ 0, future-feature uplift ≈ +0.47 — the boundary is doing the work); the reading adds 0.0000–0.0002
   Gini given true willingness.
6. **Top features** (EXP3, drop-order by SFS gain): `arrears_ratio`, **`latest_disposition`** (IV 0.31),
   `cibil_score`, `no_answer_streak`, `overdue_amount`, then `intent_calls_3m`, `last_commit_status`,
   `recent_ptp_status`, `calls_3m`, `paid_ratio_3m`. The disposition is worth +0.078 validation Gini on
   entry — more than every other behavioural feature in the book combined.
7. **Remaining bottleneck.** Three parts, measured: (a) **coverage** — 34% of the pool has no reading in
   the window (unreachable by design; the reading-precision curve assumed 100%); (b) **form** — one
   categorical reading loses the continuous latent, and a WOE scorecard cannot weight it by freshness (the
   GBM, which can, sits +0.008 above it; the freshness-aware forms did not close that in a linear model);
   (c) **KS specifically** — a single-point statistic that the ceiling itself only touches at 39.3; the
   scorecard-to-ceiling gap of 0.025 Gini / 1.3 KS is the whole remaining distance. Beyond that lies the
   0.550 → 0.578 gap to the joint oracle: capacity and reachability, which no contact reading addresses.

## 5. What this does and does not license

The reading works as the diagnosis predicted — it is the first intervention to move the scorecard's
ceiling past 0.50 — and it is *observable* in the sense the brief asked for: a field a caller could
record. It is **not** yet servable: no such column exists in the schema, the adapter cannot compute it,
and its reliability in a real floor is an assumption until a real team has produced it. Adopting it
means a product decision (a `borrower_disposition` enum on `CallLog` and `Visit`, a migration, the
adapter, the equality harness) before any model trained on it can be promoted — and the honest
expectation from this ladder is Gini ~0.50 / KS ~38 at a reliability the product would have to earn.
