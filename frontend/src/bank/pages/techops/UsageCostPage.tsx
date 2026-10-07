// Tech Ops › Usage & Cost — token counts, estimated spend and calls by
// feature and by day, read-only (GET /bank/usage). F11.
//
// Costs by feature/day are server-computed and server-stored (one price per
// model, at write time — core/llm.py). Nothing here recomputes a dollar
// figure; it only formats what the API already decided.
import { useQuery } from "@tanstack/react-query";
import { Gauge } from "lucide-react";

import { errorDetail } from "@/lib/apiError";
import {
  costCellLabel, featureLabel, formatDay, formatTokens, formatUsd, getUsage, type UsageByFeature,
} from "./usageCostModel";
import { AnalyticsError, AnalyticsLoading, Panel, Tile } from "../../components/analytics";
import { DataTable, type DataColumn } from "../../components/DataTable";
import { PageRoot, ToolHeader } from "../../components/PageTemplate";

export function UsageCostPage() {
  const q = useQuery({ queryKey: ["bank", "usage"], queryFn: getUsage });
  const data = q.data;

  const columns: DataColumn<UsageByFeature>[] = [
    { key: "feature", header: "Feature", render: (r) => featureLabel(r.feature) },
    { key: "calls", header: "Calls", className: "text-right", render: (r) => r.calls.toLocaleString() },
    { key: "input_tokens", header: "Input tokens", className: "text-right",
      render: (r) => formatTokens(r.input_tokens) },
    { key: "output_tokens", header: "Output tokens", className: "text-right",
      render: (r) => formatTokens(r.output_tokens) },
    { key: "cache_tokens", header: "Cached", className: "text-right",
      render: (r) => formatTokens(r.cache_tokens) },
    { key: "cost_usd", header: "Cost", className: "text-right font-semibold",
      render: (r) => costCellLabel(r.cost_usd, r.unpriced_calls) },
  ];

  const maxDayCost = Math.max(0.0001, ...(data?.by_day.map((d) => d.cost_usd) ?? []));

  return (
    <PageRoot>
      <ToolHeader
        title="Usage & Cost"
        icon={Gauge}
        description="LLM calls, tokens and spend by purpose, over the last 30 days."
      />

      {q.isError ? (
        <AnalyticsError>{errorDetail(q.error, "Could not load usage.")}</AnalyticsError>
      ) : !data ? (
        <AnalyticsLoading />
      ) : (
        <>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Tile label="Calls" value={data.totals.calls.toLocaleString()} />
            <Tile label="Input tokens" value={formatTokens(data.totals.input_tokens)} />
            <Tile label="Output tokens" value={formatTokens(data.totals.output_tokens)} />
            <Tile
              label="Estimated cost"
              value={formatUsd(data.totals.cost_usd)}
              sub={data.totals.unpriced_calls > 0
                ? `${data.totals.unpriced_calls} call${data.totals.unpriced_calls === 1 ? "" : "s"} at an unknown price, not counted`
                : undefined}
            />
          </div>

          <Panel title="By feature">
            {data.by_feature.length === 0 ? (
              <p className="text-sm text-muted-foreground py-6 text-center">No calls in this window.</p>
            ) : (
              <DataTable columns={columns} rows={data.by_feature} rowKey={(r) => r.feature} minWidth={640} />
            )}
          </Panel>

          <Panel title="By day">
            {data.by_day.length === 0 ? (
              <p className="text-sm text-muted-foreground py-6 text-center">No calls in this window.</p>
            ) : (
              <div className="flex items-end gap-1.5 h-32">
                {data.by_day.map((d) => {
                  // A day with real activity but no priced cost must not look
                  // identical to an empty day — height-by-cost alone would
                  // flatten it to the same near-invisible sliver either way.
                  const allUnpriced = d.calls > 0 && d.unpriced_calls === d.calls;
                  return (
                    <div key={d.day} className="flex-1 flex flex-col items-center gap-1"
                        title={`${formatDay(d.day)}: ${costCellLabel(d.cost_usd, d.unpriced_calls)}, ${d.calls} calls`}>
                      <div
                        className={allUnpriced
                          ? "w-full rounded-t border-2 border-dashed border-muted-foreground/40 bg-transparent"
                          : "w-full rounded-t bg-primary/70"}
                        style={{ height: allUnpriced ? "15%" : `${Math.max(2, (d.cost_usd / maxDayCost) * 100)}%` }}
                      />
                      <span className="text-[10px] text-muted-foreground">{formatDay(d.day)}</span>
                    </div>
                  );
                })}
              </div>
            )}
          </Panel>

          {data.coverage.pending_attribution > 0 && (
            <p className="text-[11px] mt-1 pt-3 border-t text-muted-foreground">
              <span className="font-semibold">
                {data.coverage.pending_attribution} call{data.coverage.pending_attribution === 1 ? "" : "s"} pending attribution
              </span>{" "}
              — {data.coverage.note}
            </p>
          )}
        </>
      )}
    </PageRoot>
  );
}

export default UsageCostPage;
