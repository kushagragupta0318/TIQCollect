// Command Center `components/PortfolioAnalytics.jsx` — its shared primitives,
// section header and segmented tab bar (spec §4.2), ported to TypeScript and
// made data-driven. Class strings verbatim.
import type { ReactNode } from "react";
import { Loader2 } from "lucide-react";
import { BRAND, DPD_COLORS } from "../theme/colors";
import { bar100Width, chipBackground } from "./visualMath";

/* ── Section header + segmented tab bar ─────────────────────────────────── */

export function AnalyticsSectionHeader({ title, subtitle }: { title: string; subtitle?: ReactNode }) {
  return (
    <div>
      <h3 className="text-[17px] font-bold tracking-tight text-foreground">{title}</h3>
      {subtitle && <p className="mt-1 text-[12px] text-muted-foreground">{subtitle}</p>}
    </div>
  );
}

export interface AnalyticsTab<T extends string = string> {
  id: T;
  label: string;
}

/**
 * The segmented pill bar (PortfolioAnalytics.jsx:821-832). `max-w-3xl` and
 * `overflow-x-auto`, so the bank's eight tabs scroll rather than wrap.
 */
export function AnalyticsTabBar<T extends string>({
  tabs,
  active,
  onChange,
}: {
  tabs: readonly AnalyticsTab<T>[];
  active: T;
  onChange: (id: T) => void;
}) {
  return (
    <div className="flex max-w-3xl overflow-x-auto rounded-2xl border border-border/40 bg-muted/25 p-1" role="tablist">
      {tabs.map((t) => (
        <button
          key={t.id}
          role="tab"
          aria-selected={active === t.id}
          onClick={() => onChange(t.id)}
          className={`flex-1 whitespace-nowrap rounded-xl px-3.5 py-2 text-center text-[11.5px] font-semibold transition-all duration-200 ${
            active === t.id
              ? "border border-border/40 bg-card text-foreground shadow-[0_1px_3px_rgba(0,0,0,0.04)]"
              : "text-muted-foreground hover:text-foreground"
          }`}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}

/* ── Shared primitives (PortfolioAnalytics.jsx:47-92) ───────────────────── */

export function AnalyticsLoading({ label = "Loading portfolio data…" }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 justify-center py-20 text-xs font-semibold text-muted-foreground">
      <Loader2 size={14} className="animate-spin" /> {label}
    </div>
  );
}

export function AnalyticsError({ children = "This panel could not load." }: { children?: ReactNode }) {
  return <p className="py-16 text-center text-xs font-medium text-muted-foreground">{children}</p>;
}

export function Headline({ text }: { text: ReactNode }) {
  return (
    <p className="border-l-2 border-border pl-4 text-[13px] font-normal leading-relaxed text-muted-foreground">
      {text}
    </p>
  );
}

export function Panel({
  title,
  hint,
  children,
  className = "",
}: {
  title: ReactNode;
  hint?: ReactNode;
  children?: ReactNode;
  className?: string;
}) {
  return (
    <div className={`rounded-card border border-border/60 bg-card p-6 shadow-[0_1px_2px_rgba(17,24,39,0.03)] ${className}`}>
      <div className="mb-5 flex flex-wrap items-baseline justify-between gap-3">
        <p className="text-[12.5px] font-semibold tracking-tight text-foreground">{title}</p>
        {hint && <span className="text-[11px] text-muted-foreground">{hint}</span>}
      </div>
      {children}
    </div>
  );
}

export function Tile({ label, value, sub, color }: { label: ReactNode; value: ReactNode; sub?: ReactNode; color?: string }) {
  return (
    <div className="rounded-[16px] border border-border/50 bg-card px-5 py-4">
      <p className="truncate text-[11.5px] font-medium leading-tight text-muted-foreground">{label}</p>
      <p className="mt-2.5 text-[22px] font-bold leading-none tracking-tight tabular-nums" style={color ? { color } : undefined}>
        {value}
      </p>
      {sub && <p className="mt-2 truncate text-[11px] text-muted-foreground/80">{sub}</p>}
    </div>
  );
}

export function Bar100({ pct, color, height = 6 }: { pct: number; color?: string; height?: number }) {
  return (
    <div className="bg-muted rounded-full overflow-hidden w-full" style={{ height }}>
      <div
        className="h-full rounded-full transition-all duration-500"
        style={{ width: `${bar100Width(pct)}%`, background: color || BRAND.primary }}
      />
    </div>
  );
}

// A row that opens the drill drawer. Kept as one component so every clickable
// slice on every tab has the same affordance and hover treatment.
export function DrillRow({ onDrill, children, className = "" }: { onDrill?: () => void; children: ReactNode; className?: string }) {
  return (
    <button
      type="button"
      onClick={onDrill}
      className={`w-full text-left group transition-colors hover:bg-accent/50 rounded-inner
                focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/30 ${className}`}
    >
      {children}
    </button>
  );
}

/* ── Cell helpers shared by the analytics tables ─────────────────────────── */

/**
 * A DPD bucket chip (spec §1.4). `dense` is the drill / alert-table size
 * (`text-[9px] font-bold px-1.5`); the default is the ladder's. An unmapped
 * bucket falls back to slate — CC would render `color: undefined` and
 * background "undefined15", i.e. no chip at all (UI spec §9).
 */
export function BucketChip({ bucket, dense = false }: { bucket: string; dense?: boolean }) {
  const color = DPD_COLORS[bucket] ?? BRAND.slate;
  return (
    <span
      className={dense ? "text-[9px] font-bold px-1.5 py-0.5 rounded" : "text-[10px] font-semibold px-2 py-0.5 rounded"}
      style={{ color, background: chipBackground(color) }}
    >
      {bucket}
    </span>
  );
}

/** A Bar100 with its percentage beside it — the "Share" column pattern. */
export function ShareBar({ pct, color, scale = 1 }: { pct: number; color?: string; scale?: number }) {
  return (
    <div className="flex items-center gap-2">
      <Bar100 pct={pct * scale} color={color} />
      <span className="text-[10px] font-bold text-muted-foreground w-9 shrink-0">{pct}%</span>
    </div>
  );
}
