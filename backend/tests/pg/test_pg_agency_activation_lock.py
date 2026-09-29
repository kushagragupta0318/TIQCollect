"""B19 + D02: agency_service._maybe_activate under real concurrency.

Coordinator audit HIGH (backend 51d165b): the two callers of
_maybe_activate — the last required document being verified, and the
master-login invite being accepted — are ordinarily two separate requests
by two different people, and can genuinely overlap. Without a row lock,
both transactions can read "is the other half already true?" before either
commits, each concludes "not yet" and stops — the agency never activates,
or, depending on the exact interleaving, both proceed and fire ACTIVE
twice. SQLite (the rest of this suite) has no concurrent writer to
demonstrate that against; this is the one test in the whole feature that
needs a real second connection actually blocking on the lock.
"""
from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.audit_log import AuditAction, AuditLog
from app.models.identity import UserInvite
from app.models.tenancy import Agency, AgencyDocument, Bank
from app.models.user import User, UserRole
from app.services.bank import agency_service


def _seed_activatable_agency(Session) -> tuple[str, str]:
    """A PENDING agency with every required document VERIFIED and its
    master invite ACCEPTED — both conditions _maybe_activate checks are
    already true, so any clean call to it should activate the agency."""
    db = Session()
    bank = Bank(code="PGLOCK", legal_name="PG Lock Test Bank", display_name="PG Lock Test Bank")
    db.add(bank)
    db.flush()

    verifier = User(email="pglock.verifier@example.test", phone="9810099001", full_name="PG Lock Verifier",
                    hashed_password="x", role=UserRole.BANK_ADMIN, bank_id=bank.id)
    uploader = User(email="pglock.uploader@example.test", phone="9810099002", full_name="PG Lock Uploader",
                    hashed_password="x", role=UserRole.BANK_ADMIN, bank_id=bank.id)
    db.add_all([verifier, uploader])
    db.flush()

    agency = Agency(bank_id=bank.id, code="AGY-PGLOCK", legal_name="PG Lock Test Agency", status="PENDING",
                    contacts=[])
    db.add(agency)
    db.flush()

    # ck_users_role_scope (Postgres-enforced, SQLite does not) requires
    # bank_id AND agency_id together for AGENCY_ADMIN — created after the
    # agency exists, with both set from the start, not patched in afterward.
    agency_admin = User(email="pglock.admin@example.test", phone="9810099003", full_name="PG Lock Agency Admin",
                        hashed_password="x", role=UserRole.AGENCY_ADMIN, bank_id=bank.id, agency_id=agency.id,
                        is_active=True)
    db.add(agency_admin)
    db.flush()

    for doc_type in agency_service.REQUIRED_DOC_TYPES:
        db.add(AgencyDocument(
            bank_id=bank.id, agency_id=agency.id, doc_type=doc_type, storage_key=f"agencies/pglock/{doc_type}.pdf",
            sha256="0" * 64, scan_status="PENDING", status="VERIFIED", uploaded_by=uploader.id,
            verified_by=verifier.id, verified_at=datetime.now(timezone.utc),
        ))

    invite = UserInvite(
        bank_id=bank.id, agency_id=agency.id, purpose="AGENCY_MASTER_LOGIN", email="pglock.admin@example.test",
        phone="9810099003", full_name="PG Lock Agency Admin", role=UserRole.AGENCY_ADMIN, token_sha256="0" * 64,
        invited_by=verifier.id, expires_at=datetime.now(timezone.utc) + timedelta(days=3),
        accepted_at=datetime.now(timezone.utc), accepted_user_id=agency_admin.id,
    )
    db.add(invite)
    db.commit()
    agency_id, bank_id = agency.id, bank.id
    db.close()
    return agency_id, bank_id


def test_two_concurrent_callers_activate_the_agency_exactly_once(pg_url):
    engine = create_engine(pg_url)
    Session = sessionmaker(bind=engine)
    try:
        agency_id, _bank_id = _seed_activatable_agency(Session)

        barrier = threading.Barrier(2)
        results: list[bool] = []
        errors: list[BaseException] = []
        lock = threading.Lock()

        def call_maybe_activate():
            db = Session()
            try:
                barrier.wait(timeout=10)   # both threads reach the lock attempt together
                activated = agency_service._maybe_activate(db, agency_id)
                db.commit()
                with lock:
                    results.append(activated)
            except BaseException as exc:   # noqa: BLE001 — surfaced on the main thread below
                db.rollback()
                with lock:
                    errors.append(exc)
            finally:
                db.close()

        threads = [threading.Thread(target=call_maybe_activate) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        assert not errors, f"a thread raised: {errors}"
        assert not any(t.is_alive() for t in threads), "a thread did not finish — the lock may have deadlocked"

        # Exactly one of the two calls actually flipped it — the other,
        # blocked behind the row lock until the first committed, re-read a
        # row that was no longer PENDING and correctly declined.
        assert sorted(results) == [False, True]

        verify_db = Session()
        try:
            agency = verify_db.get(Agency, agency_id)
            assert agency.status == "ACTIVE"
            activated_rows = (verify_db.query(AuditLog)
                              .filter(AuditLog.entity_type == "Agency", AuditLog.entity_id == agency_id,
                                      AuditLog.action == AuditAction.AGENCY_ACTIVATED).all())
            assert len(activated_rows) == 1, (
                f"expected exactly one AGENCY_ACTIVATED row, found {len(activated_rows)} — "
                "the row lock did not serialise the two callers"
            )
        finally:
            verify_db.close()
    finally:
        engine.dispose()
