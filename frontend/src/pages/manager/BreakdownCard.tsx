// Where the arrears sit, by branch, city or product (known issue 8). The
// columns were always in the data; the page only ever grouped by agent, DPD
// bucket and month, so a manager could not ask which branch the money is stuck
// in. The DPD card next door keeps its own component: its buckets have a fixed
// severity order and labels, these dimensions are open sets ordered by money.
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { getTeamBreakdown, type BreakdownDimension } from "@/api/manager";

const EASE = "cubic-bezier(.22,.61,.36,1)";

const TABS: { id: Exclude<BreakdownDimension, "bucket">; label: string }[] = [
  { id: "branch", label: "Branch" },
  { id: "city", label: "City" },
  { id: "product", label: "Product" },
];

// A branch list is long: 522 on the demo book. Showing every row would bury
// the point, which is where the money is, so the card shows the leaders and
// says how many it left out rather than pretending there are no more.
const TOP_N = 8;

function label(dimension: BreakdownDimension, key: string): string {
  if (dimension !== "product") return key;
  // Matches the bank filter bar's Python .title(): lowercase first, so
  // CREDIT_CARD reads "Credit Card" rather than "CREDIT CARD".
  return key.toLowerCase().replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

export function BreakdownCard({ selMonth, apiMonth, barReady }: {
  selMonth?: string | null;
  apiMonth?: string | null;
  barReady: boolean;
}) {
  const [dimension, setDimension] = useState<Exclude<BreakdownDimension, "bucket">>("branch");
  const q = useQuery({
    queryKey: ["manager", "team", "breakdown", dimension, apiMonth ?? null],
    queryFn: () => getTeamBreakdown(dimension, apiMonth ?? undefined),
    staleTime: 0,
  });
  const rows = q.data ?? [];
  const shown = rows.slice(0, TOP_N);
  const hidden = rows.length - shown.length;
  const widest = Math.max(1, ...shown.map((r) => r.collected_lakhs));

  return (
    <div className="card p-4">
      <div className="flex items-start justify-between gap-3 mb-1">
        <h2 className="text-sm font-bold" style={{ color: "#1C1C1F" }}>Where the arrears sit</h2>
        <div className="flex rounded-lg overflow-hidden" style={{ border: "1px solid #E4E6EC" }}>
          {TABS.map((t) => (
            <button
              key={t.id}
              type="button"
              onClick={() => setDimension(t.id)}
              aria-pressed={dimension === t.id}
              className="text-[11px] font-semibold px-2.5 py-1"
              style={dimension === t.id
                ? { background: "#1677FF", color: "#FFFFFF" }
                : { background: "#FFFFFF", color: "#6B6D76" }}
            >
              {t.label}
            </button>
          ))}
        </div>
      </div>
      <p className="text-xs mb-4" style={{ color: "#6B6D76" }}>
        Agency-wide
        {selMonth
          ? <> · <span className="font-semibold" style={{ color: "#1677FF" }}>{selMonth}</span> payments</>
          : " · all months"}
        {" · by collection"}
      </p>

      {q.isFetching && rows.length === 0 ? (
        <div className="space-y-3">
          {[0, 1, 2, 3].map((i) => (
            <div key={i} className="h-7 rounded-lg animate-pulse" style={{ background: "#EFF0F4" }} />
          ))}
        </div>
      ) : q.isError ? (
        <p className="text-sm text-center py-8" style={{ color: "#94a3b8" }}>Could not load the breakdown.</p>
      ) : shown.length === 0 ? (
        <p className="text-sm text-center py-8" style={{ color: "#94a3b8" }}>No case data available.</p>
      ) : (
        <>
          <div className="space-y-3">
            {shown.map((r, i) => (
              <div
                key={r.key}
                className="row-stat -mx-2 px-2 py-1 rounded-xl"
                style={{ animation: `enter 380ms ${EASE} ${i * 60}ms both` }}
              >
                <div className="flex justify-between text-sm mb-1.5 gap-3">
                  <span className="font-semibold truncate" style={{ color: "#1C1C1F" }}>
                    {label(dimension, r.key)}
                  </span>
                  <div className="flex gap-3 text-right shrink-0">
                    <span style={{ color: "#6B6D76" }}>{r.case_count} cases</span>
                    <span className="font-semibold" style={{ color: "#1C1C1F" }}>₹{r.collected_lakhs.toFixed(1)}L</span>
                    <span className="font-bold w-10" style={{ color: "#6B6D76" }}>{r.collection_rate_pct.toFixed(0)}%</span>
                  </div>
                </div>
                {/* The bar is share of the LEADER's collection, not the rate:
                    this card answers "where is the money", and the rate is
                    already the number on the right. */}
                <div className="w-full rounded-full overflow-hidden" style={{ height: 9, background: "#EFF0F4" }}>
                  <div
                    className="h-full rounded-full"
                    style={{
                      width: barReady ? `${Math.max(2, (r.collected_lakhs / widest) * 100)}%` : "0%",
                      background: "#1677FF",
                      transition: `width 900ms ${i * 60}ms ${EASE}`,
                    }}
                  />
                </div>
              </div>
            ))}
          </div>
          {hidden > 0 && (
            <p className="text-[11px] mt-3" style={{ color: "#94a3b8" }}>
              Top {shown.length} of {rows.length} · {hidden} more not shown
            </p>
          )}
        </>
      )}
    </div>
  );
}
