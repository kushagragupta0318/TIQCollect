"""Train the models and write their artifacts.

    python -m scripts.train_models                      # every model, full tier
    python -m scripts.train_models --model recovery_risk
    python -m scripts.train_models --tier dev           # fast loop

Artifacts land in app/ml/artifacts/<model>/<version>/ and ARE committed — the
dataset is regenerable from a seed, the model is the deliverable.

A model that fails its gates is still written (so the failure can be read) but
does NOT become champion. Promotion is never automatic on a FAIL.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.ml.pipeline import registry                      # noqa: E402
from app.ml.pipeline.config import REGISTRY               # noqa: E402
from app.ml.pipeline.report import write_model_document   # noqa: E402
from app.ml.pipeline.train import ModelTrainer            # noqa: E402

DATA_ROOT = BACKEND / "data" / "modelling"


def load_panel(tier: str) -> tuple[pd.DataFrame, dict]:
    d = DATA_ROOT / tier
    panel_path = d / "panel.parquet"
    if not panel_path.exists():
        raise SystemExit(
            f"no panel at {panel_path}\n"
            f"build it first:  python -m scripts.build_modelling_dataset --tier {tier}"
        )
    meta_path = d / "dataset_metadata.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    return pd.read_parquet(panel_path), meta


def prepare(panel: pd.DataFrame, model: str) -> pd.DataFrame:
    """Model-specific population and target construction.

    Kept here rather than in the trainer because "which rows is this model even
    about" is a definition, not a transformation. Contactability is undefined
    where nobody attempted a visit, so those rows are not 'negative' — they are
    not in the population at all.
    """
    if model == "contact_risk":
        df = panel[panel["visit_made"] == 1].copy()
        df["not_contacted"] = 1 - df["customer_met"]
        return df
    return panel


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", choices=list(REGISTRY) + ["all"], default="all")
    ap.add_argument("--tier", default="full")
    ap.add_argument("--no-champion", action="store_true")
    a = ap.parse_args()

    panel, meta = load_panel(a.tier)
    names = list(REGISTRY) if a.model == "all" else [a.model]

    results = []
    for name in names:
        spec = REGISTRY[name]
        print(f"\n{'='*78}\n  {name} v{spec.version}\n{'='*78}")
        df = prepare(panel, name)
        print(f"  population {len(df):,} rows   bad rate {df[spec.target].mean():.4f}")
        res = ModelTrainer(spec, df, dataset_meta=meta).run(
            make_champion=not a.no_champion)

        oot = res.metrics["oot"]
        chal = res.metrics["oot_challenger"]
        print(f"\n  selected {len(res.selected)} of "
              f"{len(spec.numeric_features) + len(spec.categorical_features)} features")
        print(f"  {', '.join(res.selected)}")
        print(f"\n  OUT-OF-TIME   Gini {oot['gini']:.4f}   KS {oot['ks']:.2f}   "
              f"top-decile lift {oot['top_decile_lift']:.2f}x   "
              f"breaks {oot['rank_order']['n_breaks']}")
        print(f"  challenger    Gini {chal['gini']:.4f}   "
              f"(champion = {res.champion_kind})")
        print(f"\n{res.gates.to_string(index=False)}")
        print(f"\n  GATES: {'PASS' if res.passed else 'FAIL'}")

        doc = write_model_document(res.artifact_dir)
        print(f"  artifact  {res.artifact_dir}")
        print(f"  document  {doc}")
        results.append((name, res.passed, oot["gini"], oot["ks"]))

    print(f"\n{'='*78}\n  SUMMARY\n{'='*78}")
    for name, passed, g, k in results:
        print(f"  {name:<18} Gini {g:.4f}  KS {k:5.2f}  {'PASS' if passed else 'FAIL'}")
    print()
    print(registry.list_models().to_string(index=False))
    return 0 if all(r[1] for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
