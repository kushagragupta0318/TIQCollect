# ─── CHANGELOG (standalone plan) ────────────────────────────────────────────
# 2026-09-24 (B02, coordinator audit item 13) — NEW. The one validator for an
#   id that arrives from outside.
#
#   Ids became native Postgres UUIDs in the v2 model. Before, a malformed id in
#   a path (/cases/does-not-exist) compared against a VARCHAR, matched nothing
#   and fell through to the endpoint's 404. Against a UUID column Postgres
#   raises `invalid input syntax for type uuid` — a DataError, surfaced as a
#   500 — while SQLite, which the test suite runs on, still matches nothing.
#   So the suite would stay green while production answered every mistyped
#   link with a server error, and a 500 on a garbage id next to a 404 on a
#   foreign one is also an existence oracle.
#
#   The rule this file enforces: a malformed id is answered EXACTLY like a
#   well-formed id this caller may not see — 404, same code, same message —
#   before any query runs. `test_media_urls_hides_existence_rather_than_
#   forbidding` pins that equality for one endpoint; tests/test_ids.py pins it
#   for the helper itself.
#
#   Why a 404 and not FastAPI's usual 422: a 422 on the format and a 404 on
#   the lookup are two answers to "is this id real?", which is the question
#   every tenant-scoped endpoint here refuses to answer.
# ────────────────────────────────────────────────────────────────────────────
"""Parse ids from paths, queries and bodies into the canonical UUID string.

    from app.core.ids import UUIDPath, UUIDQuery, parse_uuid_or_404

    @router.get("/cases/{case_id}")
    def get_case(case_id: UUIDPath, ...): ...          # malformed -> 404

    @router.get("/visits")
    def list_visits(agent_id: UUIDQuery = None, ...):   # malformed -> 404

    ids = [parse_uuid_or_404(x) for x in body.case_ids]

The value handed to the endpoint is the lower-case hyphenated form, which is
what `Uuid(as_uuid=False)` returns from the database, so `str_id == row.id`
comparisons in Python keep holding for an id the caller typed in upper case.
"""
import uuid
from typing import Annotated, Optional

from fastapi import Path, Query
from pydantic import AfterValidator

from app.core.errors import AppException, ErrorCode

__all__ = ["UUIDPath", "UUIDQuery", "UUIDQueryRequired", "UUIDStr", "parse_uuid", "parse_uuid_or_404", "is_uuid"]

_NOT_FOUND_MESSAGE = "Not found"


def parse_uuid(value) -> Optional[str]:
    """The canonical string form, or None when `value` is not a UUID."""
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return str(value)
    if not isinstance(value, str):
        return None
    try:
        return str(uuid.UUID(value.strip()))
    except (ValueError, AttributeError):
        return None


def is_uuid(value) -> bool:
    return parse_uuid(value) is not None


def parse_uuid_or_404(value, message: str = _NOT_FOUND_MESSAGE) -> str:
    """The canonical string form, or the same 404 a missing row would get.

    Raises AppException (not ValueError) on purpose: pydantic converts
    ValueError into a 422, and lets any other exception propagate, so this is
    what makes UUIDPath answer 404 from inside request validation."""
    parsed = parse_uuid(value)
    if parsed is None:
        raise AppException(404, ErrorCode.NOT_FOUND, message)
    return parsed


def _optional_or_404(value):
    return None if value is None else parse_uuid_or_404(value)


UUIDPath = Annotated[str, Path(), AfterValidator(parse_uuid_or_404)]
UUIDQuery = Annotated[Optional[str], Query(), AfterValidator(_optional_or_404)]
UUIDQueryRequired = Annotated[str, Query(), AfterValidator(parse_uuid_or_404)]

def _uuid_or_value_error(value):
    parsed = parse_uuid(value)
    if parsed is None:
        raise ValueError("not a valid id")
    return parsed


# For ids inside a request BODY (audit LOW, 2026-09-24): a malformed one is a
# 422 before any query runs — never a Postgres DataError 500. A body is not an
# existence probe the way a path is (the resource being addressed is the
# path's), so the ordinary validation answer is the right one here.
UUIDStr = Annotated[str, AfterValidator(_uuid_or_value_error)]
