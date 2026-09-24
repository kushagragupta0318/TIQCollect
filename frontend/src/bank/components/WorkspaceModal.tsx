// Command Center `components/WorkspaceModal.jsx`, ported to TypeScript (spec §4.5).
// The chrome is CC's, verbatim — frame, header, Save / Download / Share, Run
// Analysis, the toast. What CC hard-wires is now a prop: the body is
// `children` (CC switched over ten built-in workshops), the mode line and the
// Run Analysis button are the caller's, and Download asks the caller for its
// rows. The portal target is the `.bank-root` container (spec §7.3).
import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { CheckCircle, Download, Play, RefreshCw, Save, Settings, Share2, X } from "lucide-react";
import { getBankPortalRoot } from "../lib/portal";
import { useModalFocus } from "../lib/useModalFocus";
import { SampleDataNote } from "./SampleDataNote";
import { sampleFilename, scenarioStorageKey, toCsv, type WorkspaceExport, type WorkspaceTool } from "./workspace";
import { SAMPLE_DATA_LABEL } from "./sampleData";

export interface WorkspaceModalProps {
  tool: WorkspaceTool;
  /** The line under the title: what the panel actually is (CC's PANEL_MODE). */
  mode?: string;
  /** Recompute handler; the Run Analysis button shows only when this is set. */
  onRunAnalysis?: () => void;
  /** Rows to export as CSV, or null when the tool has nothing exportable. */
  getExport?: () => WorkspaceExport | null;
  /** Extra state to keep with a saved scenario. */
  getScenarioState?: () => unknown;
  /** Mark the workspace as invented figures (the gallery). It portals out of the page, so it needs its own marker. */
  sampleData?: boolean;
  onClose: () => void;
  children?: ReactNode;
}

export function WorkspaceModal({
  tool,
  mode = "Decision workspace",
  onRunAnalysis,
  getExport,
  getScenarioState,
  sampleData = false,
  onClose,
  children,
}: WorkspaceModalProps) {
  const [toastMsg, setToastMsg] = useState<string | null>(null);
  const [runLoading, setRunLoading] = useState(false);
  const [saveIcon, setSaveIcon] = useState<"save" | "check">("save");
  const [shareIcon, setShareIcon] = useState<"share" | "check">("share");
  const timers = useRef<number[]>([]);
  // Focus moves in, Tab stays in, Escape closes, focus returns, page scroll
  // locks (lib/useModalFocus.ts). CC had no Escape and no scroll lock here.
  const frameRef = useRef<HTMLDivElement>(null);
  useModalFocus(frameRef, onClose);
  const titleId = useId();

  useEffect(() => {
    const pending = timers.current;
    return () => pending.forEach((t) => window.clearTimeout(t));
  }, []);

  const later = (fn: () => void, ms: number) => {
    timers.current.push(window.setTimeout(fn, ms));
  };

  const showToast = (msg: string) => {
    setToastMsg(msg);
    later(() => setToastMsg(null), 3000);
  };

  const handleRunAnalysis = () => {
    setRunLoading(true);
    later(() => {
      onRunAnalysis?.();
      setRunLoading(false);
      showToast("Analysis complete — scenario refreshed");
    }, 450);
  };

  const handleSaveScenario = () => {
    const payload = { panel: tool.panel, label: tool.label, savedAt: new Date().toISOString(), state: getScenarioState?.() ?? {} };
    try {
      localStorage.setItem(scenarioStorageKey(tool.panel), JSON.stringify(payload));
      setSaveIcon("check");
      showToast(`Scenario saved — "${tool.label}"`);
      later(() => setSaveIcon("save"), 2000);
    } catch {
      showToast("Save failed — localStorage unavailable");
    }
  };

  const handleDownload = () => {
    const data = getExport?.();
    if (!data) {
      showToast("No exportable data for this tool");
      return;
    }
    // A sample export is labelled in its first line and its file name.
    const filename = sampleData ? sampleFilename(data.filename) : data.filename;
    const csv = toCsv(data, { banner: sampleData ? SAMPLE_DATA_LABEL : undefined });
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    showToast(`Exported ${filename}`);
  };

  const handleShare = () => {
    let saved: { savedAt?: string } | null;
    try {
      const raw = localStorage.getItem(scenarioStorageKey(tool.panel));
      saved = raw ? JSON.parse(raw) : null;
    } catch {
      saved = null; // storage blocked or a corrupt entry: share the unsaved summary
    }
    const summary = saved?.savedAt
      ? `Command Center — ${tool.label}\nSaved: ${new Date(saved.savedAt).toLocaleString()}`
      : `Command Center — ${tool.label} (unsaved simulation, no scenario saved yet)`;
    const shared = sampleData ? `${summary}\n${SAMPLE_DATA_LABEL}` : summary;
    if (!navigator.clipboard) {
      showToast("Clipboard unavailable");
      return;
    }
    navigator.clipboard
      .writeText(shared)
      .then(() => {
        setShareIcon("check");
        showToast("Scenario summary copied to clipboard");
        later(() => setShareIcon("share"), 2000);
      })
      .catch(() => showToast("Clipboard unavailable"));
  };

  return createPortal(
    <div
      className="fixed inset-0 z-[9999] flex items-center justify-center p-6 sm:p-12 animate-fade-in"
      style={{ backgroundColor: "rgba(0,0,0,0.4)", backdropFilter: "blur(4px)" }}
    >
      <div
        ref={frameRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="w-full h-full max-w-screen-2xl max-h-[90vh] bg-background border border-border/60 flex flex-col overflow-hidden animate-scale-in"
        style={{ borderRadius: "26px", boxShadow: "0 24px 48px -12px rgba(22,119,255,0.15), 0 0 0 1px rgba(0,0,0,0.04)" }}
      >
        {/* Header */}
        <div className="px-6 py-4 flex items-center justify-between border-b border-border/40 bg-white shrink-0">
          <div className="flex items-center gap-4">
            <div className="icon-circle bg-primary" style={{ boxShadow: "0 4px 10px rgba(22,119,255,0.3)" }}>
              <Settings size={18} />
            </div>
            <div>
              <h2 id={titleId} className="text-lg font-extrabold text-foreground tracking-tight">{tool.label || "Workspace"}</h2>
              <p className="text-[11px] font-semibold text-muted-foreground">{mode}</p>
            </div>
            {sampleData && <SampleDataNote />}
          </div>
          <div className="flex items-center gap-2">
            <button onClick={handleSaveScenario} title="Save scenario to browser storage" className="btn-secondary flex items-center gap-1.5 text-xs">
              {saveIcon === "check" ? <CheckCircle size={13} className="text-success" /> : <Save size={13} />}
              Save Scenario
            </button>
            <button onClick={handleDownload} title="Export data as CSV" aria-label="Export data as CSV" className="btn-secondary px-3">
              <Download size={14} />
            </button>
            <button onClick={handleShare} title="Copy scenario summary" aria-label="Copy scenario summary" className="btn-secondary px-3">
              {shareIcon === "check" ? <CheckCircle size={14} className="text-success" /> : <Share2 size={14} />}
            </button>
            {onRunAnalysis && (
              <button
                onClick={handleRunAnalysis}
                disabled={runLoading}
                className="btn-primary ml-2 flex items-center gap-1.5 min-w-[120px] justify-center text-xs"
              >
                {runLoading ? (
                  <>
                    <RefreshCw size={13} className="animate-spin" /> Recomputing…
                  </>
                ) : (
                  <>
                    <Play size={13} fill="currentColor" /> Run Analysis
                  </>
                )}
              </button>
            )}
            <div className="w-[1px] h-6 bg-border mx-1" />
            <button
              onClick={onClose}
              aria-label="Close workspace"
              className="w-8 h-8 flex items-center justify-center rounded-full bg-muted/40 hover:bg-muted text-muted-foreground transition-colors"
            >
              <X size={16} />
            </button>
          </div>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto bg-muted/5 relative p-6">{children}</div>

        {/* Toast */}
        {toastMsg && (
          <div role="status" className="absolute bottom-5 right-5 bg-foreground text-primary-foreground text-xs font-bold px-4 py-2.5 rounded-lg shadow-2xl z-[200] flex items-center gap-2 border border-border animate-slide-in">
            <CheckCircle size={14} className="text-success" />
            {toastMsg}
          </div>
        )}
      </div>
    </div>,
    getBankPortalRoot(),
  );
}
