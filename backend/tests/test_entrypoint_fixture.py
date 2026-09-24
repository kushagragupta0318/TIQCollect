"""
docker-entrypoint.sh: schema generation, fixture restore versus seed —
executed rather than read.

The entrypoint is bash, so these tests run the real script under bash with a
directory of stub executables ahead of PATH — pg_isready, psql, pg_restore,
alembic, python — each of which appends its argv to a log and returns what the
test tells it to. The assertions are on what the script CALLED, in order.

2026-09-24 (B11, coordinator audit gates 1 and 2) — the states changed. The
probe was `public.agents`, which does not exist on a v2 database, so every
restart of a populated v2 database looked empty: a crash-loop on the fixture
path and a destructive reseed on every boot on the seed path. The script now
reads the schema GENERATION:

    v2 (workforce.agents)               -> nothing, on either path
    v1 (public.agents)                  -> refuse to start, change nothing
    unreadable                          -> refuse to start, change nothing
    empty + v2 fixture                  -> pg_restore, then alembic upgrade head
    empty + a NON-v2 fixture            -> refuse, change nothing
    empty + no fixture                  -> alembic upgrade head, then the seed
    empty + SEED_FROM_FIXTURE=false     -> alembic upgrade head, then the seed

and a failed pg_restore still stops the script (pipefail + set -e) rather
than falling through to a seed on top of a half-restored database.

(test_the_committed_fixture_exists_and_is_a_custom_format_dump is gone for
now: it pinned backend/fixtures/fieldops-demo.dump — the v1 dump — as what
the entrypoint restores, and the entrypoint now refuses a v1 dump. B18
commits the v2 dump and brings the test back for it.)

Skipped where bash is not available (the script only ever runs in a Linux
container; on Windows this needs Git Bash on PATH).
"""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ENTRYPOINT = ROOT / "docker-entrypoint.sh"
BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(BASH is None, reason="bash not on PATH")

STUB = """#!/usr/bin/env bash
echo "$(basename "$0") $*" >> "$STUB_LOG"
{body}
"""


def _stub(bindir: Path, name: str, body: str = "exit 0") -> None:
    p = bindir / name
    p.write_text(STUB.format(body=body), newline="\n")
    p.chmod(p.stat().st_mode | stat.S_IEXEC)


def _run(tmp_path: Path, *, generation: str, fixture: str | None, env: dict | None = None,
         restore_exit: int = 0) -> tuple[subprocess.CompletedProcess, list[str]]:
    """generation: what the probe answers ('v2' | 'v1' | 'empty' | 'FAIL').
    fixture: None (absent), 'v2' or 'v1' (what `pg_restore -l` lists)."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "calls.log"
    _stub(bindir, "pg_isready")
    # The only psql call that reads output is the generation probe.
    _stub(bindir, "psql", "exit 1" if generation == "FAIL" else f"echo {generation}")
    listing = "echo '123; 2615 16386 SCHEMA - workforce fieldops'" if fixture == "v2" else \
              "echo '123; 1259 16400 TABLE public agents fieldops'"
    _stub(bindir, "pg_restore", f'if [ "$1" = "-l" ]; then {listing}; exit 0; fi\nexit {restore_exit}')
    _stub(bindir, "alembic")
    _stub(bindir, "python")
    fixture_path = tmp_path / "fixtures" / "fieldops-demo-v2.dump"
    if fixture:
        fixture_path.parent.mkdir()
        fixture_path.write_bytes(b"PGDMP\x00not-a-real-dump")

    e = {
        **os.environ,
        "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
        "STUB_LOG": str(log),
        "DATABASE_URL": "postgresql+psycopg2://u:p@db:5432/fieldops",
        "RUN_SEED": "true",
        "DEMO_FIXTURE": str(fixture_path),
        **(env or {}),
    }
    proc = subprocess.run([BASH, str(ENTRYPOINT), "true"], env=e, capture_output=True, text=True, cwd=tmp_path)
    calls = log.read_text().splitlines() if log.exists() else []
    return proc, calls


def _names(calls: list[str]) -> list[str]:
    return [c.split()[0] for c in calls]


def _changed_nothing(calls: list[str]) -> bool:
    return ("alembic" not in _names(calls) and not any(c.startswith("pg_restore -f") for c in calls)
            and not any("seed_data" in c for c in calls))


def test_a_v2_database_is_left_alone_even_with_a_fixture(tmp_path):
    proc, calls = _run(tmp_path, generation="v2", fixture="v2")
    assert proc.returncode == 0, proc.stderr
    assert _changed_nothing(calls)
    assert "already initialised" in proc.stdout


def test_a_v1_database_refuses_to_start_and_changes_nothing(tmp_path):
    """Gate 1: the v1 chain is off this image's upgrade path, so `alembic
    upgrade head` on a v1 database would die on an unknown revision — and v2
    code cannot run on v1 anyway."""
    proc, calls = _run(tmp_path, generation="v1", fixture="v2")
    assert proc.returncode != 0
    assert _changed_nothing(calls)
    assert "v1 schema" in proc.stdout and "alembic_v1.ini" in proc.stdout


def test_an_unreadable_generation_refuses_rather_than_guessing(tmp_path):
    proc, calls = _run(tmp_path, generation="FAIL", fixture="v2")
    assert proc.returncode != 0
    assert _changed_nothing(calls)


def test_an_empty_database_with_a_v2_fixture_is_restored_then_migrated(tmp_path):
    proc, calls = _run(tmp_path, generation="empty", fixture="v2")
    assert proc.returncode == 0, proc.stderr
    names = _names(calls)
    restore = [i for i, c in enumerate(calls) if c.startswith("pg_restore") and "-f -" in c]
    assert restore and "alembic" in names
    # The restore is `pg_restore -f - | sed | psql`, so the two ends of the
    # pipe start concurrently; only the ordering against alembic is defined.
    assert restore[0] < names.index("alembic"), "migrations must run AFTER the restore"
    assert "--no-owner" in calls[restore[0]] and "-d" not in calls[restore[0]].split()
    loader = [c for c in calls if c.startswith("psql") and "ON_ERROR_STOP=1" in c]
    assert len(loader) == 1 and "-d fieldops" in loader[0] and "-h db" in loader[0]
    assert next(c for c in calls if c.startswith("alembic")) == "alembic upgrade head"
    assert not any("seed_data" in c for c in calls), "the seed must not also run"
    assert any("demo_reset --save" in c for c in calls)


def test_a_non_v2_fixture_is_refused_before_anything_is_written(tmp_path):
    proc, calls = _run(tmp_path, generation="empty", fixture="v1")
    assert proc.returncode != 0
    assert _changed_nothing(calls)
    assert "not a v2 dump" in proc.stdout


def test_an_empty_database_without_a_fixture_is_migrated_then_seeded(tmp_path):
    proc, calls = _run(tmp_path, generation="empty", fixture=None)
    assert proc.returncode == 0, proc.stderr
    names = _names(calls)
    assert not any(c.startswith("pg_restore") for c in calls)
    seed = next(i for i, c in enumerate(calls) if c == "python -m scripts.seed_data")
    assert names.index("alembic") < seed, "the schema must exist before the seed"
    assert any("demo_reset --save" in c for c in calls)


def test_seed_from_fixture_false_forces_the_seed_even_when_the_fixture_exists(tmp_path):
    proc, calls = _run(tmp_path, generation="empty", fixture="v2", env={"SEED_FROM_FIXTURE": "false"})
    assert proc.returncode == 0, proc.stderr
    assert not any(c.startswith("pg_restore") for c in calls)
    assert any(c == "python -m scripts.seed_data" for c in calls)


def test_a_populated_v2_database_is_never_reseeded_on_restart(tmp_path):
    """Gate 2, the data-loss case: SEED_FROM_FIXTURE=false used to reseed a
    populated v2 database on EVERY boot, because public.agents never exists."""
    proc, calls = _run(tmp_path, generation="v2", fixture=None, env={"SEED_FROM_FIXTURE": "false"})
    assert proc.returncode == 0, proc.stderr
    assert _changed_nothing(calls)


def test_an_empty_fixture_file_is_treated_as_absent(tmp_path):
    # `-s` in the script: a zero-byte file (a botched LFS pointer, a failed copy)
    # must not be handed to pg_restore.
    fixture_path = tmp_path / "fixtures" / "fieldops-demo-v2.dump"
    fixture_path.parent.mkdir()
    fixture_path.write_bytes(b"")
    proc, calls = _run(tmp_path, generation="empty", fixture=None, env={"DEMO_FIXTURE": str(fixture_path)})
    assert proc.returncode == 0, proc.stderr
    assert not any(c.startswith("pg_restore") for c in calls)
    assert any(c == "python -m scripts.seed_data" for c in calls)


def test_a_failed_restore_stops_the_container_rather_than_seeding_over_it(tmp_path):
    proc, calls = _run(tmp_path, generation="empty", fixture="v2", restore_exit=1)
    assert proc.returncode != 0
    assert any(c.startswith("pg_restore") and "-f -" in c for c in calls)
    assert "alembic" not in _names(calls)
    assert not any("seed_data" in c for c in calls)


def test_worker_and_beat_never_touch_the_database(tmp_path):
    proc, calls = _run(tmp_path, generation="empty", fixture="v2", env={"RUN_SEED": "false"})
    assert proc.returncode == 0, proc.stderr
    assert _names(calls) == ["pg_isready"], calls


def test_the_v1_seed_refuses_to_run_unless_asked_by_name():
    """The seed drop_all()s the database. The entrypoint only reaches it on an
    empty database, but a destructive script must not depend on its caller's
    probe being right (gate 2)."""
    import sys
    env = {k: v for k, v in os.environ.items() if k != "ALLOW_V1_SEED"}
    proc = subprocess.run([sys.executable, "-m", "scripts.seed_data"], env=env, capture_output=True, text=True,
                          cwd=ROOT / "backend", timeout=120)
    assert proc.returncode == 0
    assert "Nothing seeded, nothing dropped" in proc.stdout
