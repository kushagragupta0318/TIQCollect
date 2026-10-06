"""Create a bank and its first BANK_ADMIN on a fresh deployment (docs/DEPLOY.md §3).

    python -m scripts.create_first_admin --bank-code GIRIVAN \\
        --bank-name "Girivan Finance Ltd" --bank-display "Girivan Finance" \\
        --email admin@bank.example --name "Asha Rao" --phone 9876543210

Every value can come from the environment instead (FIRST_ADMIN_BANK_CODE,
FIRST_ADMIN_BANK_NAME, FIRST_ADMIN_BANK_DISPLAY, FIRST_ADMIN_EMAIL,
FIRST_ADMIN_NAME, FIRST_ADMIN_PHONE); an argument wins.

No password is chosen, printed or logged. The admin is created with an
unusable password and a single-use link (72 hours) to choose their own,
printed ONCE on stdout for the operator to hand over. It is never logged.

Refuses, changing nothing, if the bank already has a BANK_ADMIN, or if the
email or phone belongs to any account. Re-running for a bank whose admin has not
used the link yet refuses too: an unused admin is still an admin, so issue a new
link with the platform admin's reset instead.

Exit codes: 0 created, 1 refused, 2 bad arguments.
"""
from __future__ import annotations

import argparse
import os
import sys

from app.core.audit import stage_audit
from app.core.config import settings
from app.core.database import SessionLocal
from app.core.errors import AppException
from app.core.security import disabled_password_hash
from app.models.audit_log import AuditAction
from app.models.tenancy import Bank
from app.models.user import User, UserRole
from app.services.invite_service import _normalise_email, _normalise_phone
from app.services.password_service import FIRST_PASSWORD_TTL, _issue, reset_password_path

_FIELDS = (("bank_code", "FIRST_ADMIN_BANK_CODE"), ("bank_name", "FIRST_ADMIN_BANK_NAME"),
           ("bank_display", "FIRST_ADMIN_BANK_DISPLAY"), ("email", "FIRST_ADMIN_EMAIL"),
           ("name", "FIRST_ADMIN_NAME"), ("phone", "FIRST_ADMIN_PHONE"))


class Refused(Exception):
    pass


def create_first_admin(db, *, bank_code: str, bank_name: str, bank_display: str,
                       email: str, name: str, phone: str) -> dict:
    """The bank (created if its code is new) and its first admin with a
    first-password token. Commits once, or raises Refused having changed nothing."""
    code = bank_code.strip().upper()
    email = _normalise_email(email)
    phone = _normalise_phone(phone)
    name = name.strip()
    if not code or "@" not in email or not name:
        raise Refused("a bank code, an email and a name are all needed")

    bank = db.query(Bank).filter(Bank.code == code).first()
    if bank is not None:
        if db.query(User.id).filter(User.bank_id == bank.id, User.role == UserRole.BANK_ADMIN).first():
            raise Refused(f"bank {code} already has a BANK_ADMIN")
    if db.query(User.id).filter((User.email == email) | (User.phone == phone)).first():
        raise Refused("an account with this email or phone already exists")

    created_bank = bank is None
    if created_bank:
        bank = Bank(code=code, legal_name=bank_name.strip() or code,
                    display_name=bank_display.strip() or bank_name.strip() or code)
        db.add(bank)
        db.flush()
    user = User(email=email, phone=phone, full_name=name, role=UserRole.BANK_ADMIN, bank_id=bank.id,
                hashed_password=disabled_password_hash(), is_active=True, is_verified=True,
                must_change_password=True)
    db.add(user)
    db.flush()
    token = _issue(db, user, "FIRST_LOGIN", FIRST_PASSWORD_TTL)
    stage_audit(db, action=AuditAction.USER_CREATED, user_id=None, entity_type="User", entity_id=user.id,
                bank_id=bank.id, agency_id=None,
                details={"role": UserRole.BANK_ADMIN.value, "bank_id": bank.id, "bank_created": created_bank,
                         "via": "scripts.create_first_admin"})
    stage_audit(db, action=AuditAction.PASSWORD_RESET_ISSUED, user_id=None, entity_type="User",
                entity_id=user.id, bank_id=bank.id, agency_id=None, details={"kind": "FIRST_PASSWORD", "via": "scripts.create_first_admin"})
    db.commit()
    return {"bank_id": bank.id, "bank_created": created_bank, "user_id": user.id, "token": token}


def _args(argv: list[str]) -> dict:
    p = argparse.ArgumentParser(description="Create a bank and its first BANK_ADMIN.")
    for field, env in _FIELDS:
        p.add_argument("--" + field.replace("_", "-"), default=os.environ.get(env, ""))
    ns = vars(p.parse_args(argv))
    missing = [f"--{f.replace('_', '-')}" for f, _ in _FIELDS if f not in ("bank_display",) and not ns[f].strip()]
    if missing:
        p.error("missing: " + ", ".join(missing))
    return ns


def main(argv: list[str] | None = None) -> int:
    ns = _args(sys.argv[1:] if argv is None else argv)
    db = SessionLocal()
    try:
        out = create_first_admin(db, **ns)
    except (Refused, AppException) as exc:
        db.rollback()
        print(f"[first-admin] REFUSED, nothing changed: {getattr(exc, 'detail', exc)}", file=sys.stderr)
        return 1
    finally:
        db.close()
    base = (settings.PUBLIC_BASE_URL or "").strip().rstrip("/")
    if not base or base.startswith("${"):
        base = "https://<APP_DOMAIN>"
    print(f"[first-admin] bank {'created' if out['bank_created'] else 'exists'}: {out['bank_id']}")
    print(f"[first-admin] BANK_ADMIN created: {out['user_id']}")
    print("[first-admin] Give this single-use link to the admin; it expires in 72 hours and is shown only now:")
    print(f"{base}{reset_password_path(out['token'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
