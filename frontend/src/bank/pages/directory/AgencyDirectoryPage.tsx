// The agency directory (D05) — every empanelled agency with its coverage,
// contract status and authorised products, filterable server-side. Replaces
// the placeholder at /bank/agencies/directory.
//
// PERFORMANCE. The Agency Performance Index (D06) comes from the leaderboard,
// one row per agency AND region (directoryLogic.performanceCell). No single
// cross-region number is shown: nobody has defined one (tiqcollect-06).
//
// FILTERING IS SERVER-SIDE. region_id, status, loan_type and
// contract_expiring_before are query params on GET /bank/agencies-directory;
// changing a filter refetches rather than filtering the already-fetched
// rows client-side, per the brief and matching how region_id's hierarchy
// match (a ZONE also matches agencies covering anything beneath it) only
// exists server-side.
import { useMemo, useState } from "react";
import { useNavigate } from "react-router";
import { useQuery } from "@tanstack/react-query";
import type L from "leaflet";
import { drawPoints } from "@/components/map/points";
import { Building2 } from "lucide-react";
import { ExecutiveHeader, PageFailure, PageLoading, PageRoot } from "../../components/PageTemplate";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "../../ui/card";
import { Button } from "../../ui/button";
import { Input } from "../../ui/input";
import { Label } from "../../ui/label";
import { Select } from "../../ui/select";
import { Badge, type BadgeProps } from "../../ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../../ui/table";
import { MapCanvas } from "@/components/map/MapCanvas";
import { DEFAULT_CENTRE } from "@/components/map/constants";
import {
  listAgencyDirectory, listAgencyLeaderboard, listRegions, AGENCY_STATUSES, LOAN_TYPES,
  type AgencyDirectoryFilters, type AgencyDirectoryRow,
} from "@/api/bank";
import { formatIndex, indexEvidenceCaption } from "../performance/performanceLogic";
import { errorDetail } from "@/lib/apiError";
import { AGENCY_STATUS_LABELS, CONTRACT_STATUS_LABELS, LOAN_TYPE_LABELS, labelFor } from "../../lib/labels";
import {
  contractSummary, performanceCell, productLabels, regionMarkersFromAgencies, summariseCoveredRegions,
  type PerformanceCell,
} from "./directoryLogic";

const AGENCY_STATUS_BADGE: Record<string, NonNullable<BadgeProps["variant"]>> = {
  PENDING: "warning",
  ACTIVE: "success",
  SUSPENDED: "destructive",
  OFFBOARDED: "outline",
};

const CONTRACT_STATUS_BADGE: Record<string, NonNullable<BadgeProps["variant"]>> = {
  DRAFT: "outline",
  ACTIVE: "success",
  EXPIRED: "warning",
  TERMINATED: "destructive",
};

const CONTRACT_TONE_CLASS = {
  normal: "text-muted-foreground",
  soon: "font-medium text-warning",
  past: "font-medium text-destructive",
} as const;

/** The contract number, a status badge only when it is not simply Active,
 *  and the end date — called out inside the renewal window or once passed. */
function ContractCell({ contract }: { contract: NonNullable<AgencyDirectoryRow["contract"]> }) {
  const summary = contractSummary(contract.end_date, new Date());
  const days = summary.daysLeft;
  return (
    <div className="space-y-1">
      <div className="text-[12px] font-medium text-foreground tabular-nums">{contract.contract_no}</div>
      {contract.status !== "ACTIVE" && (
        <Badge variant={CONTRACT_STATUS_BADGE[contract.status] ?? "outline"}>{labelFor(CONTRACT_STATUS_LABELS, contract.status)}</Badge>
      )}
      <div className={`text-[11px] ${CONTRACT_TONE_CLASS[summary.tone]}`}>
        {summary.ends}
        {summary.tone === "soon" && days != null && ` · ${days === 0 ? "today" : `in ${days} day${days === 1 ? "" : "s"}`}`}
      </div>
    </div>
  );
}

function PerformanceCellView({ cell, onOpen }: { cell: PerformanceCell; onOpen: () => void }) {
  switch (cell.kind) {
    case "scored":
      return (
        <div>
          <div className="font-semibold text-foreground tabular-nums">{formatIndex(cell.index)}</div>
          <div className="text-[11px] text-muted-foreground">{indexEvidenceCaption(cell.n)}</div>
        </div>
      );
    case "insufficient":
      return <Badge variant="outline" className="text-muted-foreground">Not enough data</Badge>;
    case "not_scored_here":
      return <span className="text-[12px] text-muted-foreground">No score in this region</span>;
    case "regions":
      if (cell.total === 0) return <span className="text-[12px] text-muted-foreground">No scored placements yet</span>;
      return (
        <button
          type="button"
          onClick={(e) => { e.stopPropagation(); onOpen(); }}
          className="text-left text-[12px] font-medium text-primary hover:underline"
          title="Open this agency's scorecard"
        >
          Scored in {cell.scored} of {cell.total} region{cell.total === 1 ? "" : "s"}
        </button>
      );
  }
}

export default function AgencyDirectoryPage() {
  const navigate = useNavigate();
  const [status, setStatus] = useState("");
  const [regionId, setRegionId] = useState("");
  const [loanType, setLoanType] = useState("");
  const [expiringBefore, setExpiringBefore] = useState("");

  const filters: AgencyDirectoryFilters = useMemo(() => {
    const f: AgencyDirectoryFilters = {};
    if (status) f.status = status;
    if (regionId) f.region_id = regionId;
    if (loanType) f.loan_type = loanType;
    if (expiringBefore) f.contract_expiring_before = expiringBefore;
    return f;
  }, [status, regionId, loanType, expiringBefore]);

  const regionsQuery = useQuery({ queryKey: ["bank-regions"], queryFn: listRegions, staleTime: 5 * 60_000 });
  const directoryQuery = useQuery({
    queryKey: ["bank-agency-directory", filters],
    queryFn: () => listAgencyDirectory(filters),
  });

  // Same key as the performance page's leaderboard, so the two share a cache.
  const leaderboardQuery = useQuery({
    queryKey: ["bank-agencies-leaderboard", regionId],
    queryFn: () => listAgencyLeaderboard(regionId ? { region_id: regionId } : {}),
  });

  const rows = useMemo(() => directoryQuery.data ?? [], [directoryQuery.data]);
  const regions = useMemo(() => [...(regionsQuery.data ?? [])].sort((a, b) => a.path.localeCompare(b.path)), [regionsQuery.data]);
  const markers = useMemo(() => regionMarkersFromAgencies(rows), [rows]);
  const filtersActive = !!(status || regionId || loanType || expiringBefore);

  function clearFilters() {
    setStatus(""); setRegionId(""); setLoanType(""); setExpiringBefore("");
  }

  /** PENDING is the only status the wizard can still usefully resume — an
   *  ACTIVE/SUSPENDED/OFFBOARDED agency's profile view is D07, a separate
   *  task, not built yet, so those rows are not clickable. */
  function openRow(row: AgencyDirectoryRow) {
    if (row.status === "PENDING") navigate(`/bank/agencies/onboard/${row.agency_id}`);
  }

  function drawMarkers(map: L.Map) {
    return drawPoints(
      map,
      markers.map((m) => ({ lat: m.latitude, lon: m.longitude, label: `${m.name} (${m.level}) — ${m.agencyNames.join(", ")}` })),
    );
  }

  const header = (
    <ExecutiveHeader
      title="Directory"
      meta={["Agencies", "Task D05", directoryQuery.data ? `${rows.length} agenc${rows.length === 1 ? "y" : "ies"}` : "Loading…"]}
      scopeNote="Every empanelled agency with coverage, contract status and authorised products. Filters are applied server-side."
    />
  );

  if (directoryQuery.isLoading) {
    return (
      <PageRoot>
        {header}
        <PageLoading label="Loading the agency directory…" />
      </PageRoot>
    );
  }

  if (directoryQuery.isError) {
    return (
      <PageRoot>
        {header}
        <PageFailure>{errorDetail(directoryQuery.error, "The agency directory could not be loaded.")}</PageFailure>
        <div className="flex justify-center">
          <Button variant="outline" onClick={() => directoryQuery.refetch()}>Retry</Button>
        </div>
      </PageRoot>
    );
  }

  return (
    <PageRoot>
      {header}

      <Card>
        <CardHeader>
          <CardTitle>Filters</CardTitle>
          <CardDescription>Region, status and product filters are hierarchy-aware and applied by the server — picking a zone matches every agency covering anything beneath it.</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <div>
            <Label htmlFor="dir-status">Status</Label>
            <Select id="dir-status" value={status} onChange={(e) => setStatus(e.target.value)} className="mt-1.5">
              <option value="">All statuses</option>
              {AGENCY_STATUSES.map((s) => <option key={s} value={s}>{AGENCY_STATUS_LABELS[s]}</option>)}
            </Select>
          </div>
          <div>
            <Label htmlFor="dir-region">Region</Label>
            <Select
              id="dir-region" value={regionId} onChange={(e) => setRegionId(e.target.value)} className="mt-1.5"
              disabled={regionsQuery.isPending}
            >
              <option value="">All regions</option>
              {regions.map((r) => <option key={r.region_id} value={r.region_id}>{r.name} · {r.level}</option>)}
            </Select>
          </div>
          <div>
            <Label htmlFor="dir-product">Product</Label>
            <Select id="dir-product" value={loanType} onChange={(e) => setLoanType(e.target.value)} className="mt-1.5">
              <option value="">All products</option>
              {LOAN_TYPES.map((t) => <option key={t} value={t}>{LOAN_TYPE_LABELS[t]}</option>)}
            </Select>
          </div>
          <div>
            <Label htmlFor="dir-expiry">Contract expiring before</Label>
            <Input
              id="dir-expiry" type="date" value={expiringBefore}
              onChange={(e) => setExpiringBefore(e.target.value)} className="mt-1.5"
            />
          </div>
        </CardContent>
        {filtersActive && (
          <CardFooter className="justify-end">
            <Button type="button" variant="ghost" size="sm" onClick={clearFilters}>Clear filters</Button>
          </CardFooter>
        )}
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Coverage map</CardTitle>
          <CardDescription>
            One marker per region covered by any agency below, naming which agencies cover it. Points only — an
            agency's drawn territory is a later task (STANDALONE-PRODUCT-PLAN §6.1).
          </CardDescription>
        </CardHeader>
        <CardContent>
          <MapCanvas onReady={drawMarkers} deps={[markers]} centre={DEFAULT_CENTRE} className="h-80 w-full rounded-inner overflow-hidden" />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Agencies</CardTitle>
          {directoryQuery.isFetching && <CardDescription>Updating…</CardDescription>}
        </CardHeader>
        <CardContent>
          {rows.length === 0 ? (
            <div className="flex items-center gap-3 rounded-inner border border-dashed border-border p-6 text-[13px] text-muted-foreground">
              <Building2 className="size-5 shrink-0 text-primary" />
              No agencies match these filters.
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Agency</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Contract</TableHead>
                  <TableHead>Covered regions</TableHead>
                  <TableHead>Authorised products</TableHead>
                  <TableHead title="Agency Performance Index, latest month">Performance</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows.map((row) => {
                  const coverage = summariseCoveredRegions(row.covered_regions);
                  const resumable = row.status === "PENDING";
                  return (
                    <TableRow
                      key={row.agency_id}
                      onClick={() => openRow(row)}
                      className={resumable ? "cursor-pointer" : undefined}
                      title={resumable ? "Continue onboarding this draft" : undefined}
                    >
                      <TableCell>
                        <div className="font-semibold text-foreground">{row.legal_name}</div>
                        {row.trade_name && <div className="text-[11px] text-muted-foreground">{row.trade_name}</div>}
                        <div className="text-[11px] text-muted-foreground">{row.code}</div>
                      </TableCell>
                      <TableCell>
                        <Badge variant={AGENCY_STATUS_BADGE[row.status] ?? "outline"}>{labelFor(AGENCY_STATUS_LABELS, row.status)}</Badge>
                      </TableCell>
                      <TableCell>
                        {row.contract ? (
                          <ContractCell contract={row.contract} />
                        ) : (
                          <span className="text-[12px] text-muted-foreground">No contract yet</span>
                        )}
                      </TableCell>
                      <TableCell>
                        {coverage.count === 0 ? (
                          <span className="text-[12px] text-muted-foreground">None</span>
                        ) : (
                          <span title={coverage.full}>{coverage.shown}</span>
                        )}
                      </TableCell>
                      <TableCell>
                        {row.authorised_products.length === 0 ? (
                          <span className="text-[12px] text-muted-foreground">None</span>
                        ) : (
                          productLabels(row.authorised_products)
                        )}
                      </TableCell>
                      <TableCell>
                        {leaderboardQuery.isPending ? (
                          <span className="text-[12px] text-muted-foreground">…</span>
                        ) : leaderboardQuery.isError ? (
                          <span className="text-[12px] text-muted-foreground" title={errorDetail(leaderboardQuery.error, "Performance could not be loaded.")}>—</span>
                        ) : (
                          <PerformanceCellView
                            cell={performanceCell(leaderboardQuery.data, row.agency_id, regionId || null)}
                            onOpen={() => navigate(`/bank/agencies/performance?agency=${encodeURIComponent(row.agency_id)}`)}
                          />
                        )}
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </PageRoot>
  );
}
