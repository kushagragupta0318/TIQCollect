// Command Center `components/DrillPanel.jsx`, ported to TypeScript (spec §4.3)
// and made data-driven: CC fetches the slice itself; here the caller passes
// `data` (null while loading) so the panel does not depend on an API that the
// bank's drill endpoint (task C05) has not built yet. Class strings verbatim.
// Two changes, both structural: the portal target is the `.bank-root` portal
// container (spec §7.3), and the splits are a list, so the bank's extra drill
// dimensions (agency, region, agent — plan §5.4) need no new markup.
import { useId, useRef, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { Loader2, X } from "lucide-react";
import { BRAND, DPD_COLORS } from "../theme/colors";
import { cr, rs } from "../theme/format";
import { getBankPortalRoot } from "../lib/portal";
import { useModalFocus } from "../lib/useModalFocus";
import { BucketChip } from "./analytics";
import { SampleDataNote } from "./SampleDataNote";
import { splitBarWidth } from "./visualMath";

export interface DrillSplitItem {
  label: string;
  sharePct: number;
  /** In crores. */
  exposureCr: number;
  accounts: number;
}

export interface DrillSplit {
  title: string;
  items: DrillSplitItem[];
  /** Colour the bars by DPD bucket (the "By DPD bucket" split). */
  bucketColors?: boolean;
}

export interface DrillAccountRow {
  accountId: string;
  bucket: string;
  /** Rupees. */
  exposure: number;
  emi: number;
  paid: number;
  keepRatePct: number;
}

export interface DrillData {
  title: string;
  subtitle?: string;
  accounts: number;
  sharePct: number;
  /** In crores. */
  exposureCr: number;
  /** Eight stat tiles, pre-formatted. */
  stats: { label: string; value: string }[];
  splits: DrillSplit[];
  rows: DrillAccountRow[];
}

export interface DrillPanelProps {
  /** Shown in the header until `data` arrives (CC shows the drill key). */
  drillKey: string;
  data: DrillData | null;
  error?: boolean;
  /** Mark the slice as invented figures (the gallery). The drawer portals out of the page, so it needs its own marker. */
  sampleData?: boolean;
  onClose: () => void;
}

function Bar({ pct, color }: { pct: number; color?: string }) {
  return (
    <div className="h-1.5 bg-muted rounded-full overflow-hidden w-full">
      <div
        className="h-full rounded-full transition-all duration-500"
        style={{ width: `${splitBarWidth(pct)}%`, background: color || BRAND.primary }}
      />
    </div>
  );
}

function SplitList({ split }: { split: DrillSplit }) {
  if (!split.items.length) return null;
  return (
    <div>
      <p className="text-[11px] font-medium text-muted-foreground mb-3">{split.title}</p>
      <div className="space-y-2.5">
        {split.items.map((s) => (
          <div key={s.label} className="flex items-center gap-3">
            <span className="text-[11px] font-bold text-foreground w-24 shrink-0 truncate">{s.label}</span>
            <div className="flex-1">
              <Bar pct={s.sharePct} color={split.bucketColors ? DPD_COLORS[s.label] : undefined} />
            </div>
            <span className="text-[10px] font-bold text-muted-foreground w-24 text-right shrink-0">
              {cr(s.exposureCr)} · {s.accounts.toLocaleString()}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

function Centered({ children }: { children: ReactNode }) {
  return <p className="text-xs text-muted-foreground py-16 text-center font-medium">{children}</p>;
}

export function DrillPanel({ drillKey, data, error = false, sampleData = false, onClose }: DrillPanelProps) {
  // Focus moves in, Tab stays in, Escape closes, focus returns (lib/useModalFocus.ts).
  const sheetRef = useRef<HTMLDivElement>(null);
  useModalFocus(sheetRef, onClose);
  const titleId = useId();

  return createPortal(
    <div
      className="fixed inset-0 z-[9998] flex justify-end animate-fade-in"
      style={{ backgroundColor: "rgba(0,0,0,0.28)", backdropFilter: "blur(2px)" }}
      onClick={onClose}
    >
      <div
        ref={sheetRef}
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="w-full max-w-[560px] h-full bg-background border-l border-border shadow-menu flex flex-col animate-slide-in overflow-hidden"
      >
        <div className="px-6 py-5 border-b border-border/60 bg-card flex items-start justify-between gap-4 shrink-0">
          <div className="min-w-0">
            <p className="text-[11px] font-medium text-muted-foreground">Drill-down</p>
            <h3 id={titleId} className="text-lg font-extrabold text-foreground tracking-tight truncate mt-0.5">{data?.title || drillKey}</h3>
            {data?.subtitle && <p className="text-[11px] font-semibold text-muted-foreground mt-1">{data.subtitle}</p>}
            {sampleData && (
              <div className="mt-2">
                <SampleDataNote />
              </div>
            )}
          </div>
          <button
            onClick={onClose}
            aria-label="Close drill-down"
            className="w-8 h-8 shrink-0 flex items-center justify-center rounded-full bg-muted/50 hover:bg-muted text-muted-foreground transition-colors"
          >
            <X size={16} />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-6 space-y-7">
          {!data && !error && (
            <div className="flex items-center gap-2 justify-center py-16 text-xs font-semibold text-muted-foreground">
              <Loader2 size={14} className="animate-spin" /> Resolving slice…
            </div>
          )}
          {error && <Centered>Could not load this slice.</Centered>}

          {data && data.accounts === 0 && <Centered>No accounts in this slice.</Centered>}

          {data && data.accounts > 0 && (
            <>
              <div className="grid grid-cols-2 gap-3">
                {data.stats.map((s) => (
                  <div key={s.label} className="rounded-[16px] border border-border/50 bg-card px-4 py-3.5">
                    <p className="text-[11px] font-medium text-muted-foreground">{s.label}</p>
                    <p className="mt-2 text-[18px] font-bold tracking-tight tabular-nums text-foreground">{s.value}</p>
                  </div>
                ))}
              </div>

              <div className="bg-accent/40 border border-border/50 rounded-[16px] px-4 py-3">
                <p className="text-[11px] font-semibold text-foreground">
                  This slice is <strong>{data.sharePct}%</strong> of the {cr(data.exposureCr)} it contributes to
                  total at-risk exposure, across <strong>{data.accounts.toLocaleString()}</strong> accounts.
                </p>
              </div>

              {data.splits.map((split) => (
                <SplitList key={split.title} split={split} />
              ))}

              <div>
                <p className="text-[11px] font-medium text-muted-foreground mb-3">Largest exposures in this slice</p>
                <div className="overflow-x-auto -mx-1 px-1">
                  <table className="w-full text-[11px] text-left">
                    <thead>
                      <tr className="border-b border-border/50">
                        {["Account", "Bucket", "Outstanding", "EMI", "Paid", "Keep"].map((h) => (
                          <th key={h} className="pb-2 pr-3 font-medium text-muted-foreground uppercase text-[9.5px] tracking-wide">
                            {h}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {data.rows.map((r) => (
                        <tr key={r.accountId} className="border-b border-border/25">
                          <td className="py-2 pr-3 font-mono text-[10px] font-bold text-foreground">{r.accountId}</td>
                          <td className="py-2 pr-3">
                            <BucketChip bucket={r.bucket} dense />
                          </td>
                          <td className="py-2 pr-3 font-bold text-foreground">{rs(r.exposure)}</td>
                          <td className="py-2 pr-3 text-muted-foreground">{rs(r.emi)}</td>
                          <td className="py-2 pr-3 font-semibold" style={{ color: r.paid > 0 ? BRAND.success : BRAND.muted }}>
                            {rs(r.paid)}
                          </td>
                          <td className="py-2 text-muted-foreground font-semibold">{r.keepRatePct}%</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            </>
          )}
        </div>
      </div>
    </div>,
    getBankPortalRoot(),
  );
}
