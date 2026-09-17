// ─── CHANGELOG (prototype → product) ───────────────────────────────────────
// 2026-09-17 — NEW. The overview's Promises card, beside Field Activity.
//
//   A promise's status is a STOCK, not a flow: kept / broken are all-time
//   over this manager's agents' promises (a "kept rate this week" over seven
//   promises would be noise), and "due this week" is forward-looking from the
//   dashboard's effective date. So this card takes no window control; the
//   subtitle says what each figure covers.
//
//   Kept rate = honoured / (honoured + broken) — the two terminal outcomes —
//   drawn as a two-tone meter on one track with a 2px gap, so the ratio IS
//   the picture and the words beneath give the counts. "Due this week" opens
//   the Cases page filtered to active promises in the next seven days.
//   Reads `ptp_health` off GET /manager/dashboard; no separate request.
//
//   2026-09-17 (later) — one line under the counts says WHEN a promise turns
//   broken, because until today nothing ever turned one: the nightly job in
//   backend/app/services/ptp_lifecycle_service.py now resolves a promise the
//   day after its grace day. Without that line, "Active 200" beside a kept
//   rate would read as 200 live promises when many were simply past their date.
// ─────────────────────────────────────────────────────────────────────────────

import { Link } from "react-router";
import { Handshake } from "lucide-react";
import type { PtpHealth } from "@/types";

const KEPT = "#059669";
const BROKEN = "#DC2626";

export function PromisesCard({ health, style }: { health: PtpHealth | undefined | null; style?: React.CSSProperties }) {
  const kept = health?.honored ?? 0;
  const broken = health?.broken ?? 0;
  const decided = kept + broken;
  const rate = health?.kept_rate_pct ?? null;
  const keptShare = decided > 0 ? kept / decided : 0;
  const dueHref = health
    ? `/manager/cases?ptp_due_from=${health.due_from}&ptp_due_to=${health.due_to}`
    : "/manager/cases";

  return (
    <div className="card p-4 sm:p-5 flex flex-col" style={{ backgroundImage: "none", ...style }}>
      <div className="flex items-center gap-2 mb-3">
        <div className="icon-circle bg-brand-600 flex-shrink-0" style={{ width: 36, height: 36 }}>
          <Handshake className="w-4 h-4 text-white" />
        </div>
        <div className="min-w-0">
          <h2 className="text-base font-bold truncate" style={{ color: "#1C1C1F" }}>Promises</h2>
          <p className="text-xs" style={{ color: "#6B6D76" }}>All promises to pay · due = next 7 days</p>
        </div>
      </div>

      {!health ? (
        <p className="text-xs" style={{ color: "#94a3b8" }}>Loading…</p>
      ) : (
        <div className="flex flex-col gap-4 flex-1">
          {/* Kept rate — the hero, over a two-tone meter. */}
          <div>
            <div className="flex items-baseline justify-between gap-2">
              <span className="text-[11px] font-medium uppercase tracking-wide" style={{ color: "#6B6D76" }}>Kept rate</span>
              <span className="text-2xl font-bold tabular-nums leading-none" style={{ color: "#1C1C1F" }} title="Honoured ÷ (honoured + broken), all time">
                {rate == null ? "—" : `${Math.round(rate)}%`}
              </span>
            </div>
            <div
              className="mt-2 flex h-2.5 w-full gap-0.5 rounded-full overflow-hidden"
              style={{ background: "#EFF0F4" }}
              role="img"
              aria-label={`${kept} promises kept, ${broken} broken`}
              title={`${kept} kept · ${broken} broken`}
            >
              {decided > 0 && (
                <>
                  <div className="h-full rounded-l-full" style={{ width: `${keptShare * 100}%`, background: KEPT, transition: "width 700ms cubic-bezier(0.16,1,0.3,1)" }} />
                  <div className="h-full rounded-r-full flex-1" style={{ background: BROKEN }} />
                </>
              )}
            </div>
            <p className="mt-1.5 text-[11px] tabular-nums" style={{ color: "#6B6D76" }}>
              <span className="inline-flex items-center gap-1"><i aria-hidden="true" style={{ width: 8, height: 8, borderRadius: 2, background: KEPT, display: "inline-block" }} /><strong style={{ color: "#1C1C1F" }}>{kept}</strong> kept</span>
              <span className="mx-2">·</span>
              <span className="inline-flex items-center gap-1"><i aria-hidden="true" style={{ width: 8, height: 8, borderRadius: 2, background: BROKEN, display: "inline-block" }} /><strong style={{ color: "#1C1C1F" }}>{broken}</strong> broken</span>
              {decided === 0 && <span className="ml-2">no promise has fallen due yet</span>}
            </p>
          </div>

          {/* Due this week — a door into Cases. */}
          <Link
            to={dueHref}
            className="flex items-center justify-between rounded-xl px-3 py-2.5 transition-colors hover:bg-slate-50"
            style={{ border: "1px solid #EAEBEF" }}
            title={`Active promises committed ${health.due_from} to ${health.due_to} — open these cases`}
          >
            <span className="text-xs font-semibold" style={{ color: "#1C1C1F" }}>Due this week</span>
            <span className="inline-flex items-center gap-2">
              <span className="text-xl font-bold tabular-nums leading-none" style={{ color: health.due_next_7_days > 0 ? "#B45309" : "#1C1C1F" }}>{health.due_next_7_days}</span>
              <span aria-hidden="true" style={{ color: "#6B6D76" }}>→</span>
            </span>
          </Link>

          <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11px] tabular-nums mt-auto" style={{ color: "#6B6D76" }}>
            <span>Active <strong style={{ color: "#1C1C1F" }}>{health.active}</strong></span>
            <span>Rescheduled <strong style={{ color: "#1C1C1F" }}>{health.rescheduled}</strong></span>
          </div>
          <p className="text-[11px] mt-1" style={{ color: "#94A3B8" }}>
            Promises are marked broken after the due date plus a 1-day grace period.
          </p>
        </div>
      )}
    </div>
  );
}
