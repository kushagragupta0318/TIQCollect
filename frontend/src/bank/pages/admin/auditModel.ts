// The Audit page's types, formatting and fetch — beside the page rather than
// in it, as overviewModel / analyticsModel / placementModel are: a file that
// exports both a component and helpers breaks fast refresh (and the lint rule
// that guards it), and these are the parts worth testing without rendering.
import api from "@/api/axios";

export interface AuditRow {
  id: string;
  created_at: string | null;
  action: string;
  actor_name: string | null;
  actor_id: string | null;
  agency_id: string | null;
  entity_type: string | null;
  entity_id: string | null;
  success: boolean;
  failure_reason: string | null;
  ip_address: string | null;
}

export interface AuditPayload {
  since: string;
  total: number;
  limit: number;
  offset: number;
  entries: AuditRow[];
  counts_by_action: Record<string, number>;
  coverage: {
    declared_action_types: number;
    sensitive_actions: string[];
    /** PLATFORM-WIDE, not this bank's — see the note. */
    pending_attribution: number;
    window_days: number;
    note: string;
  };
}

export const PAGE = 50;

/** SCREAMING_SNAKE reads as shouting in a table; the action is the row's
 *  label, not its alarm. The sensitive badge carries the severity instead. */
export function actionLabel(action: string): string {
  return action.toLowerCase().replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
}

export function when(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleString();
}

export async function getAudit(params: Record<string, string | number>): Promise<AuditPayload> {
  const { data } = await api.get<AuditPayload>("/bank/audit", { params });
  return data;
}

/** The CSV, as a blob.
 *
 * NOT window.open on the URL: /bank/audit/export is authenticated, and a bare
 * browser navigation carries no Authorization header, so it 401s and the user
 * gets nothing. manager.ts's exportAuditLog has said so in a comment since the
 * compliance page shipped; this page did it the broken way for a day.
 */
export async function exportAudit(params: Record<string, string>): Promise<Blob> {
  const res = await api.get("/bank/audit/export", { params, responseType: "blob" });
  return res.data;
}
