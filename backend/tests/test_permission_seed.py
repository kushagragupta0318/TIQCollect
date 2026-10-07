"""v2_0008 seeds the capability registry as FROZEN literals. These pin them.

The registry (app/core/permissions.py, A01) is the one definition; the
migration is a snapshot of it. The drift test runs wherever the registry
exists (ce's branch, and this one once A01 is merged) and fails the moment
the two disagree, so a changed capability needs a new revision."""
from __future__ import annotations

import importlib.util
import pathlib

import pytest

from app.models.user import UserRole

PATH = pathlib.Path(__file__).resolve().parents[1] / "alembic" / "versions" / "v2_0008_permission_seed.py"


@pytest.fixture(scope="module")
def seed():
    spec = importlib.util.spec_from_file_location("v2_0008", PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_seed_is_well_formed(seed):
    codes = [c for c, *_ in seed.PERMISSIONS]
    assert len(codes) == len(set(codes)) == 79   # +3 payment.reversal.* (ADR 0015), +2 messaging.*
    assert all(cat == code.split(".", 1)[0] for code, cat, *_ in seed.PERMISSIONS)      # category = first segment
    assert all(len(code) <= 64 and len(cat) <= 30 for code, cat, *_ in seed.PERMISSIONS)  # the column widths
    roles = {r.value for r in UserRole}
    assert all(r in roles for r, _ in seed.ROLE_GRANTS)
    assert all(c in set(codes) for _, c in seed.ROLE_GRANTS)                              # every grant names a capability
    assert len(seed.ROLE_GRANTS) == len(set(seed.ROLE_GRANTS)) == 151   # +4 reversal (ADR 0015), +9 messaging


def test_the_removed_field_ops_capability_is_not_seeded(seed):
    """The field_ops router went at the v1-main merge; granting its capability
    would document an endpoint that does not exist."""
    assert all("field_ops" not in c for c, *_ in seed.PERMISSIONS)
    assert all("field_ops" not in c for _, c in seed.ROLE_GRANTS)


def test_the_seed_equals_the_registry(seed):
    perms = pytest.importorskip("app.core.permissions", reason="A01's registry is not on this branch yet")
    caps = {c.code: c for c in perms.CAPABILITIES.values()}
    assert {c for c, *_ in seed.PERMISSIONS} == set(caps)
    for code, cat, desc, second, sensitive in seed.PERMISSIONS:
        c = caps[code]
        assert (cat, desc, second, sensitive) == (c.category, c.description, bool(c.requires_second_person),
                                                   bool(c.is_sensitive)), code
    grants = {(r.value, code) for r, codes in perms.ROLE_CAPABILITIES.items() for code in codes}
    assert set(seed.ROLE_GRANTS) == grants
