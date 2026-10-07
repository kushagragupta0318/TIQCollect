// The Usage & Cost page's types, formatting and fetch — beside the page
// rather than in it, as auditModel / overviewModel / placementModel are: a
// file that exports both a component and helpers breaks fast refresh (and
// the lint rule that guards it), and these are the parts worth testing
// without rendering.
import api from "@/api/axios";

export interface UsageTotals {
  calls: number;
  input_tokens: number;
  output_tokens: number;
  cache_tokens: number;
  cost_usd: number;
  // Calls whose model had no known price (core/llm.py's table) — already
  // excluded from cost_usd, never folded into it as a silent zero.
  unpriced_calls: number;
}

export interface UsageByFeature {
  feature: string;
  calls: number;
  input_tokens: number;
  output_tokens: number;
  cache_tokens: number;
  cost_usd: number;
}

export interface UsageByDay {
  day: string;
  calls: number;
  cost_usd: number;
}

export interface UsageCoverage {
  pending_attribution: number;
  note: string;
}

export interface UsagePayload {
  since: string;
  until: string | null;
  totals: UsageTotals;
  by_feature: UsageByFeature[];
  by_day: UsageByDay[];
  coverage: UsageCoverage;
}

export async function getUsage(): Promise<UsagePayload> {
  const { data } = await api.get<UsagePayload>("/bank/usage");
  return data;
}

/** SCREAMING_SNAKE reads as shouting in a table; the feature is the row's
 *  label, not its alarm — the same reasoning auditModel's actionLabel uses. */
export function featureLabel(feature: string): string {
  return feature.toLowerCase().replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
}

export function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return n.toLocaleString();
}

export function formatUsd(n: number): string {
  // A single call's cost is often sub-cent, where 2dp would show every one
  // as $0.00. Extra precision only kicks in there — once a figure clears a
  // cent it reads as ordinary currency, 2dp, same as any other total.
  if (n !== 0 && Math.abs(n) < 0.01) return `$${n.toFixed(4)}`;
  return `$${n.toFixed(2)}`;
}

export function formatDay(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}
