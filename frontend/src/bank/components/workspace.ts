// The non-visual half of WorkspaceModal (WorkspaceModal.jsx:55-88): what a
// workspace exports and what it saves. Separate so it is testable.

export interface WorkspaceTool {
  /** Stable key — the contract between a catalogue card and the modal. */
  panel: string;
  label: string;
}

export interface WorkspaceExport {
  filename: string;
  headers: string[];
  rows: (string | number)[][];
}

// A text cell a spreadsheet would read as a formula (OWASP "CSV injection").
const FORMULA_START = /^[=+\-@\t\r]/;

/**
 * CSV the way CC builds it — every value quoted — with three changes, none of
 * which changes a pixel (recorded in UI spec §9):
 *
 *   - an embedded quote is doubled (RFC 4180). CC's `"${v}"` wrote `"a"b"`
 *     for a value with a quote in it, which splits the column on import;
 *   - headers are quoted and escaped too (CC joined them bare, so a comma in
 *     a header shifted every column after it);
 *   - a TEXT cell starting with = + - @ tab or CR is prefixed with `'`, so
 *     Excel and Sheets show it instead of evaluating it. The names in these
 *     exports come from agencies and borrowers, i.e. from outside the bank.
 *     Numbers are left alone: -0.4 is a number, not a formula.
 */
export function toCsv(
  { headers, rows }: Pick<WorkspaceExport, "headers" | "rows">,
  { banner }: { banner?: string } = {},
): string {
  const quote = (s: string) => `"${s.replace(/"/g, '""')}"`;
  const cell = (v: string | number) => (typeof v === "number" ? quote(String(v)) : quote(FORMULA_START.test(v) ? `'${v}` : v));
  // A sample export opens with a one-cell line saying so, above the header row.
  const lines = [headers.map(cell).join(","), ...rows.map((r) => r.map(cell).join(","))];
  return (banner ? [cell(banner), ...lines] : lines).join("\n");
}

/** A sample export's file name says so too: "SAMPLE-budget-optimizer.csv". */
export const sampleFilename = (filename: string): string => (filename.startsWith("SAMPLE-") ? filename : `SAMPLE-${filename}`);

/** localStorage key for a saved scenario. CC's is `decisionCenter:scenario:<panel>`. */
export const scenarioStorageKey = (panel: string): string => `bankWorkspace:scenario:${panel}`;
