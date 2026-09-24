import { useEffect, useState } from "react";
import { subscribeEvents, type LiveEvent, type StreamStatus } from "@/lib/eventStream";
import { MANAGER_SLOT, readSlotAuth } from "./simSession";
import { sim } from "./simTheme";

/**
 * The link between the two screens, made visible: every event the manager's
 * stream receives, newest first, with how long it took to arrive.
 *
 * Reads the stream AS the manager frame (its slot token) — so what appears
 * here is exactly what the manager's pages are being told, not a separate
 * feed that could disagree with them.
 */

const LABEL: Record<string, string> = {
  "agent.checked_in": "Checked in",
  "agent.checked_out": "Checked out",
  "agent.location": "GPS ping",
  "visit.recorded": "Visit recorded",
  "payment.submitted": "Payment submitted",
  "payment.verified": "Payment verified (OTP)",
  "ptp.set": "Promise to pay set",
  "sos.triggered": "SOS raised",
  "sos.cancelled": "SOS cancelled",
};

const TONE: Record<string, string> = {
  "sos.triggered": sim.tone.danger,
  "payment.verified": sim.tone.good,
  "payment.submitted": sim.tone.good,
  "visit.recorded": sim.tone.brand,
  "ptp.set": sim.tone.warn,
};

interface Row {
  e: LiveEvent;
  receivedAt: number;
}

function detail(e: LiveEvent): string {
  const d = e.data;
  const parts: string[] = [];
  if (typeof d.case_number === "string") parts.push(d.case_number);
  if (typeof d.outcome === "string") parts.push(d.outcome.replace(/_/g, " ").toLowerCase());
  if (typeof d.amount === "number") parts.push(`₹${d.amount.toLocaleString("en-IN")}`);
  if (typeof d.committed_amount === "number") parts.push(`₹${d.committed_amount.toLocaleString("en-IN")}`);
  if (typeof d.lat === "number" && typeof d.lon === "number" && e.type.startsWith("agent."))
    parts.push(`${d.lat.toFixed(5)}, ${d.lon.toFixed(5)}`);
  return parts.join(" · ");
}

export function EventTimeline({ onStatus }: { onStatus?: (s: StreamStatus) => void }) {
  const [rows, setRows] = useState<Row[]>([]);
  // Events that happened before this page opened arrive through the catch-up
  // read of /events/recent. Their "latency" would be the page's age, not the
  // push delay (a first screenshot showed "reached manager in 58467 ms"), so
  // only events published after mount show one.
  const [mountedAt] = useState(() => Date.now());
  const [hidePings, setHidePings] = useState(false);

  useEffect(() => {
    const stop = subscribeEvents({
      getToken: () => readSlotAuth(MANAGER_SLOT).accessToken,
      onEvent: (e) => setRows((r) => [{ e, receivedAt: Date.now() }, ...r].slice(0, 150)),
      onStatus,
      // The manager frame refreshes its own tokens as it polls; the next
      // reconnect simply reads the new one from its slot.
      onUnauthorized: async () => false,
    });
    return stop;
  }, [onStatus]);

  const shown = hidePings ? rows.filter((r) => r.e.type !== "agent.location") : rows;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center justify-between px-4 pb-2">
        <p className={sim.sectionLabel}>Live events → manager</p>
        <label className="flex cursor-pointer items-center gap-1.5 text-[12px] text-[#667085]">
          <input type="checkbox" checked={hidePings} onChange={(e) => setHidePings(e.target.checked)} />
          hide GPS pings
        </label>
      </div>
      <ol className="min-h-0 flex-1 space-y-1.5 overflow-y-auto px-4 pb-4">
        {shown.length === 0 && (
          <li className="rounded-xl border border-dashed border-[#ECEDF1] p-4 text-center text-xs text-[#98A2B3]">
            Sign in on both screens, then check in on the phone. Everything the
            agent does appears here the moment the manager's view is told.
          </li>
        )}
        {shown.map(({ e, receivedAt }) => {
          const sent = Date.parse(e.at);
          const live = Number.isFinite(sent) && sent >= mountedAt;
          const latency = live ? receivedAt - sent : null;
          return (
            <li key={e.id} className={sim.eventRow}>
              <span className="mt-1 h-2 w-2 flex-shrink-0 rounded-full" style={{ background: TONE[e.type] ?? sim.tone.muted }} />
              <div className="min-w-0 flex-1">
                <div className="flex items-baseline justify-between gap-2">
                  <span className="truncate text-[13px] font-semibold text-[#101828]">{LABEL[e.type] ?? e.type}</span>
                  <span className="flex-shrink-0 font-mono text-[10px] text-[#98A2B3]">
                    {new Date(e.at).toLocaleTimeString("en-IN", { hour12: false })}
                  </span>
                </div>
                <div className="truncate text-[12px] text-[#667085]">
                  {e.agent_name ?? "agent"}{detail(e) ? ` · ${detail(e)}` : ""}
                </div>
                {latency !== null ? (
                  <div className="text-[10px] text-[#98A2B3]">reached manager in {Math.max(0, latency)} ms</div>
                ) : (
                  <div className="text-[10px] text-[#98A2B3]">earlier — before this page opened</div>
                )}
              </div>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
