# The final controlled search — is OOT KS ≥ 39 reachable under the frozen data? — 2026-09-15

**Constraints, all held.** World `wd10` frozen (DGP, events, disposition, contacts, coverage, label, split,
seed, OOT rows). Candidates = the 115 observable features only. Admissibility = train → **validation** PSI
< 0.10 (OOT distributions never consulted during the search). Every accept / reject on validation KS.
Interpretable form only (exactly-additive shape functions, monotone where the sign is declared, explicit
interpretable pairs). OOT scored **once per frozen stage** at the end. Nothing promoted; `champion.txt`
reads 1.1.0; nothing committed.

**Answer: no.** The best validation-selected admissible model reaches **OOT KS 38.77 / Gini 0.5138** —
validation KS 40.98 — and every one of the search's validation gains (+0.05 to +0.10 each) failed to
transfer to OOT. The strongest admissible ceiling on this world is **KS ≈ 38.8** (38.84 from the previous
GAM, 38.77 here — the same number within noise). Every other hard gate passes. **Stop condition met.**

Script: `scripts/research/recovery_risk_obs/gam_search.py` (rules encoded in code); log and JSON in
`gam/search/` beside this file.

## 1. The search, step by step (validation only)

**Admissibility.** 109 of 115 candidates pass train → validation PSI < 0.10. Excluded, with their
train → validation PSI: `months_on_book` 0.46, `outstanding_to_sanction` 0.30, `paid_ratio_12m` 0.15,
`days_since_hardship` 0.12, `outstanding_principal` 0.11, `payments_6m` 0.10 — a young book seasoning.
These are precisely the features the earlier "GBM on the VIF pool" (KS 39.01) and "GBM on all 115"
(39.28) leaned on; they were never admissible.

**Start.** The previous best GAM (15 features, `latest_disposition × arrears_ratio`): validation KS 40.66.

**Step 1 — every unused admissible candidate, added alone** (conditioned on the start model, validation
KS gain): `payment_count_30d` +0.09 · `commitments_90d` +0.08 · `city` +0.08 · `fraud_flag` +0.07 ·
`paid_ratio_1m` +0.06 · `partial_rate_90d` +0.05 · `mean_call_duration_6m` +0.04 · `days_since_last_payment`
+0.04 · `disposition_count_30d` +0.03 · everything else ≤ +0.03 (PTP kept/conversion, hardship, RTP,
answered-30d, sanction, days-since-call: 0.00). No unused candidate carries even a quarter of a KS point.

**Step 2 — greedy forward, accept ≥ +0.05:** `payment_count_30d` +0.09 (40.75), `mean_call_duration_6m`
+0.05 (40.80); `commitments_90d`, `city`, `fraud_flag`, `paid_ratio_1m`, `partial_rate_90d` and the rest
fell below +0.05 once re-fitted in combination.

**Step 3 — interactions from a fixed interpretable list, accept ≥ +0.05:** `latest_disposition ×
cibil_score` +0.06 (40.86), `paid_ratio_3m × arrears_ratio` +0.10 (40.96); rejected:
`disposition_recency_class × arrears_ratio` +0.01, `latest_disposition × dpd` −0.49, `× calls_3m` −0.14,
`recent_ptp_status × ptp_amount_to_emi` −0.10, `last_commit_status × arrears_ratio` −0.01,
`no_answer_streak × latest_disposition` −0.12.

**Step 4 — backward pruning (drop if it costs < 0.05):** `no_answer_streak` (+0.04 when dropped) and
`interest_rate` (−0.02) removed → 15 features, validation 40.98.

**Step 5 — smoothing grid on validation:** 32 knots / min-leaf 300 = 40.98; 64 knots = 40.95;
min-leaf 150 = 40.98. Kept the original.

## 2. The frozen model and its single OOT pass

| stage (frozen, then scored once) | k | pairs | valid KS | valid Gini | **OOT KS** | **OOT Gini** | AUC | Brier | cal gap | breaks | lift | min seg | PSI |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| start: previous best GAM | 15 | 1 | 40.66 | 0.5348 | **38.84** | 0.5118 | 0.7559 | 0.1798 | −0.014 | 0 | 1.40 | 0.351 | 0.011 |
| + payment_count_30d | 16 | 1 | 40.75 | 0.5348 | 38.73 | 0.5121 | 0.7561 | 0.1798 | −0.014 | 0 | 1.40 | 0.353 | 0.010 |
| + mean_call_duration_6m | 17 | 1 | 40.80 | 0.5354 | 38.66 | 0.5130 | 0.7565 | 0.1797 | −0.015 | 0 | 1.40 | 0.354 | 0.010 |
| + latest_disposition × cibil_score | 17 | 2 | 40.86 | 0.5353 | 38.66 | 0.5128 | 0.7564 | 0.1797 | −0.015 | 0 | 1.40 | 0.355 | 0.010 |
| + paid_ratio_3m × arrears_ratio | 17 | 3 | 40.96 | 0.5360 | 38.79 | 0.5136 | 0.7568 | 0.1796 | −0.015 | 0 | 1.41 | 0.357 | 0.010 |
| **final: after pruning** | **15** | **3** | **40.98** | 0.5356 | **38.77** | **0.5138** | 0.7569 | 0.1796 | −0.015 | 0 | 1.41 | 0.357 | 0.010 |

**Final feature list:** `arrears_ratio`, `latest_disposition`, `cibil_score`, `overdue_amount`,
`intent_calls_3m`, `last_commit_status`, `recent_ptp_status`, `calls_3m`, `paid_ratio_3m`,
`ptp_amount_to_emi`, `employment_type`, `days_since_last_contact`, `disposition_recency_class`,
`payment_count_30d`, `mean_call_duration_6m`. **Interactions:** `latest_disposition × arrears_ratio`,
`latest_disposition × cibil_score`, `paid_ratio_3m × arrears_ratio`. Form: additive shape functions,
32 knots, min-leaf 300, L2 1, 577 trees (validation-chosen).

**Incremental contribution of every accepted change:** validation +0.09 / +0.05 / +0.06 / +0.10 /
pruning +0.02 — **OOT −0.11 / −0.07 / 0.00 / +0.13 / −0.02.** The validation increments are of the size of
validation-set KS noise and net to −0.07 on OOT. That is the finding: the search found nothing the
previous GAM did not already have.

## 3. Hard gates on the frozen model

| gate | value | result |
|---|---|---|
| OOT KS ≥ 39.00 | **38.77** | **FAIL** |
| OOT Gini ≥ 0.50 | 0.5138 | PASS |
| AUC 0.70–0.85 | 0.7569 | PASS |
| score PSI < 0.10 | 0.010 | PASS |
| CSI < 0.10 (max over the 15) | 0.070 (`disposition_recency_class`), `latest_disposition` 0.069, `last_commit_status` 0.066, all others ≤ 0.064 | PASS |
| VIF < 5 (numeric inputs) | max 2.03 (`arrears_ratio`) | PASS |
| rank-order breaks | 0 | PASS |
| calibration \|gap\| < 0.03 | 0.015 | PASS |
| monotone where declared | all 8 declared-sign numerics monotone in their direction (`calls_3m`, `days_since_last_contact` unconstrained) | PASS |
| ≥ 7 features | 15 | PASS |
| IV 0.10–0.50 preferred | 9 of 15 inside; `intent_calls_3m` 0.023, `last_commit_status` 0.090, `recent_ptp_status` 0.075, `ptp_amount_to_emi` 0.007, `employment_type` 0.010, `days_since_last_contact` 0.019 below; `arrears_ratio` 0.519 above | noted |
| interpretable | exactly additive + 3 readable pairs; shape tables per `gam_explain.py` | PASS |

## 4. Where the gap to 39 comes from

KS 38.8 (best admissible) → 39.0 (GBM, 43-feature pool) → 39.3 (GBM, all 115): the missing 0.2–0.5
points are attributable, and none of the three sources is admissible:

1. **Unavailable observable signal — none left.** The single-add pass over all 94 unused admissible
   candidates found no feature worth more than +0.09 validation KS, and those transfers were negative on
   OOT. PTP kept / conversion, hardship, RTP, visit counts, recency variants: 0.00.
2. **Model form — exhausted.** On the same inputs the additive GAM equals the unrestricted GBM (38.40 vs
   38.39 in the ladder); three explicit interactions add +0.13 OOT on the best stage and −0.07 net.
   Finer smoothing (64 knots, leaf 150) changes nothing on validation.
3. **Stability — this is where the last half-point lives.** The GBMs that cross 39 use `months_on_book`
   (train → OOT PSI 0.52), `outstanding_to_sanction` (0.35), `paid_ratio_12m`, `outstanding_principal`,
   `payments_6m` — features whose distribution moves as the book seasons, excluded by the CSI gate before
   any model sees them. What they carry is *time*, not borrower behaviour: a model that learned them
   would be reading the calendar.

**Stop.** Under the frozen data, the stability gate and the interpretability requirement, the
demonstrated ceiling is **OOT KS ≈ 38.8, Gini ≈ 0.51** (best admissible GAM, 15 features, 1–3
interactions; the previous 15-feature GAM at 38.84 is the compact choice, this search's model at 38.77
the validation-selected one — indistinguishable). KS 39 is not reachable from this world without either
new observable information (a finer disposition reading, higher fresh-read coverage — both product
changes already reported as the only remaining levers) or a stability bar that admits seasoning
features, which is not proposed.
