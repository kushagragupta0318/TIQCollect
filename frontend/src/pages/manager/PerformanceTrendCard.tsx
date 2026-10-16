// The Overview's daily trend: what was planned against what came in.
//
// Grey shaded target band, blue collected line over it, ONE shared rupee axis.
// The shared axis is the point — collected is meant to read as a fraction of
// target, and a second axis would let a line sitting at 10% of target look
// like it was tracking it.
//
// Recharts primitives directly, like the other manager charts
// (PaymentModesCard, ManagerAnalyticsPage). The bank's TrendAreaChart has the
// same structure and was the reference, but it carries the bank's own chart
// theme; importing it would put two design languages on one page.
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { TrendingUp } from "lucide-react";
import {
  Area, CartesianGrid, ComposedChart, Line, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { getPerformanceSeries, type PerformancePoint } from "@/api/manager";
import { shortMoney } from "@/lib/money";

const TARGET = "#94A3B8";   // slate — the backdrop, never the subject
const COLLECTED = "#2563EB"; // the same blue PaymentModesCard uses for digital

const RANGES = [30, 60] as const;
type Range = (typeof RANGES)[number];

/** "17 Sep" — the axis has room for a day and a month, not a year. */
function dayLabel(iso: string): string {
  const d = new Date(`${iso}T00:00:00`);
  return Number.isNaN(d.getTime())
    ? iso
    : d.toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

function TrendTooltip({ active, payload, label }: {
  active?: boolean;
  payload?: { dataKey?: string | number; value?: number | string }[];
  label?: string | number;
}) {
  if (!active || !payload?.length) return null;
  const read = (k: string) => Number(payload.find((p) => p.dataKey === k)?.value ?? 0);
  const target = read("target");
  const collected = read("collected");
  // The ratio is the question the chart exists to answer, so it is stated
  // rather than left to be eyeballed off the shading.
  const pct = target > 0 ? Math.round((collected / target) * 100) : null;
  return (
    <div className="rounded-xl px-3 py-2 text-xs"
         style={{ background: "#fff", border: "1px solid #EAEBEF", boxShadow: "0 4px 12px rgba(16,24,40,0.08)" }}>
      <p className="font-semibold" style={{ color: "#1C1C1F" }}>{dayLabel(String(label))}</p>
      <p className="mt-1" style={{ color: COLLECTED }}>Collected {shortMoney(collected)}</p>
      <p style={{ color: "#6B6D76" }}>Target {shortMoney(target)}</p>
      {pct !== null && <p className="mt-0.5" style={{ color: "#6B6D76" }}>{pct}% of target</p>}
    </div>
  );
}

export function PerformanceTrendCard({ onPickDay }: { onPickDay?: (day: string) => void }) {
  const [range, setRange] = useState<Range>(30);
  const q = useQuery({
    queryKey: ["manager", "performance-series", range],
    queryFn: () => getPerformanceSeries(range),
    staleTime: 5 * 60_000,
  });

  const points: PerformancePoint[] = useMemo(() => q.data?.points ?? [], [q.data]);
  // A range where nothing was ever planned AND nothing collected is empty; a
  // range of real zeros is not, and must still plot.
  const hasAnything = points.some((p) => p.target > 0 || p.collected > 0);

  return (
    <div className="card p-4 flex flex-col">
      <div className="flex items-center justify-between gap-2 mb-1">
        <div className="flex items-center gap-2">
          <span className="flex-shrink-0" style={{ color: "#6B6D76" }} aria-hidden="true">
            <TrendingUp className="w-4 h-4" />
          </span>
          <h2 className="text-sm font-bold" style={{ color: "#1C1C1F" }}>Daily performance</h2>
        </div>
        <div className="flex rounded-xl overflow-hidden flex-shrink-0" style={{ border: "1px solid #EAEBEF" }}>
          {RANGES.map((r) => (
            <button
              key={r}
              type="button"
              onClick={() => setRange(r)}
              aria-pressed={range === r}
              className="tap-target-h px-3 py-1.5 text-xs font-semibold transition hover:brightness-95"
              style={{ background: range === r ? "#1677FF" : "#fff", color: range === r ? "#fff" : "#6B6D76" }}
            >
              {r}d
            </button>
          ))}
        </div>
      </div>
      <p className="text-xs mb-3" style={{ color: "#6B6D76" }}>
        Collected against the day's planned target. Verified payments only.
      </p>

      {q.isPending ? (
        <div className="flex items-center justify-center" style={{ height: 220 }}>
          <span className="text-xs" style={{ color: "#8A8C94" }}>Loading…</span>
        </div>
      ) : q.isError ? (
        <div className="flex flex-col items-center justify-center gap-2" style={{ height: 220 }}>
          <p className="text-xs" style={{ color: "#991B1B" }}>This trend could not be loaded.</p>
          <button type="button" onClick={() => q.refetch()}
                  className="rounded-control px-3 py-1.5 text-xs font-semibold"
                  style={{ border: "1px solid #EAEBEF", color: "#6B6D76" }}>
            Retry
          </button>
        </div>
      ) : !hasAnything ? (
        <div className="flex items-center justify-center" style={{ height: 220 }}>
          <p className="text-xs text-center" style={{ color: "#8A8C94" }}>
            No plans or collections in the last {range} days.
          </p>
        </div>
      ) : (
        <ResponsiveContainer width="100%" height={220}>
          <ComposedChart
            data={points}
            margin={{ top: 6, right: 8, bottom: 0, left: 4 }}
            // Recharts v3 types the handler's argument without activePayload,
            // though it is present at runtime — read it through a narrow shape
            // rather than widening to `any`.
            onClick={(e: unknown) => {
              const payload = (e as { activePayload?: { payload?: PerformancePoint }[] })
                ?.activePayload?.[0]?.payload;
              if (payload?.day && onPickDay) onPickDay(payload.day);
            }}
          >
            <defs>
              <linearGradient id="mgr-target-fill" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor={TARGET} stopOpacity={0.30} />
                <stop offset="95%" stopColor={TARGET} stopOpacity={0.02} />
              </linearGradient>
            </defs>
            <CartesianGrid stroke="#F1F5F9" vertical={false} />
            <XAxis dataKey="day" tickFormatter={dayLabel} tick={{ fontSize: 11, fill: "#8A8C94" }}
                   axisLine={false} tickLine={false} minTickGap={24} />
            {/* One axis on purpose: collected is read AS A SHARE of target. */}
            <YAxis tickFormatter={shortMoney} tick={{ fontSize: 11, fill: "#8A8C94" }}
                   axisLine={false} tickLine={false} width={56} />
            <Tooltip content={<TrendTooltip />} />
            <Area type="monotone" dataKey="target" name="Target" stroke={TARGET}
                  strokeWidth={1} fill="url(#mgr-target-fill)" isAnimationActive={false} />
            <Line type="monotone" dataKey="collected" name="Collected" stroke={COLLECTED}
                  strokeWidth={2} dot={false} isAnimationActive={false} />
          </ComposedChart>
        </ResponsiveContainer>
      )}

      <div className="flex items-center gap-4 mt-2 text-[11px]" style={{ color: "#6B6D76" }}>
        <span className="flex items-center gap-1.5">
          <span style={{ width: 10, height: 10, borderRadius: 3, background: TARGET, opacity: 0.45 }} />
          Target
        </span>
        <span className="flex items-center gap-1.5">
          <span style={{ width: 10, height: 2, background: COLLECTED }} />
          Collected
        </span>
      </div>
    </div>
  );
}

export default PerformanceTrendCard;
