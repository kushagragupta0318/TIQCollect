"""Two things the first boot of the v2 fixture found (B15, 2026-09-28)."""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from pydantic import TypeAdapter, ValidationError

from app.core.emails import AccountEmail
from app.services import demo_service

EMAIL = TypeAdapter(AccountEmail)


@pytest.mark.parametrize("addr", ["ananya.iyer@girivanfinance.test", "piyush.sharma@aravallifs.test",
                                  "manager1@tiqcollect.in"])
def test_the_roster_domains_are_valid_account_emails(addr):
    """pydantic's EmailStr refused `.test` (a special-use name), so every v2
    demo account answered 422 at /auth/login."""
    assert EMAIL.validate_python(addr) == addr


@pytest.mark.parametrize("bad", ["not-an-email", "a@", "@girivanfinance.test", "a b@x.test", ""])
def test_malformed_addresses_are_still_refused(bad):
    with pytest.raises(ValidationError):
        EMAIL.validate_python(bad)


def test_login_accepts_a_test_domain_email():
    from app.schemas.auth import LoginRequest
    r = LoginRequest(email="vikram.malhotra@aravallifs.test", password="x" * 12, device_id="device-0001")
    assert r.email == "vikram.malhotra@aravallifs.test"


@pytest.mark.parametrize("value", [
    date(2026, 9, 21), Decimal("48250.50"), datetime(2026, 9, 21, 10, 30, tzinfo=timezone.utc), None, 7, "x"])
def test_the_demo_baseline_round_trips_v2_types_through_json(value):
    """v2's DATE and NUMERIC columns reached save_baseline as date / Decimal,
    which json.dumps refused, so the entrypoint's snapshot failed on boot."""
    back = demo_service._deserialise(json.loads(json.dumps(demo_service._serialise(value))))
    assert back == value and type(back) is type(value)
