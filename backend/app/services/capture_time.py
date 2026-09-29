"""When did a field action happen? The one rule for a client-supplied capture time.

An offline item (I02, docs/adr/0011-offline-outbox.md) reaches the server hours
after the agent pressed Submit. The rules that depend on time (contact hours,
the day's beat) must judge the moment of capture, or every evening sync is
refused. The capture time comes from the phone's clock, so it is bounded here:
not in the future, not older than OFFLINE_MAX_AGE_HOURS, only from the agent's
bound device, and never earlier than what that device already delivered.

A phone's owner can still forge a consistent story inside these bounds. The
bounds cap the window; fraud_service's LATE_SYNC / SYNC_WITHHELD are the
detection side.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.core.errors import AppException, ErrorCode
from app.core.geo import IST
from app.core.security import device_fingerprint_for

# A capture this close to the server's clock is a live submit, judged at `now`
# exactly as before the outbox existed. Also the future tolerance for any
# client clock (location_service imports it).
LIVE_TOLERANCE_SECONDS = 120

# The oldest capture accepted, for visits, PTPs, call logs and the GPS trail
# alike (coordinator, 2026-09-29): a whole field day offline plus the next
# evening. One window, so a replayed visit always has its trail to test against.
OFFLINE_MAX_AGE_HOURS = 48


@dataclass(frozen=True)
class Capture:
    at: datetime          # the time every time-dependent rule judges
    late: bool            # True: a replay, judged at the capture time
    lag_seconds: int      # receipt minus capture; 0 for a live submit
    device: object | None = None   # the bound AgentDevice of a late item
    seq: int | None = None

    @property
    def day(self) -> date:
        """The IST calendar day the action happened on."""
        return self.at.astimezone(IST).date()


def as_utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def judge_capture(db: Session, agent, *, captured_at: datetime | None, device_seq: int | None,
                  item_device_id: str | None, token_device_id: str | None,
                  now: datetime) -> Capture:
    """The capture this item is judged at, or a typed refusal.

    No `captured_at`, or one within LIVE_TOLERANCE_SECONDS of `now`, is a live
    submit: judged at `now`, as every submit was before I02. Anything older must
    carry its device sequence and come from the agent's bound device."""
    now = as_utc(now)
    if captured_at is None:
        return Capture(at=now, late=False, lag_seconds=0)
    at = as_utc(captured_at)
    tolerance = timedelta(seconds=LIVE_TOLERANCE_SECONDS)
    if at > now + tolerance:
        # Refused, not clamped: clamping would judge contact hours at `now` again.
        raise AppException(422, ErrorCode.CAPTURE_IN_FUTURE,
                           "This phone's clock is ahead of the server's. Set the phone to automatic "
                           "time, then record the visit again.")
    lag = now - at
    if lag <= tolerance:
        # Live, but an outbox item still advances its device's high-water mark,
        # or a later replay could slip in behind it.
        device = bound_device(db, agent, token_device_id) if device_seq is not None else None
        return Capture(at=now, late=False, lag_seconds=0, device=device, seq=device_seq)
    if lag > timedelta(hours=OFFLINE_MAX_AGE_HOURS):
        raise AppException(422, ErrorCode.CAPTURE_TOO_OLD,
                           f"Recorded more than {OFFLINE_MAX_AGE_HOURS} hours ago and never sent. "
                           "Tell your manager about this visit; it cannot be accepted now.")

    device = bound_device(db, agent, token_device_id)
    if device is None or not item_device_id or item_device_id != token_device_id:
        raise AppException(403, ErrorCode.CAPTURE_DEVICE_MISMATCH,
                           "Recorded offline on a different phone. Only the phone it was recorded "
                           "on can send it.")
    bound_at = device.bound_at and as_utc(device.bound_at)
    if bound_at is not None and at < bound_at - tolerance:
        raise AppException(403, ErrorCode.CAPTURE_DEVICE_MISMATCH,
                           "Recorded before this phone was registered to you.")
    last_seq = device.last_outbox_seq
    last_at = device.last_outbox_captured_at and as_utc(device.last_outbox_captured_at)
    if device_seq is None or (last_seq is not None and device_seq <= last_seq) \
            or (last_at is not None and at < last_at - tolerance):
        raise AppException(409, ErrorCode.CAPTURE_OUT_OF_ORDER,
                           "Recorded out of order with what this phone already sent.")
    return Capture(at=at, late=True, lag_seconds=int(lag.total_seconds()), device=device, seq=device_seq)


def bound_device(db: Session, agent, token_device_id: str | None):
    """The agent's bound device, if it is the one this request's token names.

    The token's device_id is server-signed, and the A09b device secret is what
    let that device log in, so the pair names a real phone."""
    from app.models.agent import AgentDevice
    if not token_device_id:
        return None
    fp = device_fingerprint_for(token_device_id)
    return (db.query(AgentDevice)
            .filter(AgentDevice.agent_id == agent.id, AgentDevice.is_bound.is_(True),
                    AgentDevice.device_fingerprint == fp)
            .first())


def moves_case(db: Session, case, capture: Capture) -> bool:
    """May this item change the case's status? A live one always. A late one
    only if nothing newer happened to the case: no later visit, and not
    resolved meanwhile (ADR 0011 §5). Otherwise it is stored as history."""
    from app.models.visit import Visit
    if not capture.late:
        return True
    if case.resolved_at is not None:
        return False
    newer = (db.query(Visit.id)
             .filter(Visit.case_id == case.id, Visit.check_in_time > capture.at)
             .first())
    return newer is None


def same_ist_month(a: datetime, b: datetime) -> bool:
    """Monthly counters reset on the 1st (IST): an item captured last month
    and synced this month is last month's, and must not bump this one."""
    a, b = as_utc(a).astimezone(IST), as_utc(b).astimezone(IST)
    return (a.year, a.month) == (b.year, b.month)


def note_delivered(capture: Capture) -> None:
    """Advance the device's high-water mark, in the caller's business transaction."""
    device = capture.device
    if device is None or capture.seq is None:
        return
    if device.last_outbox_seq is None or capture.seq > device.last_outbox_seq:
        device.last_outbox_seq = capture.seq
    last_at = device.last_outbox_captured_at and as_utc(device.last_outbox_captured_at)
    if last_at is None or capture.at > last_at:
        device.last_outbox_captured_at = capture.at
