// Agencies › Placement (plan §6.3, task D08): filter the bank's loans, place a
// selection with one agency within its capacity and coverage, and recall a
// placement by hand. Every figure comes from /bank/placements; the gates are
// the server's (placement_service), shown here, never re-decided.
import { useMemo, useState, type ReactNode } from "react";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowRightLeft, RotateCcw } from "lucide-react";
import api from "@/api/axios";
import { errorDetail } from "@/lib/apiError";
import { AnalyticsError, AnalyticsLoading, AnalyticsTabBar, Panel, Tile } from "../components/analytics";
import { DataTable, type DataColumn } from "../components/DataTable";
import { PageRoot, ToolHeader } from "../components/PageTemplate";
import { SampleDataNote } from "../components/SampleDataNote";
import { rs } from "../theme/format";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { Dialog, DialogClose, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "../ui/dialog";
import { Input } from "../ui/input";
import { Label } from "../ui/label";
import { Select } from "../ui/select";
import { Textarea } from "../ui/textarea";
import {
  DPD_BUCKETS, EMPTY_FILTERS, LOAN_TYPES, MAX_BATCH, blockedByReason, headroomLabel, loanQuery, pageSelection,
  recallReasonError, regionOptions, selectable, toggle, togglePage, verdictText,
  type AgencyRoom, type BatchResult, type LoanFilters, type Page, type PlaceableLoan, type PlacementPage,
  type PlacementRow,
} from "./placementModel";

const PAGE_SIZE = 50;
type Tab = "place" | "placements";
const TABS = [{ id: "place", label: "Place loans" }, { id: "placements", label: "Placements" }] as const;

export function BankPlacementPage() {
  const [tab, setTab] = useState<Tab>("place");
  return (
    <PageRoot>
      <ToolHeader
        title="Placement"
        icon={ArrowRightLeft}
        description="Place delinquent loans with an agency. Each loan is checked against the agency's contract: in force, product and bucket authorised, territory covered, room under its cap."
      />
      <AnalyticsTabBar tabs={TABS} active={tab} onChange={setTab} />
      {tab === "place" ? <PlaceLoans /> : <Placements />}
    </PageRoot>
  );
}

// ── Place loans ──────────────────────────────────────────────────────────────

function PlaceLoans() {
  const qc = useQueryClient();
  const [filters, setFilters] = useState<LoanFilters>(EMPTY_FILTERS);
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [agencyId, setAgencyId] = useState("");
  const [preview, setPreview] = useState<BatchResult | null>(null);
  const [result, setResult] = useState<BatchResult | null>(null);

  const agencies = useQuery({
    queryKey: ["bank", "placements", "agencies"],
    queryFn: async () => (await api.get<{ on: string; items: AgencyRoom[] }>("/bank/placements/agencies")).data,
  });
  const params = loanQuery(filters, page, PAGE_SIZE).toString();
  const loans = useQuery({
    queryKey: ["bank", "placements", "loans", params],
    queryFn: async () => (await api.get<Page<PlaceableLoan>>(`/bank/placements/loans?${params}`)).data,
    placeholderData: keepPreviousData,
  });

  const body = () => ({ agency_id: agencyId, loan_ids: [...selected] });
  const previewM = useMutation({
    mutationFn: async () => (await api.post<BatchResult>("/bank/placements/preview", body())).data,
    onSuccess: setPreview,
  });
  const applyM = useMutation({
    mutationFn: async () => (await api.post<BatchResult>("/bank/placements", body())).data,
    onSuccess: (out) => {
      setPreview(null);
      setResult(out);
      setSelected(new Set());
      qc.invalidateQueries({ queryKey: ["bank", "placements"] });
    },
  });

  const setFilter = <K extends keyof LoanFilters>(k: K, v: LoanFilters[K]) => {
    setFilters((f) => ({ ...f, [k]: v }));
    setPage(1);
  };
  const regions = useMemo(() => regionOptions(agencies.data?.items ?? []), [agencies.data]);
  const chosen = agencies.data?.items.find((a) => a.agency_id === agencyId) ?? null;
  const rows = loans.data?.items ?? [];
  const pageSel = pageSelection(selected, rows);
  const pages = loans.data ? Math.max(1, Math.ceil(loans.data.total / PAGE_SIZE)) : 1;

  const columns: DataColumn<PlaceableLoan>[] = [
    {
      key: "sel", header: "",
      render: (l) => (
        <input
          type="checkbox"
          aria-label={`Select ${l.loan_account_number}`}
          disabled={!selectable(l) || (!selected.has(l.loan_id) && selected.size >= MAX_BATCH)}
          checked={selected.has(l.loan_id)}
          onChange={() => setSelected((s) => toggle(s, l.loan_id))}
        />
      ),
      className: "w-8",
    },
    { key: "loan_account_number", header: "Loan", className: "font-semibold text-foreground" },
    { key: "customer_name", header: "Borrower" },
    { key: "loan_type", header: "Product" },
    { key: "dpd", header: "DPD", align: "right", render: (l) => l.dpd.toLocaleString("en-IN") },
    { key: "overdue_amount", header: "Overdue", align: "right", render: (l) => rs(l.overdue_amount) },
    { key: "total_outstanding", header: "Outstanding", align: "right", render: (l) => rs(l.total_outstanding) },
    { key: "region", header: "Region", render: (l) => l.region_name ?? <span className="text-muted-foreground">No region</span> },
    {
      key: "placed", header: "Placed with",
      render: (l) => l.placed_with_agency_name ?? <span className="text-muted-foreground">Unplaced</span>,
    },
  ];

  return (
    <div className="space-y-6">
      <Panel title="Loans" hint={loans.data ? `${loans.data.total.toLocaleString("en-IN")} match` : undefined}>
        <div className="mb-5 grid grid-cols-2 gap-3 md:grid-cols-4 lg:grid-cols-7">
          <Field label="Region">
            <Select value={filters.region_id} onChange={(e) => setFilter("region_id", e.target.value)}>
              <option value="">All regions</option>
              {regions.map((r) => <option key={r.region_id} value={r.region_id}>{r.path.replaceAll(".", " › ")}</option>)}
            </Select>
          </Field>
          <Field label="Product">
            <Select value={filters.loan_type} onChange={(e) => setFilter("loan_type", e.target.value as LoanFilters["loan_type"])}>
              <option value="">All products</option>
              {LOAN_TYPES.map((t) => <option key={t} value={t}>{t.replaceAll("_", " ")}</option>)}
            </Select>
          </Field>
          <Field label="DPD bucket">
            <Select value={filters.dpd_bucket} onChange={(e) => setFilter("dpd_bucket", e.target.value as LoanFilters["dpd_bucket"])}>
              <option value="">All buckets</option>
              {DPD_BUCKETS.map((b) => <option key={b.code} value={b.code}>{b.label}</option>)}
            </Select>
          </Field>
          <Field label="DPD from">
            <Input inputMode="numeric" value={filters.dpd_min} onChange={(e) => setFilter("dpd_min", e.target.value)} />
          </Field>
          <Field label="DPD to">
            <Input inputMode="numeric" value={filters.dpd_max} onChange={(e) => setFilter("dpd_max", e.target.value)} />
          </Field>
          <Field label="Status">
            <Select value={filters.placed} onChange={(e) => setFilter("placed", e.target.value as LoanFilters["placed"])}>
              <option value="no">Unplaced</option>
              <option value="yes">Placed</option>
              <option value="any">All</option>
            </Select>
          </Field>
          <Field label="Loan account">
            <Input value={filters.search} maxLength={30} onChange={(e) => setFilter("search", e.target.value)} />
          </Field>
        </div>

        {loans.isLoading ? (
          <AnalyticsLoading label="Loading loans…" />
        ) : loans.isError ? (
          <AnalyticsError>{errorDetail(loans.error, "The loans could not be loaded.")}</AnalyticsError>
        ) : (
          <>
            <div className="mb-3 flex flex-wrap items-center justify-between gap-3 text-[11.5px] text-muted-foreground">
              <label className="flex items-center gap-2">
                <input
                  type="checkbox"
                  aria-label="Select this page"
                  checked={pageSel === "all"}
                  ref={(el) => { if (el) el.indeterminate = pageSel === "some"; }}
                  onChange={() => setSelected((s) => togglePage(s, rows))}
                />
                Select this page
              </label>
              <span>
                {selected.size.toLocaleString("en-IN")} selected{selected.size >= MAX_BATCH ? ` (the limit is ${MAX_BATCH} per batch)` : ""}
                {selected.size > 0 && (
                  <button type="button" className="ml-3 text-primary hover:underline" onClick={() => setSelected(new Set())}>
                    Clear
                  </button>
                )}
              </span>
            </div>
            <DataTable columns={columns} rows={rows} rowKey={(l) => l.loan_id} density="compact" minWidth={860} />
            <Pager page={page} pages={pages} onPage={setPage} />
          </>
        )}
      </Panel>

      <Panel title="Place with" hint={agencies.data ? `as of ${agencies.data.on}` : undefined}>
        {agencies.isError ? (
          <AnalyticsError>{errorDetail(agencies.error, "The agencies could not be loaded.")}</AnalyticsError>
        ) : (
          <div className="flex flex-col gap-4 md:flex-row md:items-end">
            <Field label="Agency" className="md:w-96">
              <Select value={agencyId} onChange={(e) => setAgencyId(e.target.value)}>
                <option value="">Choose an agency</option>
                {(agencies.data?.items ?? []).map((a) => (
                  <option key={a.agency_id} value={a.agency_id} disabled={!a.placeable}>
                    {a.name} · {headroomLabel(a)}
                  </option>
                ))}
              </Select>
            </Field>
            {chosen && (
              <p className="text-[11.5px] text-muted-foreground md:pb-2.5">
                Contract {chosen.contract_no ?? "—"} · covers{" "}
                {chosen.coverage.length ? chosen.coverage.map((r) => r.name).join(", ") : "no region (nothing can be placed)"}
              </p>
            )}
            <div className="md:ml-auto">
              <Button disabled={!agencyId || selected.size === 0 || previewM.isPending} onClick={() => previewM.mutate()}>
                {previewM.isPending ? "Checking…" : `Check ${selected.size.toLocaleString("en-IN")} loans`}
              </Button>
            </div>
          </div>
        )}
        {previewM.isError && <p role="alert" className="mt-3 text-[12px] text-destructive">{errorDetail(previewM.error, "The check failed.")}</p>}
      </Panel>

      {result && <BatchSummary title="Placed" result={result} onClose={() => setResult(null)} />}

      <Dialog open={preview !== null} onOpenChange={(o) => { if (!o) setPreview(null); }}>
        <DialogContent className="max-w-[720px]">
          <DialogHeader>
            <DialogTitle>Place with {chosen?.name ?? "agency"}</DialogTitle>
            <DialogDescription>
              Every loan was checked against the agency's contract today. Only loans that pass are placed; each opens an
              unassigned case for the agency.
            </DialogDescription>
          </DialogHeader>
          {preview && <VerdictBody result={preview} />}
          {applyM.isError && <p role="alert" className="px-6 text-[12px] text-destructive">{errorDetail(applyM.error, "Placement failed.")}</p>}
          <DialogFooter>
            <DialogClose onClose={() => setPreview(null)}>Cancel</DialogClose>
            <Button
              disabled={!preview || preview.counts.PLACED === 0 || applyM.isPending}
              onClick={() => applyM.mutate()}
            >
              {applyM.isPending ? "Placing…" : `Place ${(preview?.counts.PLACED ?? 0).toLocaleString("en-IN")} loans`}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function VerdictBody({ result }: { result: BatchResult }) {
  const blocked = blockedByReason(result.verdicts);
  return (
    <div className="space-y-4 overflow-y-auto px-6 pb-2">
      <div className="grid grid-cols-3 gap-3">
        <Tile label="Can be placed" value={result.counts.PLACED.toLocaleString("en-IN")} />
        <Tile label="Already with this agency" value={result.counts.KEPT.toLocaleString("en-IN")} />
        <Tile label="Blocked" value={result.counts.BLOCKED.toLocaleString("en-IN")} />
      </div>
      {result.headroom_before !== null && (
        <p className="text-[11.5px] text-muted-foreground">
          Room under the cap before this batch: {result.headroom_before.toLocaleString("en-IN")}
        </p>
      )}
      {blocked.length > 0 && (
        <ul className="space-y-1 text-[12px]">
          {blocked.map((b) => (
            <li key={b.code} className="flex justify-between gap-4">
              <span>{b.text}</span>
              <span className="font-semibold tabular-nums">{b.count.toLocaleString("en-IN")}</span>
            </li>
          ))}
        </ul>
      )}
      <DataTable
        columns={[
          { key: "loan_account_number", header: "Loan", className: "font-semibold text-foreground" },
          {
            key: "outcome", header: "Result",
            render: (v) => (
              <Badge variant={v.outcome === "BLOCKED" ? "softDanger" : v.outcome === "PLACED" ? "softSuccess" : "softPrimary"}>
                {verdictText(v)}
              </Badge>
            ),
          },
          { key: "case_number", header: "Case", render: (v) => v.case_number ?? "" },
        ]}
        rows={result.verdicts}
        rowKey={(v) => v.loan_id}
        density="compact"
        minWidth={0}
      />
    </div>
  );
}

function BatchSummary({ title, result, onClose }: { title: string; result: BatchResult; onClose: () => void }) {
  return (
    <Panel title={title} hint={<button type="button" className="text-primary hover:underline" onClick={onClose}>Dismiss</button>}>
      <VerdictBody result={result} />
    </Panel>
  );
}

// ── Placements, with recall ─────────────────────────────────────────────────

function Placements() {
  const qc = useQueryClient();
  const [status, setStatus] = useState("ACTIVE");
  const [page, setPage] = useState(1);
  const [recalling, setRecalling] = useState<PlacementRow | null>(null);
  const [reason, setReason] = useState("");

  const params = new URLSearchParams({ page: String(page), page_size: String(PAGE_SIZE), ...(status ? { status } : {}) });
  const list = useQuery({
    queryKey: ["bank", "placements", "list", params.toString()],
    queryFn: async () => (await api.get<PlacementPage>(`/bank/placements?${params}`)).data,
    placeholderData: keepPreviousData,
  });
  const recall = useMutation({
    mutationFn: async () => (await api.post(`/bank/placements/${recalling!.placement_id}/recall`, { reason: reason.trim() })).data,
    onSuccess: () => {
      setRecalling(null);
      setReason("");
      qc.invalidateQueries({ queryKey: ["bank", "placements"] });
    },
  });

  const warning = list.data?.synthetic_warning ?? null;
  const columns: DataColumn<PlacementRow>[] = [
    { key: "loan_account_number", header: "Loan", className: "font-semibold text-foreground" },
    { key: "agency_name", header: "Agency" },
    { key: "placed_on", header: "Placed" },
    { key: "source", header: "How" },
    { key: "status", header: "Status" },
    { key: "dpd_at_placement", header: "DPD at placement", align: "right" },
    { key: "exposure_at_placement", header: "Exposure", align: "right", render: (p) => rs(p.exposure_at_placement) },
    {
      key: "expected", header: "Expected P(pay)", align: "right",
      render: (p) => (p.expected_recovery_prob === null ? "—" : `${(p.expected_recovery_prob * 100).toFixed(0)}%`),
    },
    {
      key: "actions", header: "", align: "right",
      render: (p) => p.status === "ACTIVE" && (
        <Button size="sm" variant="destructive" onClick={() => { setRecalling(p); setReason(""); }}>
          <RotateCcw /> Recall
        </Button>
      ),
    },
  ];
  const reasonError = recallReasonError(reason);
  const pages = list.data ? Math.max(1, Math.ceil(list.data.total / PAGE_SIZE)) : 1;

  return (
    <Panel
      title="Placements"
      hint={
        <span className="flex items-center gap-3">
          {warning && <span title={warning}><SampleDataNote label="Expected P(pay) is from a synthetic-data model" /></span>}
          {list.data ? `${list.data.total.toLocaleString("en-IN")} placements` : null}
        </span>
      }
    >
      <div className="mb-4 w-48">
        <Field label="Status">
          <Select value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }}>
            <option value="ACTIVE">Active</option>
            <option value="RECALLED">Recalled</option>
            <option value="RESOLVED">Resolved</option>
            <option value="">All</option>
          </Select>
        </Field>
      </div>
      {list.isLoading ? (
        <AnalyticsLoading label="Loading placements…" />
      ) : list.isError ? (
        <AnalyticsError>{errorDetail(list.error, "The placements could not be loaded.")}</AnalyticsError>
      ) : (
        <>
          <DataTable columns={columns} rows={list.data?.items ?? []} rowKey={(p) => p.placement_id} density="compact" minWidth={860} />
          <Pager page={page} pages={pages} onPage={setPage} />
        </>
      )}

      <Dialog open={recalling !== null} onOpenChange={(o) => { if (!o) setRecalling(null); }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Recall {recalling?.loan_account_number}</DialogTitle>
            <DialogDescription>
              The placement with {recalling?.agency_name} ends today and its open case is closed. The loan can then be placed again.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-2 px-6">
            <Label htmlFor="recall-reason">Reason (recorded in the audit trail)</Label>
            <Textarea id="recall-reason" value={reason} maxLength={500} onChange={(e) => setReason(e.target.value)} />
            {recall.isError && <p role="alert" className="text-[12px] text-destructive">{errorDetail(recall.error, "The recall failed.")}</p>}
          </div>
          <DialogFooter>
            <DialogClose onClose={() => setRecalling(null)}>Cancel</DialogClose>
            <Button variant="destructive" disabled={reasonError !== null || recall.isPending} onClick={() => recall.mutate()}>
              {recall.isPending ? "Recalling…" : "Recall placement"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Panel>
  );
}

// ── small pieces ────────────────────────────────────────────────────────────

/** A labelled control; the <label> wrapper names the control for assistive tech. */
function Field({ label, children, className = "" }: { label: string; children: ReactNode; className?: string }) {
  return (
    <label className={`block space-y-1.5 ${className}`}>
      <span className="block text-[11px] font-medium text-muted-foreground">{label}</span>
      {children}
    </label>
  );
}

function Pager({ page, pages, onPage }: { page: number; pages: number; onPage: (p: number) => void }) {
  if (pages <= 1) return null;
  return (
    <div className="mt-4 flex items-center justify-end gap-2 text-[11.5px] text-muted-foreground">
      <Button size="sm" variant="outline" disabled={page <= 1} onClick={() => onPage(page - 1)}>Previous</Button>
      <span>Page {page} of {pages}</span>
      <Button size="sm" variant="outline" disabled={page >= pages} onClick={() => onPage(page + 1)}>Next</Button>
    </div>
  );
}
