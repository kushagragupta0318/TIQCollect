"""Measures the GNPA p50 shift between mc-1.0.0 (daadc17: a random 1/12
monthly Sub-standard -> Doubtful hazard, drawn from the observed matrix) and
mc-1.1.0 (this checkout: the deterministic 12-month NPA-age rule) on the
SAME synthetic benchmark book, same seed, same everything else — isolating
exactly what GATES FIXED #2 in monte_carlo.py's CHANGELOG changed.

Why this script exists: a prior report claimed this moved GNPA p50 from
11.92 to 14.98. That number was in no commit or file, and the auditor who
re-verified 66f3bb9 could not reproduce it and expected a smaller effect.
This is how it was actually measured (see monte_carlo.py's CHANGELOG entry
dated 2026-09-24 for the two numbers this script produced, the seed and the
book, recorded rather than the unsourced ones).

daadc17's app/strategy/ (the whole package, not just monte_carlo.py — states
and synthetic changed too) is extracted with `git show` into a throwaway
temp directory and run in a SEPARATE subprocess, never imported into this
process: both versions define the module `app.strategy.monte_carlo`, and a
same-process sys.path swap would hand the second import the first version's
already-cached module.

    cd backend && python -m scripts.research.mc_gnpa_shift

Numpy does not promise its Generator distributions are stable across
versions (monte_carlo.py's own `simulate` docstring), so the two figures
below are only reproducible on the SAME numpy version this was run with —
this script prints it for exactly that reason.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]  # backend/
OLD_REV = "daadc17"
OLD_FILES = ("__init__.py", "backtest.py", "monte_carlo.py", "states.py", "synthetic.py")
BENCH_KW = {"n_accounts": 50_000, "n_paths": 1_000, "horizon_months": 12, "seed": 0}


def _extract_old_engine(tmp: Path) -> None:
    (tmp / "app").mkdir(parents=True)
    (tmp / "app" / "__init__.py").touch()
    strat = tmp / "app" / "strategy"
    strat.mkdir()
    for name in OLD_FILES:
        blob = subprocess.run(
            ["git", "show", f"{OLD_REV}:backend/app/strategy/{name}"],
            cwd=REPO_ROOT, check=True, capture_output=True, text=True, encoding="utf-8",
        ).stdout
        (strat / name).write_text(blob, encoding="utf-8")


def _run_old(tmp: Path) -> dict:
    code = (
        "import json, sys\n"
        f"sys.path.insert(0, {str(tmp)!r})\n"
        "from app.strategy.monte_carlo import benchmark\n"
        f"print(json.dumps(benchmark(**{BENCH_KW!r})))\n"
    )
    out = subprocess.run([sys.executable, "-c", code], check=True, capture_output=True, text=True,
                         encoding="utf-8").stdout
    return json.loads(out.strip().splitlines()[-1])


def _run_new() -> dict:
    sys.path.insert(0, str(REPO_ROOT))
    from app.strategy.monte_carlo import benchmark  # the checkout under test
    return benchmark(**BENCH_KW)


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        _extract_old_engine(tmp)
        old = _run_old(tmp)
    new = _run_new()

    import numpy
    print(f"python {sys.version.split()[0]}, numpy {numpy.__version__}")
    print(f"book: synthetic_book(n_accounts={BENCH_KW['n_accounts']}), "
          f"n_paths={BENCH_KW['n_paths']}, horizon_months={BENCH_KW['horizon_months']}, "
          f"seed={BENCH_KW['seed']}, scenario=PRESETS['adverse']")
    print(f"old ({OLD_REV}, mc-1.0.0, random 1/12 SUB->DOUBTFUL hazard): "
          f"GNPA p50 @ horizon = {old['gnpa_pct_p50_horizon']}")
    print(f"new (this checkout, mc-1.1.0, deterministic 12-month age rule):    "
          f"GNPA p50 @ horizon = {new['gnpa_pct_p50_horizon']}")


if __name__ == "__main__":
    main()
