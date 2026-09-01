// ─── CHANGELOG (prototype → product) ────────────────────────────────────────
// 2026-07-13 — reoptimizeBeat()'s return type gained blocked_count/
//   urgent_case_id (line 40-41); LogCallPayload gained available_from/
//   available_until (line 215-216) — kept in sync with the backend's new
//   ReoptimizeBeatResponse/LogCallRequest shapes.
// 2026-07-14 — Added transcribeAudio() (line 98) — multipart upload to the
//   new POST /agent/transcribe-audio, backing RecordVisitPage's voice-note
//   widgets now that they route through Whisper instead of browser
//   SpeechRecognition.
// 2026-07-14 (API fix pass) — getPhotoUploadUrl()'s subject type gained
//   "signature"; recordVisit()'s payload type gained signature_key — backing
//   the fix that lets the customer signature actually reach the backend.
// 2026-07-15 — Added queueVisitTranscription() — the backend endpoint/Celery
//   task existed but nothing called it, so agent_recording_transcript/
//   borrower_recording_transcript never populated. Wired from
//   RecordVisitPage.tsx right after a successful recordVisit().
// 2026-07-30 — Added sendPaymentOtp()/verifyPaymentOtp() for borrower payment
//   verification, and collectPayment()'s payload gained an optional
//   verification_id (the verified OTP → backend writes the Payment as VERIFIED
//   instead of PENDING_VERIFICATION). Backs the OTP gate + offline branch in
//   RecordVisitPage. See prototype_to_product/30.07.md.
//   Full detail + why for all: /changelog.md
// ──────────────────────────────────────────────────────────────────────────
import api from "./axios";
import type { Case } from "@/types";

export async function getHomeSummary(): Promise<{
  cases_today: number;
  visits_done: number;
  amount_collected_today: number;
  total_target_today: number;
  ptps_due_today: number;
  check_in_status: string;
  beat_status: string | null;
  sos_active: boolean;
}> {
  const { data } = await api.get("/agent/home-summary");
  return data;
}

/** Ends the working day. Takes no coordinates: check-in captures a position
 *  because it starts the day's record and anchors the geo-fence, but clocking
 *  off needs no such proof. */
export async function checkOut(): Promise<{ status: string; message: string }> {
  const { data } = await api.post("/agent/checkout");
  return data;
}

export async function checkIn(latitude: number, longitude: number): Promise<{ status: string; message: string }> {
  const { data } = await api.post("/agent/checkin", { latitude, longitude });
  return data;
}

export async function getBeat() {
  const { data } = await api.get("/agent/beat");
  return data;
}

export async function reoptimizeBeat(lat: number, lon: number): Promise<{
  ordered_case_ids: string[];
  optimized: boolean;
  pending_count?: number;
  done_count?: number;
  blocked_count?: number;
  urgent_case_id?: string | null;
  message?: string;
}> {
  const { data } = await api.post(`/agent/beat/reoptimize?lat=${lat}&lon=${lon}`);
  return data;
}

export async function getCases(): Promise<Case[]> {
  const { data } = await api.get<Case[]>("/agent/cases");
  return data;
}

export interface RankedCase extends Case {
  rank: number;
  rank_score: number;
  rank_badge: string;
  rank_badge_color: "red" | "green" | "blue" | "orange" | "grey";
  rank_reason: string;
}

export async function getRankedCases(): Promise<RankedCase[]> {
  const { data } = await api.get<RankedCase[]>("/agent/cases/ranked");
  return data;
}

export async function getCaseDetail(caseId: string) {
  const { data } = await api.get(`/agent/cases/${caseId}`);
  return data;
}

// Path and parameter style both have to match the backend exactly: it serves
// POST /cases/{id}/photo-upload-url with `subject` as a QUERY parameter
// (endpoints/agent.py). This used to post to /photos/upload-url with a JSON
// body, which 404'd on every call — and because the caller swallows the error,
// photos, signatures and the customer signature silently never reached MinIO.
export async function getPhotoUploadUrl(caseId: string, subject: "agent" | "borrower" | "object" | "signature"): Promise<{ upload_url: string; key: string }> {
  const { data } = await api.post(`/agent/cases/${caseId}/photo-upload-url`, null, { params: { subject } });
  return data;
}

export async function getCasePhotos(caseId: string): Promise<Array<{
  photo_type: string;
  view_url: string | null;
  latitude: number | null;
  longitude: number | null;
  captured_at: string | null;
}>> {
  const { data } = await api.get(`/agent/cases/${caseId}/photos`);
  return data;
}

// Same fix as getPhotoUploadUrl: the backend route is
// POST /cases/{id}/recording-upload-url with `recorder` as a query parameter.
export async function getRecordingUploadUrl(caseId: string, recorder: "agent" | "borrower"): Promise<{ upload_url: string; key: string }> {
  const { data } = await api.post(`/agent/cases/${caseId}/recording-upload-url`, null, { params: { recorder } });
  return data;
}

// Queues transcription for a full visit recording already uploaded to MinIO
// (agent_recording_key / borrower_recording_key on the Visit row) — separate
// from transcribeAudio() below, which is for short ad-hoc voice notes typed
// before a Visit exists. Fire-and-forget: the task runs in the background;
// poll getCaseDetail() for agent_recording_transcript/borrower_recording_transcript.
export async function queueVisitTranscription(
  visitId: string,
  recorder: "agent" | "borrower" | "both"
): Promise<{ status: string; task_id: string }> {
  const { data } = await api.post(`/agent/visits/${visitId}/transcribe`, null, { params: { recorder } });
  return data;
}

// Ad-hoc voice-note transcription (Whisper-backed, not tied to any case/visit) —
// used by the mic/record buttons while a visit is still being drafted, before
// there's a Visit row to attach a recording to. See core/transcription.py.
export async function transcribeAudio(blob: Blob): Promise<string> {
  const form = new FormData();
  form.append("audio", blob, "note.webm");
  const { data } = await api.post("/agent/transcribe-audio", form, {
    headers: { "Content-Type": "multipart/form-data" },
    timeout: 60000,
  });
  return data.text as string;
}

export async function recordVisit(caseId: string, payload: {
  check_in_latitude: number;
  check_in_longitude: number;
  customer_met: boolean;
  outcome: string;
  person_met?: string;
  default_reason?: string;
  not_met_reason?: string;
  notes?: string;
  consent_given?: boolean;
  property_type?: string;
  occupancy_status?: string;
  vehicle_present?: boolean;
  business_running?: boolean;
  // Agent photo
  agent_photo_key?: string;
  agent_photo_lat?: number;
  agent_photo_lon?: number;
  agent_photo_accuracy?: number;
  agent_photo_altitude?: number;
  agent_photo_captured_at?: string;
  agent_photo_sha256?: string;
  // Borrower photo
  borrower_photo_key?: string;
  borrower_photo_lat?: number;
  borrower_photo_lon?: number;
  borrower_photo_accuracy?: number;
  borrower_photo_altitude?: number;
  borrower_photo_captured_at?: string;
  borrower_photo_sha256?: string;
  // Object / asset photo
  object_photo_key?: string;
  object_photo_lat?: number;
  object_photo_lon?: number;
  object_photo_accuracy?: number;
  object_photo_altitude?: number;
  object_photo_captured_at?: string;
  object_photo_sha256?: string;
  // Recordings
  device_id?: string;
  agent_recording_key?: string;
  borrower_recording_key?: string;
  signature_key?: string;
}) {
  const { data } = await api.post(`/agent/cases/${caseId}/visit`, payload);
  return data;
}

export async function notifyVisit(caseId: string): Promise<{ status: string }> {
  const { data } = await api.post(`/agent/cases/${caseId}/notify-visit`);
  return data;
}

export async function notifyCase(caseId: string, type: "reminder" | "ptp" | "receipt"): Promise<{ status: string }> {
  const { data } = await api.post(`/agent/cases/${caseId}/notify`, { type });
  return data;
}

export async function getVoiceToken(): Promise<{ token: string }> {
  const { data } = await api.get("/agent/voice/token");
  return data;
}

export async function createPaymentLink(caseId: string, amount: number): Promise<{ image_url: string; qr_id: string }> {
  const { data } = await api.post(`/agent/cases/${caseId}/payment-link`, { amount });
  return data;
}

export async function collectPayment(caseId: string, payload: {
  amount: number;
  mode: string;
  visit_id?: string;
  upi_reference?: string;
  cheque_number?: string;
  cheque_date?: string;
  cheque_bank?: string;
  bank_reference?: string;
  verification_id?: string;   // verified borrower OTP → Payment written as VERIFIED
}) {
  const { data } = await api.post(`/agent/cases/${caseId}/payment`, payload);
  return data;
}

// ─── Borrower payment-verification OTP (2026-07-30) ─────────────────────────
export interface OtpSendResult {
  otp_id: string;
  masked_phone: string;
  expires_at: string;          // ISO
  resend_available_at: string; // ISO
  demo_otp?: string;
}

// Send a 4-digit OTP to the borrower's registered phone to confirm a collection
// amount. The OTP is issued before the payment channel is chosen, so `mode` is
// optional (the amount is what it binds to). Pass payment_id to re-verify an
// existing pending (offline) payment.
export async function sendPaymentOtp(caseId: string, payload: {
  amount: number;
  mode?: string;
  payment_id?: string;
}): Promise<OtpSendResult> {
  const { data } = await api.post(`/agent/cases/${caseId}/payment/otp/send`, payload);
  return data;
}

export async function verifyPaymentOtp(caseId: string, payload: {
  otp_id: string;
  code: string;
}): Promise<{ verified: boolean; otp_id: string; payment_id?: string | null }> {
  const { data } = await api.post(`/agent/cases/${caseId}/payment/otp/verify`, payload);
  return data;
}

export async function setPTP(caseId: string, payload: {
  committed_amount: number;
  committed_date: string;
  customer_reason?: string;
  agent_notes?: string;
}) {
  const { data } = await api.post(`/agent/cases/${caseId}/ptp`, payload);
  return data;
}

export async function getProfile() {
  const { data } = await api.get("/agent/profile");
  return data;
}

// Batch upload of queued GPS fixes for the on-duty trail. See
// lib/locationReporter.ts for the queueing and retry rules.
export interface LocationPingPayload {
  latitude: number;
  longitude: number;
  accuracy_metres: number | null;
  recorded_at: string;
  source: string;
  battery_pct: number | null;
}

export async function sendLocationBatch(pings: LocationPingPayload[]) {
  const { data } = await api.post<{ accepted: number; rejected: number; last_recorded_at: string | null }>(
    "/agent/location", { pings },
  );
  return data;
}

export interface SOSResult {
  sos_triggered: boolean;
  triggered_at: string;
  message: string;
  // LIVE | LAST_KNOWN | NONE. The agent is shown which, so a failed GPS read
  // is visible to them instead of being papered over with a fabricated
  // position — see the 2026-08-18 rework in services/agent_service.py.
  location_quality: "LIVE" | "LAST_KNOWN" | "NONE";
  location_age_seconds: number | null;
  manager_notified: boolean;
}

// Coordinates are optional: when GPS does not answer, we send nothing rather
// than a made-up fallback, and the server resolves to the last tracked fix.
export async function triggerSOS(
  latitude?: number | null,
  longitude?: number | null,
  accuracyMetres?: number | null,
) {
  const body: Record<string, number> = {};
  if (latitude != null && longitude != null) {
    body.latitude = latitude;
    body.longitude = longitude;
    if (accuracyMetres != null) body.accuracy_metres = accuracyMetres;
  }
  const { data } = await api.post<SOSResult>("/agent/sos", body);
  return data;
}

export async function cancelSOS() {
  const { data } = await api.post("/agent/sos/cancel");
  return data;
}

export async function handoverCase(caseId: string, notes: string, returnToPool = true) {
  const { data } = await api.post(`/agent/cases/${caseId}/handover`, { notes, return_to_pool: returnToPool });
  return data;
}

export async function flagCustomer(customerId: string, flags: { is_hostile?: boolean; do_not_contact?: boolean }) {
  const { data } = await api.patch(`/agent/customers/${customerId}/flag`, flags);
  return data;
}

export interface LogCallPayload {
  outcome: "ANSWERED" | "NO_ANSWER" | "BUSY" | "DECLINED" | "SWITCHED_OFF" | "WRONG_NUMBER";
  duration_seconds?: number;
  phone_used?: "PRIMARY" | "ALTERNATE";
  customer_response_notes?: string;
  visit_feasible_today?: boolean | null;
  best_time_to_visit?: string;
  available_from?: string;
  available_until?: string;
  blocked_until_date?: string;
  alternate_location_hint?: string;
  payment_intent_signalled?: boolean | null;
  verbal_payment_date?: string;
  ai_intel_summary?: string;
}

export async function logCall(caseId: string, payload: LogCallPayload): Promise<{ id: string; called_at: string; outcome: string }> {
  const { data } = await api.post(`/agent/cases/${caseId}/call-log`, payload);
  return data;
}

export interface VisitStrategyBrief {
  best_time_to_visit: string;
  customer_situation: string;
  recommended_approach: string;
  payment_readiness: "LOW" | "MEDIUM" | "HIGH";
  risk_flags: string[];
  key_leverage_points: string[];
  opening_line: string;
  generated_at: string;
  case_id: string;
  /** False when the rule-based brief produced this rather than the model. An
   *  agent deciding how to open a conversation should know which they hold. */
  ai_generated?: boolean;
  ai_status?: string;
  ai_failure_reason?: string | null;
}

export async function getVisitStrategy(caseId: string): Promise<VisitStrategyBrief> {
  const { data } = await api.get(`/agent/cases/${caseId}/visit-strategy`);
  return data;
}

// ---------------------------------------------------------------------------
// Availability Calendar
// ---------------------------------------------------------------------------

export interface CalendarDay {
  date: string;
  day_of_week: string;
  status: "ON_DUTY" | "OFF_DUTY";
  beat_status: string | null;
  cases: number;
}

export interface MonthSummary {
  month: string;
  on_duty: number;
  off_duty: number;
  total_cases: number;
  attendance_pct: number;
}

export interface AvailabilityCalendar {
  agent_id: string;
  current_status: string;
  calendar: CalendarDay[];
  summary: {
    total_working_days: number;
    on_duty_days: number;
    off_duty_days: number;
    attendance_rate_pct: number;
  };
  monthly_summary: MonthSummary[];
}

export async function getAvailabilityCalendar(): Promise<AvailabilityCalendar> {
  const { data } = await api.get<AvailabilityCalendar>("/agent/availability/calendar");
  return data;
}
