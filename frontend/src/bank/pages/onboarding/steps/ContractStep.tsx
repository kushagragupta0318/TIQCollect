// Step 3 — Contract. PATCHes the same endpoint as Coverage (step 2), sending
// only this step's own keys so an independent save here never touches
// region_ids — see api/bank.ts's updateCoverageContract docblock.
//
// A REAL GAP, not a frontend shortcut: GET /bank/agencies/{id} echoes back
// contract_id/no/status/dates/capacity/SLA/recall — see agency_service.
// _contract_dict — but NOT performance_bonus_pct, performance_target_pct,
// security_deposit, or the commission-slab rows (contract_terms has no
// reader at all; nothing in bank_agencies_admin.py returns it). Resuming a
// draft therefore cannot prefill those four things even when they were saved
// earlier. Two mitigations, both below: the three bare numbers are simply
// left blank (omitted fields mean "unchanged" server-side, so leaving them
// blank and saving does not erase a previously-saved value); the slab table
// additionally tracks whether THIS session touched it (`termsTouched`) and
// only sends `contract_terms` — a wholesale replace — when it has, so
// reopening this step to fix one other field can never silently wipe a
// slab table it never re-loaded in the first place.
import { useState, type FormEvent } from "react";
import { useMutation } from "@tanstack/react-query";
import { Plus, Trash2 } from "lucide-react";
import { toast } from "react-hot-toast";
import { Card, CardContent, CardFooter, CardHeader, CardTitle, CardDescription } from "../../../ui/card";
import { Button } from "../../../ui/button";
import { Input } from "../../../ui/input";
import { Label } from "../../../ui/label";
import { Select } from "../../../ui/select";
import { Separator } from "../../../ui/separator";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../../../ui/table";
import {
  updateCoverageContract, DPD_BUCKETS, LOAN_TYPES,
  type AgencyDetail, type ContractTerm, type UpdateCoverageContractBody,
} from "@/api/bank";
import { errorDetail } from "@/lib/apiError";

interface Props {
  agencyId: string;
  detail: AgencyDetail;
  onSaved: () => void;
}

function isoToday(offsetDays = 0): string {
  const d = new Date();
  d.setDate(d.getDate() + offsetDays);
  return d.toISOString().slice(0, 10);
}

function emptyTerm(): ContractTerm {
  return { loan_type: LOAN_TYPES[0], dpd_bucket: DPD_BUCKETS[0], commission_pct: 0, fixed_fee_per_resolution: null, is_authorised: true };
}

export function ContractStep({ agencyId, detail, onSaved }: Props) {
  const contract = detail.contract;
  const [startDate, setStartDate] = useState(() => contract?.start_date ?? isoToday());
  const [endDate, setEndDate] = useState(() => contract?.end_date ?? isoToday(365));
  const [maxPlacedCases, setMaxPlacedCases] = useState(() => contract?.max_placed_cases?.toString() ?? "");
  const [maxAgents, setMaxAgents] = useState(() => contract?.max_agents?.toString() ?? "");
  const [maxVisitsPerMonth, setMaxVisitsPerMonth] = useState(() => contract?.max_visits_per_month?.toString() ?? "");
  const [slaFirstVisitDays, setSlaFirstVisitDays] = useState(() => contract?.sla_first_visit_days?.toString() ?? "3");
  const [recallNoActivityDays, setRecallNoActivityDays] = useState(() => contract?.recall_no_activity_days?.toString() ?? "");
  const [recallOnSlaBreach, setRecallOnSlaBreach] = useState(() => contract?.recall_on_sla_breach ?? false);
  const [recallAtContractEnd, setRecallAtContractEnd] = useState(() => contract?.recall_at_contract_end ?? true);
  const [performanceBonusPct, setPerformanceBonusPct] = useState("");
  const [performanceTargetPct, setPerformanceTargetPct] = useState("");
  const [securityDeposit, setSecurityDeposit] = useState("");
  const [terms, setTerms] = useState<ContractTerm[]>([]);
  const [termsTouched, setTermsTouched] = useState(false);

  const save = useMutation({
    mutationFn: (body: UpdateCoverageContractBody) => updateCoverageContract(agencyId, body),
    onSuccess: () => { toast.success("Contract saved"); onSaved(); },
    onError: (err) => toast.error(errorDetail(err, "Could not save the contract")),
  });

  function updateTerm(index: number, patch: Partial<ContractTerm>) {
    setTermsTouched(true);
    setTerms((rows) => rows.map((r, i) => (i === index ? { ...r, ...patch } : r)));
  }

  function num(value: string): number | undefined {
    return value.trim() === "" ? undefined : Number(value);
  }

  function submit(e: FormEvent) {
    e.preventDefault();
    if (new Date(endDate) < new Date(startDate)) {
      toast.error("Contract end date must be on or after the start date.");
      return;
    }
    const body: UpdateCoverageContractBody = {
      start_date: startDate,
      end_date: endDate,
      max_placed_cases: num(maxPlacedCases),
      max_agents: num(maxAgents),
      max_visits_per_month: num(maxVisitsPerMonth),
      sla_first_visit_days: num(slaFirstVisitDays),
      recall_no_activity_days: num(recallNoActivityDays),
      recall_on_sla_breach: recallOnSlaBreach,
      recall_at_contract_end: recallAtContractEnd,
      performance_bonus_pct: num(performanceBonusPct),
      performance_target_pct: num(performanceTargetPct),
      security_deposit: num(securityDeposit),
    };
    if (termsTouched) body.contract_terms = terms;
    save.mutate(body);
  }

  return (
    <form onSubmit={submit} className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>Term & capacity</CardTitle>
          <CardDescription>How long the agency is empanelled for, and how much work it may carry.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-2">
            <div>
              <Label htmlFor="ct-start">Start date</Label>
              <Input id="ct-start" type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)} className="mt-1.5" />
            </div>
            <div>
              <Label htmlFor="ct-end">End date</Label>
              <Input id="ct-end" type="date" value={endDate} onChange={(e) => setEndDate(e.target.value)} className="mt-1.5" />
            </div>
          </div>
          <div className="grid gap-4 sm:grid-cols-3">
            <div>
              <Label htmlFor="ct-max-cases">Max placed cases</Label>
              <Input id="ct-max-cases" type="number" min={0} value={maxPlacedCases} onChange={(e) => setMaxPlacedCases(e.target.value)} className="mt-1.5" />
            </div>
            <div>
              <Label htmlFor="ct-max-agents">Max agents</Label>
              <Input id="ct-max-agents" type="number" min={0} value={maxAgents} onChange={(e) => setMaxAgents(e.target.value)} className="mt-1.5" />
            </div>
            <div>
              <Label htmlFor="ct-max-visits">Max visits / month</Label>
              <Input id="ct-max-visits" type="number" min={0} value={maxVisitsPerMonth} onChange={(e) => setMaxVisitsPerMonth(e.target.value)} className="mt-1.5" />
            </div>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>SLA & recall</CardTitle>
          <CardDescription>When a first visit is due, and when an unworked case comes back to the pool.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-2">
            <div>
              <Label htmlFor="ct-sla">SLA — first visit within (days)</Label>
              <Input id="ct-sla" type="number" min={1} value={slaFirstVisitDays} onChange={(e) => setSlaFirstVisitDays(e.target.value)} className="mt-1.5" />
            </div>
            <div>
              <Label htmlFor="ct-recall-days">Recall after no activity for (days)</Label>
              <Input id="ct-recall-days" type="number" min={0} value={recallNoActivityDays} onChange={(e) => setRecallNoActivityDays(e.target.value)} className="mt-1.5" />
            </div>
          </div>
          <div className="flex flex-col gap-2 sm:flex-row sm:gap-6">
            <label className="flex items-center gap-2 text-[13px] text-foreground">
              <input type="checkbox" className="size-4 rounded border-input" checked={recallOnSlaBreach} onChange={(e) => setRecallOnSlaBreach(e.target.checked)} />
              Recall on SLA breach
            </label>
            <label className="flex items-center gap-2 text-[13px] text-foreground">
              <input type="checkbox" className="size-4 rounded border-input" checked={recallAtContractEnd} onChange={(e) => setRecallAtContractEnd(e.target.checked)} />
              Recall at contract end
            </label>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Performance & deposit</CardTitle>
          <CardDescription>
            Left blank on purpose when resuming a draft — the read-back endpoint does not echo these three back, so a
            blank field here means "leave whatever was saved before", not "clear it".
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4 sm:grid-cols-3">
          <div>
            <Label htmlFor="ct-bonus">Performance bonus (%)</Label>
            <Input id="ct-bonus" type="number" min={0} max={100} step="0.1" value={performanceBonusPct} onChange={(e) => setPerformanceBonusPct(e.target.value)} className="mt-1.5" />
          </div>
          <div>
            <Label htmlFor="ct-target">Performance target (%)</Label>
            <Input id="ct-target" type="number" min={0} max={100} step="0.1" value={performanceTargetPct} onChange={(e) => setPerformanceTargetPct(e.target.value)} className="mt-1.5" />
          </div>
          <div>
            <Label htmlFor="ct-deposit">Security deposit</Label>
            <Input id="ct-deposit" type="number" min={0} value={securityDeposit} onChange={(e) => setSecurityDeposit(e.target.value)} className="mt-1.5" />
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Commission slabs</CardTitle>
          <CardDescription>
            One row per loan type × DPD bucket. Saving replaces the agency's whole slab table with what's below — for
            the same read-back reason as above, previously-saved rows are not shown here; add every row this agency
            should have before saving.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {terms.length > 0 && (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Loan type</TableHead>
                  <TableHead>DPD bucket</TableHead>
                  <TableHead>Commission %</TableHead>
                  <TableHead>Fixed fee</TableHead>
                  <TableHead>Authorised</TableHead>
                  <TableHead />
                </TableRow>
              </TableHeader>
              <TableBody>
                {terms.map((term, i) => (
                  <TableRow key={i}>
                    <TableCell>
                      <Select aria-label="Loan type" value={term.loan_type} onChange={(e) => updateTerm(i, { loan_type: e.target.value })}>
                        {LOAN_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
                      </Select>
                    </TableCell>
                    <TableCell>
                      <Select aria-label="DPD bucket" value={term.dpd_bucket} onChange={(e) => updateTerm(i, { dpd_bucket: e.target.value })}>
                        {DPD_BUCKETS.map((b) => <option key={b} value={b}>{b}</option>)}
                      </Select>
                    </TableCell>
                    <TableCell>
                      <Input
                        aria-label="Commission percent" type="number" min={0} max={100} step="0.1" className="w-24"
                        value={term.commission_pct} onChange={(e) => updateTerm(i, { commission_pct: Number(e.target.value) })}
                      />
                    </TableCell>
                    <TableCell>
                      <Input
                        aria-label="Fixed fee per resolution" type="number" min={0} className="w-28"
                        value={term.fixed_fee_per_resolution ?? ""}
                        onChange={(e) => updateTerm(i, { fixed_fee_per_resolution: e.target.value === "" ? null : Number(e.target.value) })}
                      />
                    </TableCell>
                    <TableCell>
                      <input
                        type="checkbox" aria-label="Authorised" className="size-4 rounded border-input"
                        checked={term.is_authorised ?? true} onChange={(e) => updateTerm(i, { is_authorised: e.target.checked })}
                      />
                    </TableCell>
                    <TableCell>
                      <Button
                        type="button" variant="ghost" size="icon" aria-label="Remove slab"
                        onClick={() => { setTermsTouched(true); setTerms((rows) => rows.filter((_, idx) => idx !== i)); }}
                      >
                        <Trash2 size={16} />
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
          <Button type="button" variant="outline" size="sm" onClick={() => { setTermsTouched(true); setTerms((rows) => [...rows, emptyTerm()]); }}>
            <Plus size={14} /> Add slab
          </Button>
        </CardContent>
        <Separator />
        <CardFooter className="justify-end gap-3">
          <Button type="submit" disabled={save.isPending}>
            {save.isPending ? "Saving…" : "Save & continue"}
          </Button>
        </CardFooter>
      </Card>
    </form>
  );
}
