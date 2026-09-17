// ─── CHANGELOG (prototype → product) ───────────────────────────────────────
// 2026-09-17 — NEW. The overview's Field Activity funnel.
//
//   Four stages — Planned → Visited → Met → Paid or promised — for the cases
//   on the team's routes in one window (Today, default; 7d; 30d), with the
//   drop-off between stages printed where the money and the effort go
//   missing, and the two reason lists that tell a manager WHY: "not met"
//   (not available / address issue) and "met, no money" (revisit / refused /
//   dispute …). Every number links to the Cases page carrying the same window.
//
//   TODAY MEANS TODAY'S VISITS ONLY. The card renders exactly what
//   GET /manager/dashboard/field-activity counted for the window and nothing
//   else; it holds no case state and re-derives nothing. On a day with no
//   visits it says so in words ("No visits recorded yet today") under bars
//   that honestly read 0 — never a historical Met count.
//
//   Form: a horizontal funnel is a single series across an ordered set of
//   stages, so one hue stepped darker-to-lighter (lightness monotone), 4px
//   rounded bar ends, counts in ink beside each bar rather than on it, and a
//   native tooltip on every stage naming its definition. The window control
//   is the same segmented radiogroup the Field Plan page uses.
// ─────────────────────────────────────────────────────────────────────────────

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router";
import { Footprints } from "lucide-react";
import { getFieldActivity } from "@/api/manager";
import {
  DEFAULT_WINDOW, STAGE_DEFS, WINDOWS, WINDOW_LABEL, WINDOW_WORDS,
  casesLink, dropOffLabel, emptyVisitsMessage, reasonRows, shareOfPlanned,
  type ActivityWindow, type Stage,
} from "./fieldActivity";

// One hue, lightness monotone from the funnel's mouth to its tip.
const RAMP: Record<Stage, string> = {
  planned: "#1E3A8A",
  visited: "#1D4ED8",
  met: "#3B82F6",
  paid_or_promised: "#93C5FD",
};

const dayWords = (iso: string) =>
  new Date(`${iso}T00:00:00`).toLocaleDateString("en-IN", { day: "numeric", month: "short" });

export function FieldActivityCard({ style }: { style?: React.CSSProperties }) {
  const [window, setWindow] = useState<ActivityWindow>(DEFAULT_WINDOW);
  const q = useQuery({
    queryKey: ["manager", "dashboard", "field-activity", window],
    queryFn: () => getFieldActivity(window),
    retry: false,
    staleTime: 30_000,
    refetchOnWindowFocus: false,
  });
  const d = q.data ?? null;
  const empty = d ? emptyVisitsMessage(d) : null;
  const notMet = d ? reasonRows(window, "not_met", d.not_met_reasons) : [];
  const noMoney = d ? reasonRows(window, "met_no_money", d.met_no_money_reasons) : [];
  const dropAfter: Record<Stage, number | null | undefined> = {
    planned: d?.drop_offs.planned_to_visited,
    visited: d?.drop_offs.visited_to_met,
    met: d?.drop_offs.met_to_paid_or_promised,
    paid_or_promised: undefined,
  };
  const rangeWords = d
    ? (d.window_start === d.window_end ? dayWords(d.window_end) : `${dayWords(d.window_start)} – ${dayWords(d.window_end)}`)
    : null;

  return (
    <div className="card p-4 sm:p-5 flex flex-col" style={{ backgroundImage: "none", ...style }}>
      <div className="flex items-start justify-between gap-3 mb-3">
        <div className="flex items-center gap-2 min-w-0">
          <div className="icon-circle bg-brand-600 flex-shrink-0" style={{ width: 36, height: 36 }}>
            <Footprints className="w-4 h-4 text-white" />
          </div>
          <div className="min-w-0">
            <h2 className="text-base font-bold truncate" style={{ color: "#1C1C1F" }}>
              Field Activity <span className="font-medium" style={{ color: "#6B6D76" }}>· {WINDOW_WORDS[window]}</span>
            </h2>
            <p className="text-xs" style={{ color: "#6B6D76" }}>
              {rangeWords ? `Cases on the routes for ${rangeWords} — visits inside that window only` : "What happened to the cases planned for the field"}
            </p>
          </div>
        </div>
        <div role="radiogroup" aria-label="Activity window" className="inline-flex items-center rounded-lg border border-slate-200 bg-slate-100 p-0.5 text-[11px] font-medium flex-shrink-0">
          {WINDOWS.map((w) => (
            <button
              key={w}
              type="button"
              role="radio"
              aria-checked={window === w}
              onClick={() => setWindow(w)}
              className={`rounded-md px-2 py-0.5 transition-all ${window === w ? "bg-white shadow-sm font-semibold text-slate-900" : "text-slate-500 hover:text-slate-800"}`}
            >
              {WINDOW_LABEL[w]}
            </button>
          ))}
        </div>
      </div>

      {q.isError ? (
        <p className="text-xs" style={{ color: "#B45309" }}>Could not load field activity.</p>
      ) : !d ? (
        <p className="text-xs" style={{ color: "#94a3b8" }}>Loading…</p>
      ) : (
        <>
          {/* The funnel. Label column | bar | count, with the drop-off to the
              next stage printed in the gap beneath each bar. */}
          <ol className="space-y-1.5" aria-label="Field activity funnel">
            {STAGE_DEFS.map((s, i) => {
              const count = d[s.key];
              const share = shareOfPlanned(count, d.planned);
              const drop = dropOffLabel(dropAfter[s.key]);
              const next = STAGE_DEFS[i + 1];
              return (
                <li key={s.key}>
                  <Link
                    to={casesLink(window, s.key)}
                    title={`${s.label}: ${s.hint} Click to open these cases.`}
                    className="grid items-center gap-3 rounded-lg -mx-2 px-2 py-1 hover:bg-slate-50 transition-colors"
                    style={{ gridTemplateColumns: "minmax(88px, 120px) 1fr auto" }}
                  >
                    <span className="text-xs font-semibold truncate" style={{ color: "#1C1C1F" }}>{s.label}</span>
                    <div className="h-3.5 rounded-[4px] overflow-hidden" style={{ background: "#EFF0F4" }}>
                      <div
                        className="h-full rounded-[4px]"
                        style={{
                          width: share > 0 ? `${Math.max(share * 100, 1)}%` : 0,
                          minWidth: count > 0 ? 6 : 0,
                          background: RAMP[s.key],
                          transition: "width 700ms cubic-bezier(0.16,1,0.3,1)",
                        }}
                      />
                    </div>
                    <span className="text-sm font-bold tabular-nums text-right" style={{ color: "#1C1C1F", minWidth: 36 }}>{count}</span>
                  </Link>
                  {next && (
                    <div className="grid gap-3 -mx-2 px-2" style={{ gridTemplateColumns: "minmax(88px, 120px) 1fr auto" }}>
                      <span />
                      <span className="text-[10.5px] tabular-nums leading-tight" style={{ color: drop ? "#B45309" : "#94a3b8" }}>
                        {drop ? `${drop} to ${next.label.toLowerCase()}` : "—"}
                      </span>
                      <span />
                    </div>
                  )}
                </li>
              );
            })}
          </ol>

          {empty ? (
            <p className="mt-3 text-xs font-medium" style={{ color: "#6B6D76" }}>{empty}</p>
          ) : (
            <div className="mt-3 pt-3 space-y-1.5 text-[11px]" style={{ borderTop: "1px solid #EAEBEF", color: "#6B6D76" }}>
              <ReasonLine
                label={`Not met ${d.not_met}`}
                title="Visited inside the window, but nobody was reached: not available or address issue. Cases with no visit in the window are not here — they are the drop from Planned to Visited."
                rows={notMet}
                allHref={casesLink(window, "not_met")}
              />
              <ReasonLine
                label={`Met, no payment or promise ${d.met_no_money}`}
                title="Reached inside the window, but the visit ended without money or a promise: revisit needed, refused, dispute…"
                rows={noMoney}
                allHref={casesLink(window, "met_no_money")}
              />
            </div>
          )}
        </>
      )}
    </div>
  );
}

function ReasonLine({ label, title, rows, allHref }: {
  label: string; title: string;
  rows: Array<{ outcome: string; label: string; count: number; href: string }>;
  allHref: string;
}) {
  return (
    <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5" title={title}>
      <Link to={allHref} className="font-semibold hover:underline" style={{ color: "#1C1C1F" }}>{label}</Link>
      {rows.length === 0 ? (
        <span>—</span>
      ) : rows.map((r, i) => (
        <span key={r.outcome}>
          {i > 0 && <span className="mr-2" aria-hidden="true">·</span>}
          <Link to={r.href} className="hover:underline" title={`Open Cases: ${r.label}, this window`}>
            {r.label} <span className="tabular-nums font-semibold" style={{ color: "#1C1C1F" }}>{r.count}</span>
          </Link>
        </span>
      ))}
    </div>
  );
}
