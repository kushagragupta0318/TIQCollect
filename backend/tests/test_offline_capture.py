"""I02 offline outbox: a replayed item is judged at its capture time, inside bounds.

docs/adr/0011-offline-outbox.md. Pinned here:
  1. the bounds on a client capture time (services/capture_time.py): live
     tolerance, future, 48 h age, bound device, per-device order;
  2. a late visit is judged at capture (contact hours, the capture day's beat),
     never takes a case over, never regresses newer case state, never texts a
     borrower out of hours, and counts in its own month;
  3. idempotency by client_submission_id for visits, PTPs and call logs;
  4. the GPS trail shares the 48 h window (location 24 h -> 48 h);
  5. an outbox photo upload is keyed by its submission and final once stored.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.core.errors import ErrorCode
from app.core.geo import IST
from app.core.security import create_access_token, device_fingerprint_for
from app.main import app
from app.models.agent import Agent, AgentDevice, AgentSpecialization, AgentStatus, AgentTier
from app.models.agent_location import AgentLocation
from app.models.audit_log import AuditAction, AuditLog
from app.models.beat import Beat
from app.models.call_log import CallLog, CallOutcome
from app.models.case import Case, CaseStatus
from app.models.customer import Customer
from app.models.loan import DPDBucket, Loan, LoanStatus, LoanType
from app.models.ptp import PTP
from app.models.user import User, UserRole
from app.models.visit import Visit, VisitOutcome
from app.schemas.agent import LocationPing, LogCallRequest, RecordVisitRequest, SetPTPRequest
from app.services import call_log_service as cls_mod
from app.services import capture_time as ct
from app.services import location_service as ls
from app.services import media_service as ms
from app.services import payment_service as ps
from app.services import visit_service as vs
from tests._db import create_schema, drop_schema, make_engine, make_session_factory

engine = make_engine()
Session = make_session_factory(autocommit=False, autoflush=False, bind=engine)

DEVICE = "phone-a-0001"
DAY = date(2026, 9, 29)


def ist(d: date, h: int, m: int = 0) -> datetime:
    return datetime.combine(d, time(h, m), tzinfo=IST).astimezone(timezone.utc)


def _uid() -> str:
    return str(uuid.uuid4())


def _freeze(monkeypatch, fixed: datetime) -> datetime:
    """Every service on the write path reads its clock through its module's
    `datetime` name; pin them all to one instant."""
    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed.astimezone(tz) if tz else fixed.replace(tzinfo=None)
    for mod in (vs, ps, cls_mod, ms, ls):
        monkeypatch.setattr(mod, "datetime", _Frozen)
    return fixed


def _agent(db, code):
    u = User(id=_uid(), email=f"{code.lower()}@t.io", phone=("9" + code).ljust(10, "0")[:10],
             full_name=f"Agent {code}", hashed_password="x", role=UserRole.FIELD_AGENT,
             is_active=True, is_verified=True)
    db.add(u)
    db.flush()
    a = Agent(id=_uid(), user_id=u.id, employee_code=code, id_card_number=code + "-ID", gender="M",
              base_latitude=28.63, base_longitude=77.21, territory="Delhi", languages_spoken=["HINDI"],
              status=AgentStatus.ON_DUTY, tier=AgentTier.TIER_1, specialization=AgentSpecialization.BOTH,
              ranking_score=80.0, max_cases_per_day=5)
    db.add(a)
    db.flush()
    return a


def _case(db, agent, ref):
    c = Customer(id=_uid(), customer_ref=ref, full_name=f"Borrower {ref}", date_of_birth=date(1990, 1, 1),
                 gender="M", pan_masked="ABCDE1234F", aadhaar_masked="123456789012",
                 phone_primary="98" + ref.ljust(8, "0"), address_line1="Delhi", city="Delhi", state="Delhi",
                 pincode="110001", latitude=28.6315, longitude=77.2167, language_preference="HINDI")
    db.add(c)
    db.flush()
    loan = Loan(id=_uid(), customer_id=c.id, loan_account_number="L" + ref, loan_type=LoanType.PERSONAL,
                branch_code="DL01", sanctioned_amount=100000.0, disbursed_amount=100000.0,
                outstanding_principal=50000.0, total_outstanding=50000.0, overdue_amount=10000.0,
                emi_amount=5000.0, interest_rate=12.0, disbursement_date=date(2022, 1, 1),
                maturity_date=date(2027, 1, 1), dpd=45, dpd_bucket=DPDBucket.BUCKET_2, status=LoanStatus.ACTIVE)
    db.add(loan)
    db.flush()
    k = Case(id=_uid(), case_number="C-" + ref, customer_id=c.id, loan_id=loan.id, agent_id=agent.id,
             status=CaseStatus.ASSIGNED, target_amount=20000.0, collected_amount=0.0, allocation_date=DAY)
    db.add(k)
    db.flush()
    return k


@pytest.fixture()
def w():
    create_schema(engine)
    db = Session()
    agent = _agent(db, "OFA")
    other = _agent(db, "OFB")
    dev = AgentDevice(id=_uid(), agent_id=agent.id, device_fingerprint=device_fingerprint_for(DEVICE),
                      is_bound=True, bound_at=ist(DAY - timedelta(days=30), 9))
    db.add(dev)
    case = _case(db, agent, "A1")
    case2 = _case(db, agent, "A2")
    db.commit()
    yield SimpleNamespace(db=db, agent=agent, other=other, dev=dev, case=case, case2=case2)
    db.close()
    drop_schema(engine)


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    """Best-effort side effects are not under test; the notice is recorded."""
    sent = []
    monkeypatch.setattr(vs.VisitService, "_notify_visit_completed",
                        lambda self, agent, case, now: sent.append(case.id))
    monkeypatch.setattr(vs.AIReportService, "generate_visit_report", staticmethod(lambda *a, **k: None))
    return sent


def _visit(**kw) -> RecordVisitRequest:
    base = dict(check_in_latitude=28.6315, check_in_longitude=77.2167, customer_met=False,
                outcome=VisitOutcome.NOT_AVAILABLE)
    base.update(kw)
    return RecordVisitRequest(**base)


def _offline(captured_at, seq=1, **kw):
    return dict(client_submission_id=str(uuid.uuid4()), captured_at=captured_at, device_seq=seq,
                device_id=DEVICE, **kw)


def _record(w, req, case=None, token=DEVICE):
    return vs.VisitService(w.db).record_visit(w.agent, (case or w.case).id, req, token_device_id=token)


def _code(exc) -> ErrorCode:
    return getattr(exc.value, "code", None)


# ═══════════════════════════════════════════════════════════════════════════
# 1. Bounds on the client capture time
# ═══════════════════════════════════════════════════════════════════════════

def _judge(w, captured_at, now, seq=1, item=DEVICE, token=DEVICE):
    return ct.judge_capture(w.db, w.agent, captured_at=captured_at, device_seq=seq,
                            item_device_id=item, token_device_id=token, now=now)


def test_no_capture_time_is_a_live_submit_judged_now(w):
    now = ist(DAY, 11)
    c = ct.judge_capture(w.db, w.agent, captured_at=None, device_seq=None, item_device_id=None,
                         token_device_id=None, now=now)
    assert (c.at, c.late, c.lag_seconds) == (now, False, 0)


@pytest.mark.parametrize("offset_s, late", [(0, False), (-120, False), (120, False), (-121, True)])
def test_live_tolerance_is_120_seconds_either_side(w, offset_s, late):
    now = ist(DAY, 11)
    c = _judge(w, now + timedelta(seconds=offset_s), now)
    assert c.late is late
    assert c.at == (now + timedelta(seconds=offset_s) if late else now)


def test_capture_beyond_future_tolerance_is_refused_not_clamped(w):
    now = ist(DAY, 11)
    with pytest.raises(HTTPException) as exc:
        _judge(w, now + timedelta(seconds=121), now)
    assert exc.value.status_code == 422 and _code(exc) == ErrorCode.CAPTURE_IN_FUTURE


def test_capture_age_bound_is_48_hours(w):
    now = ist(DAY, 11)
    assert ct.OFFLINE_MAX_AGE_HOURS == 48
    ok = _judge(w, now - timedelta(hours=48), now)
    assert ok.late and ok.lag_seconds == 48 * 3600
    with pytest.raises(HTTPException) as exc:
        _judge(w, now - timedelta(hours=48, seconds=1), now)
    assert exc.value.status_code == 422 and _code(exc) == ErrorCode.CAPTURE_TOO_OLD


@pytest.mark.parametrize("item, token", [
    (DEVICE, None),                 # no device on the token
    ("phone-b-0002", DEVICE),       # item captured on another phone
    ("phone-b-0002", "phone-b-0002"),  # token's device is not the bound one
    (None, DEVICE),                 # item names no device
])
def test_late_item_must_come_from_the_bound_device_it_was_captured_on(w, item, token):
    now = ist(DAY, 11)
    with pytest.raises(HTTPException) as exc:
        _judge(w, now - timedelta(hours=2), now, item=item, token=token)
    assert exc.value.status_code == 403 and _code(exc) == ErrorCode.CAPTURE_DEVICE_MISMATCH


def test_unbound_device_is_refused(w):
    w.dev.is_bound = False
    w.db.commit()
    now = ist(DAY, 11)
    with pytest.raises(HTTPException) as exc:
        _judge(w, now - timedelta(hours=2), now)
    assert _code(exc) == ErrorCode.CAPTURE_DEVICE_MISMATCH


def test_capture_before_the_device_was_bound_is_refused(w):
    now = ist(DAY, 11)
    w.dev.bound_at = now - timedelta(hours=1)
    w.db.commit()
    with pytest.raises(HTTPException) as exc:
        _judge(w, now - timedelta(hours=2), now)
    assert _code(exc) == ErrorCode.CAPTURE_DEVICE_MISMATCH


def test_order_rules_sequence_and_time(w):
    now = ist(DAY, 11)
    w.dev.last_outbox_seq = 5
    w.dev.last_outbox_captured_at = now - timedelta(hours=1)
    w.db.commit()
    for seq, at in [(None, now - timedelta(minutes=30)),           # late with no sequence
                    (5, now - timedelta(minutes=30)),              # not after the last one
                    (6, now - timedelta(hours=1, seconds=121))]:   # earlier than delivered
        with pytest.raises(HTTPException) as exc:
            _judge(w, at, now, seq=seq)
        assert exc.value.status_code == 409 and _code(exc) == ErrorCode.CAPTURE_OUT_OF_ORDER
    ok = _judge(w, now - timedelta(minutes=30), now, seq=6)
    ct.note_delivered(ok)
    assert (w.dev.last_outbox_seq, ct.as_utc(w.dev.last_outbox_captured_at)) == (6, now - timedelta(minutes=30))


def test_a_live_outbox_item_still_advances_the_high_water_mark(w):
    now = ist(DAY, 11)
    live = _judge(w, now - timedelta(seconds=30), now, seq=9)
    assert not live.late
    ct.note_delivered(live)
    assert w.dev.last_outbox_seq == 9
    with pytest.raises(HTTPException) as exc:     # a replay cannot slip in behind it
        _judge(w, now - timedelta(hours=1), now, seq=8)
    assert _code(exc) == ErrorCode.CAPTURE_OUT_OF_ORDER


# ═══════════════════════════════════════════════════════════════════════════
# 2. A late visit is judged at capture
# ═══════════════════════════════════════════════════════════════════════════

def test_visit_captured_1830_synced_2030_is_accepted_at_its_capture_time(w, monkeypatch, _quiet):
    _freeze(monkeypatch, ist(DAY, 20, 30))
    out = _record(w, _visit(**_offline(ist(DAY, 18, 30))))
    v = w.db.get(Visit, out["id"])
    assert ct.as_utc(v.check_in_time) == ist(DAY, 18, 30)
    assert v.within_contact_hours is True
    assert _quiet == [], "an evening sync must not text the borrower at 20:30"
    audit = w.db.query(AuditLog).filter(AuditLog.action == AuditAction.VISIT_RECORDED).one()
    assert audit.details["offline"] is True and audit.details["lag_s"] == 7200


def test_live_visit_at_2030_is_still_refused(w, monkeypatch):
    _freeze(monkeypatch, ist(DAY, 20, 30))
    with pytest.raises(HTTPException) as exc:
        _record(w, _visit())
    assert exc.value.status_code == 403


def test_visit_captured_1910_is_refused_and_audited_at_capture(w, monkeypatch):
    _freeze(monkeypatch, ist(DAY, 20, 30))
    with pytest.raises(HTTPException) as exc:
        _record(w, _visit(**_offline(ist(DAY, 19, 10))))
    assert exc.value.status_code == 403
    row = w.db.query(AuditLog).filter(AuditLog.action == AuditAction.CONTACT_HOUR_VIOLATION_ATTEMPT).one()
    assert row.details["ist_hour"] == 19 and row.details["offline"] is True
    assert w.db.query(Visit).count() == 0
    assert w.dev.last_outbox_seq is None, "a refused item does not advance the device"


def test_late_notice_is_sent_only_inside_hours_on_the_capture_day(w, monkeypatch, _quiet):
    _freeze(monkeypatch, ist(DAY, 15))
    _record(w, _visit(**_offline(ist(DAY, 12), seq=1)))
    assert _quiet == [w.case.id]
    _freeze(monkeypatch, ist(DAY + timedelta(days=1), 10))
    _record(w, _visit(**_offline(ist(DAY, 13), seq=2)), case=w.case2)
    assert _quiet == [w.case.id], "a next-day sync sends nothing"


def test_same_submission_twice_makes_one_visit(w, monkeypatch):
    _freeze(monkeypatch, ist(DAY, 15))
    req = _visit(**_offline(ist(DAY, 12)))
    first = _record(w, req)
    again = _record(w, req)
    assert first["id"] == again["id"]
    assert w.db.query(Visit).count() == 1


def test_submission_id_reused_for_another_case_is_refused(w, monkeypatch):
    _freeze(monkeypatch, ist(DAY, 15))
    offline = _offline(ist(DAY, 12))
    _record(w, _visit(**offline))
    with pytest.raises(HTTPException) as exc:
        _record(w, _visit(**offline), case=w.case2)
    assert exc.value.status_code == 409 and _code(exc) == ErrorCode.IDEMPOTENCY_KEY_REUSED


def test_reallocated_case_is_accepted_on_its_capture_days_beat_and_not_taken_over(w, monkeypatch):
    w.case.agent_id = w.other.id                     # the 20:00 allocation moved it
    w.db.add(Beat(agent_id=w.agent.id, beat_date=DAY, beat_number="1", ordered_case_ids=[w.case.id]))
    w.db.commit()
    _freeze(monkeypatch, ist(DAY + timedelta(days=1), 9))
    _record(w, _visit(**_offline(ist(DAY, 17))))
    w.db.refresh(w.case)
    assert w.case.agent_id == w.other.id, "a replay never takes a case over"


def test_reallocated_case_without_the_capture_days_beat_is_404(w, monkeypatch):
    w.case.agent_id = w.other.id
    w.db.add(Beat(agent_id=w.agent.id, beat_date=DAY + timedelta(days=1), beat_number="1",
                  ordered_case_ids=[w.case.id]))          # today's beat, not the capture day's
    w.db.commit()
    _freeze(monkeypatch, ist(DAY + timedelta(days=1), 9))
    with pytest.raises(HTTPException) as exc:
        _record(w, _visit(**_offline(ist(DAY, 17))))
    assert exc.value.status_code == 404


def test_late_visit_does_not_regress_a_newer_case_state(w, monkeypatch):
    _freeze(monkeypatch, ist(DAY, 16))
    _record(w, _visit(outcome=VisitOutcome.RTP, customer_met=True, person_met="BORROWER"))   # live, 16:00
    w.db.refresh(w.case)
    assert w.case.status == CaseStatus.ESCALATED
    _record(w, _visit(**_offline(ist(DAY, 12)), outcome=VisitOutcome.PTP, customer_met=True, person_met="BORROWER"))
    w.db.refresh(w.case)
    assert w.case.status == CaseStatus.ESCALATED, "the 12:00 visit is history, not the case's state"
    assert w.db.query(Visit).count() == 2


def test_late_visit_moves_the_case_when_nothing_newer_happened(w, monkeypatch):
    _freeze(monkeypatch, ist(DAY, 16))
    _record(w, _visit(**_offline(ist(DAY, 12)), outcome=VisitOutcome.PTP, customer_met=True, person_met="BORROWER"))
    w.db.refresh(w.case)
    assert w.case.status == CaseStatus.PTP_SET


def test_late_visit_counts_in_its_own_month(w, monkeypatch):
    before = w.agent.current_month_visits or 0
    _freeze(monkeypatch, ist(date(2026, 10, 1), 10))
    _record(w, _visit(**_offline(ist(date(2026, 9, 30), 18))))
    w.db.refresh(w.agent)
    assert (w.agent.current_month_visits or 0) == before


def test_late_visit_does_not_move_a_newer_last_known_position(w, monkeypatch):
    w.agent.last_location_update = ist(DAY, 15)
    w.agent.last_known_latitude, w.agent.last_known_longitude = 28.7, 77.3
    w.db.commit()
    _freeze(monkeypatch, ist(DAY, 16))
    _record(w, _visit(**_offline(ist(DAY, 12))))
    w.db.refresh(w.agent)
    assert (w.agent.last_known_latitude, w.agent.last_known_longitude) == (28.7, 77.3)


def test_live_visit_without_outbox_fields_behaves_as_before(w, monkeypatch):
    fixed = _freeze(monkeypatch, ist(DAY, 11))
    out = _record(w, _visit(), token=None)
    v = w.db.get(Visit, out["id"])
    assert ct.as_utc(v.check_in_time) == fixed and v.client_submission_id is None


# ═══════════════════════════════════════════════════════════════════════════
# 3. PTPs and call logs replay the same way
# ═══════════════════════════════════════════════════════════════════════════

def test_same_ptp_submission_twice_makes_one_promise(w, monkeypatch):
    _freeze(monkeypatch, ist(DAY, 15))
    req = SetPTPRequest(committed_amount=5000, committed_date=DAY + timedelta(days=5),
                        **_offline(ist(DAY, 12)))
    a = ps.PaymentService(w.db).set_ptp(w.agent, w.case.id, req, token_device_id=DEVICE)
    b = ps.PaymentService(w.db).set_ptp(w.agent, w.case.id, req, token_device_id=DEVICE)
    assert a["id"] == b["id"] and w.db.query(PTP).count() == 1


def test_late_ptp_on_a_reallocated_case_uses_the_capture_days_beat(w, monkeypatch):
    w.case.agent_id = w.other.id
    w.db.add(Beat(agent_id=w.agent.id, beat_date=DAY, beat_number="1", ordered_case_ids=[w.case.id]))
    w.db.commit()
    _freeze(monkeypatch, ist(DAY + timedelta(days=1), 9))
    req = SetPTPRequest(committed_amount=5000, committed_date=DAY + timedelta(days=5),
                        **_offline(ist(DAY, 17)))
    ps.PaymentService(w.db).set_ptp(w.agent, w.case.id, req, token_device_id=DEVICE)
    w.db.refresh(w.case)
    assert w.case.agent_id == w.other.id


def test_call_log_replay_is_idempotent_and_dated_at_capture(w, monkeypatch):
    _freeze(monkeypatch, ist(DAY, 15))
    req = LogCallRequest(outcome=CallOutcome.NO_ANSWER, **_offline(ist(DAY, 12)))
    a = cls_mod.CallLogService(w.db).log_call(w.agent, w.case.id, req, token_device_id=DEVICE)
    b = cls_mod.CallLogService(w.db).log_call(w.agent, w.case.id, req, token_device_id=DEVICE)
    assert a["id"] == b["id"] and w.db.query(CallLog).count() == 1
    assert ct.as_utc(w.db.get(CallLog, a["id"]).called_at) == ist(DAY, 12)


# ═══════════════════════════════════════════════════════════════════════════
# 4. The GPS trail shares the window (behaviour change: 24 h -> 48 h)
# ═══════════════════════════════════════════════════════════════════════════

def test_trail_accepts_fixes_up_to_48_hours_old(w, monkeypatch):
    now = _freeze(monkeypatch, ist(DAY, 12))
    assert ls.MAX_AGE_HOURS == ct.OFFLINE_MAX_AGE_HOURS == 48
    pings = [LocationPing(latitude=28.60, longitude=77.20, recorded_at=now - timedelta(hours=47)),
             LocationPing(latitude=28.70, longitude=77.30, recorded_at=now - timedelta(hours=49))]
    out = ls.LocationService(w.db).record_batch(w.agent, pings)
    assert (out["accepted"], out["rejected"]) == (1, 1)
    assert w.db.query(AgentLocation).count() == 1


# ═══════════════════════════════════════════════════════════════════════════
# 5. Outbox photo uploads
# ═══════════════════════════════════════════════════════════════════════════

def test_outbox_photo_key_is_stable_per_submission_and_final_once_stored(w, monkeypatch):
    monkeypatch.setattr(ms.storage, "presigned_upload_url", lambda key, **k: f"https://minio.test/{key}")
    _freeze(monkeypatch, ist(DAY, 15))
    off = _offline(ist(DAY, 12))
    svc = ms.MediaService(w.db)
    args = dict(client_submission_id=off["client_submission_id"], captured_at=off["captured_at"],
                device_seq=1, device_id=DEVICE, token_device_id=DEVICE)
    k1 = svc.get_photo_upload_url(w.agent, w.case.id, "agent", **args)["key"]
    k2 = svc.get_photo_upload_url(w.agent, w.case.id, "agent", **args)["key"]
    assert k1 == k2 and off["client_submission_id"] in k1
    _record(w, _visit(**off, agent_photo_key=k1))
    with pytest.raises(HTTPException) as exc:
        svc.get_photo_upload_url(w.agent, w.case.id, "agent", **args)
    assert exc.value.status_code == 409


# ═══════════════════════════════════════════════════════════════════════════
# 6. Through HTTP: the route hands the token's device to the rule
# ═══════════════════════════════════════════════════════════════════════════

def test_route_judges_the_tokens_device(w, monkeypatch):
    def override():
        db = Session()
        try:
            yield db
        finally:
            db.close()
    app.dependency_overrides[get_db] = override
    _freeze(monkeypatch, ist(DAY, 15))
    user = w.db.get(User, w.agent.user_id)
    body = _visit(**_offline(ist(DAY, 12))).model_dump(mode="json")
    try:
        with TestClient(app) as c:
            wrong = c.post(f"/api/v1/agent/cases/{w.case.id}/visit", json=body, headers={
                "Authorization": f"Bearer {create_access_token(user.id, user.role.value, 'phone-b-0002')}"})
            assert wrong.status_code == 403 and wrong.json().get("code") == "CAPTURE_DEVICE_MISMATCH"
            right = c.post(f"/api/v1/agent/cases/{w.case.id}/visit", json=body, headers={
                "Authorization": f"Bearer {create_access_token(user.id, user.role.value, DEVICE)}"})
            assert right.status_code == 200, right.text
    finally:
        app.dependency_overrides.pop(get_db, None)


# ═══════════════════════════════════════════════════════════════════════════
# 7. Manager signals: LATE_SYNC and SYNC_WITHHELD (fraud_service)
# ═══════════════════════════════════════════════════════════════════════════

def _vis(w, captured, received, csid=True):
    return SimpleNamespace(
        id=_uid(), agent_id=w.agent.id, case_id=w.case.id, check_in_time=captured, created_at=received,
        client_submission_id=_uid() if csid else None,
        agent=SimpleNamespace(employee_code="OFA", user=SimpleNamespace(full_name="Agent OFA")),
        case=SimpleNamespace(case_number="C-A1"))


def _ping(w, received):
    w.db.add(AgentLocation(agent_id=w.agent.id, latitude=28.6, longitude=77.2,
                           recorded_at=received, received_at=received))
    w.db.commit()


def _signals(w, visits):
    from app.services.fraud_service import FraudService
    return [(f["type"], f["severity"]) for f in FraudService(w.db)._late_sync(visits)]


def test_offline_visit_synced_three_hours_later_is_low_late_sync(w):
    c = ist(DAY, 12)
    assert _signals(w, [_vis(w, c, c + timedelta(hours=3))]) == [("LATE_SYNC", "LOW")]


def test_short_offline_gap_and_live_visits_raise_nothing(w):
    c = ist(DAY, 12)
    assert _signals(w, [_vis(w, c, c + timedelta(minutes=30)),
                        _vis(w, c, c + timedelta(hours=3), csid=False)]) == []


def test_gps_getting_through_long_before_the_visit_is_sync_withheld(w):
    c = ist(DAY, 12)
    _ping(w, c + timedelta(hours=1))
    assert _signals(w, [_vis(w, c, c + timedelta(hours=3))]) == [("SYNC_WITHHELD", "MEDIUM")]


def test_gps_in_the_same_flush_as_the_visit_is_not_withheld(w):
    c = ist(DAY, 12)
    _ping(w, c + timedelta(hours=3) - timedelta(minutes=2))     # same flush, within slack
    _ping(w, c + timedelta(minutes=5))                           # before the phone went offline
    assert _signals(w, [_vis(w, c, c + timedelta(hours=3))]) == [("LATE_SYNC", "LOW")]


def test_another_agents_gps_is_not_evidence(w):
    c = ist(DAY, 12)
    w.db.add(AgentLocation(agent_id=w.other.id, latitude=28.6, longitude=77.2,
                           recorded_at=c + timedelta(hours=1), received_at=c + timedelta(hours=1)))
    w.db.commit()
    assert _signals(w, [_vis(w, c, c + timedelta(hours=3))]) == [("LATE_SYNC", "LOW")]


def test_late_visit_records_its_bound_device(w, monkeypatch):
    _freeze(monkeypatch, ist(DAY, 15))
    out = _record(w, _visit(**_offline(ist(DAY, 12))))
    assert w.db.get(Visit, out["id"]).agent_device_id == w.dev.id


# ═══════════════════════════════════════════════════════════════════════════
# 8. Do Not Contact is judged at sync (decision 5), and a live payment visit
#    carries only its key
# ═══════════════════════════════════════════════════════════════════════════

def test_late_visit_to_a_borrower_now_on_do_not_contact_is_refused_with_its_code(w, monkeypatch):
    w.case.customer.do_not_contact = True        # flagged after the capture
    w.db.commit()
    _freeze(monkeypatch, ist(DAY, 15))
    with pytest.raises(HTTPException) as exc:
        _record(w, _visit(**_offline(ist(DAY, 12))))
    assert exc.value.status_code == 403 and _code(exc) == ErrorCode.DO_NOT_CONTACT
    assert exc.value.detail == "Customer is marked Do Not Contact"      # the detail is unchanged


def test_live_submit_with_only_a_key_is_idempotent_and_judged_now(w, monkeypatch):
    fixed = _freeze(monkeypatch, ist(DAY, 11))
    req = _visit(client_submission_id=str(uuid.uuid4()))
    a = _record(w, req, token=None)
    b = _record(w, req, token=None)
    assert a["id"] == b["id"] and w.db.query(Visit).count() == 1
    assert ct.as_utc(w.db.get(Visit, a["id"]).check_in_time) == fixed


def test_a_binding_with_no_time_refuses_late_items(w):
    """Fail closed (audit LOW): without bound_at nothing shows the capture came after binding."""
    w.dev.bound_at = None
    w.db.commit()
    now = ist(DAY, 11)
    with pytest.raises(HTTPException) as exc:
        _judge(w, now - timedelta(hours=2), now)
    assert _code(exc) == ErrorCode.CAPTURE_DEVICE_MISMATCH
    assert not _judge(w, now - timedelta(seconds=30), now).late       # live submits unaffected


# ═══════════════════════════════════════════════════════════════════════════
# 9. Opus audit of 1a85ffd
# ═══════════════════════════════════════════════════════════════════════════

def test_ptp_dated_before_its_capture_day_is_refused(w, monkeypatch):
    _freeze(monkeypatch, ist(DAY + timedelta(days=1), 10))
    req = SetPTPRequest(committed_amount=5000, committed_date=DAY - timedelta(days=1),
                        **_offline(ist(DAY, 12)))
    with pytest.raises(HTTPException) as exc:
        ps.PaymentService(w.db).set_ptp(w.agent, w.case.id, req, token_device_id=DEVICE)
    assert exc.value.status_code == 422
    assert w.db.query(PTP).count() == 0
    ok = SetPTPRequest(committed_amount=5000, committed_date=DAY, **_offline(ist(DAY, 12), seq=2))
    ps.PaymentService(w.db).set_ptp(w.agent, w.case.id, ok, token_device_id=DEVICE)   # the capture day itself is fine


def test_late_item_does_not_move_a_case_with_a_newer_promise(w, monkeypatch):
    _freeze(monkeypatch, ist(DAY, 16))
    live = SetPTPRequest(committed_amount=5000, committed_date=DAY + timedelta(days=3))
    ps.PaymentService(w.db).set_ptp(w.agent, w.case.id, live)                         # live, 16:00
    w.case.status = CaseStatus.IN_PROGRESS                                            # something moved it since
    w.db.commit()
    _record(w, _visit(**_offline(ist(DAY, 12)), outcome=VisitOutcome.RTP, customer_met=True, person_met="BORROWER"))
    w.db.refresh(w.case)
    assert w.case.status == CaseStatus.IN_PROGRESS, "a 12:00 visit must not override a 16:00 promise"


def test_call_log_to_a_do_not_contact_borrower_is_refused(w, monkeypatch):
    w.case.customer.do_not_contact = True
    w.db.commit()
    _freeze(monkeypatch, ist(DAY, 15))
    with pytest.raises(HTTPException) as exc:
        cls_mod.CallLogService(w.db).log_call(w.agent, w.case.id, LogCallRequest(outcome=CallOutcome.NO_ANSWER))
    assert exc.value.status_code == 403 and _code(exc) == ErrorCode.DO_NOT_CONTACT
    assert w.db.query(CallLog).count() == 0


def test_photo_url_route_takes_the_phone_id_as_capture_device_ref(w, monkeypatch):
    monkeypatch.setattr(ms.storage, "presigned_upload_url", lambda key, **k: f"https://minio.test/{key}")

    def override():
        db = Session()
        try:
            yield db
        finally:
            db.close()
    app.dependency_overrides[get_db] = override
    _freeze(monkeypatch, ist(DAY, 15))
    user = w.db.get(User, w.agent.user_id)
    auth = {"Authorization": f"Bearer {create_access_token(user.id, user.role.value, DEVICE)}"}
    off = _offline(ist(DAY, 12))
    params = {"subject": "agent", "client_submission_id": off["client_submission_id"],
              "captured_at": off["captured_at"].isoformat(), "device_seq": 1}
    try:
        with TestClient(app) as c:
            url = f"/api/v1/agent/cases/{w.case.id}/photo-upload-url"
            ok = c.post(url, params={**params, "capture_device_ref": DEVICE}, headers=auth)
            assert ok.status_code == 200, ok.text
            assert off["client_submission_id"] in ok.json()["key"]
            other = c.post(url, params={**params, "capture_device_ref": "phone-b-0002"}, headers=auth)
            assert other.status_code == 403
    finally:
        app.dependency_overrides.pop(get_db, None)
