/**
 * Demo API — handles all HTTP calls without a backend.
 * Reads from / writes to demoStore. Used when backend is unavailable.
 */
import { useAuthStore } from "@/store/authStore";
import { useDemoStore } from "./demoStore";
import { DEMO_USERS, ALL_AGENTS } from "./sampleData";
import type { AuthUser } from "@/types";

function delay(ms = 350): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}

// ─── Auth ────────────────────────────────────────────────────────────────────
export async function mockLogin(email: string, password: string, deviceId: string) {
  await delay(600);
  const user = DEMO_USERS.find((u) => u.email === email && u.password === password);
  if (!user) throw new Error("Invalid credentials");
  const fakeToken = btoa(JSON.stringify({ sub: user.user_id, role: user.role, exp: Date.now() + 900000 }));
  return {
    access_token: fakeToken,
    refresh_token: `refresh_${fakeToken}`,
    token_type: "bearer",
    role: user.role,
    user_id: user.user_id,
    full_name: user.full_name,
  };
}

export async function mockGetMe(): Promise<AuthUser> {
  await delay(100);
  const auth = useAuthStore.getState();
  const user = DEMO_USERS.find((u) => u.user_id === auth.user?.id);
  if (!user) throw new Error("Not authenticated");
  return { id: user.user_id, email: user.email, full_name: user.full_name, role: user.role, is_active: true };
}

// ─── Agent endpoints ──────────────────────────────────────────────────────────
export async function mockGetDaySummary() {
  await delay(200);
  const { cases, checkedIn, payments } = useDemoStore.getState();
  const resolvedToday = cases.filter((c) => ["PAID", "PARTIALLY_PAID"].includes(c.status));
  const collectedToday = resolvedToday.reduce((sum, c) => sum + c.collected_amount, 0);
  const ptpsDue = cases.filter((c) => c.status === "PTP_SET").length;
  const visitsToday = useDemoStore.getState().visits.filter((v) => {
    const d = new Date(v.check_in_time);
    const today = new Date();
    return d.toDateString() === today.toDateString();
  }).length;

  return {
    cases_assigned: cases.length,
    visits_done: visitsToday + 3,
    amount_collected: collectedToday + 12500,
    ptps_due_today: ptpsDue,
    is_checked_in: checkedIn,
    beat_status: "IN_PROGRESS",
    total_target: cases.reduce((s, c) => s + c.target_amount, 0),
  };
}

export async function mockGetCases() {
  await delay(300);
  return useDemoStore.getState().cases;
}

export async function mockGetCaseById(id: string) {
  await delay(200);
  const { cases, visits, payments, ptps } = useDemoStore.getState();
  const c = cases.find((x) => x.id === id);
  if (!c) throw new Error("Case not found");
  return {
    ...c,
    visits: visits.filter((v) => v.case_id === id),
    payments: payments.filter((p) => p.case_id === id),
    ptps: ptps.filter((p) => p.case_id === id),
  };
}

export async function mockCheckIn(selfieUrl?: string) {
  await delay(500);
  useDemoStore.getState().checkIn(selfieUrl);
  return { success: true, message: "Checked in successfully" };
}

export async function mockRecordVisit(caseId: string, data: Record<string, unknown>) {
  await delay(600);
  const cases = useDemoStore.getState().cases;
  const c = cases.find((x) => x.id === caseId);
  if (!c) throw new Error("Case not found");

  const visitId = `visit-${Date.now()}`;
  const visit = {
    id: visitId,
    case_id: caseId,
    check_in_time: new Date().toISOString(),
    check_out_time: new Date().toISOString(),
    outcome: data.outcome as string,
    customer_met: data.customer_met as boolean,
    notes: (data.notes as string) || "",
    visit_number: c.visit_count + 1,
    geo_verified: true,
    not_met_reason: data.not_met_reason as string | undefined,
    selfie_url: data.selfie_url as string | undefined,
    premises_photo_url: data.premises_photo_url as string | undefined,
  };

  let caseUpdate: Partial<typeof c> = { status: "IN_PROGRESS" };
  if (data.outcome === "PAYMENT_COLLECTED") caseUpdate.status = "PAID";
  else if (data.outcome === "PARTIAL_PAYMENT_PTP") caseUpdate.status = "PARTIALLY_PAID";
  else if (data.outcome === "PTP_SET") caseUpdate.status = "PTP_SET";
  else if (data.outcome === "CUSTOMER_HOSTILE") caseUpdate.is_escalated = true;

  useDemoStore.getState().recordVisit(visit, caseUpdate);
  return { visit_id: visitId, success: true };
}

export async function mockCollectPayment(caseId: string, data: Record<string, unknown>) {
  await delay(500);
  const payId = `pay-${Date.now()}`;
  const payment = {
    id: payId,
    case_id: caseId,
    amount: data.amount as number,
    mode: data.mode as string,
    status: "PENDING_VERIFICATION",
    receipt_number: `RCP${Math.floor(Math.random() * 90000000 + 10000000)}`,
    payment_date: new Date().toISOString(),
    agent_id: "agent-001",
    upi_reference: data.upi_reference as string | undefined,
    receipt_photo_url: data.receipt_photo_url as string | undefined,
  };

  const cases = useDemoStore.getState().cases;
  const c = cases.find((x) => x.id === caseId);
  const newCollected = (c?.collected_amount ?? 0) + (data.amount as number);
  const newStatus = newCollected >= (c?.target_amount ?? 0) ? "PAID" : "PARTIALLY_PAID";

  useDemoStore.getState().addPayment(payment, { status: newStatus as "PAID" | "PARTIALLY_PAID" });
  return { payment_id: payId, receipt_number: payment.receipt_number, success: true };
}

export async function mockSetPTP(caseId: string, data: Record<string, unknown>) {
  await delay(400);
  const ptpId = `ptp-${Date.now()}`;
  const ptp = {
    id: ptpId,
    case_id: caseId,
    committed_amount: data.committed_amount as number,
    committed_date: data.committed_date as string,
    status: "ACTIVE",
    customer_reason: (data.customer_reason as string) || "",
    agent_notes: data.agent_notes as string | undefined,
  };
  useDemoStore.getState().addPTP(ptp, { status: "PTP_SET" });
  return { ptp_id: ptpId, success: true };
}

export async function mockTriggerSOS(lat: number, lon: number) {
  await delay(300);
  useDemoStore.getState().setSosActive(true, { lat, lon });
  return { success: true, message: "SOS alert sent to manager and emergency contacts" };
}

export async function mockGetProfile() {
  await delay(200);
  return ALL_AGENTS[0]; // agent-001 is Rajesh Kumar
}

export async function mockGetBeat() {
  await delay(250);
  const { beat, cases } = useDemoStore.getState();
  const orderedCases = beat.ordered_case_ids.map((id) => cases.find((c) => c.id === id)).filter(Boolean);
  return { ...beat, cases: orderedCases };
}

// ─── Manager endpoints ────────────────────────────────────────────────────────
export async function mockGetDashboardSummary() {
  await delay(300);
  const { cases } = useDemoStore.getState();
  return {
    total_agents: 50,
    agents_on_duty: 32,
    total_cases: 724,
    cases_assigned: 612,
    cases_resolved_today: 87 + cases.filter((c) => c.status === "PAID").length,
    amount_collected_today: 2850000 + useDemoStore.getState().payments.reduce((s, p) => s + p.amount, 0),
    amount_target_today: 5400000,
    collection_rate_today: 52.8,
    ptps_due_today: 43,
    sos_active_count: useDemoStore.getState().sosActive ? 1 : 0,
  };
}

export async function mockGetAllAgents() {
  await delay(300);
  return ALL_AGENTS;
}

export async function mockGetAllCases() {
  await delay(350);
  // Combine demo agent cases + fake additional cases
  return useDemoStore.getState().cases;
}
