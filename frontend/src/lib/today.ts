/**
 * "Today" on the IST calendar — the one definition, so a phone in any
 * timezone (or with a wrong clock) agrees with the backend's
 * leave_today() (services/leave_service.py), and a screen never reads as
 * yesterday between 00:00 and 05:30 IST the way `new Date().toISOString()`
 * does (CLAUDE.md known issue 3). Built on serverClock's server-corrected
 * now rather than the device clock, for the same reason capture times are.
 * IST is UTC+5:30 with no DST, so a fixed offset is exact.
 */
import { serverNow } from "./serverClock";

const IST_OFFSET_MS = 5.5 * 60 * 60 * 1000;

/** YYYY-MM-DD for the given instant (default: server-corrected now), IST calendar. */
export function istDateStr(when: Date = serverNow()): string {
  const shifted = new Date(when.getTime() + IST_OFFSET_MS);
  const y = shifted.getUTCFullYear();
  const m = String(shifted.getUTCMonth() + 1).padStart(2, "0");
  const d = String(shifted.getUTCDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}

/** Today, IST calendar, as YYYY-MM-DD. */
export function todayIso(): string {
  return istDateStr();
}

/** This month, IST calendar, as YYYY-MM. */
export function todayMonthIso(): string {
  return istDateStr().slice(0, 7);
}
