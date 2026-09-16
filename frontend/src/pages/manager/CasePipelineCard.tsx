// ─── CHANGELOG (prototype → product) ───────────────────────────────────────
// 2026-09-16 — NEW. The case-pipeline donut.
//
//   Lives on the Analytics page, under "Collection by DPD Bucket" — the two
//   cards read the same 883 cases two ways (by DPD, by state), so they sit in
//   one column. It was first placed beside "Today's Collections" on the
//   overview earlier the same day and moved here on product direction.
//   Answers: of everything the team holds, how much is finished, how much is
//   being worked, and how much has not been started.
//
//   THE GROUPING IS THE PRODUCT'S OWN DEFINITION, NOT A NEW ONE. "Resolved" is
//   exactly models/case.py RESOLVED_STATUSES (PAID, CLOSED, WRITTEN_OFF) — the
//   set that file says was written twice before and merged once. It is
//   restated in casePipeline.ts because a TS module cannot import a Python
//   frozenset; the comment there names the source so a change there is a
//   change here. ESCALATED is in progress, not resolved, for the reason that
//   file gives: it is open, visitable, and the work a manager most wants
//   surfaced.
//
//   The numbers come from GET /manager/dashboard `case_status_counts`, a
//   single GROUP BY over the cases this manager's agents hold, zero-filled per
//   status. Nothing is computed here but sums.
//
//   The ring itself is DonutCard (shared with the overview's today-by-DPD
//   card since later on 2026-09-16); this file supplies slices and words.
//   Colours validated with the dataviz palette checker (light and dark
//   surfaces, all six checks): emerald / blue / amber.
// ─────────────────────────────────────────────────────────────────────────────

import type { CSSProperties } from "react";
import { Layers } from "lucide-react";
import { DonutCard } from "./DonutCard";
import { pipelineSlices, statusWords, type PipelineSlice } from "./casePipeline";

const SHORT_LABEL: Record<PipelineSlice["key"], string> = {
  resolved: "resolved",
  in_progress: "in progress",
  not_started: "not started",
};

export function CasePipelineCard({
  counts,
  onOpen,
  style,
}: {
  counts: Record<string, number> | undefined | null;
  /** Open the Cases page filtered to these statuses. */
  onOpen?: (statuses: string[]) => void;
  style?: CSSProperties;
}) {
  const slices = pipelineSlices(counts);
  return (
    <DonutCard
      icon={<Layers className="w-4 h-4 text-white" />}
      title="Case Pipeline"
      subtitle="Every case held by your agents"
      loading={counts == null}
      emptyMessage="No cases held by your agents yet."
      style={style}
      slices={slices.map((s) => ({
        key: s.key,
        label: s.label,
        colour: s.colour,
        count: s.count,
        shortLabel: SHORT_LABEL[s.key],
        // Each status inside the slice is its own link into the Cases page,
        // which filters on ONE status server-side.
        detail: s.parts.length > 0 ? (
          <div className="flex flex-wrap gap-x-2 gap-y-0.5">
            {s.parts.map((p) => (
              <button
                key={p.status}
                type="button"
                onClick={() => onOpen?.([p.status])}
                className="text-[11px] hover:underline"
                style={{ color: "#6B6D76" }}
                title={`Open Cases filtered to ${statusWords(p.status)}`}
              >
                {statusWords(p.status)} <span className="tabular-nums" style={{ color: "#1C1C1F" }}>{p.count}</span>
              </button>
            ))}
          </div>
        ) : undefined,
      }))}
    />
  );
}
