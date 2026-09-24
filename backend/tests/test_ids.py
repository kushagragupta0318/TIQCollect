"""app/core/ids.py — a malformed id is answered exactly like an unseen one.

2026-09-24 (coordinator audit item 13). Ids are native Postgres UUIDs in the
v2 model; a garbage id reaching a query raises `invalid input syntax for type
uuid` there (a 500) while SQLite, which this suite runs on, quietly matches
nothing. These tests execute the validator through FastAPI's own request
validation, which is the only place the 404-not-422 behaviour can be seen.
"""
from __future__ import annotations

import ast
import pathlib
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.errors import AppException
from app.core.ids import UUIDPath, UUIDQuery, UUIDQueryRequired, is_uuid, parse_uuid, parse_uuid_or_404

GOOD = "3f2b8c1e-9a4d-4e6f-8b1a-2c3d4e5f6a7b"


def test_parse_uuid_canonicalises_case_and_whitespace():
    assert parse_uuid(GOOD.upper()) == GOOD
    assert parse_uuid(f"  {GOOD} ") == GOOD
    assert parse_uuid(uuid.UUID(GOOD)) == GOOD


@pytest.mark.parametrize("bad", ["", "does-not-exist", "c1", "123", GOOD[:-1], GOOD + "0", None, 42, "' OR 1=1 --"])
def test_non_uuids_are_rejected(bad):
    assert parse_uuid(bad) is None
    assert not is_uuid(bad)


def test_parse_uuid_or_404_raises_the_typed_404():
    with pytest.raises(AppException) as exc:
        parse_uuid_or_404("does-not-exist")
    assert exc.value.status_code == 404
    assert exc.value.code.value == "NOT_FOUND"


@pytest.fixture()
def client():
    app = FastAPI()

    @app.get("/things/{thing_id}")
    def get_thing(thing_id: UUIDPath):
        return {"id": thing_id}

    @app.get("/things")
    def list_things(owner_id: UUIDQuery = None):
        return {"owner_id": owner_id}

    @app.get("/export")
    def export(run_id: UUIDQueryRequired):
        return {"run_id": run_id}

    return TestClient(app)


def test_path_id_malformed_is_404_not_422(client):
    r = client.get("/things/does-not-exist")
    assert r.status_code == 404, r.text
    # The same body a missing row gets — no hint that the FORMAT was the problem.
    assert r.json()["detail"] == "Not found"


def test_path_id_is_handed_over_canonical(client):
    r = client.get(f"/things/{GOOD.upper()}")
    assert r.status_code == 200
    assert r.json() == {"id": GOOD}


def test_optional_query_id(client):
    assert client.get("/things").json() == {"owner_id": None}
    assert client.get(f"/things?owner_id={GOOD}").json() == {"owner_id": GOOD}
    assert client.get("/things?owner_id=nope").status_code == 404


def test_required_query_id(client):
    assert client.get(f"/export?run_id={GOOD}").json() == {"run_id": GOOD}
    assert client.get("/export?run_id=nope").status_code == 404
    # Absent is still FastAPI's own 422: nothing was guessed at.
    assert client.get("/export").status_code == 422


def test_every_id_path_parameter_on_the_routers_is_validated():
    """TRIPWIRE, not proof: reads the router source, so it can see that a route
    DECLARES `{x_id}` with a bare `str` annotation and nothing about what the
    handler then does. It exists so the next route added with `case_id: str`
    fails here instead of 500-ing on Postgres."""
    endpoints = pathlib.Path(__file__).resolve().parents[1] / "app" / "api" / "v1" / "endpoints"
    not_uuid = {"account_id"}   # Command Centre's loan account number
    offenders = []
    for f in sorted(endpoints.glob("*.py")):
        src = f.read_text(encoding="utf-8")
        for fn in ast.walk(ast.parse(src)):
            if not isinstance(fn, ast.FunctionDef):
                continue
            path = next((d.args[0].value for d in fn.decorator_list
                         if isinstance(d, ast.Call) and d.args and isinstance(d.args[0], ast.Constant)
                         and isinstance(d.args[0].value, str) and d.args[0].value.startswith("/")), None)
            if path is None:
                continue
            for a in fn.args.args + fn.args.kwonlyargs:
                if not a.arg.endswith("_id") or a.arg in not_uuid or a.annotation is None:
                    continue
                ann = ast.get_source_segment(src, a.annotation)
                if ann in ("str", "Optional[str]", "str | None"):
                    offenders.append(f"{f.name}:{fn.name}({a.arg}: {ann})")
    assert offenders == []
