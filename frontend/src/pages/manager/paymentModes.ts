/**
 * Collection by Payment Mode — the pure half: words, ordering, bar sizing and
 * the cash / digital footer. The figures arrive computed from
 * GET /manager/analytics/payment-modes (VERIFIED payments by the manager's
 * agents, optionally one month); nothing here re-derives them.
 */

export interface PaymentModeRow {
  mode: string;
  count: number;
  amount: number;
  share_pct: number;
  avg_ticket: number;
  is_cash: boolean;
  is_digital: boolean;
}

export interface PaymentModeMonth {
  month: string;          // YYYY-MM
  cash: number;
  digital: number;
  paper: number;          // cheque + DD
  total: number;
  count: number;
  cash_share_pct: number;
}

export interface PaymentModes {
  month: string | null;
  /** The last six months, cash / digital / paper — NOT narrowed by `month`. */
  monthly: PaymentModeMonth[];
  total_amount: number;
  total_count: number;
  cash_amount: number;
  cash_share_pct: number;
  digital_amount: number;
  digital_share_pct: number;
  modes: PaymentModeRow[];
}

export const MODE_WORDS: Record<string, string> = {
  CASH: "Cash",
  UPI: "UPI",
  NEFT: "NEFT",
  RTGS: "RTGS",
  CHEQUE: "Cheque",
  DD: "Demand draft",
  ONLINE: "Online",
  BANK_DIRECT: "Paid to bank",
};
export const modeWords = (m: string) => MODE_WORDS[m] ?? m.replace(/_/g, " ");

/** Bar width as a share of the LARGEST mode, so the top bar is full width. */
export function shareOfLargest(amount: number, rows: readonly PaymentModeRow[]): number {
  const max = rows.reduce((m, r) => Math.max(m, r.amount), 0);
  if (!(max > 0) || !(amount > 0)) return 0;
  return Math.min(1, amount / max);
}

/** Rows as the card lists them: largest first (the API already does this;
 *  re-sorted here so the card never depends on wire order). */
export function orderedModes(rows: readonly PaymentModeRow[] | undefined | null): PaymentModeRow[] {
  return [...(rows ?? [])].sort((a, b) => b.amount - a.amount || a.mode.localeCompare(b.mode));
}

/** Cash above this share of the month's collections is called out. The
 *  threshold is a display choice, not a policy: it marks the row, it does
 *  not judge it. */
export const CASH_ATTENTION_SHARE_PCT = 25;

export function cashCallout(d: Pick<PaymentModes, "cash_share_pct" | "cash_amount">): string | null {
  if (!(d.cash_amount > 0)) return null;
  return d.cash_share_pct >= CASH_ATTENTION_SHARE_PCT
    ? `Cash is ${Math.round(d.cash_share_pct)}% of collections — handled in the field by agents.`
    : null;
}
