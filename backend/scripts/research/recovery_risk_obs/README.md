# recovery_risk — the observability ladder, 2026-09-15

Research scripts for the question "can the ledger world produce better *legitimate*
observable evidence so that an interpretable model reaches Gini ≥ 0.50 / KS ≥ 39?".
Findings and every number:
`app/ml/artifacts/recovery_risk/2.1.0/observability/OBSERVABILITY_REPORT.md`.
Short answer: no — best interpretable 0.486 / 36.4, best ceiling 0.509 / 38.0; what
is missing is a willingness reading taken *at* scoring time.

Run from `backend/`:

```bash
python scripts/research/recovery_risk_obs/diagnose.py CANDIDATES.parquet UNIVARIATE.csv   # Section 2, current world
python scripts/research/recovery_risk_obs/build_worlds.py w0 w1 w2 w3 w4 w1z wi_chan wi_rho wi_obs
python scripts/research/recovery_risk_obs/run_ladder.py OUT exp1 exp2 exp3 exp4 exp3b exp4b exp1z exp5 exp6 wi_chan wi_rho wi_obs
python scripts/research/recovery_risk_obs/summarise.py OUT
python scripts/research/recovery_risk_obs/read_precision.py w1                              # Section 6.7
```

| script | does |
|---|---|
| `diagnose.py` | A/B/C/D of the brief on the current world: observable ceiling, oracle, 2.1.0, gaps, least-observed latent, coverage |
| `build_worlds.py` | one world per `LedgerConfig` variant (channels on/off, coverage, sweep, what-ifs), same tier and intercept as `data/ledger/full`; `w0` must be bit-identical to it up to the intercept's stored rounding |
| `run_ladder.py` | the PRODUCTION trainer on a scratch spec per experiment, artifacts redirected out of the repo; plus the world's ceiling / oracle on the same OOT rows |
| `summarise.py` | the Section 13 table |
| `read_precision.py` | the ceiling as a function of the precision of one fresh willingness reading |
| `rebuild_panel.py` | rebuild a stored world's panel after panel.py gains features; refuses if any existing column changes |
| `model_form.py` | A WOE-LR / B regularised LR / C GBM on one experiment's selected set and VIF pool, KS everywhere |
| `gam_ladder.py` | the interpretable-model ladder on the frozen wd10 world: WOE-LR -> additive GAM (boosted shape functions, monotone, 32 knots) -> + interactions -> pool -> validation-selected; GBM benchmarks; selection on validation KS |
| `gam_explain.py` | shape tables, monotonicity vs declared sign, missing routing, interaction grid, one borrower decomposed |
| `gam_search.py` | the final controlled search: admissibility by train->VALIDATION PSI, greedy add / interaction / prune / smoothing on validation KS, one OOT pass per frozen stage |
| `gam_gates.py` | hard gates on the frozen search model: CSI train->OOT, VIF, monotonicity vs declared sign, refit check |
| `disposition_audit.py` | Section 7 of the disposition brief: is the recorded stance a realistic observation (distribution, coverage, CSI, relation to the latent and to the outcome, FP/FN, nothing beyond the latent)? |
| `gam_common.py` | 2026-09-16: re-exports `interaction_cst_for` / `remap_index` / `remapped_order` from `app/ml/pipeline/gam.py` — ONE definition of the constraint in sklearn's remapped index space, after the audit found the ladder's declared pair had landed on a different one |
| `audit_production_readiness.py` | the 2026-09-16 audit, sections 1-3 and 5-7: artifact recovery and bit-for-bit refit, metadata, feature parity (reads `adapter_parity.json`), row-at-a-time score reproduction, calibration, stability incl. missingness drift |
| `audit_adapter_parity.py` | EXECUTED feature parity: the production adapter against a rewound database built from the wd10 recipe, all 15 reference features vs the panel |
| `audit_explainability.py` | exact decomposition (background substitution with the pair residual attributed), sigmoid check, determinism, the production gaps |

**The disposition ladder** (worlds `wd_off wd30 wd20 wd10`, experiments `d_off d30 d20 d10 d30b d20b d10b`):
a structured stance recorded at contact, generated from current willingness at read noise
0.30 / 0.20 / 0.10, on a sweep that stops once reached. Findings in
`app/ml/artifacts/recovery_risk/2.1.0/observability/DISPOSITION_REPORT.md`: Gini 0.501 / KS 38.0
at read noise 0.10 (ceiling 0.525 / 39.3) — Gini marginal, KS short at every level.
**The freshness ladder** (`f1 f2 f3 f4 f4c`, `FRESHNESS_REPORT.md`): freshness features change
nothing (all correlation-pruned against `latest_disposition`), a 5-day sweep buys Gini not KS,
and KS >= 39 is crossed only by a GBM over the VIF pool (39.03) — the functional-form gap is
+0.5-1.0 KS on the same inputs.
**The GAM ladder** (`GAM_REPORT.md`): additive shape functions on the same 10 inputs +0.59 KS
(38.40, matching the GBM on those inputs); one interaction +0.06; validation-selected 15 features
0.5118 / **38.84**. KS 39 is touched only by GBMs over pools with CSI 0.46.
**The final search** (`FINAL_SEARCH_REPORT.md`): over the 109 admissible candidates, no unused
feature is worth more than +0.09 validation KS; the validation-selected model (15 features, 3 pairs,
valid 40.98) scores OOT **38.77 / 0.5138** — the demonstrated ceiling of the frozen world is
KS ≈ 38.8. Stop condition met.

**The production-readiness audit and its closure** (`PRODUCTION_READINESS_AUDIT.md`,
2026-09-16): the reference GAM was found unservable on implementation grounds — four inputs
absent from the adapter, no engine support for the model type, no calibrator / bands / reason
codes — and its one interaction on the wrong pair (sklearn does not remap `interaction_cst`;
every number in the reports above stands, the label was wrong; refitted where declared, 38.65).
Closed the same day as **recovery_risk 2.2.0**: `scripts/train_recovery_risk_gam.py` (the
frozen protocol, no search), `app/ml/pipeline/gam.py` (serving, exact decomposition, bands,
reason codes), the four features in the adapter (two via migration `c9a3d5e7f102`), the
missingness monitor beside PSI. Report: `app/ml/artifacts/recovery_risk/2.2.0/PRODUCTION_READINESS_CLOSURE.md`.

The simulator flags these worlds use (`observe_declines`, `observe_call_duration`,
`observe_verbal_commitments`, `pre_scoring_call_days`, `channel_noise_scale`) are
**off by default**; `tests/test_ledger_observability.py` pins that off draws nothing.
Nothing here promotes. *(This used to end "the panel features added for the ladder are in
no production spec and not in the adapter" — true until 2026-09-16, when the fifteen the
GAM selected became `config.RECOVERY_RISK_GAM` and the four the adapter lacked were built.)*
