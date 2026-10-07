// AI Strategy › Board Reports (plan §7, task E10). Renders a board or
// agency-review pack from this bank's own figures — the same ones the
// Command Center screens show (compute_overview, agency_scorecard) — as
// PDF, PowerPoint or Excel, via E09's report engine (app/reports/*).
//
// This page computes nothing: report_templates.py on the backend reshapes
// figures that already exist, and the AI commentary inside the file is
// labelled there (Narrative.ai_generated), not here. The only state this
// page owns is which pack, period and format to ask for, and the links it
// has been handed back this session.
import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Download, FileText } from "lucide-react";
import { listAgencyDirectory } from "@/api/bank";
import {
  generateReport, listReportTypes, type GeneratedReport, type ReportFormat, type ReportPeriod,
  type ReportTemplate,
} from "@/api/bankReports";
import { errorDetail, errorStatus } from "@/lib/apiError";
import { AnalyticsLoading, Panel } from "../../components/analytics";
import { PageRoot, ToolHeader } from "../../components/PageTemplate";
import { Button } from "../../ui/button";
import { Label } from "../../ui/label";
import { Select } from "../../ui/select";

const PERIODS: { value: ReportPeriod; label: string }[] = [
  { value: "mtd", label: "Month to date" },
  { value: "l30", label: "Last 30 days" },
  { value: "qtd", label: "Quarter to date" },
  { value: "fytd", label: "Financial year to date" },
];

const FORMATS: { value: ReportFormat; label: string }[] = [
  { value: "pdf", label: "PDF" },
  { value: "pptx", label: "PowerPoint" },
  { value: "xlsx", label: "Excel" },
];

function historyKey(r: GeneratedReport): string {
  return `${r.report_id}-${r.format}-${r.sha256}`;
}

export default function BoardReportsPage() {
  const [template, setTemplate] = useState<ReportTemplate>("board");
  const [format, setFormat] = useState<ReportFormat>("pdf");
  const [period, setPeriod] = useState<ReportPeriod>("mtd");
  const [agencyId, setAgencyId] = useState("");
  const [history, setHistory] = useState<GeneratedReport[]>([]);

  const types = useQuery({ queryKey: ["bank", "reports", "types"], queryFn: listReportTypes, staleTime: Infinity });
  const current = types.data?.find((t) => t.template === template);
  const needsAgency = current?.requires_agency ?? template === "agency_review";

  const agencies = useQuery({
    queryKey: ["bank", "agencies", "directory"],
    queryFn: () => listAgencyDirectory(),
    enabled: needsAgency,
    staleTime: 5 * 60_000,
  });

  const gen = useMutation({
    mutationFn: () => generateReport({
      template, format,
      ...(template === "board" ? { period } : {}),
      ...(needsAgency ? { agency_id: agencyId } : {}),
    }),
    onSuccess: (report) => {
      setHistory((h) => [report, ...h]);
      window.open(report.url, "_blank", "noopener");
    },
  });

  const canGenerate = (!needsAgency || Boolean(agencyId)) && !gen.isPending;

  return (
    <PageRoot>
      <ToolHeader
        title="Board Reports"
        icon={FileText}
        description="Board and agency-review packs, built from the same figures the Command Center screens show, as PDF, PowerPoint or Excel."
      />

      <Panel title="What to generate" hint="Every figure in the pack comes from this bank's own book, never a placeholder">
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <div>
            <Label htmlFor="report-template">Report</Label>
            <Select
              id="report-template"
              value={template}
              onChange={(e) => { setTemplate(e.target.value as ReportTemplate); setAgencyId(""); gen.reset(); }}
            >
              {(types.data ?? []).map((t) => <option key={t.template} value={t.template}>{t.name}</option>)}
            </Select>
          </div>
          {template === "board" && (
            <div>
              <Label htmlFor="report-period">Period</Label>
              <Select id="report-period" value={period} onChange={(e) => setPeriod(e.target.value as ReportPeriod)}>
                {PERIODS.map((p) => <option key={p.value} value={p.value}>{p.label}</option>)}
              </Select>
            </div>
          )}
          {needsAgency && (
            <div>
              <Label htmlFor="report-agency">Agency</Label>
              <Select id="report-agency" value={agencyId} onChange={(e) => setAgencyId(e.target.value)}>
                <option value="">Select an agency…</option>
                {(agencies.data ?? []).map((a) => (
                  <option key={a.agency_id} value={a.agency_id}>{a.trade_name || a.legal_name}</option>
                ))}
              </Select>
            </div>
          )}
          <div>
            <Label htmlFor="report-format">Format</Label>
            <Select id="report-format" value={format} onChange={(e) => setFormat(e.target.value as ReportFormat)}>
              {FORMATS.map((f) => <option key={f.value} value={f.value}>{f.label}</option>)}
            </Select>
          </div>
        </div>

        {current && <p className="mt-3 text-[12px] text-muted-foreground">{current.description}</p>}
        {template === "agency_review" && (
          <p className="mt-1 text-[11px] text-muted-foreground">
            Agency Review always covers the latest available month; a custom date range is not wired for this pack yet.
          </p>
        )}

        <div className="mt-5 flex flex-wrap items-center gap-3">
          <Button onClick={() => gen.mutate()} disabled={!canGenerate}>
            <FileText className="size-4" />
            {gen.isPending ? "Generating…" : "Generate"}
          </Button>
          {needsAgency && !agencyId && (
            <span className="text-[11.5px] text-muted-foreground">Pick an agency first.</span>
          )}
        </div>

        {gen.isError && (
          <div
            role="alert"
            className="mt-4 rounded-card border border-destructive/40 bg-destructive/5 px-5 py-4 text-[12.5px] text-destructive"
          >
            {errorStatus(gen.error) === 403
              // The capability matrix lives in core/permissions.py; naming a role here would
              // be a second copy of it that could drift (same rule MonteCarloPage follows).
              ? "Generating or downloading a report needs a capability this role does not hold."
              : errorDetail(gen.error, "The report could not be generated.")}
          </div>
        )}
      </Panel>

      {types.isFetching && !types.data && <AnalyticsLoading label="Loading report types…" />}

      {history.length > 0 && (
        <Panel title="Generated this session" hint="Each link expires; generate again for a fresh one">
          <ul className="divide-y divide-border/50">
            {history.map((r) => (
              <li key={historyKey(r)} className="flex flex-wrap items-center justify-between gap-3 py-2.5 text-[12.5px]">
                <div>
                  <span className="font-medium text-foreground">{r.report_id}</span>
                  <span className="ml-2 uppercase text-muted-foreground">{r.format}</span>
                  <span className="ml-2 text-[11px] text-muted-foreground">
                    {(r.size_bytes / 1024).toFixed(0)} KB · link expires in {r.expires_minutes} min
                  </span>
                </div>
                <a
                  href={r.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1.5 text-primary hover:underline"
                >
                  <Download className="size-3.5" aria-hidden="true" />
                  Download
                </a>
              </li>
            ))}
          </ul>
        </Panel>
      )}
    </PageRoot>
  );
}
