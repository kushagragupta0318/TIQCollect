"""B22 — invented numbers are never contacted.

2026-09-24. The demo book is invented people with real-format Indian mobiles,
and before this change the only gate between a recorded visit and an SMS to a
stranger was whether Twilio credentials happened to be set. These tests swap
in a fake Twilio client that records every message it is asked to send, so
"suppressed" is proved by the transport never being called — not by reading
the code.
"""
from __future__ import annotations

import sys
import types

import pytest

from app.core.config import settings
from app.services.notification_service import NotificationService, twilio_configured

INVENTED = "+919876501234"      # an invented borrower's number
PRESENTER = "8015935790"        # DEMO_CONTACT_PHONE default


@pytest.fixture()
def sent(monkeypatch):
    """A fake `twilio.rest.Client`; returns the list of messages it was asked to send."""
    calls: list[dict] = []

    class _Messages:
        def create(self, **kw):
            calls.append(kw)

    class _Client:
        def __init__(self, *_a, **_k):
            self.messages = _Messages()

    fake_rest = types.ModuleType("twilio.rest")
    fake_rest.Client = _Client
    monkeypatch.setitem(sys.modules, "twilio.rest", fake_rest)
    monkeypatch.setattr(settings, "TWILIO_ACCOUNT_SID", "AC_test_sid")
    monkeypatch.setattr(settings, "TWILIO_AUTH_TOKEN", "test_token")
    monkeypatch.setattr(settings, "TWILIO_PHONE_NUMBER", "+15550001111")
    monkeypatch.setattr(settings, "TWILIO_WHATSAPP_FROM", "")
    monkeypatch.setattr(settings, "DEMO_CONTACT_PHONE", PRESENTER)
    monkeypatch.setattr(settings, "DEMO_NOTIFY_ALLOWLIST", "")
    return calls


def test_demo_mode_never_dials_an_invented_number(sent, monkeypatch):
    monkeypatch.setattr(settings, "DEMO_MODE", True)
    assert NotificationService.send_sms(INVENTED, "Your OTP is 123456") is False
    assert NotificationService.send_twilio(INVENTED, "receipt", "receipt") is False
    assert sent == []


def test_demo_mode_still_reaches_the_presenter(sent, monkeypatch):
    monkeypatch.setattr(settings, "DEMO_MODE", True)
    to = "+" + NotificationService.normalize_phone(PRESENTER)
    assert NotificationService.send_sms(to, "Your OTP is 123456") is True
    assert [c["to"] for c in sent] == [to]


def test_the_allowlist_admits_extra_numbers_in_any_format(sent, monkeypatch):
    monkeypatch.setattr(settings, "DEMO_MODE", True)
    monkeypatch.setattr(settings, "DEMO_NOTIFY_ALLOWLIST", "98100 00001, +91-9810000002")
    assert NotificationService.send_sms("+919810000001", "hi") is True
    assert NotificationService.send_sms("+919810000002", "hi") is True
    assert NotificationService.send_sms(INVENTED, "hi") is False
    assert len(sent) == 2


def test_a_demo_tenant_is_suppressed_even_outside_demo_mode(sent, monkeypatch):
    monkeypatch.setattr(settings, "DEMO_MODE", False)
    assert NotificationService.send_sms(INVENTED, "hi", demo_tenant=True) is False
    assert sent == []


def test_a_real_tenant_outside_demo_mode_is_sent(sent, monkeypatch):
    monkeypatch.setattr(settings, "DEMO_MODE", False)
    assert NotificationService.send_sms(INVENTED, "hi") is True
    assert len(sent) == 1


@pytest.mark.parametrize("sid", ["${TWILIO_ACCOUNT_SID}", "", "  ${X}"])
def test_an_uninterpolated_credential_counts_as_unset(sent, monkeypatch, sid):
    # A docker --env-file does not interpolate: `.env.example`'s
    # TWILIO_ACCOUNT_SID=${TWILIO_ACCOUNT_SID} arrives as a literal string.
    monkeypatch.setattr(settings, "DEMO_MODE", False)
    monkeypatch.setattr(settings, "TWILIO_ACCOUNT_SID", sid)
    assert twilio_configured() is False
    assert NotificationService.send_sms(INVENTED, "hi") is False
    assert sent == []
