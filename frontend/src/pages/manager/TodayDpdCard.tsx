// ─── CHANGELOG (prototype → product) ───────────────────────────────────────
// 2026-09-16 — NEW. "Today's Cases by DPD" on the overview.
//
//   Replaces "Portfolio by DPD Bucket", which showed the LIFETIME book — every
//   case the agents have ever held, resolved ones included, 883 of them — and
//   duplicated the Analytics page's "Collection by DPD Bucket" card row for
//   row. This is a different question: the 214 cases on TODAY'S beats, the
//   ones the team is actually going out to, and how old they are.
//
//   Same donut as the Analytics page's Case Pipeline (DonutCard), so the two
//   read as one family. Under each bucket: what is still collectable on those
//   cases — target less collected, the figure the planner works from — and a
//   link into the Cases page filtered to the bucket.
// ─────────────────────────────────────────────────────────────────────────────

import { CalendarClock } from "lucide-react";
import { DonutCard } from "./DonutCard";
import { shortMoney } from "@/lib/money";
import { todayDpdSlices, type TodayDpdRow } from "./todayDpd";

export function TodayDpdCard({
  rows,
  effectiveDate,
  onOpen,
}: {
  rows: readonly TodayDpdRow[] | undefined | null;
  /** The day the beats are for, ISO — shown so "today" is never ambiguous on
   *  seeded data where it trails the wall clock. */
  effectiveDate?: string;
  /** Open the Cases page filtered to this bucket. */
  onOpen?: (bucket: string) => void;
}) {
  const slices = todayDpdSlices(rows);
  const collectable = slices.reduce((t, s) => t + s.collectable_amount, 0);
  const dateWords = effectiveDate
    ? new Date(`${effectiveDate}T00:00:00`).toLocaleDateString("en-IN", { day: "numeric", month: "short" })
    : "today";

  return (
    <DonutCard
      icon={<CalendarClock className="w-4 h-4 text-white" />}
      title="Today's Cases by DPD"
      subtitle={rows ? `On today's routes (${dateWords}) · ${shortMoney(collectable)} collectable` : "Cases on today's routes, by how overdue they are"}
      loading={rows == null}
      emptyMessage="No routes planned for today yet."
      slices={slices.map((s) => ({
        key: s.bucket,
        label: s.label,
        colour: s.colour,
        count: s.case_count,
        shortLabel: s.shortLabel,
        detail: (
          <button
            type="button"
            onClick={() => onOpen?.(s.bucket)}
            className="text-[11px] hover:underline text-left"
            style={{ color: "#6B6D76" }}
            title={onOpen ? `Open Cases in ${s.label}` : undefined}
          >
            <span className="text-success-600 font-semibold tabular-nums">{shortMoney(s.collectable_amount)}</span> collectable
          </button>
        ),
      }))}
    />
  );
}
