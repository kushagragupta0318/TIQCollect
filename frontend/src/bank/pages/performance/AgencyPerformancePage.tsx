// The agency scorecard + regional leaderboard (D06) — replaces the
// placeholder at /bank/agencies/performance. Two sections on one page: pick
// an agency and see every §6.2 metric, or see every agency ranked by
// Performance Index. Kept as two stacked cards rather than tabs — there is
// no tab-kit component in bank/ui, and both sections are short enough that
// splitting them behind a click would only cost an extra interaction.
//
// PERFORMANCE INDEX is agency_effect's, not recomputed here (D06's own
// docblock) — `index` can be null when the estimator found nothing for the
// window, and `n` is the evidence behind a real one. Every null here means
// "not knowable", never zero — see api/bank.ts's AgencyScorecard docblock.
import { useMemo, useState } from "react";
import { useSearchParams } from "react-router";
import { useQuery } from "@tanstack/react-query";
import { Info, Trophy } from "lucide-react";
import { ExecutiveHeader, PageFailure, PageLoading, PageRoot } from "../../components/PageTemplate";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../ui/card";
import { Button } from "../../ui/button";
import { Label } from "../../ui/label";
import { Select } from "../../ui/select";
import { Badge } from "../../ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../../ui/table";
import { Bar100, Tile } from "../../components/analytics";
import {
  getAgencyScorecard, listAgencies, listAgencyLeaderboard, listRegions,
  type Agency, type PerformanceIndex,
} from "@/api/bank";
import { errorDetail } from "@/lib/apiError";
import {
  formatCostPer100, formatFraudPer100, formatIndex, formatPerAgentPerDay, formatRatioPercent,
  indexEvidenceCaption, percentBarWidth, rankLeaderboard,
} from "./performanceLogic";

/** A ratio Tile: value as a percentage, a capped bar underneath so a value
 *  over 100% (recovery_vs_expected, the two workforce ratios) still shows
 *  its real, uncapped number in text while the bar itself tops out. */
function RatioTile({ label, value, sub }: { label: string; value: number | null; sub?: string }) {
  return (
    <div className="rounded-[16px] border border-border/50 bg-card px-5 py-4">
      <p className="truncate text-[11.5px] font-medium leading-tight text-muted-foreground">{label}</p>
      <p className="mt-2.5 text-[22px] font-bold leading-none tracking-tight tabular-nums">{formatRatioPercent(value)}</p>
      <div className="mt-3">
        <Bar100 pct={percentBarWidth(value)} />
      </div>
      {sub && <p className="mt-2 truncate text-[11px] text-muted-foreground/80">{sub}</p>}
    </div>
  );
}

function ScorecardSection() {
  // ?agency= preselects (the directory's Performance cell links here).
  const [searchParams] = useSearchParams();
  const [agencyId, setAgencyId] = useState(() => searchParams.get("agency") ?? "");

  const agenciesQuery = useQuery({ queryKey: ["bank-agencies-plain"], queryFn: () => listAgencies(), staleTime: 5 * 60_000 });
  const agencies = useMemo(
    () => [...(agenciesQuery.data ?? [])].sort((a, b) => a.legal_name.localeCompare(b.legal_name)),
    [agenciesQuery.data],
  );

  const scorecardQuery = useQuery({
    queryKey: ["bank-agency-scorecard", agencyId],
    queryFn: () => getAgencyScorecard(agencyId),
    enabled: !!agencyId,
  });

  const s = scorecardQuery.data;
  const pi = s?.performance_index ?? null;

  return (
    <Card>
      <CardHeader>
        <CardTitle>Agency scorecard</CardTitle>
        <CardDescription>Pick an agency to see its Performance Index and every scorecard metric for the latest month.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-6">
        <div className="max-w-sm">
          <Label htmlFor="scorecard-agency">Agency</Label>
          <Select
            id="scorecard-agency" value={agencyId} onChange={(e) => setAgencyId(e.target.value)} className="mt-1.5"
            disabled={agenciesQuery.isPending}
          >
            <option value="">{agenciesQuery.isPending ? "Loading agencies…" : "Select an agency"}</option>
            {agencies.map((a: Agency) => (
              <option key={a.agency_id} value={a.agency_id}>{a.trade_name?.trim() || a.legal_name} ({a.code})</option>
            ))}
          </Select>
        </div>

        {!agencyId && (
          <p className="text-[13px] text-muted-foreground">Select an agency above to see its scorecard.</p>
        )}

        {agencyId && scorecardQuery.isLoading && <PageLoading label="Loading the scorecard…" />}

        {agencyId && scorecardQuery.isError && (
          <div className="space-y-3">
            <PageFailure>{errorDetail(scorecardQuery.error, "The scorecard could not be loaded.")}</PageFailure>
            <div className="flex justify-center">
              <Button variant="outline" onClick={() => scorecardQuery.refetch()}>Retry</Button>
            </div>
          </div>
        )}

        {agencyId && s && s.n_rows === 0 && (
          <div className="rounded-inner border border-dashed border-border p-6 text-[13px] text-muted-foreground">
            No scorecard data at all for this agency in the latest window — nothing has been placed or worked yet.
          </div>
        )}

        {agencyId && s && s.n_rows > 0 && (
          <div className="space-y-6">
            <div className="rounded-[16px] border border-border/50 bg-card px-6 py-5">
              <div className="flex flex-wrap items-baseline justify-between gap-3">
                <div>
                  <p className="text-[11.5px] font-medium leading-tight text-muted-foreground">Performance Index</p>
                  {pi ? (
                    <>
                      <p className="mt-1 text-[40px] font-bold leading-none tracking-tight tabular-nums">{formatIndex(pi.index)}</p>
                      <p className="mt-2 text-[11px] text-muted-foreground" title="Placement-months the estimator has actually matured and scored for this agency">
                        {indexEvidenceCaption(pi.n)}
                      </p>
                    </>
                  ) : (
                    <p className="mt-1 text-[15px] font-semibold text-muted-foreground">Not enough data to score this agency yet</p>
                  )}
                </div>
                <div className="text-right text-[11px] text-muted-foreground">
                  <div>{s.month_start} · {s.months} month{s.months === 1 ? "" : "s"}</div>
                  <div>{s.version}</div>
                </div>
              </div>
            </div>

            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              <RatioTile label="Collection Efficiency" value={s.collection_efficiency} />
              <RatioTile label="Resolution Rate" value={s.resolution_rate} />
              <RatioTile
                label="Recovery vs Expected" value={s.recovery_vs_expected}
                sub="Can read above 100% — compares the whole book's collections to only the newest placements' baseline. Not an error."
              />
              <RatioTile label="PTP Conversion" value={s.ptp_conversion} />
              <RatioTile label="Contact Rate" value={s.contact_rate} />
              <RatioTile label="SLA Adherence" value={s.sla_adherence} />
              <Tile label="Productivity" value={formatPerAgentPerDay(s.productivity_per_agent_per_day)} />
              <Tile label="Cost" value={formatCostPer100(s.cost_per_100_inr)} sub={s.cost_per_100_inr == null ? "No cost rate configured for this bank yet" : undefined} />
              <Tile
                label="Evidence Integrity" value={formatFraudPer100(s.evidence_integrity_per_100_visits)}
                sub="Confirmed fraud findings per 100 visits — lower is better"
              />
              <RatioTile label="Workforce Active" value={s.workforce_active_ratio} sub="Active vs contracted agents — can exceed 100% if overstaffed" />
              <RatioTile label="Workforce Attrition" value={s.workforce_attrition_ratio} />
              <RatioTile label="Workforce Leave Rate" value={s.workforce_leave_rate} />
            </div>

            <div className="rounded-[16px] border border-dashed border-border/70 bg-muted/20 px-5 py-4">
              <div className="flex items-start gap-2.5">
                <Info className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
                <div>
                  <div className="flex items-center gap-2">
                    <p className="text-[13px] font-semibold text-foreground">Compliance Score</p>
                    <Badge variant="outline" className="text-muted-foreground">Provisional</Badge>
                  </div>
                  <p className="mt-1 text-[22px] font-bold leading-none tracking-tight tabular-nums">
                    {s.compliance_score == null ? "—" : s.compliance_score.toFixed(1)}
                  </p>
                  <p className="mt-2 text-[11.5px] text-muted-foreground">
                    A placeholder computed from breach and consent columns — not the bank-wide Compliance Score header
                    KPI, which has not been built yet. Will be replaced once that KPI lands.
                  </p>
                </div>
              </div>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function LeaderboardSection() {
  const [regionId, setRegionId] = useState("");

  const regionsQuery = useQuery({ queryKey: ["bank-regions"], queryFn: listRegions, staleTime: 5 * 60_000 });
  const agenciesQuery = useQuery({ queryKey: ["bank-agencies-plain"], queryFn: () => listAgencies(), staleTime: 5 * 60_000 });
  const leaderboardQuery = useQuery({
    queryKey: ["bank-agencies-leaderboard", regionId],
    queryFn: () => listAgencyLeaderboard(regionId ? { region_id: regionId } : {}),
  });

  const regions = useMemo(() => [...(regionsQuery.data ?? [])].sort((a, b) => a.path.localeCompare(b.path)), [regionsQuery.data]);
  const regionName = useMemo(() => new Map(regions.map((r) => [r.region_id, r.name])), [regions]);
  const agencyName = useMemo(
    () => new Map((agenciesQuery.data ?? []).map((a: Agency) => [a.agency_id, a.trade_name?.trim() || a.legal_name])),
    [agenciesQuery.data],
  );

  const rows = useMemo(() => rankLeaderboard(leaderboardQuery.data ?? []), [leaderboardQuery.data]);

  return (
    <Card>
      <CardHeader>
        <CardTitle>Regional leaderboard</CardTitle>
        <CardDescription>Every agency ranked by Performance Index descending. An agency the estimator could not score for this window is listed last, unranked.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="max-w-sm">
          <Label htmlFor="leaderboard-region">Region</Label>
          <Select
            id="leaderboard-region" value={regionId} onChange={(e) => setRegionId(e.target.value)} className="mt-1.5"
            disabled={regionsQuery.isPending}
          >
            <option value="">Whole bank</option>
            {regions.map((r) => <option key={r.region_id} value={r.region_id}>{r.name} · {r.level}</option>)}
          </Select>
        </div>

        {leaderboardQuery.isLoading && <PageLoading label="Loading the leaderboard…" />}

        {leaderboardQuery.isError && (
          <div className="space-y-3">
            <PageFailure>{errorDetail(leaderboardQuery.error, "The leaderboard could not be loaded.")}</PageFailure>
            <div className="flex justify-center">
              <Button variant="outline" onClick={() => leaderboardQuery.refetch()}>Retry</Button>
            </div>
          </div>
        )}

        {!leaderboardQuery.isLoading && !leaderboardQuery.isError && rows.length === 0 && (
          <div className="flex items-center gap-3 rounded-inner border border-dashed border-border p-6 text-[13px] text-muted-foreground">
            <Trophy className="size-5 shrink-0 text-primary" />
            No agencies in scope for this region.
          </div>
        )}

        {!leaderboardQuery.isLoading && !leaderboardQuery.isError && rows.length > 0 && (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Rank</TableHead>
                <TableHead>Agency</TableHead>
                <TableHead>Region</TableHead>
                <TableHead>Performance Index</TableHead>
                <TableHead>Evidence (n)</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((row: PerformanceIndex & { rank: number | null }) => (
                <TableRow key={row.agency_id}>
                  <TableCell>{row.rank ?? <span className="text-muted-foreground">—</span>}</TableCell>
                  <TableCell className="font-semibold text-foreground">
                    {agencyName.get(row.agency_id) ?? row.agency_id}
                  </TableCell>
                  <TableCell>{row.region_id ? (regionName.get(row.region_id) ?? row.region_id) : "—"}</TableCell>
                  <TableCell>
                    {row.index == null ? (
                      <Badge variant="outline" className="text-muted-foreground">Not enough data</Badge>
                    ) : (
                      <span className="font-semibold tabular-nums">{formatIndex(row.index)}</span>
                    )}
                  </TableCell>
                  <TableCell>{row.n}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}

export default function AgencyPerformancePage() {
  const header = (
    <ExecutiveHeader
      title="Performance"
      meta={["Agencies", "Task D06"]}
      scopeNote="Agency scorecards, recovery against expectation and the regional leaderboard."
    />
  );

  return (
    <PageRoot>
      {header}
      <ScorecardSection />
      <LeaderboardSection />
    </PageRoot>
  );
}
