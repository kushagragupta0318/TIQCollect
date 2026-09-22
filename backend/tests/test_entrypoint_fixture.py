"""
docker-entrypoint.sh: fixture restore versus seed, executed rather than read.

The entrypoint is bash, so these tests run the real script under bash with a
directory of stub executables ahead of PATH — pg_isready, psql, pg_restore,
alembic, python — each of which appends its argv to a log and returns what the
test tells it to. The assertions are on what the script CALLED, in order, for
each of the four states that matter:

    database already seeded           -> nothing, on either path
    empty + fixture present           -> pg_restore, then alembic upgrade head
    empty + fixture absent            -> python -m scripts.seed_data
    empty + fixture + SEED_FROM_FIXTURE=false -> the seed, not the fixture

and that a failed pg_restore stops the script (pipefail + set -e) rather than
falling through to a seed on top of a half-restored database.

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


def _run(tmp_path: Path, *, seeded: bool, fixture: bool, env: dict | None = None,
         restore_exit: int = 0) -> tuple[subprocess.CompletedProcess, list[str]]:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "calls.log"
    _stub(bindir, "pg_isready")
    # The entrypoint's only psql call is the seeded-check; answer it.
    _stub(bindir, "psql", f"echo {'t' if seeded else 'f'}")
    _stub(bindir, "pg_restore", f"exit {restore_exit}")
    _stub(bindir, "alembic")
    _stub(bindir, "python")
    fixture_path = tmp_path / "fixtures" / "fieldops-demo.dump"
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
    proc = subprocess.run(
        [BASH, str(ENTRYPOINT), "true"],
        env=e, capture_output=True, text=True, cwd=tmp_path,
    )
    calls = log.read_text().splitlines() if log.exists() else []
    return proc, calls


def _names(calls: list[str]) -> list[str]:
    return [c.split()[0] for c in calls]


def test_an_already_seeded_database_is_left_alone_even_with_a_fixture(tmp_path):
    proc, calls = _run(tmp_path, seeded=True, fixture=True)
    assert proc.returncode == 0, proc.stderr
    assert "pg_restore" not in _names(calls)
    assert "alembic" not in _names(calls)
    assert not any("seed_data" in c for c in calls)
    assert "already seeded" in proc.stdout


def test_an_empty_database_with_a_fixture_is_restored_then_migrated(tmp_path):
    proc, calls = _run(tmp_path, seeded=False, fixture=True)
    assert proc.returncode == 0, proc.stderr
    names = _names(calls)
    assert "pg_restore" in names and "alembic" in names
    # The restore is `pg_restore -f - | grep | psql`, so the two ends of the
    # pipe start concurrently and their log order is not defined; only the
    # ordering against alembic is.
    assert max(names.index("pg_restore"), names.index("psql", 1)) < names.index("alembic"),         "migrations must run AFTER the restore"
    restore = next(c for c in calls if c.startswith("pg_restore"))
    assert "-f -" in restore and "--no-owner" in restore, "SQL to stdout, not a direct -d restore (PG17 client vs PG16 server)"
    assert "-d" not in restore.split()
    loader = [c for c in calls if c.startswith("psql") and "ON_ERROR_STOP=1" in c]
    assert len(loader) == 1 and "-d fieldops" in loader[0] and "-h db" in loader[0]
    assert next(c for c in calls if c.startswith("alembic")) == "alembic upgrade head"
    assert not any("seed_data" in c for c in calls), "the seed must not also run"
    # The demo baseline is still captured, as the seed path always did.
    assert any("demo_reset --save" in c for c in calls)


def test_an_empty_database_without_a_fixture_falls_back_to_the_seed(tmp_path):
    proc, calls = _run(tmp_path, seeded=False, fixture=False)
    assert proc.returncode == 0, proc.stderr
    assert "pg_restore" not in _names(calls)
    assert any(c == "python -m scripts.seed_data" for c in calls)
    assert any("demo_reset --save" in c for c in calls)


def test_seed_from_fixture_false_forces_the_seed_even_when_the_fixture_exists(tmp_path):
    proc, calls = _run(tmp_path, seeded=False, fixture=True, env={"SEED_FROM_FIXTURE": "false"})
    assert proc.returncode == 0, proc.stderr
    assert "pg_restore" not in _names(calls)
    assert any(c == "python -m scripts.seed_data" for c in calls)


def test_an_empty_fixture_file_is_treated_as_absent(tmp_path):
    # `-s` in the script: a zero-byte file (a botched LFS pointer, a failed copy)
    # must not be handed to pg_restore.
    fixture_path = tmp_path / "fixtures" / "fieldops-demo.dump"
    fixture_path.parent.mkdir()
    fixture_path.write_bytes(b"")
    proc, calls = _run(tmp_path, seeded=False, fixture=False, env={"DEMO_FIXTURE": str(fixture_path)})
    assert proc.returncode == 0, proc.stderr
    assert "pg_restore" not in _names(calls)
    assert any(c == "python -m scripts.seed_data" for c in calls)


def test_a_failed_restore_stops_the_container_rather_than_seeding_over_it(tmp_path):
    proc, calls = _run(tmp_path, seeded=False, fixture=True, restore_exit=1)
    assert proc.returncode != 0
    names = _names(calls)
    assert "pg_restore" in names
    assert "alembic" not in names
    assert not any("seed_data" in c for c in calls)


def test_worker_and_beat_never_touch_the_database(tmp_path):
    proc, calls = _run(tmp_path, seeded=False, fixture=True, env={"RUN_SEED": "false"})
    assert proc.returncode == 0, proc.stderr
    assert _names(calls) == ["pg_isready"], calls


def test_the_committed_fixture_exists_and_is_a_custom_format_dump():
    f = ROOT / "backend" / "fixtures" / "fieldops-demo.dump"
    assert f.is_file(), "backend/fixtures/fieldops-demo.dump is what the entrypoint restores"
    assert f.stat().st_size > 1_000_000
    assert f.read_bytes()[:5] == b"PGDMP", "pg_dump custom-format magic"
