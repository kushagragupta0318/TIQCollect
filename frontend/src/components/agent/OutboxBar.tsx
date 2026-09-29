/**
 * What is waiting on this phone (I02, lib/outbox.ts): "N pending · M MB", a
 * Sync now, and the records the server refused, each with its reason and a
 * Discard. Nothing leaves the list silently: a refused record stays until the
 * agent has read why and discarded it.
 */
import { useState, useSyncExternalStore } from "react";
import { AlertTriangle, CloudUpload, RefreshCw, Trash2, X } from "lucide-react";
import type { OutboxItem } from "@/lib/outbox";
import {
  discardOutboxItem, flushOutbox, outboxItems, outboxUsage, subscribeOutbox,
} from "@/lib/outboxRunner";

function mb(bytes: number): string {
  return bytes >= 104857.6 ? `${(bytes / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

function when(iso: string): string {
  return new Date(iso).toLocaleString("en-IN", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
}

export function OutboxBar() {
  const usage = useSyncExternalStore(subscribeOutbox, outboxUsage);
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<OutboxItem[]>([]);
  const [syncing, setSyncing] = useState(false);

  if (usage.pending === 0 && usage.attention === 0 && !open) return null;

  const reload = () => outboxItems().then(setItems).catch(() => setItems([]));
  const show = () => { setOpen(true); void reload(); };
  const sync = async () => {
    setSyncing(true);
    await flushOutbox();
    setSyncing(false);
    void reload();
  };
  const drop = async (it: OutboxItem) => {
    const what = it.kind === "visit" ? "visit" : "call log";
    if (!window.confirm(`Discard this ${what} for ${it.caseLabel}? It will not be recorded.`)) return;
    await discardOutboxItem(it.id);
    void reload();
  };

  return (
    <>
      <button
        type="button"
        onClick={show}
        className={`w-full text-xs text-center py-1.5 px-4 font-medium flex items-center justify-center gap-1.5 ${
          usage.attention > 0 ? "bg-amber-100 text-amber-900" : "bg-sky-50 text-sky-900"}`}
      >
        {usage.attention > 0 ? <AlertTriangle className="w-3 h-3" /> : <CloudUpload className="w-3 h-3" />}
        {usage.pending > 0 && <span>{usage.pending} waiting to send · {mb(usage.bytes)}</span>}
        {usage.pending > 0 && usage.attention > 0 && <span>·</span>}
        {usage.attention > 0 && <span>{usage.attention} need your attention</span>}
      </button>

      {open && (
        <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center bg-black/40 p-2"
             role="dialog" aria-label="Records on this phone" onClick={() => setOpen(false)}>
          <div className="w-full max-w-md max-h-[80svh] overflow-y-auto rounded-card bg-white shadow-premium"
               onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between px-4 py-3 border-b border-border">
              <p className="font-semibold text-sm">Records on this phone</p>
              <button type="button" aria-label="Close" onClick={() => setOpen(false)}><X className="w-4 h-4" /></button>
            </div>
            <p className="px-4 pt-3 text-[11px] text-slate-500">
              Saved when you pressed Submit and sent in that order when there is signal. Payments are never
              saved here: they need signal, because the borrower's OTP must reach the server.
            </p>
            <ul className="px-4 py-2 divide-y divide-border">
              {items.length === 0 && <li className="py-3 text-xs text-slate-500">Nothing waiting.</li>}
              {items.map((it) => (
                <li key={it.id} className="py-2.5 text-xs">
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-medium">
                      {it.kind === "visit" ? "Visit" : "Call log"} · {it.caseLabel}
                    </span>
                    <span className="text-slate-500">{when(it.capture.captured_at)}</span>
                  </div>
                  {it.state === "attention" ? (
                    <div className="mt-1.5 rounded bg-amber-50 border border-amber-200 px-2 py-1.5 text-amber-900">
                      <p>Not accepted: {it.error?.message}</p>
                      {it.kind === "visit" && it.visitId && (
                        <p className="mt-1 text-amber-800">The visit itself was recorded; the promise to pay was not.</p>
                      )}
                      <button type="button" onClick={() => void drop(it)}
                              className="mt-1.5 inline-flex items-center gap-1 font-semibold text-amber-900">
                        <Trash2 className="w-3 h-3" /> Discard
                      </button>
                    </div>
                  ) : (
                    <p className="mt-0.5 text-slate-500">
                      Waiting for signal{it.attempts > 0 ? ` · tried ${it.attempts}×` : ""}
                      {it.kind === "visit" && it.media.length > 0 ? ` · ${it.media.length} photo${it.media.length > 1 ? "s" : ""}` : ""}
                    </p>
                  )}
                </li>
              ))}
            </ul>
            <div className="px-4 pb-4">
              <button type="button" disabled={syncing || !navigator.onLine} onClick={() => void sync()}
                      className="w-full inline-flex items-center justify-center gap-1.5 rounded-lg bg-brand-600 text-white text-sm font-semibold py-2 disabled:opacity-50">
                <RefreshCw className={`w-4 h-4 ${syncing ? "animate-spin" : ""}`} />
                {navigator.onLine ? (syncing ? "Sending…" : "Sync now") : "No signal"}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
