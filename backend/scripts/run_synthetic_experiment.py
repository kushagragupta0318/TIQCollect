"""One command: build a synthetic past and run the real pipeline through it.

    python scripts/run_synthetic_experiment.py --reset --seed 42

Each stage below is the PRODUCTION code path, pointed at an isolated database.
Nothing here reimplements scoring, labelling, EB or training — if any of them
were broken, this would fail rather than paper over it. That is the point of
the exercise.

    1. generate    scripts/generate_synthetic_repayment_history.py
                   simulates a book forward through time and calls the real
                   RepaymentService.rescore at each scoring date
    2. label       RepaymentService.attach_outcomes()
                   the real _infer_outcome, the real 30-day horizon
    3. eb          scripts/backfill_eb_features.py
                   the real EmpiricalBayesAgentAdjuster, fitted point-in-time
    4. train       app/ml/train_shadow_model.py
                   the real trainer, temporal split, shadow only
    5. validate    scripts/validate_synthetic_eb.py
                   closed loop: did EB recover the agent skill that was planted?
    6. shadow      scripts/shadow_allocation_sim.py
                   capacity-constrained ranking comparison; the production
                   allocator is not touched and does not consume the model

ISOLATION
    Everything runs against DATABASE_URL pointing at `fieldops_synth` (or
    --db-name). The demo database is never opened by any stage. To remove the
    experiment entirely:

        python scripts/run_synthetic_experiment.py --drop

RESULTS ARE SYNTHETIC. They demonstrate that the architecture works. They say
NOTHING about how the model would perform on real borrowers.
"""
from __future__ import annotations

import argparse
import os
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
BACKEND = HERE.parent
sys.path.insert(0, str(BACKEND))

from scripts.generate_synthetic_repayment_history import (   # noqa: E402
    DEFAULT_SYNTH_DB, synth_url, _reset_database,
)


def run(label: str, argv: list[str], env: dict) -> None:
    print("\n" + "=" * 72)
    print(f"  {label}")
    print("=" * 72)
    proc = subprocess.run(argv, cwd=str(BACKEND), env=env)
    if proc.returncode != 0:
        raise SystemExit(f"stage failed: {label} (exit {proc.returncode})")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--borrowers", type=int, default=1400)
    ap.add_argument("--agents", type=int, default=6)
    ap.add_argument("--start", default="2025-09-01")
    ap.add_argument("--end", default="2026-08-31")
    ap.add_argument("--snapshot-every", type=int, default=21)
    ap.add_argument("--db-name", default=DEFAULT_SYNTH_DB)
    ap.add_argument("--reset", action="store_true",
                    help="drop and recreate the synthetic database, then generate")
    ap.add_argument("--drop", action="store_true",
                    help="delete the synthetic database and exit")
    ap.add_argument("--skip-generate", action="store_true",
                    help="reuse the existing synthetic book; label/EB/train only")
    args = ap.parse_args()

    if args.drop:
        import psycopg2
        from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
        from scripts.generate_synthetic_repayment_history import _demo_url
        dsn = (_demo_url().rsplit("/", 1)[0] + "/postgres").replace(
            "postgresql+psycopg2://", "postgresql://")
        conn = psycopg2.connect(dsn)
        conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        with conn.cursor() as cur:
            cur.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                        "WHERE datname = %s AND pid <> pg_backend_pid()", (args.db_name,))
            cur.execute(f'DROP DATABASE IF EXISTS "{args.db_name}"')
        conn.close()
        print(f"dropped {args.db_name}")
        return

    url = synth_url(args.db_name)
    env = dict(os.environ)
    env["DATABASE_URL"] = url
    env.setdefault("SECRET_KEY", "synthetic-experiment")
    env.setdefault("COMMAND_CENTRE_API_KEY", "synthetic-experiment")
    py = sys.executable

    print(f"synthetic database: {url}")

    if not args.skip_generate:
        gen = [py, "scripts/generate_synthetic_repayment_history.py",
               "--seed", str(args.seed), "--borrowers", str(args.borrowers),
               "--agents", str(args.agents), "--start", args.start,
               "--end", args.end, "--snapshot-every", str(args.snapshot_every),
               "--db-name", args.db_name]
        if args.reset:
            gen.append("--reset")
        run("1/6  GENERATE synthetic history (real rescore at each date)", gen, env)

    run("2/6  LABEL outcomes (real attach_outcomes, 30-day horizon)",
        [py, "scripts/label_synthetic_outcomes.py"], env)
    run("3/6  BACKFILL point-in-time EB (real EmpiricalBayesAgentAdjuster)",
        [py, "scripts/backfill_eb_features.py", "--apply"], env)
    run("4/6  TRAIN shadow model (real trainer, temporal split)",
        [py, "-m", "app.ml.train_shadow_model"], env)
    run("5/6  VALIDATE EB against the simulator's planted agent ability",
        [py, "scripts/validate_synthetic_eb.py"], env)
    run("6/6  SHADOW ALLOCATION simulation (ranking only, allocator untouched)",
        [py, "scripts/shadow_allocation_sim.py"], env)

    print("\n" + "=" * 72)
    print("  SYNTHETIC VALIDATION COMPLETE — NOT PRODUCTION PERFORMANCE")
    print("=" * 72)
    print(f"  database : {url}")
    print(f"  remove   : python scripts/run_synthetic_experiment.py --drop")


if __name__ == "__main__":
    main()
