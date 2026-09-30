"""B15 transform (scripts/migrate_v1_to_v2.py): the conversion rules that
must abort rather than guess (design §9.4), and the roster mapping.

The end-to-end run is Postgres-only (it reads a restored v1 dump and writes an
empty v2 database); its evidence is in the B15 commit. These pin the rules.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
import sqlalchemy as sa

from scripts import migrate_v1_to_v2 as m


def test_the_roster_email_is_first_dot_last_on_the_agency_domain():
    assert m.email_for("Piyush Sharma", m.AGENCY_DOMAIN) == "piyush.sharma@aravallifs.test"
    assert m.email_for("Arjun Singh Chauhan", m.AGENCY_DOMAIN) == "arjun.chauhan@aravallifs.test"
    with pytest.raises(m.TransformError):
        m.email_for("  ", m.AGENCY_DOMAIN)


def test_the_master_accounts_are_bank_user_manager_agent_and_a_second_bank_admin():
    """The owner's decisions (v2; the 4th on 2026-09-30); apply_demo_logins'
    positional role slots refuse anything else."""
    assert m.MASTER_ACCOUNTS == ("ananya.iyer@girivanfinance.test", "vikram.malhotra@aravallifs.test",
                                 "piyush.sharma@aravallifs.test", "kavya.reddy@girivanfinance.test")
    roles = {f"{l}@{m.BANK_DOMAIN}": r for l, _, r, _ in m.BANK_USERS}
    roles.update({f"{l}@{m.AGENCY_DOMAIN}": r for l, _, r in m.V1_STAFF.values()})
    assert [roles.get(e, "FIELD_AGENT") for e in m.MASTER_ACCOUNTS] ==         ["BANK_ADMIN", "AGENCY_MANAGER", "FIELD_AGENT", "BANK_ADMIN"]


def test_new_ids_are_deterministic_and_distinct():
    assert m.new_id("placement", "L1") == m.new_id("placement", "L1")
    assert m.new_id("placement", "L1") != m.new_id("placement", "L2") != m.new_id("case", "L1")


@pytest.mark.parametrize("v,ok", [("2026-09-21", date(2026, 9, 21)), ("", None), (None, None),
                                  (datetime(2026, 9, 21, 9, tzinfo=timezone.utc), date(2026, 9, 21))])
def test_dates_parse_exactly(v, ok):
    assert m._date(v, "t.c") == ok


@pytest.mark.parametrize("bad", ["2026-09", "2026-09-21T10:00", "21/09/2026"])
def test_a_date_that_is_not_exactly_iso_aborts_rather_than_truncates(bad):
    with pytest.raises(m.TransformError):
        m._date(bad, "t.c")


def test_money_rounds_to_paise_and_refuses_what_is_not_a_paise_amount():
    r = m.Report()
    assert m._money(48250.5, "loans.x", r) == Decimal("48250.50")
    assert m._money(0.1 + 0.2, "loans.x", r) == Decimal("0.30") and r.money_changed["loans.x"] == 1
    with pytest.raises(m.TransformError):
        m._money(12.345, "loans.x", r)          # a third decimal place is not a rupee-paise figure


def test_an_enum_value_v2_does_not_know_aborts():
    col = sa.Column("status", sa.Enum("ACTIVE", "CLOSED", name="t_status"))
    sa.Table("t", sa.MetaData(), col)
    assert m.convert("ACTIVE", col, m.Report()) == "ACTIVE"
    with pytest.raises(m.TransformError):
        m.convert("ARCHIVED", col, m.Report())


def test_a_non_uuid_id_aborts():
    with pytest.raises(m.TransformError):
        m._uuid("AGENCY-TIQ-001")
    assert m._uuid("5F0C1B8E-2F3A-4D7E-9B61-0A4F7C2E9D11") == "5f0c1b8e-2f3a-4d7e-9b61-0a4f7c2e9d11"
