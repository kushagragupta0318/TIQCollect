// Board Reports (E10): GET /bank/reports/types, POST /bank/reports/generate
// (backend/app/api/v1/endpoints/bank_reports.py, app/reports/*). Rendering
// returns a presigned, time-limited download link — never the file bytes
// inline — so a multi-megabyte PDF never sits in this axios response.
import api from "./axios";

export type ReportTemplate = "board" | "agency_review";
export type ReportFormat = "pdf" | "pptx" | "xlsx";
export type ReportPeriod = "mtd" | "l30" | "qtd" | "fytd" | "custom";

export interface ReportType {
  template: ReportTemplate;
  name: string;
  description: string;
  requires_agency: boolean;
}

export interface GenerateReportRequest {
  template: ReportTemplate;
  format: ReportFormat;
  /** Board pack only; agency_review always covers the latest available month. */
  period?: ReportPeriod;
  start?: string;
  end?: string;
  /** Required when the template's requires_agency is true. */
  agency_id?: string;
}

export interface GeneratedReport {
  report_id: string;
  format: ReportFormat;
  url: string;
  expires_minutes: number;
  size_bytes: number;
  sha256: string;
}

export async function listReportTypes(): Promise<ReportType[]> {
  return (await api.get<ReportType[]>("/bank/reports/types")).data;
}

export async function generateReport(body: GenerateReportRequest): Promise<GeneratedReport> {
  return (await api.post<GeneratedReport>("/bank/reports/generate", body)).data;
}
