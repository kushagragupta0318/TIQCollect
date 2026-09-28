// ─── CHANGELOG (prototype → product) ───────────────────────────────────────
// 2026-09-24 — NEW (G06). The manager nav, as ONE list that both the desktop
//   rail and the mobile bottom bar read. ManagerLayout used to map one
//   NAV_ITEMS array into both, which was fine while both showed everything;
//   the phone bar now shows four of the seven plus a "More" sheet, and a
//   second hand-kept list for the phone would be the two-copies-of-one-rule
//   drift CLAUDE.md warns about. `primary` on the item is the only thing that
//   decides which surface an entry lands on.
//
//   Why the bar had to shrink, measured: seven items across a 360 px phone
//   is 336 px of bar (left-2/right-2 + px-1) — 48 px a slot, 4 px over the
//   44 px touch floor, with "Compliance" and "Field Plan" truncated at 10 px.
//   Four plus More is 67 px a slot and every label whole.
//
//   Primary = what a manager reaches for on a phone, away from a desk: today's
//   numbers (Overview), who is on duty or asking for leave (Agents — the bell
//   deep-links there), where they are (Live Map), and one borrower's case
//   (Cases). Field Plan, Analytics and Compliance are sit-down reviews.
// ─────────────────────────────────────────────────────────────────────────────

import {
  BarChart2, Briefcase, Compass, LayoutDashboard, MapPin, Shield, Users,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";

export interface ManagerNavItem {
  to: string;
  icon: LucideIcon;
  label: string;
  /** On the phone's bottom bar. False → it lives in the "More" sheet. The
   *  desktop rail shows every item regardless. */
  primary: boolean;
}

/** Every manager destination, in desktop-rail order. */
export const MANAGER_NAV: readonly ManagerNavItem[] = [
  { to: "/manager/overview",   icon: LayoutDashboard, label: "Overview",   primary: true },
  { to: "/manager/agents",     icon: Users,           label: "Agents",     primary: true },
  { to: "/manager/live-map",   icon: MapPin,          label: "Live Map",   primary: true },
  // "Field Plan" in the manager's words; the path keeps the code's word.
  { to: "/manager/beat-plan",  icon: Compass,         label: "Field Plan", primary: false },
  { to: "/manager/cases",      icon: Briefcase,       label: "Cases",      primary: true },
  { to: "/manager/analytics",  icon: BarChart2,       label: "Analytics",  primary: false },
  { to: "/manager/compliance", icon: Shield,          label: "Compliance", primary: false },
];

/** The phone's bottom bar, before the More button. */
export const MOBILE_PRIMARY: readonly ManagerNavItem[] = MANAGER_NAV.filter((i) => i.primary);

/** Everything the More sheet lists. */
export const MOBILE_MORE: readonly ManagerNavItem[] = MANAGER_NAV.filter((i) => !i.primary);

const trimSlash = (p: string) => (p.length > 1 && p.endsWith("/") ? p.slice(0, -1) : p);

/**
 * Whether `pathname` is inside a nav item — the same rule react-router's
 * NavLink applies by default (not `end`, not case-sensitive): the path itself
 * or anything under it, on a segment boundary, so `/manager/agents/42` is
 * Agents and `/manager/agentsX` is not.
 */
export function isWithin(pathname: string, to: string): boolean {
  const p = trimSlash(pathname.toLowerCase());
  const t = trimSlash(to.toLowerCase());
  return p === t || p.startsWith(`${t}/`);
}

/** The More-sheet item the current route belongs to, or null. */
export function activeMoreItem(pathname: string): ManagerNavItem | null {
  return MOBILE_MORE.find((i) => isWithin(pathname, i.to)) ?? null;
}

/**
 * Whether the More button reads as active. It does while its sheet is open,
 * and — the case that matters — while the page on screen is one of its
 * items: on Analytics none of the four primary tabs is lit, and a bar with
 * nothing highlighted tells the manager they are nowhere.
 */
export function moreButtonActive(pathname: string, sheetOpen: boolean): boolean {
  return sheetOpen || activeMoreItem(pathname) !== null;
}

/**
 * The More sheet is open FOR ONE LOCATION: opening it records the router's
 * location object, and it is open only while that same object is current.
 * Any navigation — a tap in the sheet, the back button, a deep link — makes a
 * new location and so closes it, with no effect syncing state to the route
 * (react-hooks/set-state-in-effect).
 *
 * Identity, not `location.key`: a key belongs to a history ENTRY and comes
 * back when the user returns to it, so a sheet opened on page B, left with
 * Back, would reopen on Forward. A location object is new on every
 * navigation, including a return to an entry already visited.
 */
export function isSheetOpen<L extends object>(openedFor: L | null, current: L): boolean {
  return openedFor !== null && openedFor === current;
}
