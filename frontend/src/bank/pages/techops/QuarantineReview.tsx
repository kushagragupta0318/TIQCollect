// Tech Ops › Loan Feed › quarantine review. Opens under a selected batch, so a
// reviewer sees the rows THAT file held and can release them.
//
// WHAT RELEASE PROMISES. It is a RE-TRY through the same placement rules the
// nightly feed uses, not a correction. So this component is careful about three
// things, each of which the backend decides and this only reports:
//
//   * "Released" means placed with an agency and a case opened — not that the
//     request returned 200. A row the rules refused AGAIN comes back
//     STILL_REFUSED and is shown as unresolved, never as a success.
//   * A row that never created a loan (UNKNOWN_BRANCH) cannot be released from
//     here at all. It is shown, disabled, with what to do instead.
//   * Counts are the server's, over the whole batch. A value the server did not
//     send is "—", never 0.
//
// Whether this user may release is the SERVER's answer: the button is offered,
// and a 403 is reported if it comes back. There is deliberately no role list
// here — core/permissions.py is the one permission model.
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, Info, ShieldAlert } from "lucide-react";

import { errorDetail, errorStatus } from "@/lib/apiError";
import {
  getQuarantine, outcomeMeaning, reasonLabel, releaseRows,
  type QuarantineRow, type ReleaseResult,
} from "./quarantineModel";
import { AnalyticsError, AnalyticsLoading, Panel, Tile } from "../../components/analytics";

const RELEASE_REFUSED =
  "Releasing a quarantined row needs a capability this role does not hold.";

const TONE_CLASS: Record<string, string> = {
  good: "text-success-600",
  warn: "text-warning-600",
  bad: "text-destructive",
  pending: "text-muted-foreground",
};

export function QuarantineReview({ batchId }: { batchId: string }) {
  const qc = useQueryClient();
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [confirming, setConfirming] = useState(false);
  const [result, setResult] = useState<ReleaseResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const q = useQuery({
    queryKey: ["bank", "feed", "quarantine", batchId],
    queryFn: () => getQuarantine(batchId),
  });

  const release = useMutation({
    mutationFn: (rowIds: string[]) => releaseRows(batchId, rowIds),
    onSuccess: (r) => {
      setResult(r);
      setError(null);
      setSelected(new Set());
      setConfirming(false);
      // The batch's own counts change with a release, so the history and the
      // batch detail are both stale now.
      void qc.invalidateQueries({ queryKey: ["bank", "feed", "quarantine", batchId] });
      void qc.invalidateQueries({ queryKey: ["bank", "feed"] });
    },
    onError: (e) => {
      setConfirming(false);
      setError(errorStatus(e) === 403 ? RELEASE_REFUSED
        : errorDetail(e, "The release could not be completed. Nothing was changed."));
    },
  });

  function toggle(rowId: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(rowId)) next.delete(rowId);
      else next.add(rowId);
      return next;
    });
  }

  if (q.isPending) {
    return <Panel title="Quarantined rows"><AnalyticsLoading label="Loading quarantined rows…" /></Panel>;
  }
  if (q.isError) {
    return (
      <Panel title="Quarantined rows">
        <AnalyticsError>
          {errorStatus(q.error) === 403 ? RELEASE_REFUSED
            : errorDetail(q.error, "The quarantined rows could not be loaded.")}
        </AnalyticsError>
        <button type="button" onClick={() => q.refetch()}
                className="mt-3 rounded-control border border-border px-3 py-1.5 text-[12px] font-semibold">
          Retry
        </button>
      </Panel>
    );
  }

  const data = q.data!;
  const releasable = data.items.filter((r) => r.releasable);
  const chosen = [...selected];

  return (
    <Panel title="Quarantined rows" hint="Held by the ingestion rules, for review">
      <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-4" data-testid="quarantine-tiles">
        <Tile label="Held" value={data.total_quarantined} />
        <Tile label="Can be released" value={data.total_releasable} />
        <Tile label="Need a re-ingest" value={data.total_blocked} />
        <Tile label="Released earlier" value={data.released_count} />
      </div>

      {!data.batch_reviewable && (
        // The batch has not finished processing, so what is held is not yet
        // decided. Said rather than showing an empty table.
        <p className="mb-3 flex items-start gap-1.5 text-[12px] text-muted-foreground"
           data-testid="quarantine-not-reviewable">
          <Info size={13} className="mt-px shrink-0" />
          This batch is {data.batch_status.toLowerCase()}. Rows can be reviewed once it has been processed.
        </p>
      )}

      {data.total_quarantined === 0 ? (
        <p className="py-6 text-center text-[12.5px] text-muted-foreground" data-testid="quarantine-empty">
          Nothing from this file is quarantined.
        </p>
      ) : (
        <>
          <div className="overflow-x-auto">
            <table className="w-full text-[12.5px]" data-testid="quarantine-table">
              <thead>
                <tr className="border-b border-border text-left text-[11px] text-muted-foreground">
                  <th className="w-8 py-2" />
                  <th className="py-2 pr-3">Row</th>
                  <th className="py-2 pr-3">Loan account</th>
                  <th className="py-2 pr-3">Reason</th>
                  <th className="py-2 pr-3">Detail</th>
                  <th className="py-2 pr-3 text-right">Attempts</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((row) => (
                  <Row key={row.row_id} row={row} checked={selected.has(row.row_id)}
                       disabled={!data.batch_reviewable} onToggle={() => toggle(row.row_id)} />
                ))}
              </tbody>
            </table>
          </div>

          {data.total_blocked > 0 && (
            <p className="mt-3 flex items-start gap-1.5 text-[11.5px] text-muted-foreground"
               data-testid="quarantine-blocked-note">
              <ShieldAlert size={13} className="mt-px shrink-0" />
              {data.total_blocked} of these never created a loan, so there is nothing to place. Add the
              branch to this bank and re-ingest the file — releasing cannot fix them.
            </p>
          )}

          {/* ── Confirmation. Release places loans with an agency and opens
                 cases, so it is never one click. ── */}
          {!confirming ? (
            <button
              type="button"
              data-testid="quarantine-release-button"
              disabled={chosen.length === 0 || release.isPending || !data.batch_reviewable}
              onClick={() => setConfirming(true)}
              className="mt-4 rounded-control bg-primary px-3 py-1.5 text-[12px] font-semibold text-primary-foreground disabled:opacity-50"
            >
              Review and release{chosen.length ? ` (${chosen.length})` : ""}
            </button>
          ) : (
            <div className="mt-4 rounded-card border border-border px-4 py-3" data-testid="quarantine-confirm">
              <p className="text-[12.5px] font-semibold text-foreground">
                Release {chosen.length} row{chosen.length === 1 ? "" : "s"} for processing?
              </p>
              <p className="mt-1 text-[11.5px] text-muted-foreground">
                Each row is re-checked against the placement rules. A row whose reason has not been
                resolved will be refused again and stay quarantined — nothing is forced through.
              </p>
              <div className="mt-3 flex gap-2">
                <button
                  type="button"
                  data-testid="quarantine-confirm-button"
                  disabled={release.isPending}
                  onClick={() => release.mutate(chosen)}
                  className="rounded-control bg-primary px-3 py-1.5 text-[12px] font-semibold text-primary-foreground disabled:opacity-50"
                >
                  {release.isPending ? "Releasing…" : "Release"}
                </button>
                <button
                  type="button"
                  disabled={release.isPending}
                  onClick={() => setConfirming(false)}
                  className="rounded-control border border-border px-3 py-1.5 text-[12px] font-semibold"
                >
                  Cancel
                </button>
              </div>
            </div>
          )}
        </>
      )}

      {error && (
        <p role="alert" className="mt-3 text-[12.5px] text-destructive" data-testid="quarantine-error">
          {error}
        </p>
      )}

      {result && <ReleaseReport result={result} />}

      {releasable.length === 0 && data.total_quarantined > 0 && data.batch_reviewable && (
        <p className="mt-3 text-[11.5px] text-muted-foreground">
          None of the held rows can be released from here.
        </p>
      )}
    </Panel>
  );
}

function Row({ row, checked, disabled, onToggle }: {
  row: QuarantineRow; checked: boolean; disabled: boolean; onToggle: () => void;
}) {
  const blocked = !row.releasable;
  return (
    <tr className="border-b border-border/60" data-testid={`quarantine-row-${row.row_no}`}>
      <td className="py-2">
        <input
          type="checkbox"
          checked={checked}
          disabled={blocked || disabled}
          onChange={onToggle}
          aria-label={`Select row ${row.row_no}`}
          title={blocked ? "This row never created a loan, so it cannot be released here." : undefined}
        />
      </td>
      <td className="py-2 pr-3 tabular-nums">{row.row_no}</td>
      <td className="py-2 pr-3">{row.loan_account_number ?? "—"}</td>
      <td className="py-2 pr-3">{row.reason ? reasonLabel(row.reason) : "—"}</td>
      <td className="py-2 pr-3 text-muted-foreground">{row.detail ?? "—"}</td>
      <td className="py-2 pr-3 text-right tabular-nums">{row.attempts}</td>
    </tr>
  );
}

/** The per-row outcome of one release. A mixed selection is normal, so this
 *  reports each verdict rather than one overall "success". */
function ReleaseReport({ result }: { result: ReleaseResult }) {
  const released = result.released;
  return (
    <div className="mt-4 rounded-card border border-border px-4 py-3" data-testid="quarantine-release-result">
      {released > 0 ? (
        <p className="flex items-center gap-1.5 text-[12.5px] font-semibold text-success-600">
          <CheckCircle2 size={14} />
          {released} row{released === 1 ? "" : "s"} released — placed with an agency and a case opened.
        </p>
      ) : (
        <p className="flex items-center gap-1.5 text-[12.5px] font-semibold text-warning-600">
          <AlertTriangle size={14} /> No rows were released.
        </p>
      )}

      {result.still_refused > 0 && (
        <p className="mt-1 text-[11.5px] text-muted-foreground" data-testid="quarantine-still-refused">
          {result.still_refused} row{result.still_refused === 1 ? " was" : "s were"} refused again and
          remain quarantined: the reason they were held has not been resolved.
        </p>
      )}
      {result.already_released > 0 && (
        <p className="mt-1 text-[11.5px] text-muted-foreground" data-testid="quarantine-already-released">
          {result.already_released} had already been handled. Nothing was done twice.
        </p>
      )}

      <p className="mt-1 text-[11.5px] text-muted-foreground">
        {result.remaining_quarantined} row{result.remaining_quarantined === 1 ? "" : "s"} still held for
        this batch.
      </p>

      <ul className="mt-2 space-y-1">
        {result.verdicts.map((v) => {
          const m = outcomeMeaning(v.outcome);
          return (
            <li key={v.row_id} className="text-[11.5px]">
              <span className="text-muted-foreground">Row {v.row_no ?? "—"}: </span>
              <span className={`font-semibold ${TONE_CLASS[m.tone]}`}>{m.label}</span>
              {v.case_number && <span className="text-muted-foreground"> · case {v.case_number}</span>}
              {v.reason && v.outcome !== "RELEASED" && (
                <span className="text-muted-foreground"> · {reasonLabel(v.reason)}</span>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

export default QuarantineReview;
