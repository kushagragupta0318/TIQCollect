// Step 6 — Review. Read-only: no submit/activate button. Activation is
// automatic and server-side (agency_service._maybe_activate) once every
// required document is VERIFIED and the master-login invite is accepted —
// both happen outside this wizard, so this step only reads GET
// /bank/agencies/{id} (plus the invite listing) back and says what is still
// outstanding.
import { useMemo, type ReactNode } from "react";
import { CheckCircle2, Clock3, RefreshCw, XCircle } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "../../../ui/card";
import { Button } from "../../../ui/button";
import { Badge, type BadgeProps } from "../../../ui/badge";
import { Separator } from "../../../ui/separator";
import { DOC_TYPE_LABELS, REQUIRED_DOC_TYPES, type AgencyDetail, type DocType, type InviteSummary, type Region } from "@/api/bank";
import { latestDocumentsByType, unverifiedRequiredDocs } from "../onboardingLogic";

interface Props {
  detail: AgencyDetail;
  invite: InviteSummary | null;
  regions: Region[];
  onRefresh: () => void;
}

const DOC_STATUS_BADGE: Record<string, NonNullable<BadgeProps["variant"]>> = {
  UPLOADED: "warning", VERIFIED: "success", REJECTED: "destructive", EXPIRED: "destructive", SUPERSEDED: "outline",
};

function Field({ label, value }: { label: string; value: ReactNode }) {
  // `value || …` would print "—" for a real 0 (e.g. max_placed_cases: 0) —
  // checked against null/undefined/"" instead so a genuine zero still shows.
  const empty = value === null || value === undefined || value === "";
  return (
    <div>
      <p className="text-[10.5px] font-semibold uppercase tracking-wide text-muted-foreground">{label}</p>
      <p className="mt-0.5 text-[13px] text-foreground">{empty ? <span className="text-muted-foreground">—</span> : value}</p>
    </div>
  );
}

export function ReviewStep({ detail, invite, regions, onRefresh }: Props) {
  const required = detail.required_doc_types.length ? detail.required_doc_types : REQUIRED_DOC_TYPES;
  const latestDocs = latestDocumentsByType(detail.documents);
  const outstanding = unverifiedRequiredDocs(detail.documents, required);
  const inviteAccepted = invite?.status === "ACCEPTED";

  const regionNames = useMemo(() => {
    const byId = new Map(regions.map((r) => [r.region_id, r.name]));
    return detail.region_ids.map((id) => byId.get(id) ?? id);
  }, [regions, detail.region_ids]);

  const address = detail.registered_address;
  const addressLine = address
    ? [address.line1, address.line2, address.city, address.state, address.pincode].filter(Boolean).join(", ")
    : "";

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader className="flex-row items-center justify-between space-y-0">
          <div>
            <CardTitle>Activation status</CardTitle>
            <CardDescription>
              Awaiting document verification and master login acceptance — the agency activates automatically once
              both are complete. Nothing on this page can activate it directly.
            </CardDescription>
          </div>
          <Button type="button" variant="outline" size="sm" onClick={onRefresh}>
            <RefreshCw size={13} /> Refresh
          </Button>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex items-center gap-2">
            <Badge variant={detail.status === "ACTIVE" ? "success" : "warning"}>{detail.status}</Badge>
            {detail.activated_at && <span className="text-[12px] text-muted-foreground">since {detail.activated_at}</span>}
          </div>

          <div>
            <p className="mb-1.5 text-[11.5px] font-semibold text-foreground">Required documents</p>
            <ul className="space-y-1">
              {required.map((type) => {
                const doc = latestDocs.get(type);
                return (
                  <li key={type} className="flex items-center gap-2 text-[12.5px]">
                    {doc?.status === "VERIFIED" ? (
                      <CheckCircle2 size={14} className="shrink-0 text-success" />
                    ) : doc?.status === "REJECTED" ? (
                      <XCircle size={14} className="shrink-0 text-destructive" />
                    ) : (
                      <Clock3 size={14} className="shrink-0 text-muted-foreground" />
                    )}
                    <span className="text-foreground">{DOC_TYPE_LABELS[type as DocType] ?? type}</span>
                    <Badge variant={doc ? DOC_STATUS_BADGE[doc.status] ?? "outline" : "outline"}>
                      {doc ? doc.status : "NOT UPLOADED"}
                    </Badge>
                    {doc?.status === "REJECTED" && doc.rejection_reason && (
                      <span className="text-muted-foreground">— {doc.rejection_reason}</span>
                    )}
                  </li>
                );
              })}
            </ul>
          </div>

          <div className="flex items-center gap-2 text-[12.5px]">
            {inviteAccepted ? (
              <CheckCircle2 size={14} className="shrink-0 text-success" />
            ) : (
              <Clock3 size={14} className="shrink-0 text-muted-foreground" />
            )}
            <span className="text-foreground">Master login</span>
            <Badge variant={invite ? (inviteAccepted ? "success" : inviteStatusBadge(invite.status)) : "outline"}>
              {invite ? invite.status : "NOT SENT"}
            </Badge>
            {invite && <span className="text-muted-foreground">{invite.full_name} · {invite.email}</span>}
          </div>

          {outstanding.length === 0 && inviteAccepted && detail.status !== "ACTIVE" && (
            <p className="text-[12px] text-muted-foreground">
              Both conditions look met — activation happens on the next write to either one; refresh to check again.
            </p>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle>Identity</CardTitle></CardHeader>
        <CardContent className="grid gap-4 sm:grid-cols-3">
          <Field label="Legal name" value={detail.legal_name} />
          <Field label="Trade name" value={detail.trade_name} />
          <Field label="Code" value={detail.code} />
          <Field label="Entity type" value={detail.entity_type} />
          <Field label="CIN / LLPIN" value={detail.cin} />
          <Field label="RBI registration no." value={detail.rbi_registration_no} />
          <Field label="PAN" value={detail.pan} />
          <Field label="GSTIN" value={detail.gstin} />
          <Field label="HQ city" value={detail.hq_city} />
          <Field label="Website" value={detail.website} />
          <div className="sm:col-span-3"><Field label="Registered address" value={addressLine} /></div>
          <div className="sm:col-span-3"><Field label="Primary contact" value={[detail.contact_name, detail.contact_email, detail.contact_phone].filter(Boolean).join(" · ")} /></div>
        </CardContent>
        {(detail.contacts ?? []).length > 0 && (
          <>
            <Separator />
            <CardContent>
              <p className="mb-2 text-[11.5px] font-semibold text-foreground">Named contacts</p>
              <ul className="space-y-1 text-[12.5px] text-muted-foreground">
                {(detail.contacts ?? []).map((c, i) => (
                  <li key={i}>{c.role ? `${c.role}: ` : ""}{c.name} · {c.email} · {c.phone}</li>
                ))}
              </ul>
            </CardContent>
          </>
        )}
      </Card>

      <Card>
        <CardHeader><CardTitle>Coverage</CardTitle></CardHeader>
        <CardContent>
          {regionNames.length === 0 ? (
            <p className="text-[13px] text-muted-foreground">No regions selected yet.</p>
          ) : (
            <div className="flex flex-wrap gap-1.5">
              {regionNames.map((name, i) => <Badge key={i} variant="outline">{name}</Badge>)}
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle>Contract</CardTitle></CardHeader>
        <CardContent className="grid gap-4 sm:grid-cols-3">
          {detail.contract ? (
            <>
              <Field label="Contract no." value={detail.contract.contract_no} />
              <Field label="Status" value={detail.contract.status} />
              <Field label="Start date" value={detail.contract.start_date} />
              <Field label="End date" value={detail.contract.end_date} />
              <Field label="Max placed cases" value={detail.contract.max_placed_cases} />
              <Field label="Max agents" value={detail.contract.max_agents} />
              <Field label="Max visits / month" value={detail.contract.max_visits_per_month} />
              <Field label="SLA first visit (days)" value={detail.contract.sla_first_visit_days} />
              <Field label="Recall — no activity (days)" value={detail.contract.recall_no_activity_days} />
              <Field label="Recall on SLA breach" value={detail.contract.recall_on_sla_breach ? "Yes" : "No"} />
              <Field label="Recall at contract end" value={detail.contract.recall_at_contract_end ? "Yes" : "No"} />
            </>
          ) : (
            <p className="text-[13px] text-muted-foreground sm:col-span-3">No contract saved yet.</p>
          )}
          <p className="text-[11.5px] text-muted-foreground sm:col-span-3">
            Performance bonus/target, the security deposit and the commission-slab table were collected in step 3 but
            are not part of this read-back endpoint yet, so they cannot be shown here.
          </p>
        </CardContent>
      </Card>
    </div>
  );
}

function inviteStatusBadge(status: string): NonNullable<BadgeProps["variant"]> {
  return status === "OPEN" ? "warning" : status === "ACCEPTED" ? "success" : "destructive";
}
