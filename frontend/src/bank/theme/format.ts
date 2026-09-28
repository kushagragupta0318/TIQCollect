// ─── CHANGELOG (prototype → product) ─────────────────────────────────────────
// 2026-09-24 — New file (task UI02). Command Center's number formatters,
//   ported VERBATIM (spec §4.12). They are deliberately NOT `lib/money.ts`:
//   that is TIQCollect's own rupee rule (₹34.2L, no space, K below a lakh) and
//   the agency/agent views keep it. The bank portal must print exactly what
//   CC prints, and CC has three distinct shapes that must not be merged:
//
//     cr / cr1   "₹12.34 Cr"   input ALREADY IN CRORES, space before the unit
//     fmtINR     "₹1.23Cr"     input in rupees, adaptive, NO space
//     pulseMoney "₹1,234.5 Cr" the server's KPI strings — Python's `,` groups
//                              in thousands (1,234.5), not the Indian lakh
//                              grouping `toLocaleString('en-IN')` produces
//
//   Folding any two of them into one helper would change what a bank screen
//   prints next to the CC screen it is compared against (UI06 parity).
// ─────────────────────────────────────────────────────────────────────────────

type Num = number | string | null | undefined;

/* ── Client formatters (PortfolioAnalytics.jsx:40-43 = DrillPanel.jsx:15-16 =
      DecisionAlerts.jsx:29-30) ─────────────────────────────────────────── */

/** "₹12.34 Cr" — the input is already in crores. */
export const cr = (v: Num): string => `₹${Number(v ?? 0).toFixed(2)} Cr`;
/** "₹12.3 Cr" — the input is already in crores. */
export const cr1 = (v: Num): string => `₹${Number(v ?? 0).toFixed(1)} Cr`;
/** "₹1,23,456" — rupees with Indian digit grouping. */
export const rs = (v: Num): string => `₹${Number(v ?? 0).toLocaleString("en-IN")}`;
/** "1,23,456" — a count with Indian digit grouping. */
export const n = (v: Num): string => Number(v ?? 0).toLocaleString("en-IN");

/* ── Adaptive rupee formatter (AccountDrawer.jsx:18-23 = RiskRadar.jsx:18-23 =
      PreDelinquency.jsx:19-24). NO space before the unit. ─────────────── */

export const fmtINR = (v: Num): string => {
  const x = Number(v) || 0;
  if (x >= 1e7) return `₹${(x / 1e7).toFixed(2)}Cr`;
  if (x >= 1e5) return `₹${(x / 1e5).toFixed(2)}L`;
  return `₹${x.toLocaleString("en-IN")}`;
};

/* ── The server's KPI strings (backend/engines/portfolio_pulse.py:39-40,
      299-305), for when the client has to build the same shape — sample data,
      a locally derived figure. The bank's kpi_catalog.py must emit these same
      strings; PulseKpiFlow renders them as-is.

        CR = 1e7; LAKH = 1e5
        def _cr(v, dp=1):  return f"₹{v / CR:,.{dp}f} Cr"
        def _money(v):     return _cr(v) if abs(v) >= CR else f"₹{v / LAKH:,.1f} L"
   ─────────────────────────────────────────────────────────────────────── */

export const CR = 1e7;
export const LAKH = 1e5;

// Python's `,` format spec: groups of three, whatever the locale.
const pyGrouped = (x: number, dp: number): string =>
  x.toLocaleString("en-US", { minimumFractionDigits: dp, maximumFractionDigits: dp });

/** `_cr(v, dp)`: rupees → "₹1,234.5 Cr". */
export const pulseCr = (v: number, dp = 1): string => `₹${pyGrouped(v / CR, dp)} Cr`;

/** `_money(v)`: crores at or above ₹1 Cr, otherwise lakhs → "₹85.2 L". */
export const pulseMoney = (v: number): string =>
  Math.abs(v) >= CR ? pulseCr(v) : `₹${pyGrouped(v / LAKH, 1)} L`;

/** `f"{x:+.1f}"` — Python's explicit sign; zero prints as "+0.0". */
export const signed = (x: number, dp = 1): string => `${x < 0 || Object.is(x, -0) ? "-" : "+"}${Math.abs(x).toFixed(dp)}`;

/* ── Dates ──────────────────────────────────────────────────────────────── */

/** The top-bar date (TopBar.jsx:18): "24 Sep 2026". */
export const chromeDate = (d: Date): string =>
  d.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** Python `"%d %b %Y"` (frame labels, "30 days to 22 Sep 2026"): day is zero-padded. */
export const pyDate = (d: Date): string =>
  `${String(d.getDate()).padStart(2, "0")} ${MONTHS[d.getMonth()]} ${d.getFullYear()}`;
