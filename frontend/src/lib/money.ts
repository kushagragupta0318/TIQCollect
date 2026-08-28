// ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
// 2026-08-28 — New file. ONE rupee formatter for the whole app.
//
//   WHY. There were five, plus seven inline copies of `(n / 1000).toFixed(0)}K`.
//   The same amount rendered differently depending on which screen you were on,
//   and the K-only copies produced figures nobody uses: the case detail modal
//   showed a ₹34,18,392 balance as "₹3418K". Rupees are grouped and spoken in
//   lakhs and crores; "3418K" has to be converted in the head before it means
//   anything, and at a glance it reads as three-point-four lakh.
//
//   ManagerOverviewPage's `tileMoney` had already worked this out in a comment
//   — "₹414K is really ₹4.1L, and the rest of the card speaks lakhs" — and then
//   applied it in exactly one place. This is that rule, applied everywhere.
//
//   THRESHOLDS AND PRECISION follow what the majority of the old call sites
//   already used, so this changed the K-only screens and left the rest looking
//   as they did: Cr to 2dp, L to 1dp, K to 0dp.
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Compact rupees in Indian units: ₹1.25Cr · ₹34.2L · ₹8K · ₹640.
 *
 * For dense surfaces — table cells, tiles, chart labels — where the exact
 * figure would not fit. Where a number is acted on rather than scanned, show
 * `exactRupees` instead; the case detail modal shows both.
 */
export function shortMoney(n: number): string {
  return `₹${shortAmount(n)}`;
}

/** `shortMoney` without the symbol, for callers that draw their own ₹ icon. */
export function shortAmount(n: number): string {
  if (!Number.isFinite(n)) return "0";
  const abs = Math.abs(n);
  const sign = n < 0 ? "-" : "";
  if (abs >= 1e7) return `${sign}${(abs / 1e7).toFixed(2)}Cr`;
  if (abs >= 1e5) return `${sign}${(abs / 1e5).toFixed(1)}L`;
  if (abs >= 1e3) return `${sign}${Math.round(abs / 1e3)}K`;
  return `${sign}${Math.round(abs)}`;
}

/**
 * The exact amount with Indian digit grouping: ₹34,18,392.
 *
 * The grouping IS lakh notation — 34,18,392 reads as thirty-four lakh eighteen
 * thousand — so this needs no unit suffix to be read correctly.
 */
export const exactRupees = (n: number): string =>
  `₹${Math.round(n).toLocaleString("en-IN")}`;

/**
 * The unit spelled out, as a secondary reading beneath an exact figure:
 * "34.18 lakh". Empty below ₹1 lakh, where the exact number is already short
 * enough to take in at a glance.
 */
export function lakhWords(n: number): string {
  if (!Number.isFinite(n) || Math.abs(n) < 1e5) return "";
  return Math.abs(n) >= 1e7
    ? `${(n / 1e7).toFixed(2)} crore`
    : `${(n / 1e5).toFixed(2)} lakh`;
}
