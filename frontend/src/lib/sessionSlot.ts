/**
 * Session slots — two logins in one browser tab.
 *
 * 2026-09-24 (standalone plan, task P0-01) — the mobile simulator puts the
 * agent app and the manager view side by side as two iframes on the SAME
 * origin. Same origin means same localStorage, and the auth store persists to
 * one key, so logging the manager in silently logged the agent out (the second
 * write wins, and the agent's next request carries the manager's token).
 *
 * A slot namespaces the persisted keys. It is read from `?slot=<name>` on the
 * first load and then carried in `window.name`, because:
 *   - the query string does not survive in-app navigation (react-router drops
 *     it on the first `navigate()`), and
 *   - sessionStorage does NOT separate the frames: same-origin iframes share
 *     their top-level tab's session storage, so a slot kept there would be
 *     overwritten by the other frame exactly like the token was.
 * `window.name` belongs to one browsing context — each iframe has its own —
 * and survives reloads and navigations inside it.
 *
 * No slot (every normal page load) means the historical keys, unchanged: a
 * user who has never seen the simulator keeps their existing session.
 */

const NAME_PREFIX = "tiq-slot:";
const SLOT_RE = /^[a-z0-9_-]{1,32}$/i;

/**
 * Pure resolution: the query wins (it is how a frame is told its slot), then
 * the slot already carried in window.name. Returns the slot and the
 * window.name that should carry it forward. An invalid name is ignored rather
 * than sanitised — a slot is a storage key, not user content.
 */
export function resolveSlotFrom(
  search: string,
  windowName: string,
): { slot: string | null; windowName: string } {
  const fromQuery = new URLSearchParams(search).get("slot");
  const fromName = windowName.startsWith(NAME_PREFIX)
    ? windowName.slice(NAME_PREFIX.length)
    : null;
  const slot = fromQuery ?? fromName;
  if (!slot || !SLOT_RE.test(slot)) return { slot: null, windowName };
  return { slot, windowName: NAME_PREFIX + slot };
}

function resolveSlot(): string | null {
  try {
    const { slot, windowName } = resolveSlotFrom(window.location.search, window.name);
    if (slot) window.name = windowName;
    return slot;
  } catch {
    // No window (tests without jsdom) or a locked-down embed: no slot.
    return null;
  }
}

/** The slot this page was opened in, or null for an ordinary session. */
export const SESSION_SLOT: string | null = resolveSlot();

/** Namespace a persisted key by the current slot; unchanged when there is none. */
export function slotKey(base: string, slot: string | null = SESSION_SLOT): string {
  return slot ? `${base}:${slot}` : base;
}
