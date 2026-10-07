// Command Center's three chart shapes from PortfolioAnalytics.jsx (spec §4.2,
// §4.11), as data-driven wrappers on the ported chart theme. They use the
// analytics variant of the theme (dark 8px tooltip, circle legend) because
// that is what those surfaces render; `chartTheme.ts` holds all three variants.
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
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
  simulatorAxisTick,
  simulatorTooltipStyle,
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

/* ── Percentile range: p5-p95 as a band, p10-p90 darker inside it, p50 marked
   (RiskSimulator's own light-tooltip variant — this is CC's Monte Carlo
   page). A Band is never a point (simulatorModel.ts's own rule), so this is
   the one way a Band is charted: five STACKED segments per row — an
   invisible [0,p5] base, a light [p5,p10], a solid [p10,p50) / (p50,p90]
   split either side of a thin marker tick at p50, and a light [p90,p95] —
   never a Scatter overlay, which would need a second geometry on a
   categorical axis the other three chart shapes here don't use either. ── */

export interface PercentileRow {
  name: string;
  p5: number;
  p10: number;
  p50: number;
  p90: number;
  p95: number;
}

function percentileSegments(r: PercentileRow) {
  const span = Math.max(r.p95 - r.p5, 0);
  // The marker tick: visible but never wide enough to eat the band it sits
  // in. A degenerate band (p95 === p5, e.g. a metric with no simulated
  // spread) collapses every segment to 0 — an honest "nothing to show",
  // not a divide-by-zero.
  const eps = Math.min(span * 0.015, (r.p90 - r.p10) / 4);
  const preMarker = Math.max(r.p50 - eps, r.p10);
  const postMarker = Math.max(r.p90 - preMarker - 2 * eps, 0);
  return {
    base: r.p5,
    lowOuter: Math.max(r.p10 - r.p5, 0),
    preMarker: Math.max(preMarker - r.p10, 0),
    marker: Math.max(Math.min(2 * eps, r.p90 - preMarker), 0),
    postMarker,
    highOuter: Math.max(r.p95 - r.p90, 0),
  };
}

function PercentileTooltip({ active, payload, format }: {
  active?: boolean;
  payload?: Array<{ payload: PercentileRow }>;
  format: (v: number) => string;
}) {
  if (!active || !payload?.length) return null;
  const r = payload[0].payload;
  return (
    <div style={simulatorTooltipStyle} className="bg-card px-3 py-2">
      <p className="text-[11px] font-semibold text-foreground">{r.name}</p>
      <p className="text-[11px] text-muted-foreground">p5–p95: {format(r.p5)} – {format(r.p95)}</p>
      <p className="text-[11px] text-muted-foreground">p10–p90: {format(r.p10)} – {format(r.p90)}</p>
      <p className="text-[11px] font-semibold text-foreground">Median: {format(r.p50)}</p>
    </div>
  );
}

export function PercentileRangeChart({
  rows,
  color = BRAND.primary,
  format = (v) => String(v),
  /** 0-anchored for a share-of-book metric; tight-fit for everything else
   *  (a cash band's interesting part is the comparison, not the distance
   *  from an uninformative zero far off to one side). */
  domainFrom0 = false,
  height,
}: {
  rows: PercentileRow[];
  color?: string;
  format?: (v: number) => string;
  domainFrom0?: boolean;
  height?: number;
}) {
  const data = rows.map((r) => ({ ...r, ...percentileSegments(r) }));
  const domain: [number, number] = domainFrom0
    ? [0, Math.max(...rows.map((r) => r.p95)) * 1.05]
    : [Math.min(...rows.map((r) => r.p5)) * 0.95, Math.max(...rows.map((r) => r.p95)) * 1.05];
  const rowHeight = 34;
  return (
    <ResponsiveContainer width="100%" height={height ?? Math.max(120, rows.length * rowHeight)}>
      <BarChart data={data} layout="vertical" margin={chartMargin} barCategoryGap={8}>
        <CartesianGrid {...gridProps} horizontal={false} />
        <XAxis type="number" domain={domain} tick={simulatorAxisTick} axisLine={false} tickLine={false} tickFormatter={format} />
        <YAxis type="category" dataKey="name" tick={simulatorAxisTick} axisLine={false} tickLine={false} width={150} />
        <Tooltip content={<PercentileTooltip format={format} />} cursor={false} />
        <Bar dataKey="base" stackId="p" fill="transparent" isAnimationActive={false} />
        <Bar dataKey="lowOuter" stackId="p" fill={color} fillOpacity={0.25} isAnimationActive={false} />
        <Bar dataKey="preMarker" stackId="p" fill={color} fillOpacity={0.65} isAnimationActive={false} />
        <Bar dataKey="marker" stackId="p" fill={BRAND.ink} isAnimationActive={false} />
        <Bar dataKey="postMarker" stackId="p" fill={color} fillOpacity={0.65} isAnimationActive={false} />
        <Bar dataKey="highOuter" stackId="p" fill={color} fillOpacity={0.25} radius={[0, 3, 3, 0]} isAnimationActive={false} />
      </BarChart>
    </ResponsiveContainer>
  );
}

/* ── Two bars + a line on the right axis (IFRS-9: EAD and ECL are both INR
   and directly comparable; Coverage is a fraction and needs its own axis).
   BarLineChart above takes exactly one bar series, which IFRS-9 doesn't fit
   — rather than widen that shared shape's props for the one caller that
   needs two bars, this is its own small sibling. ── */

export function GroupedBarLineChart({
  data,
  xKey,
  bars,
  line,
  height = 230,
}: {
  data: Row[];
  xKey: string;
  bars: ChartSeries[];
  line: ChartSeries;
  height?: number;
}) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <ComposedChart data={data} margin={chartMargin}>
        <CartesianGrid {...gridProps} />
        <XAxis dataKey={xKey} tick={simulatorAxisTick} axisLine={false} tickLine={false} />
        <YAxis yAxisId="l" tick={simulatorAxisTick} axisLine={false} tickLine={false} />
        <YAxis yAxisId="r" orientation="right" tick={simulatorAxisTick} axisLine={false} tickLine={false} />
        <Tooltip contentStyle={simulatorTooltipStyle} />
        <Legend iconSize={8} iconType="circle" wrapperStyle={analyticsLegendStyle} />
        {bars.map((b) => (
          <Bar key={b.key} yAxisId="l" dataKey={b.key} name={b.name} fill={b.color} radius={[3, 3, 0, 0]} maxBarSize={36} />
        ))}
        <Line yAxisId="r" type="monotone" dataKey={line.key} name={line.name} stroke={line.color} strokeWidth={2} dot={{ r: 3 }} />
      </ComposedChart>
    </ResponsiveContainer>
  );
}
