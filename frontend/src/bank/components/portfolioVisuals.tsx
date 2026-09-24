// Command Center's portfolio visuals (PortfolioAnalytics.jsx, spec §4.2):
// the exposure funnel, the product × bucket heat grid, the transition matrix
// and the cure-against-roll bars. Class strings verbatim; the arithmetic lives
// in visualMath.ts. Every slice is clickable when `onDrill` is given.
import { BRAND, DPD_COLORS } from "../theme/colors";
import { cr, cr1, n } from "../theme/format";
import { funnelColor, funnelWidth, heatCellBackground, transitionCellStyle } from "./visualMath";

/* ── Funnel (:108-133) ──────────────────────────────────────────────────── */

export interface FunnelStage {
  stage: string;
  /** In crores. */
  exposureCr: number;
  accounts: number;
}

export function ExposureFunnel({ stages }: { stages: FunnelStage[] }) {
  const max = stages[0]?.exposureCr || 1;
  return (
    <div className="space-y-2.5">
      {stages.map((f, i) => {
        const color = funnelColor(i);
        return (
          <div key={f.stage} className="flex items-center gap-4">
            <span className="text-[11px] font-bold text-foreground w-40 shrink-0 truncate">{f.stage}</span>
            <div className="flex-1 min-w-0">
              <div className="h-7 bg-muted rounded-inner overflow-hidden relative">
                <div
                  className="h-full rounded-inner transition-all duration-700 flex items-center px-3"
                  style={{ width: `${funnelWidth(f.exposureCr, max)}%`, background: `${color}22`, borderLeft: `3px solid ${color}` }}
                >
                  <span className="text-[11px] font-semibold whitespace-nowrap" style={{ color }}>
                    {cr1(f.exposureCr)}
                  </span>
                </div>
              </div>
            </div>
            <span className="text-[10.5px] font-semibold text-muted-foreground w-28 text-right shrink-0">
              {n(f.accounts)} accounts
            </span>
          </div>
        );
      })}
    </div>
  );
}

/* ── Heat grid, product × bucket (:180-223) ─────────────────────────────── */

export interface HeatGridCell {
  exposureCr: number;
  accounts: number;
}

export interface HeatGridRow {
  /** Row label — a product in CC; an agency or region works the same. */
  label: string;
  delqRatePct: number;
  delqExposureCr: number;
  cells: Record<string, HeatGridCell>;
}

export interface HeatGridProps {
  rows: HeatGridRow[];
  /** Column order — DPD buckets, coloured by DPD_COLORS. */
  buckets: string[];
  rowHeader?: string;
  onDrill?: (row: HeatGridRow) => void;
}

export function HeatGrid({ rows, buckets, rowHeader = "Product", onDrill }: HeatGridProps) {
  return (
    <>
      <div className="overflow-x-auto">
        <table className="w-full text-[11px] min-w-[600px]">
          <thead>
            <tr className="border-b border-border">
              <th className="pb-2.5 pr-3 text-left font-medium text-muted-foreground uppercase text-[9.5px] tracking-wide">{rowHeader}</th>
              <th className="pb-2.5 pr-3 text-right font-medium text-muted-foreground uppercase text-[9.5px] tracking-wide">Delinq. rate</th>
              {buckets.map((b) => (
                <th key={b} className="pb-2.5 px-1 text-center font-bold uppercase text-[9px] tracking-wider" style={{ color: DPD_COLORS[b] }}>
                  {b}
                </th>
              ))}
              <th className="pb-2.5 pl-3 text-right font-medium text-muted-foreground uppercase text-[9.5px] tracking-wide">At risk</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const max = Math.max(...buckets.map((b) => row.cells[b]?.exposureCr ?? 0), 0.01);
              return (
                <tr
                  key={row.label}
                  onClick={onDrill ? () => onDrill(row) : undefined}
                  className="border-b border-border/30 cursor-pointer hover:bg-accent/50 transition-colors"
                >
                  <td className="py-2.5 pr-3 font-bold text-foreground">{row.label}</td>
                  <td className="py-2.5 pr-3 text-right font-semibold text-muted-foreground">{row.delqRatePct}%</td>
                  {buckets.map((b) => {
                    const c = row.cells[b] ?? { exposureCr: 0, accounts: 0 };
                    return (
                      <td key={b} className="py-2.5 px-1 text-center">
                        <div className="rounded-inner py-1.5" style={{ background: heatCellBackground(DPD_COLORS[b] ?? BRAND.slate, c.exposureCr / max) }}>
                          <span className="text-[10px] font-semibold text-foreground">{c.exposureCr}</span>
                          <span className="block text-[8.5px] font-semibold text-muted-foreground">{n(c.accounts)}</span>
                        </div>
                      </td>
                    );
                  })}
                  <td className="py-2.5 pl-3 text-right font-semibold text-foreground">{cr(row.delqExposureCr)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="text-[9.5px] text-muted-foreground font-medium mt-3">
        Cell values in ₹ Cr outstanding; smaller figure is the account count.
      </p>
    </>
  );
}

/* ── Transition matrix (:275-322) ───────────────────────────────────────── */

export interface TransitionMatrixProps {
  /** Bucket labels, in order; rows are "from", columns "to". */
  labels: string[];
  /** Percentages; each row sums to 100. */
  matrix: number[][];
  /** Observed transitions behind the matrix, for the footnote. */
  observed?: number;
  onDrill?: (from: string, to: string) => void;
}

export function TransitionMatrix({ labels, matrix, observed, onDrill }: TransitionMatrixProps) {
  return (
    <>
      <div className="overflow-x-auto">
        <table className="w-full text-[10.5px] min-w-[520px]">
          <thead>
            <tr>
              <th className="pb-2 pr-2 text-left font-medium text-muted-foreground uppercase text-[9.5px] tracking-wide">From \ To</th>
              {labels.map((l) => (
                <th key={l} className="pb-2 px-1 text-center font-bold uppercase text-[9px] tracking-wider" style={{ color: DPD_COLORS[l] }}>
                  {l}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {matrix.map((row, i) => (
              <tr key={labels[i]}>
                <td className="py-1 pr-2 font-semibold text-[10px]" style={{ color: DPD_COLORS[labels[i]] }}>
                  {labels[i]}
                </td>
                {row.map((v, j) => {
                  const s = transitionCellStyle(v, i, j);
                  return (
                    <td key={labels[j]} className="p-0.5">
                      <button
                        type="button"
                        onClick={() => v > 0 && onDrill?.(labels[i], labels[j])}
                        disabled={v === 0}
                        className="w-full rounded-inner py-2 text-center transition-transform hover:scale-[1.06] disabled:cursor-default disabled:hover:scale-100"
                        style={{ background: s.background, boxShadow: s.boxShadow }}
                      >
                        <span className="text-[10px] font-semibold" style={{ color: s.textColor }}>{v}</span>
                      </button>
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-[9.5px] text-muted-foreground font-medium mt-3 leading-relaxed">
        Probability (%) of moving between DPD states in one month
        {observed != null && <> ({n(observed)} observed transitions)</>}. Rows sum to 100. Green is improvement,
        red is deterioration, the outlined diagonal is persistence.
      </p>
    </>
  );
}

/* ── Cure against roll, by bucket (:328-345) ────────────────────────────── */

export interface CureRollFlow {
  bucket: string;
  exposureCr: number;
  accounts: number;
  curePct: number;
  improvePct: number;
  holdPct: number;
  rollPct: number;
  curableCr: number;
  atRiskCr: number;
}

export function CureRollBars({ flows }: { flows: CureRollFlow[] }) {
  return (
    <div className="space-y-4">
      {flows.map((f) => (
        <div key={f.bucket}>
          <div className="flex items-baseline justify-between mb-1.5">
            <span className="text-[11px] font-semibold" style={{ color: DPD_COLORS[f.bucket] }}>{f.bucket}</span>
            <span className="text-[10px] font-semibold text-muted-foreground">{cr(f.exposureCr)} · {n(f.accounts)} a/c</span>
          </div>
          {/* Diverging split: cure/improve to the left, roll to the right. */}
          <div className="flex h-4 rounded-inner overflow-hidden border border-border/40">
            <div style={{ width: `${f.curePct}%`, background: BRAND.success }} title={`Cure ${f.curePct}%`} />
            <div style={{ width: `${f.improvePct}%`, background: `${BRAND.success}66` }} title={`Improve ${f.improvePct}%`} />
            <div style={{ width: `${f.holdPct}%`, background: BRAND.grid }} title={`Hold ${f.holdPct}%`} />
            <div style={{ width: `${f.rollPct}%`, background: BRAND.destructive }} title={`Roll ${f.rollPct}%`} />
          </div>
          <div className="flex justify-between text-[9.5px] font-semibold mt-1">
            <span style={{ color: BRAND.success }}>{f.curePct}% cure · {cr(f.curableCr)} recoverable</span>
            <span style={{ color: BRAND.destructive }}>{f.rollPct}% roll · {cr(f.atRiskCr)} at stake</span>
          </div>
        </div>
      ))}
    </div>
  );
}
