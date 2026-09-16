"""Rebuild a stored world's panel from its ledger tables (no re-simulation).

    python scripts/research/recovery_risk_obs/rebuild_panel.py wd10

Used when panel.py gains features after a world was built. Existing columns
must come back identical; the script checks and refuses otherwise.
"""
import hashlib, json, sys
from pathlib import Path
import pandas as pd
BACKEND = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(BACKEND))
from app.ml.simulation.ledger import LedgerConfig                       # noqa: E402
from app.ml.simulation.ledger.panel import build_panel                  # noqa: E402
from app.ml.simulation.ledger.simulator import Ledger                   # noqa: E402

for w in sys.argv[1:]:
    d = BACKEND / "data" / "ledger" / f"obs_{w}"
    meta = json.loads((d / "dataset_metadata.json").read_text())
    cfg = LedgerConfig(**{k: v for k, v in meta["config"].items()
                          if k in LedgerConfig.__dataclass_fields__ and k != "start_date"})
    tables = {k: pd.read_parquet(d / f"{k}.parquet") for k in
              ("borrowers", "loans", "installments", "bureau_pulls", "payments", "visits",
               "ptps", "lifecycle", "agents", "ground_truth", "calls", "flags")}
    led = Ledger(**tables, config=meta["config"])
    old = pd.read_parquet(d / "panel.parquet")
    new = build_panel(led, cfg)
    shared = [c for c in old.columns if c in new.columns]
    h = lambda df: hashlib.sha256(pd.util.hash_pandas_object(df[shared].reset_index(drop=True), index=False).values).hexdigest()[:12]
    assert h(old) == h(new), f"{w}: existing columns changed on rebuild"
    new.to_parquet(d / "panel.parquet")
    print(f"{w}: rebuilt, {len(new.columns)} columns (+{len(new.columns) - len(old.columns)}), existing {len(shared)} identical")
