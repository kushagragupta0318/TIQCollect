# recovery_risk 2.1.0 — the discovery pass, 2026-09-15

Research scripts, not production code. They produced the numbers in
`app/ml/artifacts/recovery_risk/2.1.0/FRESH_DEVELOPMENT_REPORT.md` and their
outputs are kept beside the artifact under `discovery/`. Run from `backend/`
with an output directory:

```bash
python -m scripts.build_ledger_dataset --tier full          # the world
python scripts/research/recovery_risk_v21/build_candidates.py OUT/candidates.parquet
python scripts/research/recovery_risk_v21/univariate.py OUT  # dev split only
python scripts/research/recovery_risk_v21/families.py OUT    # ceilings, selection, A-D
python scripts/research/recovery_risk_v21/gap.py OUT         # information gap
```

| script | what it does | touches OOT? |
|---|---|---|
| `build_candidates.py` | ~50 extra point-in-time candidates straight from the event tables (`day < t`, `[t-W, t)`), joined to the panel | no (features only) |
| `univariate.py` | IV, WOE-Gini, missingness, per-month stability per candidate, by family — on train+valid | **no** |
| `families.py` | observable ceiling (GBM on every candidate), joint oracle, the pipeline's own selection (IV → corr → VIF → SFS on valid → sign), families A–D, per-family ablation | OOT scored once per final candidate |
| `gap.py` | what each hidden latent would add to the observables; calendar probe; live-PTP probe; WOE tables of the selected set | ceiling measurements only |

Nothing here changes `signal_scale`, `observation_noise`, the target, the
split or any gate. The six candidates the selection reached were then
implemented in `ledger/panel.py` and `services/ml_scoring_service.py` and
trained through `scripts/train_recovery_risk_v2.py --version 2.1.0`.
