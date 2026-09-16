"""Build the experiment worlds for the observability ladder (Section 10).

    python scripts/research/recovery_risk_obs/build_worlds.py [w0|w1|w2 ...]

    w0  channels OFF, coverage unchanged   -> must be BIT-IDENTICAL to data/ledger/full
    w1  channels ON,  coverage unchanged   (EXP1)
    w2  channels ON,  realistic coverage   (EXP2, also the world for EXP3/EXP4)

Same tier config as `scripts/build_ledger_dataset --tier full` (5,000 opening
borrowers, 24 months, seed 42) and the SAME calibrated intercept (-4.23438),
held fixed rather than re-bisected so the prevalence process is untouched;
the material-payment rate each world actually produces is printed.
"""
from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import replace
from pathlib import Path

import pandas as pd

BACKEND = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(BACKEND))

from app.ml.simulation.ledger import LedgerConfig, LedgerSimulator   # noqa: E402
from app.ml.simulation.ledger.panel import build_panel                # noqa: E402
from app.ml.simulation.ledger.realism import realism_report           # noqa: E402
from scripts.build_ledger_dataset import TIERS                        # noqa: E402

INTERCEPT = -4.23438            # data/ledger/full/dataset_metadata.json
BASE = TIERS["full"]
WORLDS = {
    "w0": replace(BASE),
    "w1": replace(BASE, observe_declines=True, observe_call_duration=True,
                  observe_verbal_commitments=True),
    # EXP2 — coverage. A delinquent account in field collections is dialled
    # several times a week, not 2.7 times a month; 0.13/day is ~3.9 attempts a
    # month, still conservative. Set once, here, before any model was run on
    # it, and not revisited.
    "w2": replace(BASE, observe_declines=True, observe_call_duration=True,
                  observe_verbal_commitments=True,
                  call_hazard_delinquent=0.13, call_hazard_current=0.020),
    # Attribution: the three channels with NO commitment effect on the
    # hazard — pure observation, outcome process untouched (fails the
    # commitment-kept band at 0.16; built to separate "new readings" from
    # "new mechanism" in the ceiling, not as a candidate world).
    "w1z": replace(BASE, observe_declines=True, observe_call_duration=True,
                   observe_verbal_commitments=True, commitment_hazard_effect=0.0),
    # EXP5 — a pre-scoring sweep, 3 days. See LedgerConfig.pre_scoring_call_days.
    "w3": replace(BASE, observe_declines=True, observe_call_duration=True,
                  observe_verbal_commitments=True, pre_scoring_call_days=3),
    # EXP6 — the same sweep, attempted on each of the 3 days (tele-calling
    # retries). The second and LAST process design; not iterated further.
    "w4": replace(BASE, observe_declines=True, observe_call_duration=True,
                  observe_verbal_commitments=True, pre_scoring_call_days=3,
                  pre_scoring_call_attempts=3),
    # ── The DISPOSITION ladder (the brief of 2026-09-15, later still) ──────
    # Sweep that stops once reached (~75% of the pool), plus a structured
    # disposition recorded on every answered call / met visit, at three read
    # noises. `wd_off` is the same world without the disposition, so the
    # reading itself can be attributed.
    "wd_off": replace(BASE, observe_declines=True, observe_call_duration=True,
                      observe_verbal_commitments=True, pre_scoring_call_days=3,
                      pre_scoring_call_attempts=3, pre_scoring_until_reached=True),
    "wd30": replace(BASE, observe_declines=True, observe_call_duration=True,
                    observe_verbal_commitments=True, pre_scoring_call_days=3,
                    pre_scoring_call_attempts=3, pre_scoring_until_reached=True,
                    observe_disposition=True, disposition_read_noise=0.30),
    "wd20": replace(BASE, observe_declines=True, observe_call_duration=True,
                    observe_verbal_commitments=True, pre_scoring_call_days=3,
                    pre_scoring_call_attempts=3, pre_scoring_until_reached=True,
                    observe_disposition=True, disposition_read_noise=0.20),
    "wd10": replace(BASE, observe_declines=True, observe_call_duration=True,
                    observe_verbal_commitments=True, pre_scoring_call_days=3,
                    pre_scoring_call_attempts=3, pre_scoring_until_reached=True,
                    observe_disposition=True, disposition_read_noise=0.10),
    # The ONE coverage experiment: a 5-day sweep window (until reached) instead
    # of 3, so an account that was out for three days gets two more tries.
    "wd10c": replace(BASE, observe_declines=True, observe_call_duration=True,
                     observe_verbal_commitments=True, pre_scoring_call_days=5,
                     pre_scoring_call_attempts=5, pre_scoring_until_reached=True,
                     observe_disposition=True, disposition_read_noise=0.10),
    # ── WHAT-IFS for Section 12.7, "the smallest change that could close it".
    # Measured, NOT adopted. Each changes ONE constant the brief names as
    # off-limits (or, for wi_chan, one it does not name but that is tuning).
    "wi_chan": replace(BASE, observe_declines=True, observe_call_duration=True,
                       observe_verbal_commitments=True, channel_noise_scale=0.6),
    "wi_rho": replace(BASE, observe_declines=True, observe_call_duration=True,
                      observe_verbal_commitments=True, latent_rho=0.992),
    "wi_obs": replace(BASE, observe_declines=True, observe_call_duration=True,
                      observe_verbal_commitments=True, observation_noise=0.85),
}
OUT = BACKEND / "data" / "ledger"


def _hash(df: pd.DataFrame, cols) -> str:
    return hashlib.sha256(pd.util.hash_pandas_object(
        df[cols].reset_index(drop=True), index=False).values).hexdigest()[:16]


def build(name: str) -> None:
    cfg = WORLDS[name]
    print(f"\n=== {name}: declines={cfg.observe_declines} duration={cfg.observe_call_duration} "
          f"commitments={cfg.observe_verbal_commitments} call_hazard={cfg.call_hazard_delinquent}/"
          f"{cfg.call_hazard_current} ===", flush=True)
    led = LedgerSimulator(cfg).run(intercept=INTERCEPT)
    panel = build_panel(led, cfg)
    rep = realism_report(led, panel, cfg)
    d = OUT / f"obs_{name}"
    d.mkdir(parents=True, exist_ok=True)
    for k, v in led.tables().items():
        v.to_parquet(d / f"{k}.parquet")
    panel.to_parquet(d / "panel.parquet")
    (d / "dataset_metadata.json").write_text(json.dumps(
        {"world": name, "intercept": INTERCEPT, "realism": rep,
         "config": {k: (str(v) if not isinstance(v, (int, float, bool, dict)) else v)
                    for k, v in cfg.__dict__.items()}}, indent=2, default=str))
    for c in rep["checks"]:
        print(f"  {c['check']:<34} {c['value']!s:>10}  {c['status']}")
    print(f"  realism {'PASS' if rep['passed'] else 'FAIL'} ({rep['n_failed']} failed)  "
          f"rows {len(panel):,} calls {len(led.calls):,} answered {int(led.calls.answered.sum()):,}  "
          f"bad rate {panel.y.mean():.4f}", flush=True)
    if name == "w0":
        ref = pd.read_parquet(OUT / "full" / "panel.parquet")
        cols = [c for c in ref.columns if c in panel.columns]
        same = _hash(ref, cols) == _hash(panel, cols)
        print(f"  w0 vs data/ledger/full on {len(cols)} shared columns: "
              f"{'IDENTICAL' if same else 'DIFFERENT'}", flush=True)


if __name__ == "__main__":
    for w in (sys.argv[1:] or ["w0", "w1", "w2"]):
        build(w)
