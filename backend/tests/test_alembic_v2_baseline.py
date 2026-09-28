"""The v2 Alembic baseline agrees with the models, without a database.

2026-09-24 (B11, coordinator audit gates 4 and MED 5). Postgres-level proof
(`alembic upgrade head` / `downgrade base` / `alembic check` = "No new upgrade
operations detected") was taken on a private database and is recorded in the
commit; these tests are what CI can run.

- ENUM DRIFT is the gap `alembic check` cannot see: it never compares enum
  VALUES, so a new AuditAction member would pass check and SQLite and then
  fail at INSERT on Postgres. v2_0001 freezes every type's values; any later
  revision that adds values declares them in `ENUM_ADDITIONS`; together they
  must equal every native Enum in Base.metadata, in order.
- v2_0002 is re-derived from the committed raw autogenerate by the committed
  post-processor and must match byte for byte.
"""
from __future__ import annotations

import importlib.util
import pathlib

import pytest
from sqlalchemy import Enum

import app.models  # noqa: F401
from app.core.database import SEARCH_PATH, Base

BACKEND = pathlib.Path(__file__).resolve().parents[1]
VERSIONS = BACKEND / "alembic" / "versions"


def _load(path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(f"rev_{path.stem}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _chain():
    """The live revisions, base first."""
    mods = {m.revision: m for m in (_load(p) for p in VERSIONS.glob("*.py"))}
    by_parent = {m.down_revision: m for m in mods.values()}
    out, cur = [], by_parent[None]
    while cur is not None:
        out.append(cur)
        cur = by_parent.get(cur.revision)
    assert len(out) == len(mods), "the chain is not linear"
    return out


def _migrated_enums() -> dict[str, list[str]]:
    chain = _chain()
    enums = {name: list(values) for name, values in chain[0].ENUMS}
    for rev in chain[1:]:
        for name, values in getattr(rev, "ENUM_ADDITIONS", ()):
            enums.setdefault(name, []).extend(values)
    return enums


def _model_enums() -> dict[str, list[str]]:
    out = {}
    for table in Base.metadata.sorted_tables:
        for col in table.columns:
            if isinstance(col.type, Enum) and col.type.native_enum and col.type.name:
                out[col.type.name] = list(col.type.enums)
    return out


def _drift(migrated: dict, models: dict) -> dict:
    missing = {n: ("<not migrated>", models[n]) for n in models if n not in migrated}
    stale = {n: (migrated[n], "<not in models>") for n in migrated if n not in models}
    changed = {n: (migrated[n], models[n]) for n in models if n in migrated and migrated[n] != models[n]}
    return {**missing, **stale, **changed}


def test_every_model_enum_is_migrated_with_the_same_values_in_order():
    assert _drift(_migrated_enums(), _model_enums()) == {}


def test_the_drift_check_catches_a_new_value_a_reorder_and_a_new_type():
    """Mutation check, executed through the same function."""
    migrated, models = _migrated_enums(), _model_enums()
    added = dict(models, audit_action_enum=models["audit_action_enum"] + ["SOMETHING_NEW"])
    reordered = dict(models, user_role_enum=list(reversed(models["user_role_enum"])))
    new_type = dict(models, brand_new_enum=["A"])
    for mutated in (added, reordered, new_type):
        assert _drift(migrated, mutated) != {}


def test_the_database_search_path_is_the_one_the_app_documents():
    assert _chain()[0].SEARCH_PATH == SEARCH_PATH
    assert SEARCH_PATH.split(", ")[0] == "public"          # audit W6


def test_v2_0002_is_exactly_the_postprocessed_raw_autogenerate():
    from scripts.dev.alembic_v2_postprocess import postprocess
    raw = (BACKEND / "alembic" / "raw" / "v2_0002_tables.autogen.txt").read_text(encoding="utf-8")
    committed = (VERSIONS / "v2_0002_tables.py").read_text(encoding="utf-8")
    assert postprocess(raw) == committed


def test_v2_0002_leaves_no_use_alter_fk_inline_and_partitions_all_five():
    src = (VERSIONS / "v2_0002_tables.py").read_text(encoding="utf-8")
    body = src[src.index("def upgrade"):]
    assert "use_alter=True" not in body
    assert body.count("postgresql_partition_by=") == 5
    assert "sa.Enum(" not in body and "sa.JSON()" not in body
    # NO ACTION, not RESTRICT (design §2.8, audit MED 7)
    assert "RESTRICT" not in body


def test_the_session_revoke_reasons_check_is_migrated_as_the_model_declares_it():
    """`alembic check` does not compare CHECK text, so a reason added to
    models/identity.SESSION_REVOKE_REASONS without a migration would pass it
    and then fail every INSERT on Postgres. The latest revision that freezes
    the list must equal the model's tuple, in order."""
    from app.models.identity import SESSION_REVOKE_REASONS
    frozen = [m.SESSION_REVOKE_REASONS for m in _chain() if hasattr(m, "SESSION_REVOKE_REASONS")]
    assert frozen, "no revision freezes the session revoke reasons"
    assert tuple(frozen[-1]) == tuple(SESSION_REVOKE_REASONS)
    # ... and the migrated SQL names every one of them.
    latest = next(m for m in reversed(_chain()) if hasattr(m, "SESSION_REVOKE_REASONS"))
    assert all(f"'{r}'" in latest._check(latest.SESSION_REVOKE_REASONS) for r in SESSION_REVOKE_REASONS)


class _FakeOp:
    """Stands in for alembic.op: answers the downgrade's row count and records DDL."""
    def __init__(self, count):
        self.count, self.ddl = count, []

    def get_bind(self):
        op = self

        class _Bind:
            def execute(self, _stmt):
                class _R:
                    def scalar(self_inner):
                        return op.count
                return _R()
        return _Bind()

    def drop_constraint(self, *a, **k):
        self.ddl.append(("drop", a))

    def create_check_constraint(self, *a, **k):
        self.ddl.append(("create", a))

    def f(self, name):
        return name


@pytest.mark.parametrize("count", [0, 3])
def test_v2_0005_downgrade_refuses_by_count_before_touching_the_constraint(monkeypatch, count):
    """Audit LOW 2026-09-28: the refusal was only a raw CHECK violation. It
    is now an explicit count, raised BEFORE the constraint is dropped, so a
    refused downgrade leaves the table exactly as it was."""
    mod = next(m for m in _chain() if m.revision == "v2_0005")
    fake = _FakeOp(count)
    monkeypatch.setattr(mod, "op", fake)
    if count:
        with pytest.raises(RuntimeError, match=r"3 user_sessions row\(s\).*MFA_CHANGED"):
            mod.downgrade()
        assert fake.ddl == []
    else:
        mod.downgrade()
        assert [k for k, _ in fake.ddl] == ["drop", "create"]
        assert "MFA_CHANGED" not in fake.ddl[1][1][2]
