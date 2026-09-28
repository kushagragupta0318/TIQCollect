// ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
// 2026-09-24 — New file (task UI03). Where bank dialogs and drawers portal to.
//
//   CC's Dialog, DrillPanel and WorkspaceModal all call
//   `createPortal(…, document.body)`. Here that would put them OUTSIDE the
//   `.bank-root` wrapper, where every bank rule is scoped and every CC variable
//   is declared — they would render with TIQCollect's blue tokens and none of
//   the bank's utilities. CC's own `FieldAnalytics.css:194-195` hit exactly
//   this (spec §7.3). So they portal into one `<div class="bank-root"
//   data-bank-portal>` on <body>, created on first use and reused after.
//   bank.css gives that div `display: contents`, so it has no box of its own.
// ─────────────────────────────────────────────────────────────────────────────

const PORTAL_ATTR = "data-bank-portal";

let portalRoot: HTMLElement | null = null;

export function getBankPortalRoot(): HTMLElement {
  if (portalRoot?.isConnected) return portalRoot;
  const existing = document.querySelector<HTMLElement>(`[${PORTAL_ATTR}]`);
  if (existing) {
    portalRoot = existing;
    return existing;
  }
  const el = document.createElement("div");
  el.className = "bank-root";
  el.setAttribute(PORTAL_ATTR, "");
  document.body.appendChild(el);
  portalRoot = el;
  return el;
}
