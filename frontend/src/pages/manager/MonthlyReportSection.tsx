import { useEffect, useState } from "react";
import { toast } from "react-hot-toast";
import { Brain, Loader2 } from "lucide-react";
import { getMonthlyReport } from "@/api/manager";
import type { AgentPerfEntry } from "@/api/manager";
import { AiBadge } from "@/components/ui/AiBadge";
import { EASE } from "@/lib/motion";

// ── AI Monthly Report Section ─────────────────────────────────────────────────

export function MonthlyReportSection({ months, selectedAgent, preSelectedMonth }: { months: string[]; selectedAgent: AgentPerfEntry | null; preSelectedMonth?: string }) {
  const defaultMonth = months[months.length - 2] ?? months[months.length - 1] ?? "";
  const [month, setMonth] = useState(defaultMonth);
  const [loading, setLoading] = useState(false);
  const [report, setReport] = useState<{
    text: string; month: string; scope: string;
    aiGenerated?: boolean; aiStatus?: string; model?: string | null;
  } | null>(null);

  useEffect(() => { setReport(null); }, [selectedAgent?.agent_id]);
  useEffect(() => {
    if (preSelectedMonth && months.includes(preSelectedMonth)) {
      setMonth(preSelectedMonth);
      setReport(null);
    }
  }, [preSelectedMonth]);

  async function generate() {
    setLoading(true);
    try {
      const data = await getMonthlyReport(month, selectedAgent?.agent_id);
      setReport({
        text: data.report_text, month: data.month, scope: data.scope,
        aiGenerated: data.ai_generated, aiStatus: data.ai_status, model: data.ai_model,
      });
    } catch {
      toast.error("Could not generate report");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="card p-4 sm:p-6" style={{ animation: `enter 420ms ${EASE} 360ms both` }}>
      <div className="flex flex-wrap items-center gap-3 mb-4">
        <div className="w-8 h-8 rounded-xl flex items-center justify-center flex-shrink-0"
          style={{ background: "#EFF6FF" }}>
          <Brain className="w-4 h-4 text-primary" />
        </div>
        <div className="flex-1 min-w-0">
          <h2 className="text-base font-bold" style={{ color: "#1C1C1F" }}>AI Monthly Performance Report</h2>
          <p className="text-xs mt-0.5" style={{ color: "#6B6D76" }}>
            {selectedAgent ? `Scoped to ${selectedAgent.agent_name}` : "Agency-level summary"}
            {" · "}60–100 word AI performance brief
          </p>
        </div>
        <div className="flex items-center gap-2 flex-shrink-0 w-full sm:w-auto">
          <select
            value={month}
            onChange={(e) => { setMonth(e.target.value); setReport(null); }}
            aria-label="Report month"
            className="tap-target-h text-xs rounded-xl px-3 py-1.5 font-semibold flex-1 sm:flex-none min-w-0"
            style={{ border: "1px solid #EAEBEF", color: "#1C1C1F", background: "#F5F6F9", outline: "none" }}
          >
            {months.map((m) => (
              <option key={m} value={m}>
                {new Date(m + "-01").toLocaleDateString("en-IN", { month: "long", year: "numeric" })}
              </option>
            ))}
          </select>
          <button
            onClick={generate}
            disabled={loading}
            className="tap-target flex flex-shrink-0 items-center justify-center gap-1.5 rounded-control border border-primary bg-white px-4 py-1.5 text-xs font-semibold text-primary transition-opacity hover:bg-brand-100"
            style={{ opacity: loading ? 0.7 : 1 }}
          >
            {loading ? <Loader2 className="w-3 h-3 animate-spin" /> : <Brain className="w-3 h-3" />}
            Generate Report
          </button>
        </div>
      </div>

      {!report && !loading && (
        <div className="rounded-xl py-10 text-center" style={{ background: "#F5F6F9", border: "1.5px dashed #DDDFE8" }}>
          <Brain className="w-6 h-6 mx-auto mb-2" style={{ color: "#C4C6CF" }} />
          <p className="text-sm" style={{ color: "#94a3b8" }}>Choose a month and click Generate to get an AI summary</p>
        </div>
      )}

      {loading && (
        <div className="rounded-xl py-10 text-center"
          style={{ background: "#F7F8FA", border: "1px solid #ECEDF1" }}>
          <Loader2 className="w-5 h-5 mx-auto mb-2 animate-spin" style={{ color: "#7c3aed" }} />
          <p className="text-sm font-medium" style={{ color: "#7c3aed" }}>Analysing performance data…</p>
        </div>
      )}

      {report && !loading && (
        <div className="rounded-xl p-5" style={{ background: "#F7F8FA", border: "1px solid #ECEDF1" }}>
          <div className="flex items-center gap-2 mb-3 flex-wrap">
            <span className="text-xs px-2 py-0.5 rounded-full font-semibold"
              style={{ background: "rgba(124,58,237,0.10)", color: "#7c3aed" }}>
              {new Date(report.month + "-01").toLocaleDateString("en-IN", { month: "long", year: "numeric" })}
            </span>
            <span className="text-xs px-2 py-0.5 rounded-full font-semibold"
              style={{ background: "#F5F6F9", color: "#6B6D76" }}>
              {report.scope}
            </span>
            {/* The model that actually answered, not a name typed in once and
                left to rot. Shows the fallback badge instead when no model did. */}
            <span className="ml-auto flex items-center gap-2">
              <AiBadge aiGenerated={report.aiGenerated} status={report.aiStatus} />
              {report.aiGenerated !== false && report.model && (
                <span className="text-xs px-2 py-0.5 rounded-full font-semibold"
                  style={{ background: "rgba(124,58,237,0.06)", color: "#9333ea" }}>
                  {report.model}
                </span>
              )}
            </span>
          </div>
          <p className="text-sm leading-loose" style={{ color: "#1f2937", whiteSpace: "pre-line" }}>
            {report.text}
          </p>
          <p className="text-xs mt-3 pt-3" style={{ color: "#9ca3af", borderTop: "1px solid rgba(124,58,237,0.08)" }}>
            Eagle-view AI brief · Based on live performance data · For internal use only
          </p>
        </div>
      )}
    </div>
  );
}
