const ENTITIES: Record<string, string> = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };

/**
 * Escape a value for an HTML string. Leaflet tooltips, popups and divIcons take their string
 * content as innerHTML, so any name, label or code from the API goes through this first.
 */
export function escapeHtml(value: string | number | null | undefined): string {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ENTITIES[c]);
}
