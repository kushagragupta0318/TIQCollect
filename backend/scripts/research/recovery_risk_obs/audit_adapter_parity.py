"""Definitive feature parity: run the PRODUCTION adapter against a rewound
database built from the frozen wd10 recipe, and compare its key set and its
values with the panel's."""
import sys, json, math, warnings
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[3]))
warnings.filterwarnings("ignore")
from datetime import datetime, time, timedelta, timezone
import numpy as np, pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.ml.simulation.ledger import LedgerConfig, LedgerSimulator
from app.ml.simulation.ledger.materialise import Materialiser
from app.ml.simulation.ledger.panel import build_panel
from app.models.base import Base
from app.models.loan import Loan
from app.services.ml_scoring_service import MLScoringService

COLS = ['arrears_ratio', 'latest_disposition', 'cibil_score', 'no_answer_streak', 'overdue_amount',
        'intent_calls_3m', 'last_commit_status', 'recent_ptp_status', 'calls_3m', 'paid_ratio_3m',
        'ptp_amount_to_emi', 'employment_type', 'days_since_last_contact', 'disposition_recency_class',
        'interest_rate']
CFG = LedgerConfig(n_borrowers=300, months=12, seed=17, observe_declines=True,
                   observe_call_duration=True, observe_verbal_commitments=True,
                   pre_scoring_call_days=3, pre_scoring_call_attempts=3,
                   pre_scoring_until_reached=True, observe_disposition=True,
                   disposition_read_noise=0.10)
DAY = 300
engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
Session = sessionmaker(bind=engine)
Base.metadata.create_all(bind=engine)
led = LedgerSimulator(CFG).run(intercept=-4.23438)
panel = build_panel(led, CFG)
db = Session()
mat = Materialiser(led, CFG)
mat.load(db)
mat.rewind_to(db, DAY)
as_of = datetime.combine(CFG.start_date + timedelta(days=DAY), time(23, 59), tzinfo=timezone.utc)
want = panel[panel.month_index == DAY // CFG.cycle_days].set_index("loan_id")
svc = MLScoringService(db)
vectors = {}
for lid in want.index[:250]:
    loan = db.query(Loan).filter(Loan.id == lid).first()
    if loan is not None:
        vectors[lid] = svc.build_features(loan, as_of=as_of)
print("adapter rows", len(vectors))
keys = set().union(*(set(v) for v in vectors.values()))
print("\n=== the 15 reference features against the adapter ===")
out = []
for c in COLS:
    present = c in keys
    nonnull = sum(1 for v in vectors.values() if v.get(c) is not None) if present else 0
    agree = None
    if present and nonnull:
        diffs = []
        for lid, v in vectors.items():
            a, b = want.loc[lid, c] if c in want.columns else None, v.get(c)
            if isinstance(a, float) and math.isnan(a):
                a = None
            if a is None and b is None:
                continue
            if isinstance(a, (int, float)) and isinstance(b, (int, float)):
                diffs.append(abs(float(a) - float(b)))
            else:
                diffs.append(0.0 if str(a) == str(b) else 1.0)
        agree = (max(diffs) if diffs else 0.0)
    out.append({"feature": c, "adapter_emits_key": present, "non_null": nonnull,
                "max_abs_diff_vs_panel": None if agree is None else round(agree, 6)})
t = pd.DataFrame(out)
print(t.to_string(index=False))
missing = t[~t.adapter_emits_key].feature.tolist()
null_always = t[(t.adapter_emits_key) & (t.non_null == 0)].feature.tolist()
print("\nNOT emitted by the adapter:", missing)
print("emitted but always None:", null_always)
json.dump({"adapter_keys": sorted(keys), "table": out, "missing": missing,
           "null_always": null_always, "n_rows": len(vectors)},
          open(sys.argv[1], "w"), indent=1, default=str)
