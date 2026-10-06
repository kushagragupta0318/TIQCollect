// The bank's agency profile (P3 D07). Clicking an ACTIVE/SUSPENDED/OFFBOARDED
// row in the Directory used to do nothing — AgencyDirectoryPage.openRow's own
// comment named this exact gap. Contract + commission are the SAME shape the
// agency's own G04 view reads (services/agency_profile_service.
// build_agency_profile, via services/bank/agency_profile_service) — one
// definition, not a second copy for the bank side. Performance is NOT
// recomputed here: D06's agency_scorecard already owns it; this page links
// to it instead.
import { useNavigate, useParams } from "react-router";
import { useQuery } from "@tanstack/react-query";
import { Building2, ExternalLink, Percent, Users } from "lucide-react";
import { ExecutiveHeader, PageFailure, PageLoading, PageRoot } from "../../components/PageTemplate";
import { Card, CardContent, CardHeader, CardTitle } from "../../ui/card";
import { Badge } from "../../ui/badge";
import { Button } from "../../ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../../ui/table";
import { Tile } from "../../components/analytics";
import { getBankAgencyProfile } from "@/api/bank";
import { errorDetail } from "@/lib/apiError";
import { rs } from "../../theme/format";

const humanize = (s: string) => s.replace(/_/g, " ").toLowerCase().replace(/^\w/, (c) => c.toUpperCase());

function Field({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div>
      <p className="text-[11px] uppercase tracking-wide text-muted-foreground">{label}</p>
      <p className="text-sm font-semibold text-foreground">{value ?? "—"}</p>
    </div>
  );
}

export default function AgencyProfilePage() {
  const { agencyId = "" } = useParams<{ agencyId: string }>();
  const navigate = useNavigate();

  const q = useQuery({
    queryKey: ["bank", "agency-profile", agencyId],
    queryFn: () => getBankAgencyProfile(agencyId),
    enabled: !!agencyId,
  });

  if (q.isLoading) return <PageLoading label="Loading agency profile…" />;
  if (q.isError) return <PageFailure>{errorDetail(q.error, "This agency's profile could not load.")}</PageFailure>;
  if (!q.data) return <PageFailure>No data for this agency.</PageFailure>;

  const { agency, contract, commission_terms, placed_volume, people } = q.data;

  return (
    <PageRoot>
      <ExecutiveHeader
        title={agency.legal_name}
        meta={["Agencies", "Task D07", agency.trade_name ?? undefined].filter(Boolean) as string[]}
        scopeNote="Contract, commission, placed volume and people. Performance lives on its own page — see the button below."
      />

      <div className="flex justify-end">
        <Button variant="outline" onClick={() => navigate(`/bank/agencies/performance?agency=${encodeURIComponent(agencyId)}`)}>
          <ExternalLink className="mr-1.5 size-4" /> View performance (D06)
        </Button>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2"><Building2 className="size-4" /> Identity</CardTitle>
        </CardHeader>
        <CardContent className="grid grid-cols-2 gap-4 sm:grid-cols-4">
          <Field label="RBI registration" value={agency.rbi_registration_no} />
          <Field label="Status" value={<Badge>{humanize(agency.status)}</Badge>} />
          <Field label="HQ city" value={agency.hq_city} />
          <Field label="Contact" value={agency.contact_name} />
          <Field label="Email" value={agency.contact_email} />
          <Field label="Phone" value={agency.contact_phone} />
        </CardContent>
      </Card>

      {!contract ? (
        <Card><CardContent className="py-8 text-center text-sm text-muted-foreground">No contract on file for this agency.</CardContent></Card>
      ) : (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              Contract {contract.contract_no} <Badge variant="outline">{humanize(contract.status)}</Badge>
            </CardTitle>
            {!contract.is_current && (
              <p className="mt-1 rounded-md bg-destructive/10 px-2.5 py-1.5 text-[12px] font-medium text-destructive">
                No ACTIVE contract — showing the most recent one on file. These terms are not currently in force.
              </p>
            )}
          </CardHeader>
          <CardContent className="grid grid-cols-2 gap-4 sm:grid-cols-4">
            <Field label="Start" value={contract.start_date} />
            <Field label="End" value={contract.end_date} />
            <Field label="Seat limit (agents)" value={contract.max_agents} />
            <Field label="Max placed cases" value={contract.max_placed_cases} />
            <Field label="Max visits / month" value={contract.max_visits_per_month} />
            <Field label="SLA — first visit" value={`${contract.sla_first_visit_days} days`} />
            <Field label="Recall on SLA breach" value={contract.recall_on_sla_breach ? "Yes" : "No"} />
            <Field label="Recall at contract end" value={contract.recall_at_contract_end ? "Yes" : "No"} />
          </CardContent>
        </Card>
      )}

      {commission_terms.length > 0 && (
        <Card>
          <CardHeader><CardTitle className="flex items-center gap-2"><Percent className="size-4" /> Commission</CardTitle></CardHeader>
          <CardContent>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Loan type</TableHead>
                  <TableHead>DPD bucket</TableHead>
                  <TableHead>Commission</TableHead>
                  <TableHead>Fixed fee</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {commission_terms.map((t, i) => (
                  <TableRow key={i}>
                    <TableCell>{humanize(t.loan_type)}</TableCell>
                    <TableCell>{humanize(t.dpd_bucket)}</TableCell>
                    <TableCell className="font-semibold">{t.commission_pct}%</TableCell>
                    <TableCell>{t.fixed_fee_per_resolution != null ? rs(t.fixed_fee_per_resolution) : "—"}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <Tile label="Active placements" value={placed_volume.active_count} sub="loans currently with this agency" />
        <Tile label="Active exposure" value={rs(placed_volume.active_exposure)} sub="outstanding on active placements" />
        <Tile label="Lifetime placements" value={placed_volume.lifetime_count} sub="ever placed with this agency" />
      </div>

      <Card>
        <CardHeader><CardTitle className="flex items-center gap-2"><Users className="size-4" /> People</CardTitle></CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
            <Field label="Agents" value={people.agent_count} />
            <Field label="On duty" value={people.agents_on_duty} />
          </div>
          {people.managers.length > 0 && (
            <Table>
              <TableHeader>
                <TableRow><TableHead>Manager</TableHead><TableHead>Email</TableHead><TableHead>Role</TableHead></TableRow>
              </TableHeader>
              <TableBody>
                {people.managers.map((m) => (
                  <TableRow key={m.id}>
                    <TableCell>{m.full_name}</TableCell>
                    <TableCell>{m.email}</TableCell>
                    <TableCell>{humanize(m.role)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </PageRoot>
  );
}
