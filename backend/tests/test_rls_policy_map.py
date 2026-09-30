"""A13: every table in the models is either under a tenancy policy or on the
explicit no-RLS list of v2_0012, and the policy each gets fits its columns.
No database; tests/pg/test_pg_rls.py proves the policies themselves."""
from __future__ import annotations

import importlib.util
import pathlib

import pytest

import app.models  # noqa: F401
from app.core.database import Base
from app.models.user import UserRole, tenant_scope

REV = pathlib.Path(__file__).resolve().parents[1] / "alembic" / "versions" / "v2_0012_rls.py"


def _rev(path=REV):
    spec = importlib.util.spec_from_file_location(f"rev_{path.stem}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


RLS = _rev()
# Tables added after v2_0012 carry their own RLS_BANK_ONLY / RLS_POLICIES (v2_0013: cost_rates).
LATER = [m for m in (_rev(p) for p in sorted(REV.parent.glob("v2_*.py")) if p.stem > REV.stem)
         if hasattr(m, "RLS_POLICIES")]
TABLES = {t.fullname: {c.name for c in t.columns} for t in Base.metadata.sorted_tables}
GROUPS = {
    "AGENCY_OWNED": set(RLS.AGENCY_OWNED), "VIA_PLACEMENT": set(RLS.VIA_PLACEMENT),
    "VIA_CUSTOMER_LOANS": set(RLS.VIA_CUSTOMER_LOANS),
    "BANK_ONLY": set(RLS.BANK_ONLY).union(*(set(getattr(m, "RLS_BANK_ONLY", ())) for m in LATER)),
    "SPECIAL": set(RLS.SPECIAL), "NO_RLS": set(RLS.NO_RLS),
}


def test_later_revisions_use_v2_0012_s_templates():
    bank_only = RLS._policies()[RLS.BANK_ONLY[0]]
    for m in LATER:
        assert set(m.RLS_POLICIES) == set(getattr(m, "RLS_BANK_ONLY", ())), m.__name__
        for table, expr in m.RLS_POLICIES.items():
            assert expr == bank_only, table


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
    for t in RLS.AGENCY_OWNED:
        assert {"bank_id", "agency_id"} <= TABLES[t], t
    for t, col in RLS.VIA_PLACEMENT.items():
        assert "bank_id" in TABLES[t] and col in TABLES[t] and "agency_id" not in TABLES[t], t
    for t in (*RLS.VIA_CUSTOMER_LOANS, *RLS.BANK_ONLY):
        assert "bank_id" in TABLES[t], t
    # An agency-owned row must never be classed bank-only or via-placement: that would hide it from its agency.
    assert sorted(t for t in (*RLS.BANK_ONLY, *RLS.VIA_PLACEMENT) if "agency_id" in TABLES[t]) == []


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


SCOPE_OF = {   # (role, carries an agency) -> scope; a literal table, not the rule restated
    (UserRole.PLATFORM_ADMIN, False): "PLATFORM",
    (UserRole.BANK_ADMIN, False): "BANK", (UserRole.BANK_ANALYST, False): "BANK",
    (UserRole.BANK_TECHOPS, False): "BANK",
    (UserRole.SERVICE, False): "BANK", (UserRole.SERVICE, True): "AGENCY",
    (UserRole.AGENCY_ADMIN, True): "AGENCY", (UserRole.AGENCY_MANAGER, True): "AGENCY",
    (UserRole.FIELD_AGENT, True): "AGENT",
}


def test_the_scope_table_names_every_role():
    assert {r for r, _ in SCOPE_OF} == set(UserRole)


@pytest.mark.parametrize("role,with_agency", list(SCOPE_OF))
def test_the_scope_of_every_role(role, with_agency):
    assert tenant_scope(role, "agency-1" if with_agency else None) == SCOPE_OF[(role, with_agency)]


def test_the_downgrade_revokes_exactly_what_the_upgrade_granted():
    """Coordinator's audit LOW: it used to REVOKE ALL ON ALL TABLES IN SCHEMA,
    which would also strip grants another revision made."""
    granted = [g.replace(" TO ", " FROM ").replace("GRANT ", "REVOKE ") for g in RLS._grants()]
    assert sorted(granted) == sorted(RLS._revokes())
    assert not [r for r in RLS._revokes() if "ALL TABLES IN SCHEMA" in r]
