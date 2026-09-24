// Command Center `components/DecisionAlerts.jsx`, ported to TypeScript (spec §4.4)
// and made data-driven: CC fetches its own alerts; here they are a prop
// (null = still scanning), so task C06's rules plug straight in. Class strings
// verbatim, including the inert `animate-slide-up` (spec §0.1).
import { useState } from "react";
import { ArrowRight, ChevronDown, Loader2 } from "lucide-react";
import { DPD_COLORS } from "../theme/colors";
import { n, rs } from "../theme/format";
import { BucketChip } from "./analytics";
import { severityStyle, type AlertSeverity } from "./alertSeverity";
import { SampleDataNote } from "./SampleDataNote";

export interface AlertAccountRow {
  accountId: string;
  product: string;
  bucket: string;
  state: string;
  /** Rupees. */
  exposure: number;
  monthsDelinquent: number;
  keepRatePct: number;
  [extra: string]: string | number;
}

export interface DecisionAlert {
  id: string;
  severity: AlertSeverity;
  title: string;
  summary: string;
  metrics: { label: string; value: string }[];
  breakdown?: { label: string; value: number }[];
  rows?: AlertAccountRow[];
  /** One extra column on the cohort table; `collateral` is rendered as rupees. */
  rowExtra?: { key: string; label: string };
  actions: { label: string; target: string }[];
  /** How the alert is derived. */
  basis: string;
}

function AlertBody({ alert, onAction }: { alert: DecisionAlert; onAction?: (target: string) => void }) {
  const maxBreak = Math.max(...(alert.breakdown || []).map((b) => b.value), 1);
  const accent = severityStyle(alert.severity).accent;
  return (
    <div className="p-5 space-y-5">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {alert.metrics.map((m) => (
          <div key={m.label} className="rounded-[16px] border border-border/50 bg-card px-4 py-3.5">
            <p className="truncate text-[11px] font-medium text-muted-foreground">{m.label}</p>
            <p className="mt-2 text-[18px] font-bold tracking-tight tabular-nums text-foreground">{m.value}</p>
          </div>
        ))}
      </div>

      {alert.breakdown && alert.breakdown.length > 0 && (
        <div>
          <p className="text-[11px] font-medium text-muted-foreground mb-3">Distribution</p>
          <div className="space-y-2">
            {alert.breakdown.map((b) => (
              <div key={b.label} className="flex items-center gap-3">
                <span className="text-[10.5px] font-bold text-muted-foreground w-24 shrink-0 truncate">{b.label}</span>
                <div className="flex-1 h-2 bg-muted rounded-full overflow-hidden">
                  <div
                    className="h-full rounded-full transition-all duration-500"
                    style={{ width: `${(b.value / maxBreak) * 100}%`, background: DPD_COLORS[b.label] || accent }}
                  />
                </div>
                <span className="text-[10.5px] font-bold text-foreground w-14 text-right shrink-0">{n(b.value)}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {alert.rows && alert.rows.length > 0 && (
        <div>
          <p className="text-[11px] font-medium text-muted-foreground mb-3">Largest exposures in this cohort</p>
          <div className="overflow-x-auto">
            <table className="w-full text-[11px] text-left">
              <thead>
                <tr className="border-b border-border/50">
                  {["Account", "Product", "Bucket", "State", "Outstanding", "Months", "PTP keep", ...(alert.rowExtra ? [alert.rowExtra.label] : [])].map((h) => (
                    <th key={h} className="pb-2 pr-3 font-medium text-muted-foreground uppercase text-[9.5px] tracking-wide">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {alert.rows.map((r) => (
                  <tr key={r.accountId} className="border-b border-border/25">
                    <td className="py-2 pr-3 font-mono text-[10px] font-bold text-foreground">{r.accountId}</td>
                    <td className="py-2 pr-3 text-muted-foreground">{r.product}</td>
                    <td className="py-2 pr-3">
                      <BucketChip bucket={r.bucket} dense />
                    </td>
                    <td className="py-2 pr-3 text-muted-foreground">{r.state}</td>
                    <td className="py-2 pr-3 font-bold text-foreground">{rs(r.exposure)}</td>
                    <td className="py-2 pr-3 text-muted-foreground">{r.monthsDelinquent}</td>
                    <td className="py-2 pr-3 text-muted-foreground">{r.keepRatePct}%</td>
                    {alert.rowExtra && (
                      <td className="py-2 font-semibold text-foreground">
                        {alert.rowExtra.key === "collateral" ? rs(r[alert.rowExtra.key]) : r[alert.rowExtra.key]}
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <div className="flex items-center justify-between gap-3 flex-wrap pt-1">
        <div className="flex items-center gap-2 flex-wrap">
          {alert.actions.map((a) => (
            <button
              key={a.target}
              onClick={() => onAction?.(a.target)}
              className="flex min-h-9 items-center gap-1.5 text-[12px] font-semibold px-3 rounded-control
                         bg-card hover:bg-accent text-primary border border-primary transition-colors group"
            >
              {a.label}
              <ArrowRight size={12} className="group-hover:translate-x-0.5 transition-transform" />
            </button>
          ))}
        </div>
        <p className="text-[9.5px] font-medium text-muted-foreground max-w-md">{alert.basis}</p>
      </div>
    </div>
  );
}

export interface DecisionAlertsProps {
  /** null while the rules are still running. */
  alerts: DecisionAlert[] | null;
  onAction?: (target: string) => void;
  /**
   * CC's is "AI Alerts". The bank's alerts are SQL rules (task C06), and this
   * repo does not let a rule present itself as AI (CLAUDE.md), so the default
   * is plain "Alerts" — a recorded deviation (UI spec §9).
   */
  title?: string;
  /** Provenance line. No default: CC's "Derived live from the loan book…" is a claim only the caller can make. */
  subtitle?: string;
  /** Mark the cards as invented figures (the gallery). */
  sampleData?: boolean;
  /** Open one card initially (screenshots, deep links). */
  defaultOpenId?: string | null;
}

export function DecisionAlerts({
  alerts,
  onAction,
  title = "Alerts",
  subtitle,
  sampleData = false,
  defaultOpenId = null,
}: DecisionAlertsProps) {
  const [openId, setOpenId] = useState<string | null>(defaultOpenId);

  if (!alerts) {
    return (
      <section className="mb-8">
        <div className="flex items-center gap-2 justify-center py-14 text-xs font-semibold text-muted-foreground">
          <Loader2 size={14} className="animate-spin" /> Scanning the book…
        </div>
      </section>
    );
  }

  return (
    <section>
      <div className="flex items-center justify-between mb-4 px-1 gap-3 flex-wrap">
        <div>
          <h2 className="text-[11px] font-bold text-muted-foreground uppercase tracking-widest">{title}</h2>
          {subtitle && <p className="text-[11px] text-muted-foreground mt-1">{subtitle}</p>}
        </div>
        <div className="flex items-center gap-2">
          {sampleData && <SampleDataNote />}
          <span className="text-[10px] text-muted-foreground font-semibold bg-muted px-2.5 py-1 rounded-full border border-border/50">
            {alerts.length} active
          </span>
        </div>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-3">
        {alerts.map((a, i) => {
          const cfg = severityStyle(a.severity);
          const isOpen = openId === a.id;
          return (
            <div
              key={a.id}
              style={{ animationDelay: `${i * 35}ms` }}
              className={`animate-slide-up rounded-card border border-border overflow-hidden bg-card shadow-resting ${isOpen ? "xl:col-span-2" : ""}`}
            >
              <button
                onClick={() => setOpenId(isOpen ? null : a.id)}
                aria-expanded={isOpen}
                className={`w-full min-h-[92px] px-5 py-4 flex items-center justify-between gap-4 text-left focus:outline-none
                            focus-visible:ring-2 focus-visible:ring-primary/20 ${isOpen ? cfg.bg : "hover:bg-muted"}`}
              >
                <div className="flex items-center gap-3.5 flex-1 min-w-0">
                  <span className={`flex size-10 shrink-0 items-center justify-center rounded-inner ${cfg.soft}`}>
                    <cfg.Icon className={`size-[18px] ${cfg.iconColor}`} />
                  </span>
                  <div className="min-w-0">
                    <p className="text-[14px] font-semibold leading-snug text-foreground">{a.title}</p>
                    <p className="text-[11.5px] font-medium text-muted-foreground leading-snug mt-1">{a.summary}</p>
                  </div>
                </div>
                <ChevronDown
                  size={15}
                  className={`text-muted-foreground shrink-0 transition-transform duration-300 ${isOpen ? "rotate-180" : ""}`}
                />
              </button>
              {isOpen && (
                <div className="border-t border-border bg-card animate-fade-in">
                  <AlertBody alert={a} onAction={onAction} />
                </div>
              )}
            </div>
          );
        })}
      </div>
    </section>
  );
}
