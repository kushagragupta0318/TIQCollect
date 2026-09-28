"""Settings that refuse to start a production deployment (docs/DEPLOY.md)."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings

BASE = dict(DATABASE_URL="sqlite://", MINIO_ACCESS_KEY="x", MINIO_SECRET_KEY="x")
STRONG = "k" * 48


@pytest.mark.parametrize("key", ["", "short", "x" * 31, "${SECRET_KEY}"[:12]])
def test_production_refuses_a_short_secret_key(key):
    with pytest.raises(ValidationError, match="SECRET_KEY"):
        Settings(**BASE, APP_ENV="production", SECRET_KEY=key)


def test_production_starts_with_a_long_key_and_development_is_not_policed():
    assert Settings(**BASE, APP_ENV="production", SECRET_KEY=STRONG).APP_ENV == "production"
    assert Settings(**BASE, APP_ENV="development", SECRET_KEY="dev").SECRET_KEY == "dev"


@pytest.mark.parametrize("key", ["", "${TOTP_ENC_KEY}"])
def test_mandatory_mfa_needs_the_totp_key(key):
    with pytest.raises(ValidationError, match="TOTP_ENC_KEY"):
        Settings(**BASE, SECRET_KEY=STRONG, BANK_MFA_REQUIRED="true", TOTP_ENC_KEY=key)


def test_mfa_off_or_keyed_starts():
    Settings(**BASE, SECRET_KEY=STRONG, BANK_MFA_REQUIRED="", TOTP_ENC_KEY="")
    Settings(**BASE, SECRET_KEY=STRONG, BANK_MFA_REQUIRED="TRUE", TOTP_ENC_KEY="a-fernet-key")
