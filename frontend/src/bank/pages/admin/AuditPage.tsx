// Admin › Audit — the bank's recorded actions and its agencies'. Every row
// comes from /bank/audit; nothing is computed here, and nothing is hidden
// here either.
//
// The "pending attribution" line is not decoration. Rows written with no
// actor and no entity tenant carry no bank (known issue 6), so no bank can
// read them — and a trail that silently dropped them would read as a quiet
// week to the one person whose job is noticing that it was not. The server
// counts them; this page says so out loud.
import { useState } from "react";
import { useSearchParams } from "react-router";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Download, ScrollText } from "lucide-react";

import { errorDetail } from "@/lib/apiError";
import { PAGE, actionLabel, entityQuery, exportAudit, getAudit, when, type AuditRow } from "./auditModel";
import { AnalyticsError, AnalyticsLoading, Panel } from "../../components/analytics";
import { DataTable, type DataColumn } from "../../components/DataTable";
import { PageRoot, ToolHeader, ToolHeaderAction } from "../../components/PageTemplate";
import { Badge } from "../../ui/badge";
import { Input } from "../../ui/input";
import { Label } from "../../ui/label";
import { Select } from "../../ui/select";

export function AuditPage() {
  // Read once on mount, same as a normal default — not synced back to the
  // URL on every filter change, so this is a deep-link landing value, not a
  // second source of truth to keep in step with `action`'s own state.
  const [searchParams] = useSearchParams();
  const [action, setAction] = useState(() => searchParams.get("action") ?? "");
  const [actorId, setActorId] = useState("");
  // A deep link from somewhere that knows ONE entity -- a message thread, a
  // reversal -- pins the page to that entity's own rows. Read once, like
  // `action`, and cleared by the banner rather than by a filter control:
  // nobody types a thread's uuid, they arrive holding one.
  const [entity, setEntity] = useState(() => {
    const type = searchParams.get("entity_type") ?? "";
    const id = searchParams.get("entity_id") ?? "";
    return type ? { type, id } : null;
  });
  const [offset, setOffset] = useState(0);
  const [exportError, setExportError] = useState<string | null>(null);

  const q = useQuery({
    queryKey: ["bank", "audit", action, actorId, entity?.type, entity?.id, offset],
    queryFn: () => getAudit({
      limit: PAGE, offset,
      ...(action ? { action } : {}),
      ...(actorId ? { actor_id: actorId } : {}),
      ...entityQuery(entity),
    }),
    placeholderData: keepPreviousData,
  });

  const data = q.data;
  const sensitive = new Set(data?.coverage.sensitive_actions ?? []);

  const columns: DataColumn<AuditRow>[] = [
    { key: "created_at", header: "When", className: "whitespace-nowrap", render: (r) => when(r.created_at) },
    {
      key: "action", header: "Action",
      render: (r) => (
        <span className="flex items-center gap-2">
          {actionLabel(r.action)}
          {sensitive.has(r.action) && <Badge variant="secondary">sensitive</Badge>}
        </span>
      ),
    },
    // "(system)" rather than blank: an actor-less row is a real event written
    // by the platform, not a missing value.
    { key: "actor_name", header: "Actor", render: (r) => r.actor_name ?? "(system)" },
    { key: "entity", header: "Entity", render: (r) => r.entity_type ?? "—" },
    {
      key: "success", header: "Result",
      render: (r) => (r.success
        ? <span className="text-muted-foreground">ok</span>
        : <span title={r.failure_reason ?? undefined} className="font-semibold text-danger-600">refused</span>),
    },
    { key: "ip_address", header: "IP", className: "whitespace-nowrap",
      render: (r) => r.ip_address ?? "—" },
  ];

  async function exportCsv() {
    setExportError(null);
    try {
      const blob = await exportAudit({
        ...(action ? { action } : {}),
        ...(actorId ? { actor_id: actorId } : {}),
        ...entityQuery(entity),       // the CSV is the view, not a wider one
      });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `audit-${new Date().toISOString().slice(0, 10)}.csv`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (e) {
      setExportError(errorDetail(e, "Could not export the audit trail."));
    }
  }

  return (
    <PageRoot>
      <ToolHeader
        title="Audit"
        icon={ScrollText}
        description="Every recorded action by this bank and its agencies. Immutable by convention; exports are themselves audited."
        actions={<ToolHeaderAction icon={Download} onClick={exportCsv}>Export CSV</ToolHeaderAction>}
      />
      {entity && (
        // Said on the page, not only in the URL: 3 rows out of thousands,
        // unlabelled, reads as "nothing happened here".
        <p role="note" className="rounded-[12px] border border-border bg-muted/40 px-4 py-2.5 text-[12.5px] text-muted-foreground">
          Pinned to one {entity.type.toLowerCase()}
          {entity.id ? "" : " (every id of that type)"} — this is not the whole trail.
          <button type="button" className="ml-2 font-medium underline"
                  onClick={() => { setEntity(null); setOffset(0); }}>
            Show everything
          </button>
        </p>
      )}

      {exportError && <p className="text-[12px] font-medium text-danger-600">{exportError}</p>}

      <Panel title="Filters">
        <div className="flex flex-wrap items-end gap-3">
          <div>
            <Label htmlFor="audit-action">Action</Label>
            <Select id="audit-action" value={action}
                    onChange={(e) => { setAction(e.target.value); setOffset(0); }}>
              <option value="">All actions</option>
              {Object.keys(data?.counts_by_action ?? {}).sort().map((a) => (
                <option key={a} value={a}>{actionLabel(a)} ({data?.counts_by_action[a]})</option>
              ))}
            </Select>
          </div>
          <div>
            <Label htmlFor="audit-actor">Actor id</Label>
            <Input id="audit-actor" value={actorId} placeholder="user id"
                   onChange={(e) => { setActorId(e.target.value); setOffset(0); }} />
          </div>
        </div>
      </Panel>

      {q.isError ? (
        <AnalyticsError>{errorDetail(q.error, "Could not load the audit trail.")}</AnalyticsError>
      ) : !data ? (
        <AnalyticsLoading />
      ) : (
        <Panel
          title={`${data.total} recorded ${data.total === 1 ? "action" : "actions"}`}
          hint={`Last ${data.coverage.window_days} days, newest first. Filter or widen the window with \`since\`.`}
        >
          {data.entries.length === 0 ? (
            <p className="text-sm text-muted-foreground py-6 text-center">
              No recorded actions in this window.
            </p>
          ) : (
            <DataTable columns={columns} rows={data.entries} rowKey={(r) => r.id} minWidth={760} />
          )}

          <div className="flex items-center justify-between gap-4 pt-3">
            <p className="text-[11px] text-muted-foreground">
              Showing {data.offset + 1}–{Math.min(data.offset + data.limit, data.total)} of {data.total}
            </p>
            <div className="flex gap-2">
              <button type="button" className="text-[11px] underline disabled:opacity-40"
                      disabled={data.offset === 0}
                      onClick={() => setOffset(Math.max(0, offset - PAGE))}>Previous</button>
              <button type="button" className="text-[11px] underline disabled:opacity-40"
                      disabled={data.offset + data.limit >= data.total}
                      onClick={() => setOffset(offset + PAGE)}>Next</button>
            </div>
          </div>

          {/* The honesty line. Shown only when there is something to admit. */}
          {data.coverage.pending_attribution > 0 && (
            <p className="text-[11px] mt-3 pt-3 border-t text-muted-foreground">
              <span className="font-semibold">
                {data.coverage.pending_attribution} system {data.coverage.pending_attribution === 1 ? "event" : "events"} pending attribution, platform-wide
              </span>{" "}
              — written with no actor and no tenant, so they cannot be shown to any one bank, and the
              count cannot be narrowed to yours for the same reason. {data.coverage.note}
            </p>
          )}
        </Panel>
      )}
    </PageRoot>
  );
}

export default AuditPage;
