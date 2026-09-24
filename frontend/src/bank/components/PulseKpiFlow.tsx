// Command Center `components/PulseKpiFlow.jsx`, ported to TypeScript (spec §4.1)
// and made data-driven: the rows are a prop (CC hard-codes its twelve ids; the
// bank's twelve are different — plan §5.3). Class strings verbatim, quirks
// included: `animate-slide-up` is undefined in CC, so the cards do not animate
// in and the inline stagger does nothing (spec §0.1); `tone` is ignored (§0.7);
// the basis tooltip is the native `title` attribute.
import { Minus, TrendingDown, TrendingUp } from "lucide-react";
import { trendDisplay, type Kpi, type KpiRow } from "./kpi";
import { SampleDataNote } from "./SampleDataNote";

const TREND_ICON = { up: TrendingUp, down: TrendingDown, flat: Minus } as const;

export function KpiCard({ kpi, index = 0, onSelect }: { kpi: Kpi; index?: number; onSelect?: (drill: string) => void }) {
  const trend = trendDisplay(kpi);
  const TrendIcon = TREND_ICON[trend.direction];

  return (
    <button
      type="button"
      title={kpi.basis}
      onClick={() => onSelect?.(kpi.drill)}
      style={{ animationDelay: `${index * 40}ms` }}
      className="animate-slide-up flex h-full flex-col rounded-[16px] border border-border/50 bg-card px-4 py-4 text-left
                 transition-all duration-200 hover:border-border hover:shadow-sm
                 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/30"
    >
      <p className="text-[11.5px] font-medium leading-tight text-muted-foreground">{kpi.label}</p>

      <p className="mt-3 text-[23px] font-bold leading-none tracking-tight text-foreground tabular-nums">
        {kpi.value}
      </p>

      <div className={`mt-3 flex items-center gap-1.5 text-[11.5px] font-semibold ${trend.className}`}>
        <TrendIcon size={13} strokeWidth={2.5} className="shrink-0" />
        <span className="truncate">{kpi.trend}</span>
      </div>

      <p className="mt-2.5 text-[11px] font-normal leading-snug text-muted-foreground/80">{kpi.sub}</p>
    </button>
  );
}

export interface PulseKpiFlowProps {
  kpis: Kpi[];
  rows: KpiRow[];
  /** The section eyebrow. CC: "Portfolio Health". */
  title?: string;
  /** The frame label on the right, e.g. "30 days to 22 Sep 2026". */
  frameLabel?: string;
  narrative?: string;
  /** Mark the figures as invented (the gallery). */
  sampleData?: boolean;
  onSelect?: (drill: string) => void;
}

export function PulseKpiFlow({ kpis, rows, title = "Portfolio Health", frameLabel, narrative, sampleData = false, onSelect }: PulseKpiFlowProps) {
  if (!kpis.length) return null;
  const byId = new Map(kpis.map((k) => [k.id, k]));
  // Card index runs across both rows, as CC's `cardIndex++` does.
  const offsets = rows.map((_, r) => rows.slice(0, r).reduce((sum, row) => sum + row.kpis.filter((id) => byId.has(id)).length, 0));

  return (
    <section className="mb-10">
      <div className="mb-5 flex items-baseline justify-between gap-4 px-1">
        <h2 className="text-[11px] font-bold uppercase tracking-widest text-muted-foreground">{title}</h2>
        <div className="flex items-baseline gap-3">
          {sampleData && <SampleDataNote />}
          {frameLabel && <span className="text-[11px] font-medium text-muted-foreground">{frameLabel}</span>}
        </div>
      </div>

      <div className="space-y-5">
        {rows.map((row, r) => (
          <div key={row.id}>
            <p className="mb-2.5 px-1 text-[11px] font-medium text-muted-foreground/70">{row.caption}</p>
            <div className="grid grid-cols-2 gap-3.5 sm:grid-cols-3 xl:grid-cols-6">
              {row.kpis
                .filter((id) => byId.has(id))
                .map((id, i) => (
                  <KpiCard key={id} kpi={byId.get(id)!} index={offsets[r] + i} onSelect={onSelect} />
                ))}
            </div>
          </div>
        ))}
      </div>

      {narrative && (
        <p className="mt-6 border-l-2 border-border pl-4 text-[13px] font-normal leading-relaxed text-muted-foreground">
          {narrative}
        </p>
      )}
    </section>
  );
}
