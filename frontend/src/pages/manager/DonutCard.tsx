// ─── CHANGELOG (prototype → product) ───────────────────────────────────────
// 2026-09-16 — NEW. The one donut the manager pages draw.
//
//   Extracted from CasePipelineCard the moment a second donut was needed
//   (today's cases by DPD bucket, on the overview). Two hand-maintained copies
//   of the same ring, hover model and legend would have drifted within a week
//   — the pattern this repo's CHANGELOG headers keep recording. The two cards
//   now supply slices and words; everything visual is here, once.
//
//   The hover model: hovering a slice OR its legend row highlights it in both
//   and swaps the centre figure to that slice. No floating tooltip — an
//   earlier Recharts <Tooltip> rendered inside the chart box, clipped at its
//   edge and printed over the total in the hole. Shares are printed ON the
//   ring (white, at mid-thickness) so identity never rests on hue; a slice
//   under 8% is too thin for a label and its count is on the legend row.
// ─────────────────────────────────────────────────────────────────────────────

import { useState, type ReactNode } from "react";
import { Cell, Pie, PieChart, ResponsiveContainer, type PieLabelRenderProps } from "recharts";

const MIN_LABELLED_SHARE = 0.08;

export interface DonutSlice {
  key: string;
  label: string;
  colour: string;
  count: number;
  /** Two or three words for the centre when this slice is hovered. */
  shortLabel: string;
  /** Rendered under the legend row — status chips, a rupee line, anything. */
  detail?: ReactNode;
}

export function DonutCard({
  icon,
  title,
  subtitle,
  slices,
  unit = "cases",
  emptyMessage = "Nothing to show yet.",
  loading = false,
  style,
}: {
  icon: ReactNode;
  title: string;
  subtitle: string;
  slices: DonutSlice[];
  /** The word under the total in the hole. */
  unit?: string;
  emptyMessage?: string;
  loading?: boolean;
  style?: React.CSSProperties;
}) {
  const total = slices.reduce((t, s) => t + s.count, 0);
  const data = slices.filter((s) => s.count > 0);
  const [active, setActive] = useState<string | null>(null);
  const activeSlice = active ? slices.find((s) => s.key === active) ?? null : null;
  const pct = (n: number) => (total > 0 ? Math.round((n / total) * 100) : 0);

  return (
    <div className="card p-4 sm:p-5 flex flex-col" style={{ backgroundImage: "none", ...style }}>
      <div className="flex items-center gap-2 mb-2">
        <div className="icon-circle bg-brand-600 flex-shrink-0" style={{ width: 36, height: 36 }}>
          {icon}
        </div>
        <div className="min-w-0">
          <h2 className="text-base font-bold truncate" style={{ color: "#1C1C1F" }}>{title}</h2>
          <p className="text-xs" style={{ color: "#6B6D76" }}>{subtitle}</p>
        </div>
      </div>

      {loading ? (
        <p className="text-xs mt-2" style={{ color: "#94a3b8" }}>Loading…</p>
      ) : total === 0 ? (
        <p className="text-xs mt-2" style={{ color: "#6B6D76" }}>{emptyMessage}</p>
      ) : (
        <div className="flex items-center gap-3 sm:gap-4 flex-1 min-h-0">
          <div className="relative flex-shrink-0" style={{ width: 148, height: 148 }} onMouseLeave={() => setActive(null)}>
            <ResponsiveContainer width="100%" height="100%">
              <PieChart>
                <Pie
                  data={data}
                  dataKey="count"
                  nameKey="label"
                  cx="50%"
                  cy="50%"
                  innerRadius={44}
                  outerRadius={70}
                  paddingAngle={data.length > 1 ? 2.5 : 0}
                  cornerRadius={4}
                  stroke="none"
                  startAngle={90}
                  endAngle={-270}
                  isAnimationActive
                  animationDuration={700}
                  labelLine={false}
                  label={(props: PieLabelRenderProps) => {
                    const cx = Number(props.cx), cy = Number(props.cy);
                    const midAngle = Number(props.midAngle);
                    const innerRadius = Number(props.innerRadius), outerRadius = Number(props.outerRadius);
                    const percent = Number(props.percent);
                    if (![cx, cy, midAngle, innerRadius, outerRadius, percent].every(Number.isFinite)) return null;
                    if (!(percent >= MIN_LABELLED_SHARE)) return null;
                    const r = (innerRadius + outerRadius) / 2;
                    const a = (-midAngle * Math.PI) / 180;
                    return (
                      <text
                        x={cx + r * Math.cos(a)}
                        y={cy + r * Math.sin(a)}
                        fill="#fff"
                        fontSize={11}
                        fontWeight={700}
                        textAnchor="middle"
                        dominantBaseline="central"
                        style={{ pointerEvents: "none" }}
                      >
                        {Math.round(percent * 100)}%
                      </text>
                    );
                  }}
                  onMouseEnter={(_, i) => setActive(data[i]?.key ?? null)}
                >
                  {data.map((s) => (
                    <Cell
                      key={s.key}
                      fill={s.colour}
                      opacity={active && active !== s.key ? 0.35 : 1}
                      style={{ transition: "opacity 150ms ease", cursor: "default", outline: "none" }}
                    />
                  ))}
                </Pie>
              </PieChart>
            </ResponsiveContainer>
            <div className="absolute inset-0 flex flex-col items-center justify-center pointer-events-none text-center px-6">
              <span className="text-xl font-bold leading-none tabular-nums" style={{ color: "#1C1C1F" }}>
                {activeSlice ? activeSlice.count : total}
              </span>
              <span className="text-[10px] uppercase tracking-wide mt-0.5 leading-tight" style={{ color: "#6B6D76" }}>
                {activeSlice ? activeSlice.shortLabel : unit}
              </span>
            </div>
          </div>

          <ul className="flex-1 min-w-0 space-y-1">
            {slices.map((s) => {
              const isActive = active === s.key;
              return (
                <li
                  key={s.key}
                  className="min-w-0 rounded-lg px-2 py-1.5 -mx-2 transition-colors"
                  style={{ background: isActive ? `${s.colour}14` : "transparent" }}
                  onMouseEnter={() => setActive(s.key)}
                  onMouseLeave={() => setActive(null)}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="inline-flex items-center gap-1.5 min-w-0">
                      <i aria-hidden="true" style={{ width: 10, height: 10, borderRadius: 3, background: s.colour, display: "inline-block", flexShrink: 0 }} />
                      <span className="text-xs font-semibold truncate" style={{ color: "#1C1C1F" }}>{s.label}</span>
                    </span>
                    <span className="text-xs font-bold tabular-nums flex-shrink-0" style={{ color: "#1C1C1F" }} title={`${pct(s.count)}% of ${total}`}>
                      {s.count}
                    </span>
                  </div>
                  {s.detail && <div className="mt-0.5 pl-4">{s.detail}</div>}
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </div>
  );
}
