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

2026-09-24 (coordinator re-audit, MED 4-6 + LOW): the restore is one
transaction (COMMIT is sent only when pg_restore finished); the TOC is
captured before it is searched (no SIGPIPE refusal of a big fixture);
search_path/timezone are re-applied after a restore and on every start of the
seeding container; and every container, not only the seeding one, refuses a
v1 or unreadable database.

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
         restore_exit: int = 0, big_toc: bool = False,
         python_fails_on: str | None = None) -> tuple[subprocess.CompletedProcess, list[str]]:
    """generation: what the probe answers ('v2' | 'v1' | 'empty' | 'FAIL').
    fixture: None (absent), 'v2' or 'v1' (what `pg_restore -l` lists).
    big_toc: the listing goes on for 200,000 lines after the workforce line."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "calls.log"
    _stub(bindir, "pg_isready")
    # The only psql call that reads output is the generation probe. The
    # loader (ON_ERROR_STOP) has its stdin — the restore SQL — kept.
    _stub(bindir, "psql", ('case "$*" in *ON_ERROR_STOP*) cat > "$STUB_LOG.sql";; esac\n'
                           + ("exit 1" if generation == "FAIL" else f"echo {generation}")))
    listing = "echo '123; 2615 16386 SCHEMA - workforce fieldops'" if fixture == "v2" else \
              "echo '123; 1259 16400 TABLE public agents fieldops'"
    if big_toc:
        listing += "; for i in $(seq 1 200000); do echo \"$i; 1259 1 TABLE lending loans_$i fieldops\"; done"
    _stub(bindir, "pg_restore", f'if [ "$1" = "-l" ]; then {listing}; exit 0; fi\nexit {restore_exit}')
    _stub(bindir, "alembic")
    _stub(bindir, "python", f'case "$*" in *{python_fails_on}*) exit 1;; esac\nexit 0' if python_fails_on else "exit 0")
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


def _restore_sql(tmp_path: Path) -> list[str]:
    f = tmp_path / "calls.log.sql"
    return f.read_text().splitlines() if f.exists() else []


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
    # ... bar its database-level settings, re-applied idempotently (MED 4).
    assert "python -m scripts.ensure_db_settings --apply" in calls


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
    # One transaction (MED 6), and the settings a restore drops re-applied (MED 4).
    sql = _restore_sql(tmp_path)
    assert sql[0] == "BEGIN;" and sql[-1] == "COMMIT;"
    settings = calls.index("python -m scripts.ensure_db_settings --apply")
    assert restore[0] < settings < names.index("alembic")


def test_a_large_fixture_toc_is_not_refused(tmp_path):
    """MED 5: `pg_restore -l | grep -q` under pipefail — grep leaves at the
    first match, pg_restore takes SIGPIPE on the rest, and a VALID fixture
    was refused as "not a v2 dump"."""
    proc, calls = _run(tmp_path, generation="empty", fixture="v2", big_toc=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "not a v2 dump" not in proc.stdout
    assert any(c.startswith("pg_restore") and "-f -" in c for c in calls)


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
    # ... and keeps nothing: the transaction was opened and never committed,
    # so a half restore cannot read as an initialised v2 database (MED 6).
    sql = _restore_sql(tmp_path)
    assert sql[:1] == ["BEGIN;"] and "COMMIT;" not in sql


def test_worker_and_beat_never_change_the_database(tmp_path):
    proc, calls = _run(tmp_path, generation="empty", fixture="v2", env={"RUN_SEED": "false"})
    assert proc.returncode == 0, proc.stderr
    # The generation probe, and nothing else. (Its SQL spans lines, so the stub
    # log splits that one call; count the lines that START a call.)
    starts = [n for n in _names(calls) if n in ("pg_isready", "psql", "pg_restore", "alembic", "python")]
    assert starts == ["pg_isready", "psql"], calls
    assert "is empty" in proc.stdout          # said out loud, not silent


@pytest.mark.parametrize("generation", ["v1", "FAIL"])
def test_worker_and_beat_refuse_a_v1_or_unreadable_database_too(tmp_path, generation):
    """LOW: only the seeding container read the generation; worker, beat and
    a reloading dev API started blind on a v1 database."""
    proc, calls = _run(tmp_path, generation=generation, fixture="v2", env={"RUN_SEED": "false"})
    assert proc.returncode != 0
    assert _changed_nothing(calls)


def test_worker_and_beat_check_the_database_settings_but_never_set_them(tmp_path):
    proc, calls = _run(tmp_path, generation="v2", fixture=None, env={"RUN_SEED": "false"})
    assert proc.returncode == 0, proc.stderr
    assert "python -m scripts.ensure_db_settings --check" in calls
    assert not any("--apply" in c for c in calls)


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


@pytest.mark.parametrize("run_seed", ["true", "false"])
def test_a_v2_database_behind_the_code_refuses_to_start_and_is_never_upgraded(tmp_path, run_seed):
    """Coordinator decision 2026-09-24: never auto-upgrade at boot, but never
    run silently against an older schema either — every container refuses."""
    proc, calls = _run(tmp_path, generation="v2", fixture=None, env={"RUN_SEED": run_seed},
                       python_fails_on="check_migrations")
    assert proc.returncode != 0
    assert "python -m scripts.check_migrations" in calls
    assert "alembic" not in _names(calls)
    assert "migration head" in proc.stdout


def test_check_migrations_names_both_revisions_and_the_command(monkeypatch, capsys):
    from scripts import check_migrations as cm
    heads = cm.code_heads()
    assert len(heads) == 1                              # one head, v2 chain
    monkeypatch.setattr(cm, "database_heads", lambda url: set(heads))
    assert cm.main() == 0
    monkeypatch.setattr(cm, "database_heads", lambda url: {"v2_0001"})
    assert cm.main() == 1
    err = capsys.readouterr().err
    assert "v2_0001" in err and next(iter(heads)) in err and "alembic upgrade head" in err
