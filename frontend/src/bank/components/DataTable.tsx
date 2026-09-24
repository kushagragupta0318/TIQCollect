// Command Center's analytics table (PortfolioAnalytics.jsx:137-175, spec §4.2),
// made data-driven. Class strings verbatim — including `pb-2.5`, `py-3`,
// `font-medium` and the accent row hover, which do NOT render: CC's default
// table rules outrank them (py 10px, th pb 12px / 600, hover muted/30 — spec
// §0.3), and bank.css ports those rules so the same thing happens here.
import type { CSSProperties, ReactNode } from "react";

export interface DataColumn<Row> {
  key: string;
  header: string;
  /** Cell content. Defaults to `String(row[key])`. */
  render?: (row: Row) => ReactNode;
  /** Extra classes on the td (e.g. "font-bold text-foreground", "w-32"). */
  className?: string;
  /** Inline colour, for money-in / roll figures (`BRAND.success`, …). */
  color?: (row: Row) => string | undefined;
  align?: "left" | "right" | "center";
}

export interface DataTableProps<Row> {
  columns: DataColumn<Row>[];
  rows: Row[];
  rowKey: (row: Row) => string;
  /** When set, rows are clickable and open the drill (`cursor-pointer hover:bg-accent/50`). */
  onRowClick?: (row: Row) => void;
  /** CC uses 620–640px on the wide tables; 0 for none. */
  minWidth?: number;
  /** "roomy" = the ladder's `py-3`; "compact" = the product and state tables' `py-2.5`. */
  density?: "roomy" | "compact";
}

const ALIGN: Record<NonNullable<DataColumn<unknown>["align"]>, string> = {
  left: "",
  right: "text-right",
  center: "text-center",
};

export function DataTable<Row>({
  columns,
  rows,
  rowKey,
  onRowClick,
  minWidth = 620,
  density = "roomy",
}: DataTableProps<Row>) {
  const tableStyle: CSSProperties | undefined = minWidth ? { minWidth } : undefined;
  const pad = density === "roomy" ? "py-3" : "py-2.5";
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[11px] text-left" style={tableStyle}>
        <thead>
          <tr className="border-b border-border">
            {columns.map((c) => (
              <th
                key={c.key}
                className={`pb-2.5 pr-3 font-medium text-muted-foreground uppercase text-[9.5px] tracking-wide ${ALIGN[c.align ?? "left"]}`}
              >
                {c.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr
              key={rowKey(row)}
              onClick={onRowClick ? () => onRowClick(row) : undefined}
              className={
                onRowClick
                  ? "border-b border-border/30 cursor-pointer hover:bg-accent/50 transition-colors"
                  : "border-b border-border/30"
              }
            >
              {columns.map((c, i) => {
                const color = c.color?.(row);
                const last = i === columns.length - 1;
                return (
                  <td
                    key={c.key}
                    className={`${pad} ${last ? "" : "pr-3"} ${ALIGN[c.align ?? "left"]} ${c.className ?? ""}`}
                    style={color ? { color } : undefined}
                  >
                    {c.render ? c.render(row) : String((row as Record<string, unknown>)[c.key] ?? "")}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
