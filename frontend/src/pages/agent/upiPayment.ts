// ─── CHANGELOG ─────────────────────────────────────────────────────────────
// 2026-09-24 (hotfix PAY-1 / PAY-2) — New file. The UPI rules RecordVisitPage
//   applies, pure so they are tested without mounting the page.
//
//   PAY-1: a static UPI QR has no callback. The page flipped to "Payment
//   received" 10 s after showing it and then waived the transaction-ID field,
//   so a "UPI payment" could be recorded with no evidence it happened — in
//   every build, production included. The flip is now a DEMO aid behind the
//   build flag VITE_DEMO_UPI_AUTOCONFIRM === "1" (off by default; the dev
//   compose sets it, the prod Dockerfile never does), and even then it does
//   not waive the reference: it fills a DEMO-UPI-… one, so the server rule
//   (a UPI payment needs a UTR) holds everywhere.
//   PAY-2: the QR's payee came from a VPA and "ABC Bank" hardcoded in the
//   bundle. It now comes from server settings, and no settings means no QR.
// ─────────────────────────────────────────────────────────────────────────────
import type { UpiConfig } from "@/api/agent";

export const DEMO_UPI_REFERENCE_PREFIX = "DEMO-UPI-";

/** The demo auto-confirm runs only when the build asked for it, exactly. */
export function demoUpiAutoconfirmEnabled(env: Record<string, unknown>): boolean {
  return env.VITE_DEMO_UPI_AUTOCONFIRM === "1";
}

/** The reference a demo auto-confirm records: labelled as a demo, never a real-looking UTR. */
export function demoUpiReference(nowMs: number): string {
  return `${DEMO_UPI_REFERENCE_PREFIX}${nowMs}`;
}

/** Mirrors the server rule (payment_service.payment_reference_problem): a UPI
 *  reference is a 12-digit UTR. The demo's DEMO-UPI- reference passes only in
 *  a demo build — and the server accepts it only with DEMO_UPI_ACCEPT. */
export function upiReferenceOk(ref: string | null | undefined, opts: { demo?: boolean } = {}): boolean {
  const r = (ref ?? "").trim();
  if (opts.demo && r.toUpperCase().startsWith(DEMO_UPI_REFERENCE_PREFIX)) return true;
  return /^\d{12}$/.test(r.replace(/\s+/g, ""));
}

export interface PaymentReferences {
  upiRef: string;
  neftRef: string;
  chequeNumber: string;
}

/** The evidence each mode must carry, as the server requires it: UPI a UTR,
 *  NEFT / RTGS / DD a bank reference, CHEQUE its number. Cash needs none. */
export function paymentReferenceOk(mode: string, refs: PaymentReferences, opts: { demo?: boolean } = {}): boolean {
  if (mode === "UPI") return upiReferenceOk(refs.upiRef, opts);
  if (mode === "NEFT" || mode === "RTGS" || mode === "DD") return refs.neftRef.trim().length > 0;
  if (mode === "CHEQUE") return refs.chequeNumber.trim().length > 0;
  return true;
}

/** The upi:// payload for the QR, or null when the server has no payee. */
export function upiQrValue(cfg: UpiConfig | null | undefined, amount: number, note: string): string | null {
  if (!cfg?.available || !cfg.vpa?.trim() || !cfg.payee_name?.trim() || !(amount > 0)) return null;
  const q = new URLSearchParams({ pa: cfg.vpa.trim(), pn: cfg.payee_name.trim(), am: String(amount), tn: note, cu: "INR" });
  return `upi://pay?${q.toString()}`;
}
