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

/**
 * CSV the way CC builds it — headers bare, every value quoted — except that an
 * embedded quote is doubled (RFC 4180). CC's `"${v}"` wrote `"a"b"` for a
 * value containing a quote, which splits the column on import. A borrower or
 * agency name can contain one; the fix changes no pixel.
 */
export function toCsv({ headers, rows }: Pick<WorkspaceExport, "headers" | "rows">): string {
  const cell = (v: string | number) => `"${String(v).replace(/"/g, '""')}"`;
  return [headers.join(","), ...rows.map((r) => r.map(cell).join(","))].join("\n");
}

/** localStorage key for a saved scenario. CC's is `decisionCenter:scenario:<panel>`. */
export const scenarioStorageKey = (panel: string): string => `bankWorkspace:scenario:${panel}`;
