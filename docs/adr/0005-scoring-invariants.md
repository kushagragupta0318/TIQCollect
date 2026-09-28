# 0005. Scoring invariants

**Status:** Accepted, 2026-09-07 (scorecards) and 2026-09-09 (trained model).

## Decisions

1. **Features are point-in-time.**
   - `build_features` takes a mandatory `as_of`, and its history filters mean "before the
     `as_of` day began". It floors `as_of` to midnight.
   - `Loan.dpd`, `PTP.status` and `Loan.last_payment_date` are overwritten in place, so a
     feature read "as it is now" leaks the future.
   - `RepaymentSnapshot` freezes one row per (loan, day). `is_backfill` marks rows that cannot
     be made honest.
   - Delinquency history cannot be reconstructed from the live schema:
     `ml/pipeline/config.py` lists `NO_HISTORY_FEATURES` and `FEED_ONLY_FEATURES`.
2. **No output of the system is an input to it.** `_FORBIDDEN_FEATURE_KEYS`
   (`services/repayment_service.py`) raises if a feature dict carries a score. Recovery is
   deliberately not P(pay) × haircut.
   - **The reason is redundancy, double counting and version dependency, not leakage.**
   - The 2026-09-11 ablation measured each score added to the champion's features: every arm
     was −0.0001 to −0.0006 OOT Gini, `equivalent_keep_incumbent`.
   - The ban covers outputs only. Raw facts the scorecards read are fair model inputs.
3. **A factor with no evidence abstains; it never scores zero silently.** Its weight is withheld
   from the coverage denominator, and coverage is not renormalised.
   - Known exception, recorded on 2026-09-09: `ptp_kept_ratio` falls back to 0.5 for a borrower
     with no promise history in the ML adapter.
4. **Nothing hand-weighted presents itself as a model.**
   - `is_modelled` travels on `ScoreOutcome` and `ScoreResult`, and `ai_generated` on
     `LLMResult`, so a UI cannot put an "AI" chip on a scorecard by forgetting to check.
   - The four scorecards have no Gini or AUC figures because they are not models.
5. **A weight, band edge or factor-definition change is a model change: bump the version**
   (`scorecard-1.1.0`, `recovery-scorecard-1.1.0`). Rows written under two versions answer
   different questions.
6. **The coverage floor applies on every scoring path.** `MIN_FEATURE_COVERAGE = 0.60`, for
   single and batch scoring alike. A below-floor borrower is recorded as a DECLINED prediction
   and is absent from what the allocator receives.
7. **Everything trained here is trained on synthetic borrowers.** Every artifact carries
   `SYNTHETIC_WARNING`, and every surface that shows a model number keeps the convention.

## Consequences

- The Phase 3 equality harness (`tests/test_ledger_phase3_adapter_equality.py`) holds the live
  adapter equal to the simulator's panel on every feature. It is the gate for any schema type
  change (B20).
- A below-floor or failed score degrades to the non-ML path and **logs at ERROR**. Graceful
  degradation made the 2026-09-08 integration failures invisible for a full cycle.
