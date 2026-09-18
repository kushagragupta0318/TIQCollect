// ─── CHANGELOG (prototype → product) ───────────────────────────────────────
// 2026-09-17 — NEW. Payment-mode cards on the Analytics page.
//
//   Two cards, side by side in the money grid's third row, one query between
//   them (React Query dedupes the shared key, so the pair costs one request):
//
//     PaymentMixCard    "Collection by Payment Mode" — one 100% composition
//                       bar, every segment a mode, cash in the status amber;
//                       a legend with rupees and share; the cash / digital
//                       footer. Narrows to the selected month like the DPD
//                       card beside it.
//     CashTrendCard     "Cash vs Digital by Month" — six stacked columns,
//                       cash / digital / paper (cheque + DD), the last six
//                       months the trend chart shows. The reason the pair
//                       exists: whether cash is RISING. On the 2026-09-17
//                       book it is, 3% in April to 30% in September. Never
//                       narrowed by the month — it is the context; click a
//                       column to select that month, click again to clear.
//
//   Cash is the only coloured category (amber, with the word, never hue
//   alone); digital is the series blue; paper the neutral.
//
//   *(First as seven full-width bars, then as one wide card holding both
//   halves — which read as two charts overlapping. Split into two cards
//   the same day, before commit.)*
// ─────────────────────────────────────────────────────────────────────────────

import { useQuery } from "@tanstack/react-query";
import { Banknote, TrendingUp } from "lucide-react";
import { Bar, BarChart, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { getPaymentModes } from "@/api/manager";
import { shortAmount, shortMoney } from "@/lib/money";
import { cashCallout, modeWords, orderedModes, type PaymentModeMonth } from "./paymentModes";
import { LIVE } from "@/lib/liveQuery";

const EASE = "cubic-bezier(0.16,1,0.3,1)";
const CASH = "#D97706";
const DIGITAL = "#2563EB";
const PAPER = "#94A3B8";
const MODE_COLOUR: Record<string, string> = {
  CASH: CASH,
  UPI: "#2563EB", NEFT: "#3B82F6", RTGS: "#60A5FA",
  CHEQUE: "#94A3B8", DD: "#CBD5E1",
};
const INK_ON: Record<string, string> = { CASH: "#fff", UPI: "#fff", NEFT: "#fff" };
const TOOLTIP_STYLE = {
  fontSize: "11px", borderRadius: "8px",
  border: "1px solid #111827", backgroundColor: "#111827",
  color: "#F9FAFB", boxShadow: "0 4px 12px rgba(0,0,0,0.18)", padding: "7px 11px",
};

const monthWords = (ym: string) =>
  new Date(`${ym}-01T00:00:00`).toLocaleDateString("en-IN", { month: "short", year: "2-digit" }).replace(" ", " '");

/** One request for both cards. */
function usePaymentModes(apiMonth: string | null) {
  return useQuery({
    queryKey: ["manager", "analytics", "payment-modes", apiMonth],
    queryFn: () => getPaymentModes(apiMonth ?? undefined),
    retry: false,
    staleTime: 0,
    ...LIVE,
  });
}

function CardShell({ icon, title, subtitle, children }: {
  icon: React.ReactNode; title: string; subtitle: React.ReactNode; children: React.ReactNode;
}) {
  return (
    <div className="card p-4 flex flex-col">
      <div className="flex items-center gap-2 mb-1">
        <span className="flex-shrink-0" style={{ color: "#6B6D76" }} aria-hidden="true">{icon}</span>
        <h2 className="text-sm font-bold" style={{ color: "#1C1C1F" }}>{title}</h2>
      </div>
      <p className="text-xs mb-3" style={{ color: "#6B6D76" }}>{subtitle}</p>
      {children}
    </div>
  );
}

export function PaymentMixCard({ apiMonth, selMonth }: { apiMonth: string | null; selMonth: string | null }) {
  const q = usePaymentModes(apiMonth);
  const d = q.data ?? null;
  const rows = orderedModes(d?.modes).filter((r) => r.amount > 0);
  const callout = d ? cashCallout(d) : null;

  return (
    <CardShell
      icon={<Banknote className="w-4 h-4" />}
      title="Collection by Payment Mode"
      subtitle={<>
        Verified collections by your agents
        {selMonth
          ? <> · <span className="font-semibold" style={{ color: "#1677FF" }}>{selMonth}</span></>
          : " · all time · click a month to filter"}
        {d && d.total_count > 0 && <> · {shortMoney(d.total_amount)} in {d.total_count} payments</>}
      </>}
    >
      {q.isError ? (
        <p className="text-sm text-center py-8" style={{ color: "#B45309" }}>Could not load payment modes.</p>
      ) : !d ? (
        <div className="space-y-3">
          <div className="h-7 rounded-lg animate-pulse" style={{ background: "#EFF0F4" }} />
          <div className="h-16 rounded-lg animate-pulse" style={{ background: "#EFF0F4" }} />
        </div>
      ) : d.total_count === 0 ? (
        <p className="text-sm text-center py-8" style={{ color: "#94a3b8" }}>
          {selMonth ? `No verified payments in ${selMonth}.` : "No verified payments yet."}
        </p>
      ) : (
        <div className="flex flex-col flex-1">
          <div className="flex h-8 w-full rounded-lg overflow-hidden gap-0.5" role="img"
               aria-label={rows.map((r) => `${modeWords(r.mode)} ${Math.round(r.share_pct)}%`).join(", ")}>
            {rows.map((r, i) => (
              <div
                key={r.mode}
                title={`${modeWords(r.mode)}: ${shortMoney(r.amount)} · ${r.count} payments · ${r.share_pct}%`}
                className="h-full flex items-center justify-center overflow-hidden"
                style={{ width: `${r.share_pct}%`, background: MODE_COLOUR[r.mode] ?? PAPER, animation: `enter 420ms ${EASE} ${i * 40}ms both` }}
              >
                {r.share_pct >= 9 && (
                  <span className="text-[10px] font-bold px-1 truncate" style={{ color: INK_ON[r.mode] ?? "#1C1C1F" }}>
                    {Math.round(r.share_pct)}%
                  </span>
                )}
              </div>
            ))}
          </div>
          <ul className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1.5 text-[11px]" style={{ color: "#6B6D76" }}>
            {rows.map((r) => (
              <li key={r.mode} className="flex items-center justify-between gap-2 min-w-0">
                <span className="inline-flex items-center gap-1.5 min-w-0">
                  <i aria-hidden="true" style={{ width: 9, height: 9, borderRadius: 2, background: MODE_COLOUR[r.mode] ?? PAPER, display: "inline-block", flexShrink: 0 }} />
                  <span className="truncate" style={{ color: r.mode === "CASH" ? "#92400E" : "#1C1C1F", fontWeight: r.mode === "CASH" ? 600 : 500 }}>
                    {modeWords(r.mode)}
                  </span>
                </span>
                <span className="tabular-nums flex-shrink-0">
                  <strong style={{ color: "#1C1C1F" }}>{shortMoney(r.amount)}</strong> · {Math.round(r.share_pct)}%
                </span>
              </li>
            ))}
          </ul>
          <div className="mt-auto pt-3 text-[11px]" style={{ borderTop: "1px solid #EAEBEF", color: "#6B6D76", marginTop: 12 }}>
            Cash <strong className="tabular-nums" style={{ color: "#92400E" }}>{Math.round(d.cash_share_pct)}%</strong>
            <span className="mx-2">·</span>
            Digital <strong className="tabular-nums" style={{ color: "#1C1C1F" }}>{Math.round(d.digital_share_pct)}%</strong>
            <span className="ml-1">(UPI, NEFT, RTGS)</span>
            {callout && <span className="block mt-1 font-medium" style={{ color: "#92400E" }}>{callout}</span>}
          </div>
        </div>
      )}
    </CardShell>
  );
}

export function CashTrendCard({ apiMonth, onMonthClick }: { apiMonth: string | null; onMonthClick?: (ym: string) => void }) {
  const q = usePaymentModes(apiMonth);
  const d = q.data ?? null;
  const monthly: PaymentModeMonth[] = d?.monthly ?? [];
  const first = monthly[0], last = monthly[monthly.length - 1];
  // Per-bar click reads the month off the bar's own payload, so it works even
  // when nothing has been hovered first (the chart-level activeLabel is only
  // set after a mouse move).
  const clickBar = (item: { payload?: { month?: string } }) => {
    const ym = item?.payload?.month;
    if (typeof ym === "string" && onMonthClick) onMonthClick(ym);
  };
  const cursor = onMonthClick ? "pointer" : "default";

  return (
    <CardShell
      icon={<TrendingUp className="w-4 h-4" />}
      title="Cash vs Digital by Month"
      subtitle={<>
        Last 6 months · click a column to select the month
        {first && last && <> · cash share <strong className="tabular-nums" style={{ color: "#1C1C1F" }}>{Math.round(first.cash_share_pct)}%</strong> → <strong className="tabular-nums" style={{ color: "#92400E" }}>{Math.round(last.cash_share_pct)}%</strong></>}
      </>}
    >
      {q.isError ? (
        <p className="text-sm text-center py-8" style={{ color: "#B45309" }}>Could not load payment modes.</p>
      ) : !d ? (
        <div className="h-40 rounded-lg animate-pulse" style={{ background: "#EFF0F4" }} />
      ) : monthly.every((m) => m.total === 0) ? (
        <p className="text-sm text-center py-8" style={{ color: "#94a3b8" }}>No verified payments in the last six months.</p>
      ) : (
        <div className="flex flex-col flex-1">
          <div style={{ height: 168 }}>
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={monthly} margin={{ top: 6, right: 4, bottom: 0, left: 0 }} barCategoryGap="30%"
                        onClick={(st) => { const ym = st?.activeLabel; if (typeof ym === "string" && onMonthClick) onMonthClick(ym); }}>
                <XAxis dataKey="month" tickFormatter={monthWords} tick={{ fontSize: 10, fill: "#94a3b8", fontWeight: 500 }} axisLine={false} tickLine={false} />
                <YAxis tickFormatter={(v: number) => (v === 0 ? "0" : shortAmount(v))} tick={{ fontSize: 10, fill: "#94a3b8", fontWeight: 500 }} axisLine={false} tickLine={false} width={40} tickCount={4} />
                <Tooltip
                  cursor={{ fill: "rgba(148,163,184,0.12)" }}
                  contentStyle={TOOLTIP_STYLE}
                  labelStyle={{ color: "#CBD5E1", marginBottom: 2 }}
                  itemStyle={{ color: "#F9FAFB", padding: 0 }}
                  labelFormatter={(ym) => `${monthWords(String(ym))} · click to select`}
                  formatter={(value, name, item) => {
                    const m = item.payload as PaymentModeMonth;
                    const label = name === "cash" ? `Cash (${m.cash_share_pct}%)` : name === "digital" ? "Digital" : "Paper (cheque, DD)";
                    return [shortMoney(Number(value)), label];
                  }}
                />
                <Bar dataKey="paper" name="paper" stackId="m" fill={PAPER} isAnimationActive animationDuration={500} onClick={clickBar} style={{ cursor }} />
                <Bar dataKey="digital" name="digital" stackId="m" fill={DIGITAL} isAnimationActive animationDuration={500} onClick={clickBar} style={{ cursor }} />
                <Bar dataKey="cash" name="cash" stackId="m" radius={[3, 3, 0, 0]} isAnimationActive animationDuration={500} onClick={clickBar} style={{ cursor }}>
                  {monthly.map((m) => (
                    <Cell key={m.month} fill={CASH} opacity={apiMonth && apiMonth !== m.month ? 0.45 : 1} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
          <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11px] mt-auto pt-3" style={{ borderTop: "1px solid #EAEBEF", color: "#6B6D76" }}>
            <span className="inline-flex items-center gap-1.5"><i aria-hidden="true" style={{ width: 9, height: 9, borderRadius: 2, background: CASH, display: "inline-block" }} />Cash</span>
            <span className="inline-flex items-center gap-1.5"><i aria-hidden="true" style={{ width: 9, height: 9, borderRadius: 2, background: DIGITAL, display: "inline-block" }} />Digital</span>
            <span className="inline-flex items-center gap-1.5"><i aria-hidden="true" style={{ width: 9, height: 9, borderRadius: 2, background: PAPER, display: "inline-block" }} />Paper (cheque, DD)</span>
          </div>
        </div>
      )}
    </CardShell>
  );
}
