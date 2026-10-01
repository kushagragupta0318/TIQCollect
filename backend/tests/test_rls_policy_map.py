"""A13: every table in the models is either under a tenancy policy or on the
explicit no-RLS list of v2_0012, and the policy each gets fits its columns.
No database; tests/pg/test_pg_rls.py proves the policies themselves."""
from __future__ import annotations

import importlib.util
import pathlib

import pytest

import app.models  # noqa: F401
from app.core.database import Base
from app.models.user import BANK_ROLES, UserRole, tenant_scope

REV = pathlib.Path(__file__).resolve().parents[1] / "alembic" / "versions" / "v2_0012_rls.py"


def _rev(path=REV):
    spec = importlib.util.spec_from_file_location(f"rev_{path.stem}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


RLS = _rev()
# Tables added after v2_0012 carry their own RLS_BANK_ONLY or RLS_AGENCY_OWNED
# beside RLS_POLICIES (v2_0013: cost_rates, bank-only). A table both tenants
# touch -- the agency raises the row, the bank signs it off -- is AGENCY_OWNED,
# which is why a later revision needs that list too: bank-only would hide the
# row from the agency that raised it.
LATER = [m for m in (_rev(p) for p in sorted(REV.parent.glob("v2_*.py")) if p.stem > REV.stem)
         if hasattr(m, "RLS_POLICIES")]


def _later(attr: str) -> set[str]:
    return set().union(*(set(getattr(m, attr, ())) for m in LATER)) if LATER else set()


TABLES = {t.fullname: {c.name for c in t.columns} for t in Base.metadata.sorted_tables}
GROUPS = {
    "AGENCY_OWNED": set(RLS.AGENCY_OWNED) | _later("RLS_AGENCY_OWNED"),
    "VIA_PLACEMENT": set(RLS.VIA_PLACEMENT),
    "VIA_CUSTOMER_LOANS": set(RLS.VIA_CUSTOMER_LOANS),
    "BANK_ONLY": set(RLS.BANK_ONLY) | _later("RLS_BANK_ONLY"),
    "SPECIAL": set(RLS.SPECIAL), "NO_RLS": set(RLS.NO_RLS),
}


def test_later_revisions_use_v2_0012_s_templates():
    """A later revision picks a template, it does not write one. Every table it
    declares is listed as bank-only or as agency-owned, and carries that
    template's expression verbatim -- a hand-rolled variant is how a scope hole
    gets in one table at a time."""
    bank_only = RLS._policies()[RLS.BANK_ONLY[0]]
    for m in LATER:
        bank = set(getattr(m, "RLS_BANK_ONLY", ()))
        agency = set(getattr(m, "RLS_AGENCY_OWNED", ()))
        assert bank & agency == set(), f"{m.__name__}: a table is in both lists"
        declared = {**{t: bank_only for t in bank}, **{t: RLS._AGENCY_OWNED for t in agency}}
        assert set(m.RLS_POLICIES) == set(declared), m.__name__
        for table, expr in m.RLS_POLICIES.items():
            assert expr == declared[table], table


def test_every_table_is_classified_exactly_once():
    seen: dict[str, list[str]] = {}
    for name, tables in GROUPS.items():
        for t in tables:
            seen.setdefault(t, []).append(name)
    assert {t: g for t, g in seen.items() if len(g) > 1} == {}
    assert sorted(set(TABLES) - set(seen)) == [], "a new table needs a policy in v2_0012 or a NO_RLS entry"
    assert sorted(set(seen) - set(TABLES)) == [], "the migration names a table the models no longer have"


def test_no_tenant_bearing_table_is_left_without_a_policy():
    assert sorted(t for t in RLS.NO_RLS if "bank_id" in TABLES[t] or "agency_id" in TABLES[t]) == []


def test_each_policy_fits_its_columns():
    for t in GROUPS["AGENCY_OWNED"]:
        assert {"bank_id", "agency_id"} <= TABLES[t], t
    for t, col in RLS.VIA_PLACEMENT.items():
        assert "bank_id" in TABLES[t] and col in TABLES[t] and "agency_id" not in TABLES[t], t
    for t in (*RLS.VIA_CUSTOMER_LOANS, *RLS.BANK_ONLY):
        assert "bank_id" in TABLES[t], t
    # An agency-owned row must never be classed bank-only or via-placement: that would hide it from its agency.
    assert sorted(t for t in (*GROUPS["BANK_ONLY"], *RLS.VIA_PLACEMENT) if "agency_id" in TABLES[t]) == []


def test_each_table_gets_the_template_of_its_group():
    pol = RLS._policies()
    for t in RLS.AGENCY_OWNED:
        assert pol[t] == RLS._AGENCY_OWNED, t
    for t, col in RLS.VIA_PLACEMENT.items():
        assert f"p.loan_id = {t.split('.')[1]}.{col} " in pol[t], t
        assert "current_scope() = 'BANK'" in pol[t], t
    for t in RLS.BANK_ONLY:
        assert pol[t] == "(bank_id = tenancy.current_bank_id() AND tenancy.current_scope() = 'BANK')", t
    assert "current_scope() = 'PLATFORM'" in pol["audit.audit_logs"]
    # PLATFORM reads no bank's directory rows: support goes through a BANK session (Q20).
    assert "PLATFORM" not in pol["tenancy.banks"] + pol["tenancy.agencies"]


@pytest.mark.parametrize("role", list(UserRole))
def test_the_scope_of_every_role(role):
    want = ("PLATFORM" if role == UserRole.PLATFORM_ADMIN else "AGENT" if role == UserRole.FIELD_AGENT
            else "BANK" if role in BANK_ROLES else "AGENCY")
    assert tenant_scope(role) == want


def test_the_downgrade_revokes_exactly_what_the_upgrade_granted():
    """Coordinator's audit LOW: it used to REVOKE ALL ON ALL TABLES IN SCHEMA,
    which would also strip grants another revision made."""
    granted = [g.replace(" TO ", " FROM ").replace("GRANT ", "REVOKE ") for g in RLS._grants()]
    assert sorted(granted) == sorted(RLS._revokes())
    assert not [r for r in RLS._revokes() if "ALL TABLES IN SCHEMA" in r]
