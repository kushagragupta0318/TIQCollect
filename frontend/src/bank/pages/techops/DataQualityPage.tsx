// Tech Ops › Data Quality — the bank-feed's own staging and quarantine
// tables (lending.bank_feed_batches / bank_feed_rows, scripts/ingest_daily.py's
// one quarantine() call site), plus two structural sanity checks over the
// live book. Read-only, tenant-scoped, gated on data_quality.read. F10.
import { useQuery } from "@tanstack/react-query";
import { Database } from "lucide-react";

import { errorDetail } from "@/lib/apiError";
import {
  formatDate, formatDateTime, freshnessLabel, getDataQuality, reasonLabel, rupees,
  type QuarantinedRow,
} from "./dataQualityModel";
import { AnalyticsError, AnalyticsLoading, Panel, Tile } from "../../components/analytics";
import { DataTable, type DataColumn } from "../../components/DataTable";
import { PageRoot, ToolHeader } from "../../components/PageTemplate";

const QUARANTINE_COLUMNS: DataColumn<QuarantinedRow>[] = [
  { key: "loan_account_number", header: "Loan account", render: (r) => r.loan_account_number ?? "—" },
  { key: "customer_ref", header: "Customer", render: (r) => r.customer_ref ?? "—" },
  { key: "feed_type", header: "Feed" },
  { key: "business_date", header: "Business date", render: (r) => formatDate(r.business_date) },
  { key: "reason", header: "Reason", render: (r) => r.dq_errors.map((e) => reasonLabel(e.reason)).join(", ") },
  { key: "detail", header: "Detail", render: (r) => r.dq_errors.map((e) => e.detail).join("; ") },
  { key: "created_at", header: "Flagged", className: "text-right", render: (r) => formatDateTime(r.created_at) },
];

export function DataQualityPage() {
  const q = useQuery({ queryKey: ["bank", "data-quality"], queryFn: getDataQuality });
  const data = q.data;

  return (
    <PageRoot>
      <ToolHeader
        title="Data Quality"
        icon={Database}
        description="The bank feed's own staging and quarantine queue, plus a couple of structural checks over the live book."
      />

      {q.isError ? (
        <AnalyticsError>{errorDetail(q.error, "Could not load data quality.")}</AnalyticsError>
      ) : !data ? (
        <AnalyticsLoading />
      ) : (
        <>
          <Panel title="Feed freshness">
            {data.feed_freshness.length === 0 ? (
              <p className="text-sm text-muted-foreground py-6 text-center">No feed has ever been received for this bank.</p>
            ) : (
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
                {data.feed_freshness.map((f) => (
                  <div key={f.feed_type} className="rounded-[16px] border border-border/50 bg-card px-4 py-3.5">
                    <p className="text-[11px] font-semibold text-muted-foreground uppercase tracking-wide">{f.feed_type}</p>
                    <p className="mt-1 text-sm font-bold">{freshnessLabel(f)}</p>
                    <p className="text-[11px] text-muted-foreground mt-1">
                      Last business date {formatDate(f.last_business_date)} · received {formatDateTime(f.last_received_at)}
                    </p>
                    <p className="text-[11px] text-muted-foreground mt-1">
                      {f.rows_accepted ?? 0} accepted · {f.rows_quarantined ?? 0} quarantined · {f.rows_skipped ?? 0} skipped
                      {" "}of {f.rows_total ?? 0}
                    </p>
                  </div>
                ))}
              </div>
            )}
          </Panel>

          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Tile label="Quarantined rows"
                 value={data.quarantined_by_reason.reduce((s, r) => s + r.rows, 0).toLocaleString()} />
            <Tile label="Duplicate customer phones" value={data.duplicate_customer_phones.count.toLocaleString()} />
            <Tile label="Out-of-range loans" value={data.out_of_range_loans.count.toLocaleString()} />
          </div>

          <Panel title="Quarantined, by reason">
            {data.quarantined_by_reason.length === 0 ? (
              <p className="text-sm text-muted-foreground py-6 text-center">Nothing is quarantined.</p>
            ) : (
              <div className="space-y-2">
                {data.quarantined_by_reason.map((r) => (
                  <div key={r.reason} className="flex items-center justify-between text-sm">
                    <span className="font-medium">{reasonLabel(r.reason)}</span>
                    <span className="font-bold tabular-nums">{r.rows.toLocaleString()}</span>
                  </div>
                ))}
              </div>
            )}
          </Panel>

          <Panel title="Quarantined rows, most recent first">
            {data.quarantined_sample.length === 0 ? (
              <p className="text-sm text-muted-foreground py-6 text-center">Nothing is quarantined.</p>
            ) : (
              <DataTable columns={QUARANTINE_COLUMNS} rows={data.quarantined_sample}
                        rowKey={(r) => `${r.feed_type}-${r.business_date}-${r.row_no}`} minWidth={760} />
            )}
          </Panel>

          <Panel title="Duplicate customer phones" hint="More than one customer record shares the same phone number">
            {data.duplicate_customer_phones.sample.length === 0 ? (
              <p className="text-sm text-muted-foreground py-6 text-center">None found.</p>
            ) : (
              <DataTable
                columns={[
                  { key: "phone_primary", header: "Phone" },
                  { key: "customers", header: "Customers", className: "text-right" },
                ]}
                rows={data.duplicate_customer_phones.sample}
                rowKey={(r) => r.phone_primary}
              />
            )}
          </Panel>

          <Panel title="Out-of-range loans" hint="Overdue, or principal outstanding, exceeds the loan's own total outstanding">
            {data.out_of_range_loans.sample.length === 0 ? (
              <p className="text-sm text-muted-foreground py-6 text-center">None found.</p>
            ) : (
              <DataTable
                columns={[
                  { key: "loan_account_number", header: "Loan account" },
                  { key: "overdue_amount", header: "Overdue", className: "text-right", render: (r) => rupees(r.overdue_amount) },
                  { key: "outstanding_principal", header: "Principal", className: "text-right", render: (r) => rupees(r.outstanding_principal) },
                  { key: "total_outstanding", header: "Total outstanding", className: "text-right", render: (r) => rupees(r.total_outstanding) },
                ]}
                rows={data.out_of_range_loans.sample}
                rowKey={(r) => r.loan_id}
              />
            )}
          </Panel>
        </>
      )}
    </PageRoot>
  );
}

export default DataQualityPage;
