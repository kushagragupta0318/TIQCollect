"""scripts/create_first_admin: the first bank admin on an empty deployment."""
from __future__ import annotations

import pytest

from app.core.security import verify_password
from app.models.audit_log import AuditAction, AuditLog
from app.models.identity import PasswordResetToken
from app.models.tenancy import Bank
from app.models.user import User, UserRole
from app.services import password_service
from scripts import create_first_admin as cfa
from tests._db import create_schema, make_engine, make_session_factory

ARGS = dict(bank_code="kumaon", bank_name="Kumaon Finance Ltd", bank_display="Kumaon Finance",
            email="Asha.Rao@Kumaon.example", name="Asha Rao", phone="+91 98765 43210")
CHOSEN = "Deodar-Ridge-2026"


@pytest.fixture()
def db():
    engine = make_engine()
    create_schema(engine)
    session = make_session_factory(engine, info={})()
    try:
        yield session
    finally:
        session.close()


def test_creates_the_bank_and_an_admin_who_chooses_their_own_password(db):
    out = cfa.create_first_admin(db, **ARGS)
    bank = db.get(Bank, out["bank_id"])
    admin = db.get(User, out["user_id"])
    assert (bank.code, bank.legal_name, bank.status) == ("KUMAON", "Kumaon Finance Ltd", "ACTIVE")
    assert (admin.role, admin.bank_id, admin.email, admin.phone) == (
        UserRole.BANK_ADMIN, bank.id, "asha.rao@kumaon.example", "9876543210")
    assert password_service.never_had_a_password(db, admin)

    password_service.reset_with_token(db, out["token"], CHOSEN)          # the link, used once
    db.refresh(admin)
    assert verify_password(CHOSEN, admin.hashed_password)
    with pytest.raises(Exception):
        password_service.reset_with_token(db, out["token"], "Another-Summit-99")


def test_it_is_audited_and_the_token_is_stored_only_as_a_hash(db):
    out = cfa.create_first_admin(db, **ARGS)
    actions = {a.action for a in db.query(AuditLog).filter(AuditLog.entity_id == out["user_id"])}
    assert {AuditAction.USER_CREATED, AuditAction.PASSWORD_RESET_ISSUED} <= actions
    row = db.query(PasswordResetToken).filter(PasswordResetToken.user_id == out["user_id"]).one()
    assert out["token"] not in (row.token_sha256, str(row.__dict__))


def test_a_bank_with_an_admin_is_refused_and_nothing_changes(db):
    cfa.create_first_admin(db, **ARGS)
    users = db.query(User).count()
    with pytest.raises(cfa.Refused, match="already has a BANK_ADMIN"):
        cfa.create_first_admin(db, **{**ARGS, "email": "second@kumaon.example", "phone": "9876500000"})
    db.rollback()
    assert db.query(User).count() == users


def test_an_email_or_phone_already_in_use_is_refused(db):
    cfa.create_first_admin(db, **ARGS)
    with pytest.raises(cfa.Refused, match="already exists"):
        cfa.create_first_admin(db, **{**ARGS, "bank_code": "OTHER", "email": "new@other.example"})


def test_main_prints_the_link_once_and_no_password(db, monkeypatch, capsys):
    from app.core.config import settings
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "https://collect.example.in")
    monkeypatch.setattr(cfa, "SessionLocal", lambda: db)
    monkeypatch.setattr(db, "close", lambda: None)
    argv = [f"--{k.replace('_', '-')}={v}" for k, v in ARGS.items()]
    assert cfa.main(argv) == 0
    out = capsys.readouterr().out
    links = [line for line in out.splitlines() if line.startswith("https://collect.example.in/reset-password?token=")]
    assert len(links) == 1
    assert "password:" not in out.lower().replace("choose", "")

    assert cfa.main(argv) == 1                                               # the second run refuses
    assert "REFUSED" in capsys.readouterr().err


def test_missing_values_are_a_usage_error(monkeypatch):
    for _, env in cfa._FIELDS:
        monkeypatch.delenv(env, raising=False)
    with pytest.raises(SystemExit) as exc:
        cfa.main(["--bank-code", "X"])
    assert exc.value.code == 2
