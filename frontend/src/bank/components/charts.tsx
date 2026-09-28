// Command Center's three chart shapes from PortfolioAnalytics.jsx (spec §4.2,
// §4.11), as data-driven wrappers on the ported chart theme. They use the
// analytics variant of the theme (dark 8px tooltip, circle legend) because
// that is what those surfaces render; `chartTheme.ts` holds all three variants.
import {
  Area,
  AreaChart,
  Bar,
  CartesianGrid,
  Cell,
  ComposedChart,
  Legend,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { useId } from "react";
import {
  analyticsLegendStyle,
  analyticsTick,
  analyticsTooltipStyle,
  BRAND,
  chartMargin,
  gridProps,
} from "../theme/chartTheme";

export interface ChartSeries {
  key: string;
  name: string;
  color: string;
}

type Row = Record<string, string | number>;

/* ── Area + lines: the 12-month delinquent trajectory (:372-389) ─────────── */

export function TrendAreaChart({
  data,
  xKey,
  area,
  lines = [],
  height = 230,
}: {
  data: Row[];
  xKey: string;
  area: ChartSeries;
  lines?: ChartSeries[];
  height?: number;
}) {
  const fillId = `bank-area-${useId().replace(/:/g, "")}`;
  return (
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart data={data} margin={chartMargin}>
        <defs>
          <linearGradient id={fillId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor={area.color} stopOpacity={0.28} />
            <stop offset="95%" stopColor={area.color} stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid {...gridProps} />
        <XAxis dataKey={xKey} tick={analyticsTick} axisLine={false} tickLine={false} />
        <YAxis tick={analyticsTick} axisLine={false} tickLine={false} />
        <Tooltip contentStyle={analyticsTooltipStyle} />
        <Legend iconSize={8} iconType="circle" wrapperStyle={analyticsLegendStyle} />
        <Area type="monotone" dataKey={area.key} name={area.name} stroke={area.color} strokeWidth={2} fill={`url(#${fillId})`} />
        {lines.map((l) => (
          <Line key={l.key} type="monotone" dataKey={l.key} name={l.name} stroke={l.color} strokeWidth={2} dot={false} />
        ))}
      </AreaChart>
    </ResponsiveContainer>
  );
}

/* ── Recovery pace against target (:425-446) ────────────────────────────── */

export function RecoveryPaceChart({
  data,
  xKey = "date",
  cumulativeKey = "cumulative",
  targetKey = "targetPace",
  dailyKey = "collected",
  unit = "₹",
  unitSuffix = " Cr",
  height = 250,
}: {
  data: Row[];
  xKey?: string;
  cumulativeKey?: string;
  targetKey?: string;
  dailyKey?: string;
  unit?: string;
  unitSuffix?: string;
  height?: number;
}) {
  const fillId = `bank-pace-${useId().replace(/:/g, "")}`;
  return (
    <ResponsiveContainer width="100%" height={height}>
      <ComposedChart data={data} margin={chartMargin}>
        <defs>
          <linearGradient id={fillId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor={BRAND.success} stopOpacity={0.25} />
            <stop offset="95%" stopColor={BRAND.success} stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid {...gridProps} />
        <XAxis dataKey={xKey} tick={analyticsTick} axisLine={false} tickLine={false} interval={Math.max(1, Math.floor(data.length / 8))} />
        <YAxis tick={analyticsTick} axisLine={false} tickLine={false} />
        <Tooltip contentStyle={analyticsTooltipStyle} formatter={(v, k) => [`${unit}${v}${unitSuffix}`, k]} />
        <Legend iconSize={8} iconType="circle" wrapperStyle={analyticsLegendStyle} />
        <Area type="monotone" dataKey={cumulativeKey} name="Recovered (cumulative)" stroke={BRAND.success} strokeWidth={2} fill={`url(#${fillId})`} />
        <Line type="monotone" dataKey={targetKey} name="Target pace" stroke={BRAND.muted} strokeWidth={1.5} strokeDasharray="5 5" dot={false} />
        <Bar dataKey={dailyKey} name="Daily" fill={`${BRAND.primary}55`} radius={[2, 2, 0, 0]} maxBarSize={9} />
      </ComposedChart>
    </ResponsiveContainer>
  );
}

/* ── Bars per category + a line on the right axis (:560-575) ─────────────── */

export function BarLineChart({
  data,
  xKey,
  bar,
  line,
  barColor,
  height = 230,
}: {
  data: Row[];
  xKey: string;
  bar: ChartSeries;
  line: ChartSeries;
  /** Per-category bar colour (CC colours cost bars by DPD bucket). */
  barColor?: (row: Row) => string;
  height?: number;
}) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <ComposedChart data={data} margin={chartMargin}>
        <CartesianGrid {...gridProps} />
        <XAxis dataKey={xKey} tick={analyticsTick} axisLine={false} tickLine={false} />
        <YAxis yAxisId="l" tick={analyticsTick} axisLine={false} tickLine={false} />
        <YAxis yAxisId="r" orientation="right" tick={analyticsTick} axisLine={false} tickLine={false} />
        <Tooltip contentStyle={analyticsTooltipStyle} />
        <Legend iconSize={8} iconType="circle" wrapperStyle={analyticsLegendStyle} />
        <Bar yAxisId="l" dataKey={bar.key} name={bar.name} fill={bar.color} radius={[3, 3, 0, 0]} maxBarSize={42}>
          {barColor && data.map((row) => <Cell key={String(row[xKey])} fill={barColor(row)} />)}
        </Bar>
        <Line yAxisId="r" type="monotone" dataKey={line.key} name={line.name} stroke={line.color} strokeWidth={2} dot={{ r: 3 }} />
      </ComposedChart>
    </ResponsiveContainer>
  );
}
