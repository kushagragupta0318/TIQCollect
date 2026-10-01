// The top-bar search index (SearchBar.jsx:24-62, 116-121). CC indexes routes,
// workspace tools, and live KPIs / alerts; the bank starts with its pages and
// takes anything else as `extra` entries, so KPIs and alerts join the index
// when the Command Center tasks (C03, C06) produce them.
import type { LucideIcon } from "lucide-react";
import { BANK_NAV_ITEMS, bankHref, isBuiltPath } from "./navigation";

export type SearchKind = "page" | "tool" | "kpi" | "alert" | "account";

export interface SearchEntry {
  kind: SearchKind;
  id: string;
  title: string;
  subtitle: string;
  path?: string;
  icon?: LucideIcon;
}

/** Result-kind chips, verbatim from SearchBar.jsx:56-62 (spec §2.3). */
export const KIND_CHIP: Record<SearchKind, { color: string; bg: string; label: string }> = {
  account: { color: "text-primary", bg: "bg-primary/10", label: "Account" },
  page: { color: "text-secondary", bg: "bg-secondary/10", label: "Page" },
  tool: { color: "text-primary", bg: "bg-primary/10", label: "Tool" },
  kpi: { color: "text-success", bg: "bg-success/10", label: "KPI" },
  alert: { color: "text-warning", bg: "bg-warning/10", label: "Alert" },
};

// Built pages only, for the same reason the rail hides the rest: a search hit
// that lands on "Not built yet" reads as a broken product.
export const PAGE_ENTRIES: SearchEntry[] = BANK_NAV_ITEMS.filter((item) => isBuiltPath(item.path)).map((item) => ({
  kind: "page",
  id: bankHref(item),
  title: item.name,
  subtitle: `Route: ${bankHref(item)}`,
  path: bankHref(item),
  icon: item.icon,
}));

/** Case-insensitive match on title or subtitle; at most `limit` results, as CC's `.slice(0, 5)`. */
export function searchEntries(entries: SearchEntry[], query: string, limit = 5): SearchEntry[] {
  const q = query.trim().toLowerCase();
  if (!q) return [];
  return entries
    .filter((r) => r.title.toLowerCase().includes(q) || r.subtitle.toLowerCase().includes(q))
    .slice(0, limit);
}
