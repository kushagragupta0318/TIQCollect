// The onboarding wizard (D01-D03): six steps building one Agency draft,
// resumable by URL. Fresh start: /bank/agencies/onboard (no id). Once step 1
// creates the draft, its submit navigates to /bank/agencies/onboard/:agencyId
// — every step after that reads and writes against that id, and reopening
// the id'd URL resumes exactly where the draft left off (GET
// /bank/agencies/{id} is the one call that reads back everything collected).
//
// WHY THE WHOLE BODY WAITS ON THE INITIAL GET. Steps 1-3 hold editable text
// state seeded from props via lazy useState initializers (this repo's own
// resync pattern — see EditAgentDrawer.tsx's docblock on why an effect would
// trip react-hooks/set-state-in-effect). That is only correct if the props a
// step first mounts with are already the final, loaded values — so this page
// renders nothing but a loading state until GET /bank/agencies/{id} resolves
// for a known id, rather than mounting steps against a still-empty draft and
// trying to resync them later. Switching between steps is a full
// unmount/remount (each is its own conditional branch below), so revisiting
// a step after another step's save always mounts fresh against current data.
import { useState } from "react";
import { useNavigate, useParams } from "react-router";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { UserPlus } from "lucide-react";
import { ExecutiveHeader, PageFailure, PageLoading, PageRoot } from "../../components/PageTemplate";
import { Button } from "../../ui/button";
import { WizardStepper, type WizardStepDef } from "./WizardStepper";
import { IdentityStep } from "./steps/IdentityStep";
import { CoverageStep } from "./steps/CoverageStep";
import { ContractStep } from "./steps/ContractStep";
import { DocumentsStep } from "./steps/DocumentsStep";
import { MasterLoginStep } from "./steps/MasterLoginStep";
import { ReviewStep } from "./steps/ReviewStep";
import { missingRequiredDocs, latestMasterLoginInvite } from "./onboardingLogic";
import { getAgencyDetail, listBankInvites, listRegions, type Agency } from "@/api/bank";
import { errorDetail } from "@/lib/apiError";

const DETAIL_KEY = (agencyId: string) => ["bank-agency-detail", agencyId] as const;

export default function OnboardAgencyWizardPage() {
  const params = useParams<{ agencyId?: string }>();
  const agencyId = params.agencyId ?? null;
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [activeStep, setActiveStep] = useState(1);

  const detailQuery = useQuery({
    queryKey: agencyId ? DETAIL_KEY(agencyId) : ["bank-agency-detail", "none"],
    queryFn: () => getAgencyDetail(agencyId as string),
    enabled: !!agencyId,
  });
  const regionsQuery = useQuery({ queryKey: ["bank-regions"], queryFn: listRegions, staleTime: 5 * 60_000 });
  const invitesQuery = useQuery({ queryKey: ["bank-invites"], queryFn: listBankInvites, enabled: !!agencyId });

  function invalidateDetail() {
    if (agencyId) queryClient.invalidateQueries({ queryKey: DETAIL_KEY(agencyId) });
  }

  /** Step 1's save handler. On a fresh draft (no agencyId yet) the create
   *  response gives the new id — navigate there, which enables the detail
   *  query for the first time. On a revisit, invalidate to re-read the
   *  server's own values instead of trusting the echoed response is exhaustive. */
  function handleIdentitySaved(agency: Agency) {
    if (agencyId) {
      invalidateDetail();
    } else {
      navigate(`/bank/agencies/onboard/${agency.agency_id}`, { replace: true });
    }
    setActiveStep(2);
  }

  function handleStepSaved(next: number) {
    invalidateDetail();
    setActiveStep(next);
  }

  if (agencyId && detailQuery.isLoading) {
    return (
      <PageRoot>
        <ExecutiveHeader title="Onboard Agency" meta={["Agencies", "Tasks D01-D03"]} />
        <PageLoading label="Loading this draft…" />
      </PageRoot>
    );
  }

  if (agencyId && detailQuery.isError) {
    return (
      <PageRoot>
        <ExecutiveHeader title="Onboard Agency" meta={["Agencies", "Tasks D01-D03"]} />
        <PageFailure>{errorDetail(detailQuery.error, "This draft could not be loaded.")}</PageFailure>
        <div className="flex justify-center gap-3">
          <Button variant="outline" onClick={() => detailQuery.refetch()}>Retry</Button>
          <Button onClick={() => navigate("/bank/agencies/onboard")}>Start a new draft</Button>
        </div>
      </PageRoot>
    );
  }

  const detail = agencyId ? (detailQuery.data ?? null) : null;
  const regions = regionsQuery.data ?? [];
  const invite = agencyId && invitesQuery.data ? latestMasterLoginInvite(invitesQuery.data, agencyId) : null;
  const locked = !agencyId;

  const steps: WizardStepDef[] = [
    { id: 1, label: "Identity", complete: !!detail },
    { id: 2, label: "Coverage", complete: (detail?.region_ids.length ?? 0) > 0 },
    { id: 3, label: "Contract", complete: !!detail?.contract && detail.contract.max_placed_cases != null },
    {
      id: 4, label: "Documents",
      complete: !!detail && missingRequiredDocs(detail.documents, detail.required_doc_types).length === 0,
    },
    { id: 5, label: "Master Login", complete: !!invite },
    { id: 6, label: "Review", complete: detail?.status === "ACTIVE" },
  ];

  const meta = ["Agencies", "Tasks D01-D03", detail ? `${detail.legal_name} · ${detail.code}` : "New agency"];

  return (
    <PageRoot>
      <ExecutiveHeader
        title="Onboard Agency"
        meta={meta}
        scopeNote="Six steps: identity, coverage, contract, documents, master login, review. Draft saves after each step — leave and come back with the same link."
      />
      <WizardStepper steps={steps} active={activeStep} locked={locked} onSelect={setActiveStep} />

      {activeStep === 1 && (
        <IdentityStep agency={detail} onSaved={handleIdentitySaved} />
      )}
      {activeStep === 2 && detail && (
        <CoverageStep
          agencyId={detail.agency_id} detail={detail} regions={regions} regionsLoading={regionsQuery.isPending}
          onSaved={() => handleStepSaved(3)}
        />
      )}
      {activeStep === 3 && detail && (
        <ContractStep agencyId={detail.agency_id} detail={detail} onSaved={() => handleStepSaved(4)} />
      )}
      {activeStep === 4 && detail && (
        <DocumentsStep
          agencyId={detail.agency_id} detail={detail} onUploaded={invalidateDetail}
          onContinue={() => setActiveStep(5)}
        />
      )}
      {activeStep === 5 && detail && (
        <MasterLoginStep
          agencyId={detail.agency_id} agency={detail} invite={invite}
          onInvited={() => { invalidateDetail(); queryClient.invalidateQueries({ queryKey: ["bank-invites"] }); }}
          onContinue={() => setActiveStep(6)}
        />
      )}
      {activeStep === 6 && detail && (
        <ReviewStep
          detail={detail} invite={invite} regions={regions}
          onRefresh={() => { detailQuery.refetch(); queryClient.invalidateQueries({ queryKey: ["bank-invites"] }); }}
        />
      )}

      {!detail && activeStep !== 1 && (
        <div className="flex items-center gap-3 rounded-card border border-dashed border-border p-6 text-[13px] text-muted-foreground">
          <UserPlus className="size-5 shrink-0 text-primary" />
          Save Identity first — the other steps need an agency to attach to.
        </div>
      )}
    </PageRoot>
  );
}
