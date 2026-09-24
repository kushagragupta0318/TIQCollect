/**
 * Reading a simulator frame's session from the parent page (P0-03).
 *
 * The phone and the manager view are same-origin iframes whose sessions live
 * under slot-namespaced keys (lib/sessionSlot.ts). The simulator page itself
 * never logs in: it reads those keys to call the API *as* that frame — the
 * agent's token to fetch the beat it will play, the manager's token to hold
 * the event timeline open. It never writes them; each frame refreshes its own
 * tokens, and a read at every call picks the new ones up.
 */
import { slotKey } from "@/lib/sessionSlot";

export const AGENT_SLOT = "agent";
export const MANAGER_SLOT = "manager";

export interface SlotAuth {
  accessToken: string | null;
  role: string | null;
  name: string | null;
}

/** Parse zustand-persist's `{"state": {...}, "version": n}` for one slot. */
export function readSlotAuth(
  slot: string,
  storage: Pick<Storage, "getItem"> | null = typeof localStorage === "undefined" ? null : localStorage,
): SlotAuth {
  const empty: SlotAuth = { accessToken: null, role: null, name: null };
  if (!storage) return empty;
  try {
    const raw = storage.getItem(slotKey("tiq_auth", slot));
    if (!raw) return empty;
    const state = (JSON.parse(raw) as { state?: Record<string, unknown> }).state ?? {};
    const user = (state.user as { role?: string; full_name?: string } | null) ?? null;
    return {
      accessToken: typeof state.accessToken === "string" ? state.accessToken : null,
      role: user?.role ?? null,
      name: user?.full_name ?? null,
    };
  } catch {
    return empty;
  }
}

/** Where a frame should start: its home when it already has a session. */
export function frameEntry(slot: string, auth: SlotAuth): string {
  return auth.accessToken ? `/?slot=${slot}` : `/login?slot=${slot}`;
}

/** GET an API path as the given slot. Throws on non-2xx. */
export async function slotGet<T>(slot: string, path: string): Promise<T> {
  const { accessToken } = readSlotAuth(slot);
  if (!accessToken) throw new Error(`${slot} frame is not logged in`);
  const r = await fetch(`/api/v1${path}`, { headers: { Authorization: `Bearer ${accessToken}` } });
  if (!r.ok) throw new Error(`${path} → HTTP ${r.status}`);
  return (await r.json()) as T;
}
