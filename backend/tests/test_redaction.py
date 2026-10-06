"""core/redaction: what must never reach a model, and what must survive (F02)."""
from __future__ import annotations

import pytest

from app.core.redaction import MIN_LITERAL, TOKENS, redact, redact_text


@pytest.mark.parametrize("raw,token", [
    ("PAN ABCDE1234F on file", "[pan]"),
    ("Aadhaar 2345 6789 0123 seen", "[aadhaar]"),
    ("Aadhaar 234567890123 seen", "[aadhaar]"),
    ("call 9899000101 today", "[phone]"),
    ("call +91 9899000101 today", "[phone]"),
    ("call 09899000101 today", "[phone]"),
    ("mail farhan.siddiqui@example.in please", "[email]"),
    ("pays 8015935790@ptsbi by UPI", "[upi-id]"),
    ("branch HDFC0001234 holds it", "[ifsc]"),
    ("account 50100123456789 debited", "[account-number]"),
])
def test_each_identifier_is_replaced(raw, token):
    out, counts = redact_text(raw)
    assert token in out and sum(counts.values()) >= 1
    for digits in ("9899000101", "2345 6789 0123", "ABCDE1234F", "8015935790@ptsbi", "50100123456789"):
        if digits in raw:
            assert digits not in out


@pytest.mark.parametrize("keep", [
    "overdue is Rs 24,600 on case PL260929AAAAAAAA",
    "loan MTB0000001 is 47 DPD, EMI 12300",
    "visited at 2026-09-30 11:45, outcome PTP",
    "promised 15000 by 2026-10-06",
])
def test_the_numbers_a_model_needs_survive(keep):
    out, counts = redact_text(keep)
    assert out == keep and counts == {}


def test_a_dict_is_redacted_by_key_where_no_pattern_could_find_it():
    """A name, a street and a latitude look like ordinary text; only the field
    they sit in says they are the borrower's."""
    clean, counts = redact({
        "full_name": "Farhan Siddiqui",
        "address_line1": "C-214, Sector 49",
        "latitude": 28.412,
        "longitude": 77.064,
        "dpd": 47,
        "outcome": "PTP",
    })
    assert clean == {
        "full_name": TOKENS["name"], "address_line1": TOKENS["address"],
        "latitude": TOKENS["coordinate"], "longitude": TOKENS["coordinate"],
        "dpd": 47, "outcome": "PTP",
    }
    assert counts["coordinate"] == 2


def test_nested_structures_and_lists_are_walked():
    clean, _ = redact({
        "case": {"customer": {"full_name": "Sunita Rawat", "phone_primary": "9899000211"}},
        "notes": ["reached on 9899000211", "PAN ABCDE1234F"],
        "phones": ["9899000211", "9899000212"],
    })
    assert clean["case"]["customer"] == {"full_name": TOKENS["name"], "phone_primary": TOKENS["phone"]}
    assert clean["notes"] == ["reached on [phone]", "PAN [pan]"]
    assert clean["phones"] == [TOKENS["phone"], TOKENS["phone"]]      # a list inherits its key


def test_the_callers_own_values_are_matched_literally_and_case_insensitively():
    clean, counts = redact(
        {"summary": "FARHAN SIDDIQUI refused; spoke to Farhan instead"},
        extra_values=["Farhan Siddiqui", "to"],        # "to" is too short to be safe, and it IS in the text
    )
    assert "FARHAN SIDDIQUI" not in clean["summary"]
    assert "Farhan" in clean["summary"]                # only the full literal was given
    assert "spoke to Farhan instead" in clean["summary"] and counts["value"] == 1
    assert MIN_LITERAL == 3


def test_a_longer_literal_wins_over_a_shorter_one_inside_it():
    clean, _ = redact({"t": "Farhan Siddiqui called"}, extra_values=["Farhan", "Farhan Siddiqui"])
    assert clean["t"] == "[redacted] called"


def test_redacting_twice_changes_nothing_more():
    once, _ = redact({"full_name": "Sunita Rawat", "note": "call 9899000211"})
    twice, counts = redact(once)
    assert twice == once and counts == {}


def test_an_empty_string_under_a_sensitive_key_is_left_alone():
    clean, counts = redact({"full_name": "", "phone_primary": None})
    assert clean == {"full_name": "", "phone_primary": None} and counts == {}


def test_counts_say_how_much_was_removed_without_saying_what():
    _, counts = redact({"a": "9899000101 and 9899000102", "full_name": "Sunita Rawat"})
    assert counts == {"phone": 2, "name": 1}
