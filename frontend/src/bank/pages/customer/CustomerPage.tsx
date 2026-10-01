// One borrower, as the bank sees them (C08). Reached from a Placements row.
//
// The rule the page is built around: NOTHING is summed across cases. The header
// identifies the borrower; every figure belongs to one case and sits inside that
// case's row. A borrower with two loans at two agencies sees two rows, each with
// its own target, collection, model score and history.
import { useMemo, useState } from "react";
import { useParams, useSearchParams } from "react-router";
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, ChevronDown, ChevronRight, Phone, User } from "lucide-react";
import { ExecutiveHeader, PageFailure, PageLoading, PageRoot } from "../../components/PageTemplate";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../ui/card";
import { Badge } from "../../ui/badge";
import { Button } from "../../ui/button";
import { Bar100 } from "../../components/analytics";
import { LoanExplanationDialog } from "../models/LoanExplanationDialog";
import {
  getCaseTimeline, getCustomer360, getCustomer360ForLoan, type CaseRow, type Customer360,
} from "@/api/bankCustomer";
import { errorDetail, errorStatus } from "@/lib/apiError";
import {
  caseSummary, collectedPct, contactFlags, day, dayTime, initialCaseId, inr, remainingOnCase,
  timelineLines, words,
} from "./customerLogic";

function Fact({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div>
      <dt className="text-[11px] text-muted-foreground">{label}</dt>
      <dd className="text-[12.5px] font-medium text-foreground">{value}</dd>
    </div>
  );
}

function Timeline({ caseId }: { caseId: string }) {
  const q = useQuery({ queryKey: ["bank-case-timeline", caseId], queryFn: () => getCaseTimeline(caseId) });
  if (q.isPending) return <p className="py-4 text-[12px] text-muted-foreground">Loading the history…</p>;
  if (q.isError) {
    return <p className="py-4 text-[12px] text-destructive">{errorDetail(q.error, "The history could not be loaded.")}</p>;
  }
  const lines = timelineLines(q.data.entries);
  if (!lines.length) return <p className="py-4 text-[12px] text-muted-foreground">Nothing has happened on this case yet.</p>;
  return (
    <div className="space-y-2">
      <ol className="space-y-2.5">
        {lines.map((l) => (
          <li key={l.key} className="flex gap-3 text-[12.5px]">
            <span className="w-[108px] shrink-0 text-[11px] tabular-nums text-muted-foreground">{l.at}</span>
            <Badge variant="outline" className="h-fit shrink-0">{l.kindLabel}</Badge>
            <span className="text-foreground">{l.summary}</span>
            {l.hasEvidence && (
              <span className="shrink-0 text-[11px] text-muted-foreground" title="Photos or a recording were captured on this visit">
                evidence captured
              </span>
            )}
          </li>
        ))}
      </ol>
      {q.data.truncated && (
        <p className="text-[11px] text-muted-foreground">
          Showing the most recent {q.data.limit}. Older entries exist on this case.
        </p>
      )}
    </div>
  );
}

function CaseCard({ row, open, onToggle, onExplain }: {
  row: CaseRow; open: boolean; onToggle: () => void; onExplain: () => void;
}) {
  const pct = collectedPct(row);
  return (
    <div className="rounded-[16px] border border-border/50 bg-card">
      <button type="button" onClick={onToggle} aria-expanded={open}
              className="flex w-full items-start justify-between gap-3 px-5 py-4 text-left">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-[14px] font-semibold text-foreground">{row.case_number}</span>
            <Badge variant="outline">{words(row.status)}</Badge>
            {row.is_escalated && <Badge variant="warning">Escalated</Badge>}
            {row.has_open_dispute && <Badge variant="destructive">Open dispute</Badge>}
          </div>
          <p className="mt-1 text-[12px] text-muted-foreground">
            {row.agency_name ?? "No agency"}
            {row.placed_on ? ` · placed ${day(row.placed_on)}` : " · not placed"}
            {row.agent_name ? ` · ${row.agent_name}` : ""}
          </p>
        </div>
        {open ? <ChevronDown className="mt-1 size-4 shrink-0" /> : <ChevronRight className="mt-1 size-4 shrink-0" />}
      </button>

      {open && (
        <div className="space-y-5 border-t border-border/50 px-5 py-4">
          <dl className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <Fact label="Product" value={words(row.loan_type)} />
            <Fact label="Days past due" value={`${row.dpd ?? "—"} (${words(row.dpd_bucket)})`} />
            <Fact label="Outstanding on the loan" value={inr(row.total_outstanding)} />
            <Fact label="Overdue" value={inr(row.overdue_amount)} />
            <Fact label="Target for this case" value={inr(row.target_amount)} />
            <Fact label="Collected (verified)" value={inr(row.collected_verified)} />
            <Fact label="Still to collect" value={inr(remainingOnCase(row))} />
            <Fact label="Last contact" value={row.last_contact_at ? dayTime(row.last_contact_at) : "Never"} />
          </dl>

          {pct != null && (
            <div>
              <div className="mb-1 flex justify-between text-[11px] text-muted-foreground">
                <span>Collected against this case's target</span><span className="tabular-nums">{pct}%</span>
              </div>
              <Bar100 pct={pct} />
            </div>
          )}

          <div className="flex flex-wrap items-center gap-3 text-[12px]">
            {row.latest_band ? (
              <>
                <span className="text-muted-foreground">Model band</span>
                <Badge variant="softPrimary">{row.latest_band}</Badge>
                <span className="text-muted-foreground">
                  {row.latest_model_version ? `recovery_risk ${row.latest_model_version}` : ""}
                </span>
                <button type="button" onClick={onExplain} className="font-medium text-primary hover:underline">
                  Why this score
                </button>
              </>
            ) : (
              <span className="text-muted-foreground">No model score on this case yet.</span>
            )}
          </div>

          {row.active_ptp_date && (
            <p className="text-[12.5px]">
              <span className="text-muted-foreground">Active promise: </span>
              {inr(row.active_ptp_amount)} by {day(row.active_ptp_date)}
            </p>
          )}

          <section>
            <h3 className="mb-2 text-[13px] font-semibold text-foreground">History</h3>
            <Timeline caseId={row.case_id} />
          </section>
        </div>
      )}
    </div>
  );
}

function Page({ data, focusLoanId }: { data: Customer360; focusLoanId: string | null }) {
  const h = data.customer;
  const flags = contactFlags(h);
  const [openCase, setOpenCase] = useState<string | null>(() => initialCaseId(data.cases, focusLoanId));
  const [explainLoan, setExplainLoan] = useState<{ id: string; label: string } | null>(null);
  const summary = useMemo(() => caseSummary(data.cases, data.loans_without_cases.length), [data]);

  return (
    <PageRoot>
      <ExecutiveHeader
        title={h.full_name}
        meta={["Borrower", summary]}
        scopeNote="Every case this borrower has with your bank. Figures belong to the case they sit in; nothing is combined across cases."
      />

      {data.region_limited && (
        <p role="note" className="rounded-[12px] border border-border bg-muted/40 px-4 py-2.5 text-[12.5px] text-muted-foreground">
          Your access is limited to one region, so this borrower may hold loans you cannot see here.
        </p>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2"><User className="size-5 text-primary" />Borrower</CardTitle>
          {flags.length > 0 && (
            <CardDescription className="flex flex-wrap items-center gap-2 pt-1">
              <AlertTriangle className="size-4 text-destructive" aria-hidden="true" />
              {flags.map((f) => <Badge key={f} variant="destructive">{f}</Badge>)}
            </CardDescription>
          )}
        </CardHeader>
        <CardContent>
          <dl className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <Fact label="Phone" value={<span className="inline-flex items-center gap-1.5"><Phone className="size-3.5" />{h.phone_primary ?? "—"}</span>} />
            <Fact label="Address" value={[h.address_line1, h.city, h.state, h.pincode].filter(Boolean).join(", ") || "—"} />
            <Fact label="PAN" value={h.pan_masked ?? "—"} />
            <Fact label="Aadhaar" value={h.aadhaar_masked ?? "—"} />
            <Fact label="Preferred language" value={words(h.language_preference)} />
          </dl>
          <p className="mt-3 text-[11px] text-muted-foreground">
            PAN and Aadhaar are stored masked; the full numbers are not held in this portal.
          </p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Cases</CardTitle>
          <CardDescription>
            One row per case. A borrower with two loans has two cases, possibly at two agencies, and each keeps its
            own target, collection and model score.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {data.cases.length === 0 && (
            <p className="text-[12.5px] text-muted-foreground">This borrower has no case yet.</p>
          )}
          {data.cases.map((row) => (
            <CaseCard
              key={row.case_id}
              row={row}
              open={openCase === row.case_id}
              onToggle={() => setOpenCase((cur) => (cur === row.case_id ? null : row.case_id))}
              onExplain={() => setExplainLoan({ id: row.loan_id, label: row.case_number })}
            />
          ))}
        </CardContent>
      </Card>

      {data.loans_without_cases.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Loans never placed</CardTitle>
            <CardDescription>These loans have never been placed with an agency, so they have no case or history.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-2">
            {data.loans_without_cases.map((l) => (
              <div key={l.loan_id} className="flex flex-wrap items-center justify-between gap-2 rounded-[12px] border border-border/50 px-4 py-3 text-[12.5px]">
                <span className="font-medium text-foreground">{l.loan_account_number}</span>
                <span className="text-muted-foreground">{words(l.loan_type)} · {l.dpd} days past due</span>
                <span className="tabular-nums">{inr(l.total_outstanding)} outstanding · {inr(l.overdue_amount)} overdue</span>
              </div>
            ))}
          </CardContent>
        </Card>
      )}

      <LoanExplanationDialog
        loanId={explainLoan?.id ?? null}
        loanLabel={explainLoan?.label ?? ""}
        onClose={() => setExplainLoan(null)}
      />
    </PageRoot>
  );
}

export default function CustomerPage() {
  const { customerId } = useParams();
  const [params] = useSearchParams();
  const loanId = params.get("loan");
  // Reached either by customer (the canonical page) or by loan (from Placements,
  // which lists loans): the loan route resolves to the same borrower.
  const byLoan = !customerId && !!loanId;
  const q = useQuery({
    queryKey: byLoan ? ["bank-customer-360-loan", loanId] : ["bank-customer-360", customerId],
    queryFn: () => (byLoan ? getCustomer360ForLoan(loanId!) : getCustomer360(customerId!)),
    enabled: !!(customerId || loanId),
  });

  const header = <ExecutiveHeader title="Borrower" meta={["Loading…"]} />;
  if (!customerId && !loanId) {
    return <PageRoot>{header}<PageFailure>No borrower was named.</PageFailure></PageRoot>;
  }
  if (q.isPending) return <PageRoot>{header}<PageLoading label="Loading the borrower…" /></PageRoot>;
  if (q.isError) {
    const status = errorStatus(q.error);
    return (
      <PageRoot>
        {header}
        <PageFailure>
          {status === 404
            ? "No such borrower, or they are outside what you can see."
            : errorDetail(q.error, "The borrower could not be loaded.")}
        </PageFailure>
        {status !== 404 && (
          <div className="flex justify-center"><Button variant="outline" onClick={() => q.refetch()}>Retry</Button></div>
        )}
      </PageRoot>
    );
  }
  return <Page data={q.data} focusLoanId={loanId} />;
}
