# ─── CHANGELOG (standalone plan) ────────────────────────────────────────────
# 2026-09-28 (B15) — NEW. The one type for an account's email address.
#   The v2 demo roster lives on `.test` domains (Appendix C: a reserved TLD, so
#   no invented address can ever reach a real mailbox). pydantic's EmailStr
#   runs email-validator, which refuses special-use names such as `.test`
#   ("The part after the @-sign is a special-use or reserved name"), so on the
#   first boot of the v2 fixture NOBODY could log in: /auth/login answered 422
#   for every account. `test_environment=True` admits the `test` domain and
#   changes nothing else that matters here (deliverability is not checked
#   either way). Use AccountEmail wherever an account's email is accepted:
#   login, invites, user creation.
# ────────────────────────────────────────────────────────────────────────────
"""AccountEmail: a syntactically valid email address, `.test` domains allowed."""
from __future__ import annotations

from typing import Annotated

from email_validator import EmailNotValidError, validate_email
from pydantic import AfterValidator, Field


def _account_email(value: str) -> str:
    try:
        return validate_email(value, check_deliverability=False, test_environment=True).normalized
    except EmailNotValidError as exc:
        raise ValueError(f"value is not a valid email address: {exc}") from exc


AccountEmail = Annotated[str, Field(min_length=3, max_length=255), AfterValidator(_account_email)]
