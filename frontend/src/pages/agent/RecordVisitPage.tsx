// ─── CHANGELOG (prototype → product) ────────────────────────────────────────
// 2026-07-13 — After a visit is recorded (line ~661), fires
//   reoptimizeBeat() best-effort if GPS is available — a completed visit is
//   the highest-value auto-reoptimize trigger since position + the done-list
//   both just changed for real. Failure is swallowed; never blocks the
//   visit-recorded success path.
// 2026-07-14 — Replaced browser SpeechRecognition-based dictation (couldn't
//   translate; hi-IN produced literal Devanagari, en-IN mangled Hindi speech
//   into garbled English) with useRecordAndTranscribe — records real audio,
//   sends it to the Whisper-backed POST /agent/transcribe-audio on stop.
//   Used by NotesWithSpeech (general/escalation notes) and VoiceNoteBox
//   (agent/borrower voice notes, which also keep the recorded blob for the
//   eventual visit recording upload). The old useSpeechToText/
//   useAudioRecorder hooks and all SpeechRecognition code are gone.
// 2026-07-14 (again) — useRecordAndTranscribe's getUserMedia constraints
//   (line ~217) flipped from echoCancellation/noiseSuppression/
//   autoGainControl: false to true — those were disabled to avoid starving
//   a parallel SpeechRecognition capture that no longer exists; leaving raw
//   unprocessed mic audio was making Whisper hallucinate an unrelated
//   sentence on real recordings (reported: spoke a name, got back
//   "Look, I'm going to show you how to do it.").
// 2026-07-14 (API fix pass) — SignaturePad already captured a customer
//   signature into form.signatureUrl, but handleSubmit() never uploaded or
//   sent it — the signature was drawn on screen and then thrown away.
//   uploadGeoPhoto()'s subject type widened to include "signature"; it's now
//   uploaded alongside the other geo photos and sent as signature_key.
// 2026-07-14 (module completion pass) — handleSubmit()'s catch block
//   (line ~685) was a bare `catch { toast.error("Failed to submit...") }` —
//   now backend geo-fence/contact-hours violations return a specific 403
//   detail message (VisitService hard-blocks them as of this pass), so the
//   catch now reads err.response.data.detail and shows the real reason
//   instead of a generic message that gave the agent no idea why.
// 2026-07-15 — handleSubmit() now calls queueVisitTranscription() right
//   after recordVisit() succeeds, for whichever recorder(s) actually
//   uploaded (agentRecKey/borrowerRecKey). The backend endpoint/Celery task
//   already existed but nothing called it — agent_recording_transcript/
//   borrower_recording_transcript were permanently empty. Fire-and-forget,
//   same pattern as the reoptimizeBeat call above.
// 2026-07-30 — Borrower OTP gate on payment collection (Section D). Before a
//   payment outcome can be submitted, the borrower must confirm the amount via
//   a 4-digit OTP sent to their registered phone (sendPaymentOtp/
//   verifyPaymentOtp → verificationId, passed to collectPayment so the backend
//   writes the Payment as VERIFIED). An explicit offline branch (borrower has
//   no signal) records the payment as PENDING with signature as interim proof;
//   the receipt modal shows verified vs pending accordingly. OTP verification
//   resets whenever the amount changes (the code binds to the amount).
//   Reordered so Section D is OTP-FIRST: the payment channel (mode buttons +
//   mode fields) stays hidden until the borrower confirms the amount by OTP
//   (4-box OtpInput, resend ×3) — no OTP, no payment, for any mode. Mode is
//   chosen after the OTP, so a mode change no longer resets verification. Once
//   an OTP is sent/verified the amount field is FROZEN (amountLocked) with NO
//   change option — the exact figure the borrower is confirming cannot be
//   altered. (Backend also binds the OTP to the amount and rejects a mismatch.)
//   The OTP store is Redis-if-reachable, else in-process (no infra needed).
// 2026-07-30 (fix) — Submit is now gated on GPS: canSubmit requires
//   locationReady (GPS captured AND within the 100m geo-fence, or the
//   ADDRESS_ISSUE outcome which is fence-exempt) — mirrors visit_service's
//   403, so an out-of-fence visit can no longer be submitted at all.
// 2026-07-31 — Demo aid: ~5s after the UPI QR is shown, the card auto-flips to
//   a green "Payment received ₹X ✓" (qrPaidDemo). A static UPI QR has no
//   callback, so the received-moment is simulated on a timer for demos; the
//   payment is still genuinely recorded VERIFIED via the OTP. The tick also
//   waives the manual UPI-transaction-ID requirement so submit unlocks.
//   See prototype_to_product/30.07.md.
//   Full detail + why for all days: /changelog.md
// 2026-08-05 - Three field fixes. (1) The lg two-column split now waits for
//   "Who Did You Meet?" to be answered: every section on the right is gated on
//   that answer, so splitting earlier left a dead 40% column beside a lone
//   three-option question. (2) The camera opens front-facing for the agent
//   selfie (rear for borrower/vehicle) and gained a flip control - it had
//   hardcoded facingMode:"environment", so no phone could reach the front
//   camera. Front preview is mirrored; the saved frame is not, because these
//   are evidence photos and text in shot must stay readable. (3) Retake
//   reopens on whichever side the agent had chosen.
// ──────────────────────────────────────────────────────────────────────────
import { useEffect, useRef, useState } from "react";
import QRCode from "react-qr-code";
import { useNavigate, useParams } from "react-router";
import { useAuthStore } from "@/store/authStore";
import {
  ArrowLeft, Camera, MapPin, CheckCircle, IndianRupee,
  Calendar, Upload, X, AlertTriangle,
  QrCode, Lock, Unlock, Mic, MicOff, ShieldCheck, Send, WifiOff, RefreshCw,
} from "lucide-react";
import { toast } from "react-hot-toast";
import { getCaseDetail, recordVisit, collectPayment, setPTP, getPhotoUploadUrl, getCasePhotos, getRecordingUploadUrl, reoptimizeBeat, transcribeAudio, queueVisitTranscription, sendPaymentOtp, verifyPaymentOtp } from "@/api/agent";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import SignaturePad from "@/components/ui/SignaturePad";
import OtpInput from "@/components/ui/OtpInput";
import PaymentReceiptModal from "@/components/ui/PaymentReceiptModal";
import VisitRecordedModal from "@/components/ui/VisitRecordedModal";
import { SOSButton } from "@/components/ui/SOSButton";
import { useBeat } from "@/contexts/BeatContext";
import type { VisitOutcome, PersonMet, DefaultReason } from "@/types";
import { haversineM } from "@/lib/geo";

// ─── Types ───────────────────────────────────────────────────────────────────

interface DocUpload { category: string; url: string; name: string }

type MeetingType = "BORROWER" | "THIRD_PARTY" | "NOT_MET" | null;

interface FormState {
  meetingType: MeetingType;
  // Person details
  personMet: PersonMet | null;
  thirdPartyName: string;
  customerMet: boolean | null;
  notMetReason: string;
  // Outcome
  outcome: VisitOutcome | null;
  defaultReason: DefaultReason | null;
  // Payment
  amount: string;
  paymentMode: string;
  upiRef: string;
  chequeNumber: string;
  chequeDate: string;
  chequeBank: string;
  neftRef: string;
  receiptPhoto: string | null;
  cashCounted: boolean;
  // PTP
  ptpAmount: string;
  ptpDate: string;
  ptpReason: string;
  // Escalation
  escalationNotes: string;
  witnessPresent: boolean;
  witnessName: string;
  // Field investigation
  propertyType: string;
  occupancyStatus: string;
  vehiclePresent: boolean | null;
  businessRunning: boolean | null;
  // Photos & docs
  agentPhoto: string | null;
  agentPhotoGps: { lat: number; lon: number; accuracy: number | null; altitude: number | null; time: string; iso: string } | null;
  agentPhotoFromPrev: boolean;
  borrowerPhoto: string | null;
  borrowerPhotoGps: { lat: number; lon: number; accuracy: number | null; altitude: number | null; time: string; iso: string } | null;
  borrowerPhotoFromPrev: boolean;
  objectPhoto: string | null;
  objectPhotoGps: { lat: number; lon: number; accuracy: number | null; altitude: number | null; time: string; iso: string } | null;
  objectPhotoFromPrev: boolean;
  documents: DocUpload[];
  // Tone & DECEASED informant
  borrowerTone: "COOPERATIVE" | "NEUTRAL" | "HOSTILE" | null;
  informantName: string;
  informantRelation: string;
  // Notes & consent (always last)
  notes: string;
  customerStatement: string;
  agentRecordingBlob: Blob | null;
  agentRecordingDuration: number;
  borrowerRecordingBlob: Blob | null;
  borrowerRecordingDuration: number;
  signatureUrl: string | null;
  consentGiven: boolean;
  // GPS
  gpsLat: number | null;
  gpsLon: number | null;
  gpsAccuracy: number | null;
  gpsAltitude: number | null;
}

// ─── Catalogue data ───────────────────────────────────────────────────────────

interface OutcomeConfig {
  value: VisitOutcome; label: string; tag: string; desc: string;
  needsPayment?: boolean; needsPTP?: boolean; needsEscalation?: boolean;
}

const BORROWER_OUTCOMES: OutcomeConfig[] = [
  { value: "PAID_FULL",     label: "Paid in Full",       tag: "PAID FULL",   desc: "Full target amount collected today",            needsPayment: true },
  { value: "PART_PAID",     label: "Partial Payment",    tag: "PART PAID",   desc: "Partial amount collected, no PTP for balance",  needsPayment: true },
  { value: "PTP",           label: "Promise to Pay",     tag: "PTP",         desc: "No money today — customer commits to a future date", needsPTP: true },
  { value: "PART_PAID_PTP", label: "Part Paid + PTP",    tag: "PART + PTP",  desc: "Partial payment now, PTP for rest",             needsPayment: true, needsPTP: true },
  { value: "BROKEN_PTP",    label: "PTP Broken",         tag: "BROKEN PTP",  desc: "Customer had a PTP commitment but did not pay", needsEscalation: true },
  { value: "RTP",           label: "Refuse to Pay",      tag: "RTP",         desc: "Borrower flat out refuses to pay",             needsEscalation: true },
  { value: "DISPUTE",       label: "Dispute",            tag: "DISPUTE",     desc: "Disputes the loan amount or loan itself",       needsEscalation: true },
  { value: "REVISIT",       label: "Revisit Required",   tag: "REVISIT",     desc: "Visit incomplete — follow-up needed" },
];

const NOT_MET_OUTCOMES: OutcomeConfig[] = [
  { value: "NOT_AVAILABLE", label: "Not Available",  tag: "NOT AVAILABLE", desc: "Customer absent from address at time of visit" },
  { value: "ADDRESS_ISSUE", label: "Address Issue",  tag: "ADDR ISSUE",    desc: "Wrong address or customer has shifted elsewhere", needsEscalation: true },
  { value: "DECEASED",      label: "Deceased",       tag: "DECEASED",      desc: "Customer is deceased — visits will be stopped",  needsEscalation: true },
];

const THIRD_PARTY_OPTIONS: { value: PersonMet; label: string }[] = [
  { value: "SPOUSE",   label: "Spouse / Wife" },
  { value: "SIBLING",  label: "Sibling" },
  { value: "RELATIVE", label: "Family Member" },
  { value: "NEIGHBOR", label: "Neighbour" },
  { value: "EMPLOYER", label: "Staff / Employer" },
  { value: "OTHER",    label: "Other" },
];

const DEFAULT_REASONS: { value: DefaultReason; label: string }[] = [
  { value: "JOB_LOSS",         label: "Lost job / unemployed" },
  { value: "SALARY_CUT",       label: "Salary reduction" },
  { value: "BUSINESS_FAILURE", label: "Business loss / closure" },
  { value: "MEDICAL",          label: "Medical emergency" },
  { value: "DEATH_IN_FAMILY",  label: "Death in family" },
  { value: "MARITAL_DISPUTE",  label: "Marital / family dispute" },
  { value: "ALREADY_PAID",     label: "Claims already paid bank directly" },
  { value: "AMOUNT_DISPUTED",  label: "Disputes the outstanding amount" },
  { value: "FRAUD_CLAIM",      label: "Denies ever taking the loan" },
  { value: "OVER_LEVERAGED",   label: "Too many other loans" },
  { value: "OTHER",            label: "Other reason" },
];

const PAYMENT_MODES = [
  { value: "CASH",   label: "Cash",         icon: "💵" },
  { value: "UPI",    label: "UPI",          icon: "📱" },
  { value: "CHEQUE", label: "Cheque / PDC", icon: "📝" },
  { value: "NEFT",   label: "NEFT / IMPS",  icon: "🏦" },
  { value: "RTGS",   label: "RTGS",         icon: "🏛️" },
];

const NOT_MET_REASONS = [
  { value: "PREMISES_LOCKED",    label: "Premises locked / no answer" },
  { value: "CUSTOMER_AWAY",      label: "Customer away / out of station" },
  { value: "WRONG_ADDRESS",      label: "Wrong address" },
  { value: "CUSTOMER_ABSCONDED", label: "Absconded / missing" },
  { value: "NEIGHBOR_MET",       label: "Neighbor informed" },
  { value: "OTHER",              label: "Other reason" },
];

const DOC_CATEGORIES = [
  { id: "BANK_STMT",      label: "Bank Statement",                  icon: "🏦" },
  { id: "ID_PROOF",       label: "ID Proof (Aadhaar / PAN)",        icon: "🪪" },
  { id: "INCOME_PROOF",   label: "GST / Salary Slip / ITR",         icon: "📄" },
  { id: "MEDICAL_SUPPORT",label: "Medical / Support Docs",          icon: "🏥" },
];

// ─── Geo helpers ──────────────────────────────────────────────────────────────



// Demo: how long after the UPI QR is shown before it auto-flips to
// "Payment received ✓". Change this one number (in milliseconds) to retime it —
// e.g. 3000 = 3s, 8000 = 8s.
const QR_DEMO_DELAY_MS = 10000;

// A short, pleasant two-note "success" chime synthesised with the Web Audio API
// (no sound asset needed). Best-effort — silently no-ops if audio is blocked.
function playSuccessChime() {
  try {
    const AudioCtx = window.AudioContext || (window as any).webkitAudioContext;
    if (!AudioCtx) return;
    const ctx = new AudioCtx();
    const now = ctx.currentTime;
    [{ f: 660, t: 0 }, { f: 988, t: 0.13 }].forEach(({ f, t }) => {   // E5 → B5, rising
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = "sine";
      osc.frequency.value = f;
      gain.gain.setValueAtTime(0.0001, now + t);
      gain.gain.exponentialRampToValueAtTime(0.18, now + t + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, now + t + 0.25);
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.start(now + t);
      osc.stop(now + t + 0.28);
    });
    setTimeout(() => ctx.close().catch(() => {}), 700);
  } catch {
    /* audio blocked / unsupported — ignore */
  }
}

// ─── Record + transcribe hook ──────────────────────────────────────────────────
// Records real audio and sends it to the Whisper-backed /agent/transcribe-audio
// endpoint on stop — replaces the old browser SpeechRecognition-based dictation,
// which can't translate (Hindi speech either came back as literal Devanagari, or
// got mangled when forced through English-only recognition). onAudioComplete is
// optional: VoiceNoteBox needs the raw blob too (it becomes the visit's saved
// recording), NotesWithSpeech only needs the transcribed text.

function useRecordAndTranscribe(
  onText: (text: string) => void,
  onAudioComplete?: (blob: Blob, durationSec: number) => void,
) {
  const [isRecording, setIsRecording] = useState(false);
  const [isTranscribing, setIsTranscribing] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [hasRecording, setHasRecording] = useState(false);
  const recRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const startRef = useRef(0);

  async function start() {
    try {
      // Echo cancellation / noise suppression / auto-gain were previously
      // disabled here to avoid starving a SpeechRecognition capture running
      // in parallel — that's gone now (transcription happens server-side,
      // after recording stops), and quiet/noisy raw mic audio is exactly
      // what makes Whisper hallucinate a plausible-sounding made-up
      // sentence instead of transcribing silence/noise as empty. Enabling
      // these gives Whisper actual cleaned-up speech to work with.
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
      const mimeType = MediaRecorder.isTypeSupported("audio/webm;codecs=opus")
        ? "audio/webm;codecs=opus" : "audio/webm";
      const rec = new MediaRecorder(stream, { mimeType });
      chunksRef.current = [];
      rec.ondataavailable = (e) => { if (e.data.size > 0) chunksRef.current.push(e.data); };
      rec.onstop = async () => {
        stream.getTracks().forEach((t) => t.stop());
        const blob = new Blob(chunksRef.current, { type: "audio/webm" });
        const dur = Math.round((Date.now() - startRef.current) / 1000);
        setHasRecording(true);
        onAudioComplete?.(blob, dur);

        setIsTranscribing(true);
        try {
          const text = await transcribeAudio(blob);
          if (text) onText(text);
        } catch {
          toast.error("Transcription failed — you can still type the note manually");
        } finally {
          setIsTranscribing(false);
        }
      };
      rec.start(200);
      recRef.current = rec;
      startRef.current = Date.now();
      setElapsed(0);
      setIsRecording(true);
      timerRef.current = setInterval(() => setElapsed((d) => d + 1), 1000);
    } catch {
      toast.error("Microphone access denied");
    }
  }

  function stop() {
    recRef.current?.stop();
    if (timerRef.current) clearInterval(timerRef.current);
    setIsRecording(false);
  }

  function reset() {
    setHasRecording(false);
    setElapsed(0);
  }

  const fmt = (s: number) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;

  return { isRecording, isTranscribing, elapsed, hasRecording, start, stop, reset, fmt };
}

// ─── Component ────────────────────────────────────────────────────────────────

type CameraTarget = "agentPhoto" | "borrowerPhoto" | "objectPhoto";

export default function RecordVisitPage() {
  const { caseId } = useParams<{ caseId: string }>();
  const navigate = useNavigate();
  const { user } = useAuthStore();
  const { refresh: refreshBeat } = useBeat();

  const [caseData, setCaseData] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [receipt, setReceipt] = useState<any>(null);
  // Success confirmation for the no-payment path. A collection ends in
  // PaymentReceiptModal, which is confirmation enough on its own.
  const [visitDone, setVisitDone] = useState<{ outcomeLabel?: string } | null>(null);
  const [showQR, setShowQR] = useState(false);
  // Demo: after the QR is shown, auto-reveal a "Payment received ✓" tick (a
  // static UPI QR has no callback, so the received-moment is simulated on a
  // timer). The payment is still genuinely recorded as VERIFIED via the OTP.
  const [qrPaidDemo, setQrPaidDemo] = useState(false);
  const [existingPhotos, setExistingPhotos] = useState<Record<string, { viewUrl: string | null; lat: number | null; lon: number | null; capturedAt: string | null }>>({});

  // ── Borrower payment-verification OTP state (2026-07-30) ───────────────────
  const [otp, setOtp] = useState<{ id: string; maskedPhone: string; expiresAt: string } | null>(null);
  const [otpCode, setOtpCode] = useState("");
  const [otpSending, setOtpSending] = useState(false);
  const [otpVerifying, setOtpVerifying] = useState(false);
  const [otpError, setOtpError] = useState<string | null>(null);
  const [verificationId, setVerificationId] = useState<string | null>(null);
  const [offlineAck, setOfflineAck] = useState(false);
  const [resendsUsed, setResendsUsed] = useState(0);   // max 3 resends

  // ── Camera modal state ─────────────────────────────────────────────────────
  const [activeCameraFor, setActiveCameraFor] = useState<CameraTarget | null>(null);
  const [facingMode, setFacingMode] = useState<"user" | "environment">("environment");
  const [capturedFrame, setCapturedFrame] = useState<string | null>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const streamRef = useRef<MediaStream | null>(null);

  const fileRefs = useRef<Record<string, HTMLInputElement | null>>({});

  const [form, setForm] = useState<FormState>({
    meetingType: null,
    personMet: null, thirdPartyName: "", customerMet: null, notMetReason: "",
    outcome: null, defaultReason: null,
    amount: "", paymentMode: "CASH", upiRef: "", chequeNumber: "", chequeDate: "",
    chequeBank: "", neftRef: "", receiptPhoto: null, cashCounted: false,
    ptpAmount: "", ptpDate: "", ptpReason: "",
    escalationNotes: "", witnessPresent: false, witnessName: "",
    borrowerTone: null, informantName: "", informantRelation: "",
    propertyType: "", occupancyStatus: "", vehiclePresent: null, businessRunning: null,
    agentPhoto: null, agentPhotoGps: null, agentPhotoFromPrev: false,
    borrowerPhoto: null, borrowerPhotoGps: null, borrowerPhotoFromPrev: false,
    objectPhoto: null, objectPhotoGps: null, objectPhotoFromPrev: false,
    documents: [],
    notes: "", customerStatement: "",
    agentRecordingBlob: null, agentRecordingDuration: 0,
    borrowerRecordingBlob: null, borrowerRecordingDuration: 0,
    signatureUrl: null,
    consentGiven: false, gpsLat: null, gpsLon: null, gpsAccuracy: null, gpsAltitude: null,
  });

  const upd = (patch: Partial<FormState>) => setForm((f) => ({ ...f, ...patch }));

  // ── Camera helpers ─────────────────────────────────────────────────────────

  // Which way the camera points. The agent shot is a selfie at the address, so
  // it opens on the front camera; the borrower and vehicle shots point outward.
  // Either can be flipped mid-capture — a field agent photographing a gate or a
  // parked car with the front camera, or handing the phone over for the other
  // two, are both normal.
  const DEFAULT_FACING: Record<CameraTarget, "user" | "environment"> = {
    agentPhoto:    "user",
    borrowerPhoto: "environment",
    objectPhoto:   "environment",
  };

  async function openCamera(target: CameraTarget, facing?: "user" | "environment") {
    const mode = facing ?? DEFAULT_FACING[target];
    setCapturedFrame(null);
    setActiveCameraFor(target);
    setFacingMode(mode);
    // Release any stream still running before asking for another — on a phone
    // the second getUserMedia can be refused while the first camera is open.
    streamRef.current?.getTracks().forEach((t) => t.stop());
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: mode, width: { ideal: 1280 }, height: { ideal: 720 } },
      });
      streamRef.current = stream;
      // Slight delay so the modal DOM is mounted before we assign srcObject
      setTimeout(() => {
        if (videoRef.current) videoRef.current.srcObject = stream;
      }, 80);
    } catch {
      toast.error("Camera permission denied or unavailable");
      setActiveCameraFor(null);
    }
  }

  // Flip without leaving the viewfinder. A laptop with only one webcam will
  // reject "environment", so fall back rather than dropping the agent into a
  // dead modal.
  async function flipCamera() {
    if (!activeCameraFor) return;
    const next = facingMode === "user" ? "environment" : "user";
    streamRef.current?.getTracks().forEach((t) => t.stop());
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: { exact: next }, width: { ideal: 1280 }, height: { ideal: 720 } },
      });
      streamRef.current = stream;
      setFacingMode(next);
      if (videoRef.current) videoRef.current.srcObject = stream;
    } catch {
      toast.error("No second camera on this device");
      void openCamera(activeCameraFor, facingMode);
    }
  }

  function snapPhoto() {
    if (!videoRef.current || !canvasRef.current) return;
    const video = videoRef.current;
    const canvas = canvasRef.current;
    canvas.width  = video.videoWidth  || 1280;
    canvas.height = video.videoHeight || 720;
    canvas.getContext("2d")?.drawImage(video, 0, 0);
    setCapturedFrame(canvas.toDataURL("image/jpeg", 0.88));
    streamRef.current?.getTracks().forEach((t) => t.stop());
  }

  function confirmPhoto() {
    if (!capturedFrame || !activeCameraFor) return;
    const gpsMap: Record<CameraTarget, keyof FormState> = {
      agentPhoto: "agentPhotoGps",
      borrowerPhoto: "borrowerPhotoGps",
      objectPhoto: "objectPhotoGps",
    };
    const fromPrevMap: Record<CameraTarget, keyof FormState> = {
      agentPhoto: "agentPhotoFromPrev",
      borrowerPhoto: "borrowerPhotoFromPrev",
      objectPhoto: "objectPhotoFromPrev",
    };
    const now = new Date();
    const patch: Partial<FormState> = {
      [activeCameraFor]: capturedFrame,
      [fromPrevMap[activeCameraFor]]: false,
    };
    if (form.gpsLat && form.gpsLon) {
      (patch as any)[gpsMap[activeCameraFor]] = {
        lat: form.gpsLat, lon: form.gpsLon,
        accuracy: form.gpsAccuracy,
        altitude: form.gpsAltitude,
        time: now.toLocaleString("en-IN", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }),
        iso: now.toISOString(),
      };
    }
    upd(patch);
    closeCamera();
  }

  function closeCamera() {
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
    setCapturedFrame(null);
    setActiveCameraFor(null);
  }

  useEffect(() => {
    if (!caseId) return;
    getCaseDetail(caseId)
      .then(setCaseData)
      .catch(() => toast.error("Case not found"))
      .finally(() => setLoading(false));

    // Fetch existing case photos to pre-populate borrower/object photos
    getCasePhotos(caseId).then((photos) => {
      const byType: Record<string, { viewUrl: string | null; lat: number | null; lon: number | null; capturedAt: string | null }> = {};
      photos.forEach((p) => {
        if (!byType[p.photo_type]) {
          byType[p.photo_type] = { viewUrl: p.view_url, lat: p.latitude, lon: p.longitude, capturedAt: p.captured_at };
        }
      });
      setExistingPhotos(byType);
    }).catch(() => {});

    const onPos = (p: GeolocationPosition) => upd({
      gpsLat: p.coords.latitude,
      gpsLon: p.coords.longitude,
      gpsAccuracy: p.coords.accuracy ?? null,
      gpsAltitude: p.coords.altitude ?? null,
    });
    // Fast first fix (coarse/cached, ~1s, capped at 5s), then refine precisely.
    navigator.geolocation?.getCurrentPosition(onPos, () => {}, {
      enableHighAccuracy: false, timeout: 5000, maximumAge: 60000,
    });
    const wid = navigator.geolocation?.watchPosition(
      onPos,
      () => {},
      { enableHighAccuracy: true, timeout: 10000, maximumAge: 5000 },
    );
    return () => { if (wid) navigator.geolocation.clearWatch(wid); };
  }, [caseId]);

  // ── Derived ────────────────────────────────────────────────────────────────

  const customer = caseData?.customer;
  const loan = caseData?.loan;
  const totalOutstanding = loan?.total_outstanding ?? 0;
  const targetAmount = caseData?.target_amount ?? 0;
  const remainingAfterPayment = Math.max(0, totalOutstanding - (Number(form.amount) || 0));

  const distanceM = (form.gpsLat && form.gpsLon && customer)
    ? Math.round(haversineM(form.gpsLat, form.gpsLon, customer.latitude, customer.longitude))
    : null;
  const withinFence = distanceM !== null && distanceM <= 100;

  const sel = BORROWER_OUTCOMES.find((o) => o.value === form.outcome)
    ?? NOT_MET_OUTCOMES.find((o) => o.value === form.outcome);

  // Payment amount validation
  const amountNum = Number(form.amount) || 0;
  const amountExceedsTarget = amountNum > targetAmount && targetAmount > 0;

  const paymentValid =
    !sel?.needsPayment ||
    (amountNum > 0 && !amountExceedsTarget &&
      (form.paymentMode !== "CASH" || form.cashCounted) &&
      // UPI needs a transaction ref — unless the QR demo has already shown
      // "Payment received", in which case the payment is treated as confirmed.
      (form.paymentMode !== "UPI" || !!form.upiRef || qrPaidDemo) &&
      (form.paymentMode !== "CHEQUE" || (!!form.chequeNumber && !!form.chequeDate && !!form.chequeBank)));

  const ptpValid = !sel?.needsPTP || (!!form.ptpAmount && !!form.ptpDate);
  const escalationValid = !sel?.needsEscalation || form.escalationNotes.length >= 10;

  // Borrower must confirm the amount before a payment outcome can be submitted:
  // either a live OTP (verificationId) or the explicit offline path, which
  // requires a signature as interim proof until the borrower verifies later.
  const paymentVerified =
    !sel?.needsPayment || !!verificationId || (offlineAck && !!form.signatureUrl);

  // Once an OTP has been sent / verified (or offline chosen), the amount is
  // frozen with no change option — it's the exact figure the borrower is
  // confirming, so it can never be edited afterward (no increase/decrease
  // fraud). The backend independently binds the OTP to this amount and rejects
  // any collect whose amount differs, so this is enforced on both sides.
  const amountLocked = !!otp || !!verificationId || offlineAck;

  // Location gate — the visit can't be recorded until GPS is captured AND
  // geo-verified (within 100m of the customer). This mirrors the backend, which
  // hard-blocks an out-of-fence visit with a 403. The one exemption is the
  // ADDRESS_ISSUE outcome: reporting a wrong address means the agent can never
  // be within the fence of it, so the fence doesn't apply there.
  const locationCaptured = form.gpsLat != null && form.gpsLon != null;
  const locationVerified = withinFence || form.outcome === "ADDRESS_ISSUE";
  const locationReady = locationCaptured && locationVerified;

  // Every section in the right-hand column is gated on meetingType, so the
  // two-column split only earns its keep once that is answered.
  const splitLayout = Boolean(form.meetingType);

  const canSubmit = (() => {
    if (!form.meetingType) return false;
    if (!locationReady) return false;
    if (form.meetingType === "BORROWER")
      return !!form.outcome && paymentValid && paymentVerified && ptpValid && escalationValid;
    if (form.meetingType === "THIRD_PARTY")
      return !!form.personMet && !!form.outcome;
    if (form.meetingType === "NOT_MET")
      return !!form.outcome;
    return false;
  })();

  // ── Handlers ───────────────────────────────────────────────────────────────

  function selectMeetingType(type: MeetingType) {
    upd({
      meetingType: type,
      personMet: type === "BORROWER" ? "BORROWER" : null,
      customerMet: type === "BORROWER" ? true : false,
      outcome: null,
      amount: "", cashCounted: false, upiRef: "",
    });
  }

  function handleFileUpload(
    e: React.ChangeEvent<HTMLInputElement>,
    field: "receiptPhoto" | "agentPhoto" | "borrowerPhoto" | "objectPhoto",
  ) {
    const file = e.target.files?.[0]; if (!file) return;
    const reader = new FileReader();
    reader.onload = (ev) => {
      const patch: Partial<FormState> = { [field]: ev.target?.result as string };
      const gpsMap: Record<string, keyof FormState> = {
        agentPhoto: "agentPhotoGps",
        borrowerPhoto: "borrowerPhotoGps",
        objectPhoto: "objectPhotoGps",
      };
      const gpsKey = gpsMap[field];
      if (gpsKey && form.gpsLat && form.gpsLon) {
        (patch as any)[gpsKey] = {
          lat: form.gpsLat,
          lon: form.gpsLon,
          time: new Date().toLocaleString("en-IN", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }),
        };
      }
      upd(patch);
    };
    reader.readAsDataURL(file);
  }

  function handleDocUpload(e: React.ChangeEvent<HTMLInputElement>, category: string) {
    const file = e.target.files?.[0]; if (!file) return;
    const reader = new FileReader();
    reader.onload = (ev) => {
      upd({ documents: [...form.documents.filter((d) => d.category !== category), { category, url: ev.target?.result as string, name: file.name }] });
    };
    reader.readAsDataURL(file);
  }

  async function uploadRecording(blob: Blob | null, recorder: "agent" | "borrower"): Promise<string | undefined> {
    if (!blob || !caseId) return undefined;
    try {
      const { upload_url, key } = await getRecordingUploadUrl(caseId, recorder);
      await fetch(upload_url, { method: "PUT", body: blob, headers: { "Content-Type": "audio/webm" } });
      return key;
    } catch {
      return undefined;
    }
  }

  async function uploadGeoPhoto(
    base64: string | null,
    subject: "agent" | "borrower" | "object" | "signature",
  ): Promise<{ key: string; sha256: string } | undefined> {
    if (!base64 || !caseId) return undefined;
    try {
      const { upload_url, key } = await getPhotoUploadUrl(caseId, subject);
      const res = await fetch(base64);
      const blob = await res.blob();
      const buffer = await blob.arrayBuffer();
      const [, hashBuffer] = await Promise.all([
        fetch(upload_url, { method: "PUT", body: blob, headers: { "Content-Type": blob.type || "image/jpeg" } }),
        crypto.subtle.digest("SHA-256", buffer),
      ]);
      const sha256 = Array.from(new Uint8Array(hashBuffer)).map((b) => b.toString(16).padStart(2, "0")).join("");
      return { key, sha256 };
    } catch {
      return undefined;
    }
  }

  // The OTP is issued before the payment channel is chosen, so it binds to the
  // AMOUNT. If the amount changes, the prior verification (and any offline
  // acknowledgement / resend count) is stale — clear it so the agent must
  // re-verify. Mode is chosen AFTER the OTP, so a mode change must NOT reset it.
  useEffect(() => {
    setVerificationId(null);
    setOtp(null);
    setOtpCode("");
    setOtpError(null);
    setOfflineAck(false);
    setResendsUsed(0);
    setQrPaidDemo(false);
  }, [form.amount]);

  // Demo: every time the UPI QR becomes visible, start a FRESH waiting→received
  // cycle — reset to "waiting", then after QR_DEMO_DELAY_MS auto-flip to
  // "Payment received ✓" (chime + toast). Because this resets on open, closing
  // and reopening the QR always shows "Waiting…" again first. (qrPaidDemo is
  // intentionally NOT a dependency, so the flip-to-paid doesn't restart it.)
  useEffect(() => {
    if (form.paymentMode !== "UPI" || !showQR || amountNum <= 0 || amountExceedsTarget) {
      setQrPaidDemo(false);
      return;
    }
    setQrPaidDemo(false);   // reopen always starts on "Waiting…"
    const t = setTimeout(() => {
      setQrPaidDemo(true);
      playSuccessChime();
      toast.success(`Payment received · ₹${amountNum.toLocaleString("en-IN")}`, { icon: "✅" });
    }, QR_DEMO_DELAY_MS);
    return () => clearTimeout(t);
  }, [form.paymentMode, showQR, amountNum, amountExceedsTarget]);

  async function handleSendOtp() {
    if (!caseId || amountNum <= 0) return;
    const isResend = !!otp;
    if (isResend && resendsUsed >= 3) return;
    setOtpError(null);
    setOtpSending(true);
    try {
      const res = await sendPaymentOtp(caseId, { amount: amountNum });
      setOtp({ id: res.otp_id, maskedPhone: res.masked_phone, expiresAt: res.expires_at });
      setOtpCode("");
      if (isResend) setResendsUsed((n) => n + 1);
      toast.success(`OTP sent to borrower (${res.masked_phone})`);
    } catch (err: any) {
      const detail = err?.response?.data?.detail;
      setOtpError(typeof detail === "string" ? detail : "Could not send OTP. Please retry.");
    } finally {
      setOtpSending(false);
    }
  }

  async function handleVerifyOtp() {
    if (!caseId || !otp || otpCode.length < 4) return;
    setOtpError(null);
    setOtpVerifying(true);
    try {
      const res = await verifyPaymentOtp(caseId, { otp_id: otp.id, code: otpCode });
      if (res.verified) {
        setVerificationId(res.otp_id);
        setOfflineAck(false);
        toast.success("Borrower verified the amount ✓");
      }
    } catch (err: any) {
      const detail = err?.response?.data?.detail;
      setOtpError(typeof detail === "string" ? detail : "Incorrect OTP. Please retry.");
    } finally {
      setOtpVerifying(false);
    }
  }

  async function handleSubmit() {
    if (!caseId || !canSubmit) return;
    // Hard location guard — belt-and-suspenders on top of canSubmit, so a visit
    // can NEVER be submitted before GPS is captured and geo-verified (the
    // backend 403s it anyway). ADDRESS_ISSUE is the one fence-exempt outcome.
    if (!locationReady) {
      toast.error(
        !locationCaptured
          ? "Waiting for GPS location — enable location and hold still."
          : `You are ${distanceM}m from the customer's address — must be within 100m to record this visit.`
      );
      return;
    }
    setSubmitting(true);
    try {
      // Build notes — prepend third-party context; append customer statement if given
      let finalNotes = form.notes;
      if (form.meetingType === "THIRD_PARTY" && form.thirdPartyName) {
        const relation = THIRD_PARTY_OPTIONS.find(p => p.value === form.personMet)?.label ?? form.personMet;
        finalNotes = `Met with: ${form.thirdPartyName} (${relation}). ${form.notes}`.trim();
      }
      if (form.borrowerTone) {
        finalNotes = `[Tone: ${form.borrowerTone}]${finalNotes ? " " + finalNotes : ""}`;
      }
      if (form.outcome === "DECEASED" && form.informantName) {
        const rel = form.informantRelation ? ` (${form.informantRelation})` : "";
        finalNotes = `Informant: ${form.informantName}${rel}. ${finalNotes}`.trim();
      }
      if (form.customerStatement.trim()) {
        finalNotes = finalNotes
          ? `${finalNotes}\n\nCUSTOMER STATEMENT: ${form.customerStatement.trim()}`
          : `CUSTOMER STATEMENT: ${form.customerStatement.trim()}`;
      }

      // Upload recordings in parallel with photos
      const [agentRecKey, borrowerRecKey] = await Promise.all([
        uploadRecording(form.agentRecordingBlob, "agent"),
        uploadRecording(form.borrowerRecordingBlob, "borrower"),
      ]);

      // Upload only NEW (freshly captured) photos — skip pre-populated "from prev visit" ones
      const [agentResult, borrowerResult, objectResult, signatureResult] = await Promise.all([
        form.agentPhotoFromPrev ? undefined : uploadGeoPhoto(form.agentPhoto, "agent"),
        form.borrowerPhotoFromPrev ? undefined : uploadGeoPhoto(form.borrowerPhoto, "borrower"),
        form.objectPhotoFromPrev ? undefined : uploadGeoPhoto(form.objectPhoto, "object"),
        uploadGeoPhoto(form.signatureUrl, "signature"),
      ]);
      const deviceId = navigator.userAgent?.substring(0, 200) || undefined;

      const visitRes = await recordVisit(caseId, {
        check_in_latitude: form.gpsLat ?? 28.4595,
        check_in_longitude: form.gpsLon ?? 77.0266,
        customer_met: form.customerMet!,
        outcome: form.outcome!,
        person_met: (form.personMet ?? undefined) as any,
        default_reason: (form.defaultReason ?? undefined) as any,
        not_met_reason: (form.notMetReason || undefined) as any,
        notes: finalNotes || undefined,
        consent_given: form.consentGiven || undefined,
        property_type: form.propertyType || undefined,
        occupancy_status: form.occupancyStatus || undefined,
        vehicle_present: form.vehiclePresent ?? undefined,
        business_running: form.businessRunning ?? undefined,
        // Agent photo — full geo metadata
        agent_photo_key: agentResult?.key,
        agent_photo_lat: agentResult ? (form.agentPhotoGps?.lat ?? form.gpsLat ?? undefined) : undefined,
        agent_photo_lon: agentResult ? (form.agentPhotoGps?.lon ?? form.gpsLon ?? undefined) : undefined,
        agent_photo_accuracy: agentResult ? (form.agentPhotoGps?.accuracy ?? form.gpsAccuracy ?? undefined) : undefined,
        agent_photo_altitude: agentResult ? (form.agentPhotoGps?.altitude ?? form.gpsAltitude ?? undefined) : undefined,
        agent_photo_captured_at: agentResult ? (form.agentPhotoGps?.iso ?? new Date().toISOString()) : undefined,
        agent_photo_sha256: agentResult?.sha256,
        // Borrower photo
        borrower_photo_key: borrowerResult?.key,
        borrower_photo_lat: borrowerResult ? (form.borrowerPhotoGps?.lat ?? form.gpsLat ?? undefined) : undefined,
        borrower_photo_lon: borrowerResult ? (form.borrowerPhotoGps?.lon ?? form.gpsLon ?? undefined) : undefined,
        borrower_photo_accuracy: borrowerResult ? (form.borrowerPhotoGps?.accuracy ?? form.gpsAccuracy ?? undefined) : undefined,
        borrower_photo_altitude: borrowerResult ? (form.borrowerPhotoGps?.altitude ?? form.gpsAltitude ?? undefined) : undefined,
        borrower_photo_captured_at: borrowerResult ? (form.borrowerPhotoGps?.iso ?? new Date().toISOString()) : undefined,
        borrower_photo_sha256: borrowerResult?.sha256,
        // Object / asset photo
        object_photo_key: objectResult?.key,
        object_photo_lat: objectResult ? (form.objectPhotoGps?.lat ?? form.gpsLat ?? undefined) : undefined,
        object_photo_lon: objectResult ? (form.objectPhotoGps?.lon ?? form.gpsLon ?? undefined) : undefined,
        object_photo_accuracy: objectResult ? (form.objectPhotoGps?.accuracy ?? form.gpsAccuracy ?? undefined) : undefined,
        object_photo_altitude: objectResult ? (form.objectPhotoGps?.altitude ?? form.gpsAltitude ?? undefined) : undefined,
        object_photo_captured_at: objectResult ? (form.objectPhotoGps?.iso ?? new Date().toISOString()) : undefined,
        object_photo_sha256: objectResult?.sha256,
        device_id: deviceId,
        agent_recording_key: agentRecKey,
        borrower_recording_key: borrowerRecKey,
        signature_key: signatureResult?.key,
      });

      // Recordings uploaded above (agentRecKey/borrowerRecKey) are never
      // transcribed unless something actually calls the endpoint for them —
      // queue it now that the Visit row exists. Fire-and-forget: never block
      // the visit-recorded success path on transcription finishing.
      const recorder = agentRecKey && borrowerRecKey ? "both" : agentRecKey ? "agent" : borrowerRecKey ? "borrower" : null;
      if (recorder) {
        queueVisitTranscription(visitRes.id, recorder).catch(() => {});
      }

      let receiptData: any = null;
      if (sel?.needsPayment && form.amount) {
        const maskedAcct = loan?.loan_account_masked ?? ("XXXX" + (loan?.loan_account_number ?? "").slice(-4));
        const res = await collectPayment(caseId, {
          amount: amountNum,
          mode: form.paymentMode as any,
          visit_id: visitRes.id,
          upi_reference: form.upiRef || undefined,
          cheque_number: form.chequeNumber || undefined,
          cheque_date: form.chequeDate || undefined,
          cheque_bank: form.chequeBank || undefined,
          bank_reference: form.neftRef || undefined,
          // Verified borrower OTP → backend writes the Payment as VERIFIED.
          // Absent (offline branch) → Payment stays PENDING_VERIFICATION.
          verification_id: verificationId || undefined,
        });
        receiptData = {
          receiptNumber: res.receipt_number,
          amount: amountNum,
          mode: form.paymentMode,
          upiRef: form.upiRef || undefined,
          customerName: customer?.full_name ?? "",
          loanAccount: maskedAcct,
          caseNumber: caseData?.case_number ?? "",
          agentName: user?.full_name ?? "",
          verified: !!verificationId,
          timestamp: new Date().toISOString(),
        };
      }

      if (sel?.needsPTP && form.ptpAmount && form.ptpDate) {
        await setPTP(caseId, {
          committed_amount: Number(form.ptpAmount),
          committed_date: form.ptpDate,
          customer_reason: form.ptpReason || undefined,
        });
      }

      // Auto-reoptimize from here — a visit just finished, so position and the
      // done-list both genuinely changed. Best-effort: never block the
      // visit-recorded success path on this.
      if (form.gpsLat != null && form.gpsLon != null) {
        try {
          await reoptimizeBeat(form.gpsLat, form.gpsLon);
        } catch {
          // route stays as-is; agent can still re-optimize manually from the beat map
        }
      }

      // Refresh beat context so My Cases and Beat Map reflect this visit immediately
      await refreshBeat();

      if (receiptData) {
        setReceipt(receiptData);
      } else {
        // Centred confirmation rather than a toast: on a phone the toast sat at
        // the edge of the screen while the page was already navigating away,
        // so the agent could not tell whether the visit had saved.
        setVisitDone({ outcomeLabel: sel?.label });
      }
    } catch (err: any) {
      const detail = err?.response?.data?.detail;
      toast.error(typeof detail === "string" ? detail : "Failed to submit. Please retry.");
    } finally {
      setSubmitting(false);
    }
  }

  // ─── Render ───────────────────────────────────────────────────────────────

  if (loading) return (
    <div className="flex items-center justify-center h-screen">
      <div className="w-8 h-8 border-4 border-brand-500 border-t-transparent rounded-full animate-spin" />
    </div>
  );

  const maskedAcct = loan?.loan_account_masked ?? ("XXXX" + (loan?.loan_account_number ?? "").slice(-4));

  return (
    <>
      {receipt && (
        <PaymentReceiptModal receipt={receipt} onClose={() => navigate(`/agent/cases/${caseId}`, { replace: true })} />
      )}

      {visitDone && (
        <VisitRecordedModal
          caseNumber={caseData?.case_number}
          customerName={customer?.full_name}
          outcomeLabel={visitDone.outcomeLabel}
          onClose={() => navigate(`/agent/cases/${caseId}`, { replace: true })}
        />
      )}

      {/* ── Camera modal ───────────────────────────────────────────────────── */}
      {activeCameraFor && (
        <div className="fixed inset-0 z-50 bg-black flex flex-col">
          {/* Header */}
          <div className="flex items-center justify-between px-4 py-3 bg-black/80">
            <p className="text-white text-sm font-semibold">
              {activeCameraFor === "agentPhoto" ? "Agent / Premises Photo" : activeCameraFor === "borrowerPhoto" ? "Borrower Photo" : "Vehicle / Asset Photo"}
            </p>
            <button onClick={closeCamera} className="text-white/70 hover:text-white">
              <X className="w-5 h-5" />
            </button>
          </div>

          {/* Viewfinder / preview */}
          <div className="flex-1 relative overflow-hidden">
            {!capturedFrame ? (
              // Front camera preview is mirrored, the way every phone camera
              // app shows a selfie. The saved frame is NOT mirrored — these are
              // evidence photos, and text in shot must stay readable.
              <video
                ref={videoRef}
                autoPlay
                playsInline
                muted
                className={`w-full h-full object-cover ${facingMode === "user" ? "scale-x-[-1]" : ""}`}
              />
            ) : (
              <img src={capturedFrame} alt="Preview" className="w-full h-full object-cover" />
            )}
            <canvas ref={canvasRef} className="hidden" />

            {/* GPS badge */}
            {form.gpsLat && (
              <div className="absolute top-3 left-3 bg-black/70 rounded-xl px-2.5 py-1.5 space-y-0.5">
                <div className="flex items-center gap-1.5">
                  <MapPin className="w-3 h-3 text-emerald-400" />
                  <span className="text-emerald-400 text-[9px] font-bold tracking-wide">GPS LOCKED</span>
                </div>
                <p className="text-white text-[10px] font-medium">Latitude: {form.gpsLat.toFixed(6)}  |  Longitude: {form.gpsLon!.toFixed(6)}</p>
                {form.gpsAccuracy != null && (
                  <p className="text-white/60 text-[10px]">Accuracy: {form.gpsAccuracy.toFixed(1)} m{form.gpsAltitude != null ? `  |  Altitude: ${form.gpsAltitude.toFixed(1)} m` : ""}</p>
                )}
              </div>
            )}
          </div>

          {/* Controls */}
          <div className="bg-black/80 px-6 py-5 flex items-center justify-center gap-6 safe-bottom">
            {!capturedFrame ? (
              // Shutter centred, flip parked to its right so the shutter stays
              // where the thumb expects it regardless of the flip button.
              <div className="relative w-full flex items-center justify-center">
                <button
                  onClick={snapPhoto}
                  aria-label="Take photo"
                  className="w-16 h-16 rounded-full bg-white flex items-center justify-center shadow-lg active:scale-95 transition-transform"
                >
                  <Camera className="w-7 h-7 text-slate-800" />
                </button>
                <button
                  onClick={flipCamera}
                  aria-label={facingMode === "user" ? "Switch to rear camera" : "Switch to front camera"}
                  title={facingMode === "user" ? "Switch to rear camera" : "Switch to front camera"}
                  className="absolute right-0 tap-target w-12 h-12 rounded-full bg-white/15 border border-white/25 flex items-center justify-center text-white active:scale-95 transition-transform"
                >
                  <RefreshCw className="w-5 h-5" />
                </button>
              </div>
            ) : (
              <>
                <button
                  onClick={() => {
                    const target = activeCameraFor!;
                    // Reopen on the same side the agent had chosen, not the
                    // default — a retake is a second attempt at the same shot.
                    const side = facingMode;
                    streamRef.current?.getTracks().forEach((t) => t.stop());
                    setCapturedFrame(null);
                    setTimeout(() => openCamera(target, side), 80);
                  }}
                  className="tap-target flex-1 py-3 rounded-xl border border-white/30 text-white text-sm font-medium"
                >
                  Retake
                </button>
                <button
                  onClick={confirmPhoto}
                  className="flex-1 py-3 rounded-xl bg-brand-500 text-white text-sm font-semibold"
                >
                  Use Photo
                </button>
              </>
            )}
          </div>
        </div>
      )}

      <div className="min-h-svh bg-slate-50 pb-32">

        {/* ── Header ───────────────────────────────────────────────────────── */}
        <div className="bg-white border-b border-slate-100 sticky top-0 z-20">
          <div className="flex items-center gap-3 p-4">
            <button onClick={() => navigate(-1)} className="p-1 -ml-1 text-slate-400">
              <ArrowLeft className="w-5 h-5" />
            </button>
            <div className="flex-1 min-w-0">
              <h1 className="font-bold text-slate-900 text-sm">Record Visit</h1>
              <p className="text-xs text-slate-400 truncate">{customer?.full_name} · {caseData?.case_number}</p>
            </div>
            <div className="flex items-center gap-2">
              <div className={`flex items-center gap-1 px-2 py-1 rounded-full text-xs font-medium ${withinFence ? "bg-success-50 text-success-700" : distanceM !== null ? "bg-danger-50 text-danger-700" : "bg-slate-100 text-slate-500"}`}>
                {withinFence ? <Unlock className="w-3 h-3" /> : <Lock className="w-3 h-3" />}
                {distanceM !== null ? `${distanceM}m` : "…"}
              </div>
              <SOSButton compact />
            </div>
          </div>
        </div>

        {/* Two columns at lg: data entry left, captured evidence right, so the
            agent can see what they have captured while completing the form.
            Below lg this is the exact single-column stack it has always been.

            The split only appears once "Who Did You Meet?" is answered. Every
            section on the right is gated on that answer, so splitting earlier
            left a dead 40% column beside a lone three-option question. Until
            then the form is one centred column, which also reads as "answer
            this first". */}
        <div
          className={`p-4 lg:p-6 space-y-4 ${
            splitLayout
              ? "lg:space-y-0 lg:grid lg:grid-cols-5 lg:gap-6 lg:items-start"
              : "lg:max-w-3xl lg:mx-auto"
          }`}
        >

          {/* ── LEFT: GPS, who was met, outcome, payment, PTP, escalation ── */}
          <div className={`space-y-4 ${splitLayout ? "lg:col-span-3" : ""}`}>

          {/* ── GPS status ─────────────────────────────────────────────────── */}
          <div className={`flex items-center gap-2 px-3 py-2 rounded-xl text-xs font-medium border ${withinFence ? "bg-success-50 border-success-200 text-success-700" : distanceM !== null ? "bg-amber-50 border-amber-200 text-amber-700" : "bg-slate-50 border-slate-200 text-slate-500"}`}>
            <MapPin className="w-3.5 h-3.5 flex-shrink-0" />
            {distanceM === null
              ? "Getting GPS…"
              : withinFence
                ? `✓ Within 100m (${distanceM}m) — GPS verified`
                : `${distanceM}m away — visit will be logged as unverified`}
          </div>

          {/* ══════════════════════════════════════════════════════════════════
              A. WHO DID YOU MEET?
          ══════════════════════════════════════════════════════════════════ */}
          <Section title="Who Did You Meet?" required>
            <div className="space-y-2">
              {/* BORROWER */}
              <button
                onClick={() => selectMeetingType("BORROWER")}
                className={`w-full flex items-center gap-3 px-4 py-3.5 rounded-xl border text-left transition-colors ${form.meetingType === "BORROWER" ? "border-brand-400 bg-brand-50" : "border-slate-200 bg-white hover:border-brand-200"}`}
              >
                <span className="text-2xl flex-shrink-0">👤</span>
                <div>
                  <p className={`text-sm font-semibold ${form.meetingType === "BORROWER" ? "text-brand-800" : "text-slate-800"}`}>Borrower is Present</p>
                  <p className="text-xs text-slate-400 mt-0.5">I am speaking directly with the borrower</p>
                </div>
                {form.meetingType === "BORROWER" && <CheckCircle className="w-5 h-5 text-brand-500 ml-auto flex-shrink-0" />}
              </button>

              {/* THIRD PARTY */}
              <button
                onClick={() => selectMeetingType("THIRD_PARTY")}
                className={`w-full flex items-center gap-3 px-4 py-3.5 rounded-xl border text-left transition-colors ${form.meetingType === "THIRD_PARTY" ? "border-warning-400 bg-warning-50" : "border-slate-200 bg-white hover:border-warning-200"}`}
              >
                <span className="text-2xl flex-shrink-0">👥</span>
                <div>
                  <p className={`text-sm font-semibold ${form.meetingType === "THIRD_PARTY" ? "text-warning-800" : "text-slate-800"}`}>Someone Else at Address</p>
                  <p className="text-xs text-slate-400 mt-0.5">Borrower not present — met a family member, neighbour or staff</p>
                </div>
                {form.meetingType === "THIRD_PARTY" && <CheckCircle className="w-5 h-5 text-warning-500 ml-auto flex-shrink-0" />}
              </button>

              {/* NOT MET */}
              <button
                onClick={() => selectMeetingType("NOT_MET")}
                className={`w-full flex items-center gap-3 px-4 py-3.5 rounded-xl border text-left transition-colors ${form.meetingType === "NOT_MET" ? "border-slate-400 bg-slate-100" : "border-slate-200 bg-white hover:border-slate-300"}`}
              >
                <span className="text-2xl flex-shrink-0">🚪</span>
                <div>
                  <p className={`text-sm font-semibold ${form.meetingType === "NOT_MET" ? "text-slate-800" : "text-slate-800"}`}>No One Available</p>
                  <p className="text-xs text-slate-400 mt-0.5">Premises locked, customer absent or unreachable</p>
                </div>
                {form.meetingType === "NOT_MET" && <CheckCircle className="w-5 h-5 text-slate-500 ml-auto flex-shrink-0" />}
              </button>
            </div>
          </Section>

          {/* ── Field Investigation (dropdowns) — shown before the visit outcome ── */}
          {form.meetingType && (
            <Section title="Field Investigation" badge="Optional">
              <div className="space-y-3">
                <div>
                  <label className="block text-sm font-medium text-slate-700 mb-1.5">Property Type</label>
                  <select value={form.propertyType} onChange={(e) => upd({ propertyType: e.target.value })} className="w-full rounded-xl border border-slate-200 bg-white text-sm px-3 py-3 focus:outline-none focus:ring-2 focus:ring-brand-300">
                    <option value="">— Select —</option>
                    <option value="OWNED">Owned</option>
                    <option value="RENTED">Rented</option>
                    <option value="COMMERCIAL">Commercial</option>
                    <option value="UNKNOWN">Unknown</option>
                  </select>
                </div>
                <div>
                  <label className="block text-sm font-medium text-slate-700 mb-1.5">Premises Status</label>
                  <select value={form.occupancyStatus} onChange={(e) => upd({ occupancyStatus: e.target.value })} className="w-full rounded-xl border border-slate-200 bg-white text-sm px-3 py-3 focus:outline-none focus:ring-2 focus:ring-brand-300">
                    <option value="">— Select —</option>
                    <option value="OCCUPIED">Occupied</option>
                    <option value="LOCKED">Locked / Closed</option>
                    <option value="VACATED">Vacated</option>
                    <option value="NOT_FOUND">Cannot Locate</option>
                  </select>
                </div>
                <div>
                  <label className="block text-sm font-medium text-slate-700 mb-1.5">Vehicle at premises?</label>
                  <select
                    value={form.vehiclePresent === null ? "" : form.vehiclePresent ? "yes" : "no"}
                    onChange={(e) => upd({ vehiclePresent: e.target.value === "" ? null : e.target.value === "yes" })}
                    className="w-full rounded-xl border border-slate-200 bg-white text-sm px-3 py-3 focus:outline-none focus:ring-2 focus:ring-brand-300"
                  >
                    <option value="">— Not assessed —</option>
                    <option value="yes">Yes</option>
                    <option value="no">No</option>
                  </select>
                </div>
                <div>
                  <label className="block text-sm font-medium text-slate-700 mb-1.5">Business actively running?</label>
                  <select
                    value={form.businessRunning === null ? "" : form.businessRunning ? "yes" : "no"}
                    onChange={(e) => upd({ businessRunning: e.target.value === "" ? null : e.target.value === "yes" })}
                    className="w-full rounded-xl border border-slate-200 bg-white text-sm px-3 py-3 focus:outline-none focus:ring-2 focus:ring-brand-300"
                  >
                    <option value="">— Not assessed —</option>
                    <option value="yes">Yes</option>
                    <option value="no">No</option>
                  </select>
                </div>
              </div>
            </Section>
          )}

          {/* ── Borrower Tone — just after Field Investigation (borrower path) ── */}
          {form.meetingType === "BORROWER" && (
            <Section title="Borrower Tone" badge="Recommended">
              <div className="grid grid-cols-3 gap-2">
                {([
                  { v: "COOPERATIVE", l: "Cooperative", emoji: "😊", sel: "border-success-400 bg-success-50 text-success-700" },
                  { v: "NEUTRAL",     l: "Neutral",     emoji: "😐", sel: "border-brand-400 bg-brand-50 text-brand-700" },
                  { v: "HOSTILE",     l: "Hostile",     emoji: "😠", sel: "border-danger-400 bg-danger-50 text-danger-700" },
                ] as const).map(({ v, l, emoji, sel: selCls }) => (
                  <button
                    key={v}
                    onClick={() => upd({ borrowerTone: form.borrowerTone === v ? null : v })}
                    className={`flex flex-col items-center gap-1 py-3 rounded-xl border text-xs font-medium transition-colors ${
                      form.borrowerTone === v ? selCls : "border-slate-200 bg-white text-slate-600 transition-colors hover:border-brand-200 hover:bg-brand-50/50"
                    }`}
                  >
                    <span className="text-2xl">{emoji}</span>{l}
                  </button>
                ))}
              </div>
            </Section>
          )}

          {/* ══════════════════════════════════════════════════════════════════
              THIRD PARTY PATH: Who specifically + notes
          ══════════════════════════════════════════════════════════════════ */}
          {form.meetingType === "THIRD_PARTY" && (
            <>
              <Section title="Who Did You Meet?" required>
                <div className="grid grid-cols-2 gap-2">
                  {THIRD_PARTY_OPTIONS.map((p) => (
                    <button
                      key={p.value}
                      onClick={() => upd({ personMet: p.value })}
                      className={`py-2.5 px-3 rounded-xl border text-sm font-medium text-left transition-colors ${form.personMet === p.value ? "border-warning-400 bg-warning-50 text-warning-800" : "border-slate-200 bg-white text-slate-600 transition-colors hover:border-brand-200 hover:bg-brand-50/50"}`}
                    >
                      {p.label}
                      {form.personMet === p.value && <CheckCircle className="w-3.5 h-3.5 text-warning-500 float-right mt-0.5" />}
                    </button>
                  ))}
                </div>
                <div className="mt-3">
                  <Input
                    label="Name of Person Met (optional)"
                    placeholder="Enter their name"
                    value={form.thirdPartyName}
                    onChange={(e) => upd({ thirdPartyName: e.target.value })}
                  />
                </div>
                <div className="mt-2 px-3 py-2 bg-amber-50 border border-amber-200 rounded-lg text-xs text-amber-700">
                  Loan details cannot be shared with third parties — RBI Fair Practice Code
                </div>
              </Section>

              <Section title="Outcome" required>
                <div className="grid grid-cols-2 gap-2">
                  {[
                    { value: "NOT_AVAILABLE" as VisitOutcome, label: "Not Available", desc: "Will revisit another time" },
                    { value: "REVISIT" as VisitOutcome,       label: "Revisit Required", desc: "Third party asked me to come back" },
                  ].map((o) => (
                    <button
                      key={o.value}
                      onClick={() => upd({ outcome: o.value })}
                      className={`py-3 px-3 rounded-xl border text-sm font-medium text-left transition-colors ${form.outcome === o.value ? "border-brand-400 bg-brand-50 text-brand-800" : "border-slate-200 bg-white text-slate-600 transition-colors hover:border-brand-200 hover:bg-brand-50/50"}`}
                    >
                      <p className="font-semibold">{o.label}</p>
                      <p className="text-xs text-slate-400 mt-0.5 font-normal">{o.desc}</p>
                    </button>
                  ))}
                </div>
              </Section>

              <Section title="Notes from Third Party" badge="Important">
                <p className="text-xs text-slate-500 mb-2">Record any useful information shared about the borrower — whereabouts, best time to visit, reason for absence, etc.</p>
                <NotesWithSpeech
                  value={form.notes}
                  onChange={(v) => upd({ notes: v })}
                  placeholder="E.g. Third party said borrower is working night shift, best time to visit is 7-9am…"
                />
              </Section>
            </>
          )}

          {/* ══════════════════════════════════════════════════════════════════
              NOT MET PATH: Reason + notes
          ══════════════════════════════════════════════════════════════════ */}
          {form.meetingType === "NOT_MET" && (
            <>
              <Section title="Reason Not Met">
                <div className="grid grid-cols-2 gap-1.5">
                  {NOT_MET_REASONS.map((r) => (
                    <button key={r.value} onClick={() => upd({ notMetReason: r.value })} className={`text-xs px-2.5 py-2 rounded-lg border text-left ${form.notMetReason === r.value ? "border-brand-400 bg-brand-50 text-brand-700" : "border-slate-200 bg-white text-slate-600 transition-colors hover:border-brand-200 hover:bg-brand-50/50"}`}>
                      {r.label}
                    </button>
                  ))}
                </div>
              </Section>

              <Section title="Outcome" required>
                <div className="space-y-2">
                  {NOT_MET_OUTCOMES.map((o) => (
                    <button
                      key={o.value}
                      onClick={() => upd({ outcome: o.value })}
                      className={`w-full py-3 px-4 rounded-xl border text-sm font-medium text-left transition-colors ${form.outcome === o.value ? "border-brand-400 bg-brand-50 text-brand-800" : "border-slate-200 bg-white text-slate-600 transition-colors hover:border-brand-200 hover:bg-brand-50/50"}`}
                    >
                      <p className="font-semibold">{o.label}</p>
                      <p className="text-xs text-slate-400 mt-0.5 font-normal">{o.desc}</p>
                    </button>
                  ))}
                </div>
                {sel?.needsEscalation && (
                  <div className="mt-3">
                    <label className="block text-sm font-medium text-slate-700 mb-1.5">Escalation Notes <span className="text-danger-500">*</span></label>
                    <NotesWithSpeech value={form.escalationNotes} onChange={(v) => upd({ escalationNotes: v })} placeholder="Describe the situation…" rows={3} />
                  </div>
                )}
              </Section>

              {/* DECEASED — informant details */}
              {form.outcome === "DECEASED" && (
                <Section title="Informant Details" badge="Required">
                  <p className="text-xs text-slate-500 mb-3">Record who informed you about the customer's passing.</p>
                  <div className="space-y-3">
                    <div>
                      <label className="block text-sm font-medium text-slate-700 mb-1.5">Informant's Name</label>
                      <input
                        className="w-full rounded-xl border border-slate-200 bg-white text-sm px-3 py-2.5 focus:outline-none focus:ring-2 focus:ring-brand-300 placeholder-slate-400"
                        placeholder="Enter their name"
                        value={form.informantName}
                        onChange={(e) => upd({ informantName: e.target.value })}
                      />
                    </div>
                    <div>
                      <p className="text-sm font-medium text-slate-700 mb-2">Relationship to Deceased</p>
                      <div className="grid grid-cols-3 gap-1.5">
                        {["Spouse", "Child", "Parent", "Sibling", "Relative", "Neighbour", "Other"].map((r) => (
                          <button
                            key={r}
                            onClick={() => upd({ informantRelation: form.informantRelation === r ? "" : r })}
                            className={`py-2 rounded-lg text-xs font-medium border transition-colors ${
                              form.informantRelation === r ? "border-brand-400 bg-brand-50 text-brand-700" : "border-slate-200 bg-white text-slate-600 transition-colors hover:border-brand-200 hover:bg-brand-50/50"
                            }`}
                          >
                            {r}
                          </button>
                        ))}
                      </div>
                    </div>
                  </div>
                </Section>
              )}

              <Section title="Notes" badge="Optional">
                <NotesWithSpeech value={form.notes} onChange={(v) => upd({ notes: v })} placeholder="Observations, next steps…" />
              </Section>
            </>
          )}

          {/* ══════════════════════════════════════════════════════════════════
              BORROWER PATH: Financial summary + full form
          ══════════════════════════════════════════════════════════════════ */}
          {form.meetingType === "BORROWER" && (
            <>
              {/* Loan financial summary — only visible because borrower confirmed */}
              {loan && (
                <div className="card bg-brand-50 border-brand-200 space-y-3">
                  <div className="flex items-center gap-2">
                    <CheckCircle className="w-4 h-4 text-brand-600 flex-shrink-0" />
                    <p className="text-xs font-semibold text-brand-700">Borrower confirmed — financial details disclosed</p>
                  </div>
                  <div className="grid grid-cols-2 gap-3">
                    <div className="bg-white rounded-lg px-3 py-2.5 border border-brand-100">
                      <p className="text-xs text-slate-400 mb-0.5">Total Outstanding</p>
                      <p className="text-base font-bold text-danger-700">₹{loan.total_outstanding?.toLocaleString("en-IN")}</p>
                      <p className="text-xs text-slate-400 mt-0.5">Full amount owed</p>
                    </div>
                    <div className="bg-white rounded-lg px-3 py-2.5 border border-brand-100">
                      <p className="text-xs text-slate-400 mb-0.5">Collection Target</p>
                      <p className="text-base font-bold text-brand-700">₹{caseData?.target_amount?.toLocaleString("en-IN")}</p>
                      <p className="text-xs text-slate-400 mt-0.5">Agency target today</p>
                    </div>
                  </div>
                  {customer?.is_hostile && (
                    <div className="flex items-center gap-2 px-3 py-2 rounded-lg bg-warning-100 border border-warning-200">
                      <AlertTriangle className="w-3.5 h-3.5 text-warning-700 flex-shrink-0" />
                      <p className="text-xs text-warning-700 font-medium">Hostile customer — exercise caution</p>
                    </div>
                  )}
                </div>
              )}

              {/* ── Visit notification WhatsApp ─────────────────────────── */}
              {/* ── B. Outcome ──────────────────────────────────────────────── */}
              <Section title="Visit Outcome" required>
                <select
                  value={form.outcome ?? ""}
                  onChange={(e) => upd({ outcome: (e.target.value as VisitOutcome) || null })}
                  className="w-full rounded-xl border border-slate-200 bg-white text-sm px-3 py-3 focus:outline-none focus:ring-2 focus:ring-brand-300"
                >
                  <option value="">— Select outcome —</option>
                  {BORROWER_OUTCOMES.map((o) => (
                    <option key={o.value} value={o.value}>{o.label} [{o.tag}]</option>
                  ))}
                </select>
                {sel && <p className="text-xs text-slate-500 mt-2 px-1">{sel.desc}</p>}
              </Section>

              {/* ── C. Reason for default ───────────────────────────────────── */}
              {form.outcome && !sel?.needsPayment && form.outcome !== "REVISIT" && (
                <Section title="Reason for Default" badge="Recommended">
                  <div className="space-y-1.5">
                    {DEFAULT_REASONS.map((r) => (
                      <button key={r.value} onClick={() => upd({ defaultReason: form.defaultReason === r.value ? null : r.value })} className={`w-full text-sm px-3 py-2.5 rounded-xl border text-left transition-colors ${form.defaultReason === r.value ? "border-brand-400 bg-brand-50 text-brand-700 font-medium" : "border-slate-200 bg-white text-slate-600 transition-colors hover:border-brand-200 hover:bg-brand-50/50"}`}>
                        {r.label}
                        {form.defaultReason === r.value && <CheckCircle className="w-4 h-4 text-brand-500 float-right mt-0.5" />}
                      </button>
                    ))}
                  </div>
                </Section>
              )}

              {/* ── D. Payment ──────────────────────────────────────────────── */}
              {sel?.needsPayment && (
                <Section title="Accept Payment" required badge="Required">
                  <Input
                    label={`Amount Collected (₹) — Max ₹${targetAmount.toLocaleString("en-IN")}`}
                    type="number"
                    placeholder="Enter exact amount received"
                    value={form.amount}
                    onChange={(e) => { upd({ amount: e.target.value }); setShowQR(false); }}
                    leftIcon={<IndianRupee className="w-4 h-4" />}
                    disabled={amountLocked}
                    className={amountLocked ? "bg-slate-100 text-slate-500 cursor-not-allowed" : ""}
                  />
                  {amountLocked && (
                    <p className="text-xs text-slate-500 flex items-center gap-1 mt-1.5">
                      <Lock className="w-3 h-3 flex-shrink-0" /> Amount locked to the figure the borrower is confirming — it cannot be changed
                    </p>
                  )}
                  {amountExceedsTarget && (
                    <p className="text-xs text-danger-600 mt-1.5 font-medium">Amount cannot exceed the target amount (₹{targetAmount.toLocaleString("en-IN")})</p>
                  )}

                  {/* ── Step 1 · Borrower OTP — MUST pass before the payment channel appears (2026-07-30) ── */}
                  <div className="mt-4 rounded-xl border border-slate-200 overflow-hidden">
                    <div className="flex items-center gap-2 px-4 py-2.5 bg-slate-50 border-b border-slate-200">
                      <ShieldCheck className="w-4 h-4 text-brand-600" />
                      <p className="text-sm font-semibold text-slate-700">OTP</p>
                      {verificationId && <CheckCircle className="w-4 h-4 text-success-600 ml-auto" />}
                    </div>
                    <div className="p-4">
                      {verificationId ? (
                        <div className="flex items-center gap-2 text-success-700 bg-success-50 border border-success-200 rounded-xl px-3 py-3">
                          <CheckCircle className="w-5 h-5 shrink-0" />
                          <p className="text-sm font-medium">Borrower confirmed ₹{amountNum.toLocaleString("en-IN")}. Choose the payment mode below.</p>
                        </div>
                      ) : offlineAck ? (
                        <div className="space-y-2">
                          <div className="flex items-start gap-2 text-amber-800 bg-amber-50 border border-amber-200 rounded-xl px-3 py-3">
                            <WifiOff className="w-5 h-5 shrink-0 mt-0.5" />
                            <p className="text-sm">Offline: payment will be saved as <strong>pending borrower verification</strong>. Capture the borrower's <strong>signature</strong> below; verify by OTP later.</p>
                          </div>
                          {!form.signatureUrl && <p className="text-xs text-danger-600 font-medium">• Signature required to submit an unverified payment</p>}
                          <button onClick={() => setOfflineAck(false)} className="text-xs text-brand-600 font-medium">← Use OTP instead</button>
                        </div>
                      ) : amountNum <= 0 || amountExceedsTarget ? (
                        <p className="text-xs text-slate-400">Enter a valid amount above, then send the borrower an OTP to unlock the payment options.</p>
                      ) : !otp ? (
                        <div className="space-y-3">
                          <p className="text-xs text-slate-500">The borrower must confirm <strong>₹{amountNum.toLocaleString("en-IN")}</strong> by OTP <strong>before</strong> any payment can be accepted — cash, UPI or NEFT.</p>
                          <Button onClick={handleSendOtp} disabled={otpSending} className="w-full">
                            <Send className="w-4 h-4 mr-1.5" /> {otpSending ? "Sending…" : "Send OTP to Borrower"}
                          </Button>
                          <button onClick={() => setOfflineAck(true)} className="w-full flex items-center justify-center gap-1.5 text-xs text-slate-400 font-medium">
                            <WifiOff className="w-3.5 h-3.5" /> Borrower has no network? Record unverified
                          </button>
                          {otpError && <p className="text-xs text-danger-600 font-medium text-center">{otpError}</p>}
                        </div>
                      ) : (
                        <div className="space-y-3">
                          <p className="text-xs text-slate-500 text-center">Enter the 4-digit OTP sent to <strong>{otp.maskedPhone}</strong></p>
                          <OtpInput value={otpCode} onChange={(v) => { setOtpCode(v); setOtpError(null); }} length={4} autoFocus disabled={otpVerifying} />
                          {otpError && <p className="text-xs text-danger-600 font-medium text-center">{otpError}</p>}
                          <Button onClick={handleVerifyOtp} disabled={otpVerifying || otpCode.length < 4} className="w-full">
                            {otpVerifying ? "Verifying…" : "Verify OTP"}
                          </Button>
                          <div className="flex items-center justify-between">
                            <button onClick={handleSendOtp} disabled={otpSending || resendsUsed >= 3} className="text-xs text-brand-600 font-medium disabled:text-slate-300">
                              {resendsUsed >= 3 ? "No resends left" : `Resend OTP (${3 - resendsUsed} left)`}
                            </button>
                            <button onClick={() => setOfflineAck(true)} className="text-xs text-slate-400 font-medium flex items-center gap-1"><WifiOff className="w-3.5 h-3.5" /> Verify later</button>
                          </div>
                        </div>
                      )}
                    </div>
                  </div>

                  {/* Step 2 · Payment channel — unlocks only after borrower OTP (or explicit offline) */}
                  {(verificationId || offlineAck) && (<>
                  <div className="mt-3">
                    <p className="text-sm font-medium text-slate-700 mb-2">Payment Mode</p>
                    <div className="grid grid-cols-3 gap-2">
                      {PAYMENT_MODES.map((m) => (
                        <button key={m.value} onClick={() => upd({ paymentMode: m.value, cashCounted: false, upiRef: "", chequeNumber: "", chequeDate: "", chequeBank: "", neftRef: "" })} className={`flex flex-col items-center gap-1 py-2.5 rounded-xl border text-xs font-medium ${form.paymentMode === m.value ? "border-brand-400 bg-brand-50 text-brand-700" : "border-slate-200 bg-white text-slate-600 transition-colors hover:border-brand-200 hover:bg-brand-50/50"}`}>
                          <span className="text-lg">{m.icon}</span>{m.label}
                        </button>
                      ))}
                    </div>
                  </div>

                  {/* CASH */}
                  {form.paymentMode === "CASH" && (
                    <div className="mt-3 space-y-3">
                      <div className="bg-amber-50 border border-amber-200 rounded-xl p-3 text-xs text-amber-800 space-y-1">
                        <p className="font-bold">Cash acceptance protocol:</p>
                        <p>1. Count notes in front of the customer</p>
                        <p>2. Confirm amount verbally with customer</p>
                        <p>3. Tick the box below to confirm</p>
                      </div>
                      <label className={`flex items-center gap-3 px-4 py-3 rounded-xl border cursor-pointer transition-colors ${form.cashCounted ? "border-success-400 bg-success-50" : "border-slate-200 bg-white"}`}>
                        <input type="checkbox" checked={form.cashCounted} onChange={(e) => upd({ cashCounted: e.target.checked })} className="w-5 h-5 rounded border-slate-300 text-success-600" />
                        <div>
                          <p className={`text-sm font-semibold ${form.cashCounted ? "text-success-700" : "text-slate-700"}`}>I have counted ₹{form.amount ? Number(form.amount).toLocaleString("en-IN") : "—"} in cash</p>
                          <p className="text-xs text-slate-400">in the customer's presence</p>
                        </div>
                      </label>
                    </div>
                  )}

                  {/* UPI */}
                  {form.paymentMode === "UPI" && (
                    <div className="mt-3 space-y-3">
                      <div className="rounded-xl border border-brand-100 bg-brand-50 overflow-hidden">
                        <button
                          onClick={() => {
                            if (amountExceedsTarget) {
                              toast.error(`Amount cannot exceed the target amount (₹${targetAmount.toLocaleString("en-IN")})`);
                              setShowQR(false);
                              return;
                            }
                            setShowQR((v) => !v);
                          }}
                          className="w-full flex items-center justify-between px-4 py-3 text-sm font-semibold text-brand-700"
                        >
                          <div className="flex items-center gap-2"><QrCode className="w-4 h-4" /> Show QR for Customer</div>
                          <span className="text-xs font-normal text-brand-400">{showQR ? "Hide" : "Show"}</span>
                        </button>
                        {amountExceedsTarget && (
                          <div className="px-4 pb-4">
                            <p className="text-xs text-danger-600 font-medium">Amount cannot exceed the target amount (₹{targetAmount.toLocaleString("en-IN")}) — QR unavailable</p>
                          </div>
                        )}
                        {showQR && !amountExceedsTarget && form.amount && Number(form.amount) > 0 && (
                          <div className="px-4 pb-4">
                            {qrPaidDemo ? (
                              // Payment received (auto-revealed a few seconds after the QR is shown).
                              <div className="bg-white rounded-2xl border-2 border-success-300 shadow-sm p-6 flex flex-col items-center gap-2">
                                <div className="w-16 h-16 rounded-full bg-success-100 flex items-center justify-center">
                                  <CheckCircle className="w-10 h-10 text-success-600" />
                                </div>
                                <p className="text-success-700 font-bold text-lg">Payment received</p>
                                <p className="text-success-800 text-3xl font-extrabold">₹{Number(form.amount).toLocaleString("en-IN")}</p>
                                <p className="text-xs text-slate-400 text-center">Received via UPI · Loan {maskedAcct}</p>
                              </div>
                            ) : (
                              <div className="bg-white rounded-2xl border border-brand-100 overflow-hidden shadow-sm">
                                <div className="bg-blue-700 px-4 py-3 flex items-center justify-between">
                                  <div>
                                    <p className="text-white font-bold text-base tracking-wide">ABC Bank</p>
                                    <p className="text-blue-200 text-xs">UPI Payment</p>
                                  </div>
                                  <div className="bg-white/20 rounded-full px-3 py-1">
                                    <p className="text-white text-xs font-semibold">Secure Pay</p>
                                  </div>
                                </div>
                                <div className="bg-blue-50 px-4 py-2 text-center border-b border-blue-100">
                                  <p className="text-blue-800 text-xs font-medium">Amount to Pay</p>
                                  <p className="text-blue-900 text-2xl font-bold">₹{Number(form.amount).toLocaleString("en-IN")}</p>
                                </div>
                                <div className="p-5 flex flex-col items-center gap-3">
                                  <div className="p-3 bg-white rounded-xl border border-slate-200 shadow-inner">
                                    <QRCode
                                      value={`upi://pay?pa=8015935790@ptsbi&pn=ABC+Bank&am=${form.amount}&tn=Loan+Recovery+${maskedAcct}&cu=INR`}
                                      size={192}
                                      bgColor="#ffffff"
                                      fgColor="#1e3a5f"
                                      level="M"
                                    />
                                  </div>
                                  <p className="text-xs text-slate-500 text-center">
                                    Scan with any UPI app · <strong>₹{Number(form.amount).toLocaleString("en-IN")}</strong> pre-filled
                                  </p>
                                  <div className="flex items-center gap-2 text-amber-600">
                                    <div className="w-3.5 h-3.5 border-2 border-amber-300 border-t-amber-600 rounded-full animate-spin" />
                                    <p className="text-xs font-medium">Waiting for payment…</p>
                                  </div>
                                  <p className="text-[10px] text-slate-400 text-center">Loan {maskedAcct}</p>
                                </div>
                              </div>
                            )}
                          </div>
                        )}
                      </div>
                      {!qrPaidDemo && (
                        <Input label="UPI Transaction ID *" placeholder="12-digit transaction ID from notification" value={form.upiRef} onChange={(e) => upd({ upiRef: e.target.value })} />
                      )}
                    </div>
                  )}

                  {/* CHEQUE */}
                  {form.paymentMode === "CHEQUE" && (
                    <div className="mt-3 space-y-3">
                      <div className="bg-blue-50 border border-blue-200 rounded-xl p-3 text-xs text-blue-800">
                        Fill details exactly as printed on the cheque.
                      </div>
                      <Input label="Cheque Number *" placeholder="6-digit cheque number" value={form.chequeNumber} onChange={(e) => upd({ chequeNumber: e.target.value })} />
                      <Input label="Cheque Date *" type="date" value={form.chequeDate} onChange={(e) => upd({ chequeDate: e.target.value })} />
                      <Input label="Issuing Bank *" placeholder="Bank name as written on cheque" value={form.chequeBank} onChange={(e) => upd({ chequeBank: e.target.value })} />
                      <div>
                        <p className="text-sm font-medium text-slate-700 mb-2">Cheque Photo <span className="text-danger-500">*</span></p>
                        {form.receiptPhoto ? (
                          <div className="relative"><img src={form.receiptPhoto} alt="Cheque" className="w-full h-36 object-cover rounded-xl border" /><button onClick={() => upd({ receiptPhoto: null })} className="absolute top-2 right-2 bg-danger-500 text-white rounded-full w-6 h-6 flex items-center justify-center"><X className="w-3.5 h-3.5" /></button></div>
                        ) : (
                          <button onClick={() => fileRefs.current["receipt"]?.click()} className="w-full h-24 border-2 border-dashed border-slate-300 rounded-xl flex flex-col items-center justify-center gap-1.5 text-slate-400 hover:border-brand-300 text-xs"><Camera className="w-5 h-5" /> Capture cheque photo</button>
                        )}
                        <input ref={(el) => { fileRefs.current["receipt"] = el; }} type="file" accept="image/*" capture="environment" className="hidden" onChange={(e) => handleFileUpload(e, "receiptPhoto")} />
                      </div>
                    </div>
                  )}

                  {/* NEFT/RTGS */}
                  {["NEFT", "RTGS"].includes(form.paymentMode) && (
                    <div className="mt-3 space-y-3">
                      <Input label="UTR / Reference Number *" placeholder="NEFT/IMPS UTR or transaction reference" value={form.neftRef} onChange={(e) => upd({ neftRef: e.target.value })} />
                      <div>
                        <p className="text-sm font-medium text-slate-700 mb-2">Payment Screenshot (optional)</p>
                        {form.receiptPhoto ? (
                          <div className="relative"><img src={form.receiptPhoto} alt="Screenshot" className="w-full h-28 object-cover rounded-xl border" /><button onClick={() => upd({ receiptPhoto: null })} className="absolute top-2 right-2 bg-danger-500 text-white rounded-full w-6 h-6 flex items-center justify-center"><X className="w-3.5 h-3.5" /></button></div>
                        ) : (
                          <button onClick={() => fileRefs.current["receipt"]?.click()} className="w-full h-16 border-2 border-dashed border-slate-200 rounded-xl flex items-center justify-center gap-2 text-slate-400 hover:border-brand-300 text-xs"><Upload className="w-4 h-4" /> Upload transaction screenshot</button>
                        )}
                        <input ref={(el) => { fileRefs.current["receipt"] = el; }} type="file" accept="image/*" className="hidden" onChange={(e) => handleFileUpload(e, "receiptPhoto")} />
                      </div>
                    </div>
                  )}
                  </>)}
                </Section>
              )}

              {/* ── E. PTP ──────────────────────────────────────────────────── */}
              {sel?.needsPTP && (
                <Section title={sel.needsPayment ? "PTP for Remaining Balance" : "Promise to Pay Details"} required badge="Required">
                  <div className="bg-brand-50 rounded-lg px-3 py-2 text-xs text-brand-700 mb-3">
                    Remaining after payment: ₹{remainingAfterPayment.toLocaleString("en-IN")}
                  </div>
                  <div className="space-y-3">
                    <Input label="Amount Customer Commits to Pay (₹) *" type="number" placeholder={`Up to ₹${remainingAfterPayment.toLocaleString("en-IN")}`} value={form.ptpAmount} onChange={(e) => upd({ ptpAmount: e.target.value })} leftIcon={<IndianRupee className="w-4 h-4" />} />
                    <Input label="Commitment Date *" type="date" value={form.ptpDate} min={new Date().toISOString().split("T")[0]} onChange={(e) => upd({ ptpDate: e.target.value })} leftIcon={<Calendar className="w-4 h-4" />} />
                    <div>
                      <label className="block text-sm font-medium text-slate-700 mb-1.5">Customer's Reason (optional)</label>
                      <textarea className="w-full rounded-xl border border-slate-200 bg-white text-sm p-3 focus:outline-none focus:ring-2 focus:ring-brand-300 resize-none placeholder-slate-400" placeholder="Why can't they pay today?" rows={2} value={form.ptpReason} onChange={(e) => upd({ ptpReason: e.target.value })} />
                    </div>
                  </div>
                </Section>
              )}

              {/* ── F. Escalation ───────────────────────────────────────────── */}
              {sel?.needsEscalation && (
                <Section title="F. Escalation Details" required badge="Manager Alert">
                  <div className="flex gap-2 items-start bg-danger-50 border border-danger-200 rounded-xl p-3 mb-3">
                    <AlertTriangle className="w-4 h-4 text-danger-600 flex-shrink-0 mt-0.5" />
                    <div>
                      <p className="text-xs font-bold text-danger-700">This will alert your supervisor immediately</p>
                      <p className="text-xs text-danger-600 mt-0.5">Document exactly what happened — this forms the audit trail.</p>
                    </div>
                  </div>
                  <label className="block text-sm font-medium text-slate-700 mb-1.5">Detailed Notes <span className="text-danger-500">*</span></label>
                  <NotesWithSpeech value={form.escalationNotes} onChange={(v) => upd({ escalationNotes: v })} placeholder="Describe exactly what happened — what was said, any threats, reason for refusal or dispute…" rows={5} />
                  <p className="text-xs text-slate-400 mt-1">{form.escalationNotes.length} chars (min 10)</p>
                  <div className="mt-3">
                    <p className="text-sm font-medium text-slate-700 mb-2">Witness present?</p>
                    <div className="flex gap-2">
                      {[true, false].map((v) => (
                        <button key={String(v)} onClick={() => upd({ witnessPresent: v })} className={`flex-1 py-2 rounded-lg text-sm font-medium border ${form.witnessPresent === v ? "border-brand-400 bg-brand-50 text-brand-700" : "border-slate-200 bg-white text-slate-600 transition-colors hover:border-brand-200 hover:bg-brand-50/50"}`}>{v ? "Yes" : "No"}</button>
                      ))}
                    </div>
                    {form.witnessPresent && (
                      <div className="mt-2"><Input label="Witness Name / Description" placeholder="Neighbor, security guard, family member…" value={form.witnessName} onChange={(e) => upd({ witnessName: e.target.value })} /></div>
                    )}
                  </div>
                </Section>
              )}

            </>
          )}

          </div>

          {/* ── RIGHT: captured evidence + what is still blocking submit.
                 Sticky so it stays in view while the left column scrolls.
                 Not rendered at all until there is something to put in it. ── */}
          {splitLayout && (
          <div className="space-y-4 mt-4 lg:mt-0 lg:col-span-2 lg:sticky lg:top-24">

          {/* ══════════════════════════════════════════════════════════════════
              FIELD INVESTIGATION & PHOTOS (always optional)
          ══════════════════════════════════════════════════════════════════ */}
          {form.meetingType && (
            <>
              <Section title="Photos & Documents" badge="Optional">
                {/* ── Three geo-tagged photo captures ── */}
                {(
                  [
                    { field: "agentPhoto" as const, gpsField: "agentPhotoGps" as const, prevField: "agentPhotoFromPrev" as const, photoType: "AGENT_SELFIE", label: "Agent / Premises Photo", hint: "Capture yourself at the address", icon: "🏠", alwaysFresh: true },
                    { field: "borrowerPhoto" as const, gpsField: "borrowerPhotoGps" as const, prevField: "borrowerPhotoFromPrev" as const, photoType: "BORROWER", label: "Borrower Photo", hint: "Photo of person met", icon: "👤", alwaysFresh: false },
                    { field: "objectPhoto" as const, gpsField: "objectPhotoGps" as const, prevField: "objectPhotoFromPrev" as const, photoType: "VEHICLE_ASSET", label: "Vehicle / Asset Photo", hint: "Vehicle, property or pledged asset", icon: "🚗", alwaysFresh: false },
                  ] as const
                ).map(({ field, gpsField, prevField, photoType, label, hint, icon, alwaysFresh }) => {
                  const photo = form[field];
                  const gps = form[gpsField];
                  const fromPrev = form[prevField];
                  const existing = existingPhotos[photoType];
                  const fallbackGps = form.gpsLat != null ? { lat: form.gpsLat, lon: form.gpsLon! } : null;
                  const displayGps = gps ?? fallbackGps;

                  // If no photo captured yet and there's an existing one from prior visit, offer to use it
                  if (!photo && existing?.viewUrl && !alwaysFresh) {
                    return (
                      <div key={field} className="mb-3">
                        <p className="text-sm font-medium text-slate-700 mb-1.5"><span className="mr-1.5">{icon}</span>{label}</p>
                        <div className="relative rounded-xl overflow-hidden border border-brand-200">
                          <img src={existing.viewUrl} alt={label} className="w-full h-36 object-cover opacity-80" />
                          <div className="absolute inset-0 flex flex-col justify-between p-2">
                            <div className="flex justify-end">
                              <button
                                onClick={() => openCamera(field)}
                                className="bg-white/90 text-slate-700 text-xs font-medium px-2.5 py-1 rounded-full shadow"
                              >
                                Retake
                              </button>
                            </div>
                            <div className="bg-brand-600/80 rounded-lg px-2 py-1.5">
                              <p className="text-white text-[10px] font-bold">FROM PREVIOUS VISIT</p>
                              {existing.lat != null && (
                                <p className="text-white/80 text-[10px]">Latitude: {existing.lat.toFixed(6)}  |  Longitude: {existing.lon!.toFixed(6)}</p>
                              )}
                            </div>
                          </div>
                          <button
                            onClick={() => upd({ [field]: existing.viewUrl, [prevField]: true } as any)}
                            className="absolute bottom-0 left-0 right-0 bg-brand-500 text-white text-xs font-semibold py-1.5 text-center"
                          >
                            Use existing photo ✓
                          </button>
                        </div>
                      </div>
                    );
                  }

                  return (
                    <div key={field} className="mb-3">
                      <p className="text-sm font-medium text-slate-700 mb-1.5">
                        <span className="mr-1.5">{icon}</span>{label}
                        {alwaysFresh && <span className="ml-2 text-[10px] text-slate-400 font-normal">fresh photo required each visit</span>}
                      </p>
                      {photo ? (
                        <div className="relative">
                          <img src={photo} alt={label} className="w-full h-36 object-cover rounded-xl" />
                          <button
                            onClick={() => upd({ [field]: null, [gpsField]: null, [prevField]: false } as any)}
                            className="absolute top-2 right-2 bg-danger-500 text-white rounded-full w-6 h-6 flex items-center justify-center"
                          >
                            <X className="w-3.5 h-3.5" />
                          </button>
                          {fromPrev ? (
                            <div className="absolute bottom-0 left-0 right-0 bg-brand-600/80 rounded-b-xl px-2.5 py-1.5">
                              <p className="text-white text-[10px] font-bold">FROM PREVIOUS VISIT — using existing</p>
                            </div>
                          ) : (
                            <div className="absolute bottom-0 left-0 right-0 bg-black/60 rounded-b-xl px-2.5 py-2">
                              <div className="flex items-center gap-1.5 mb-0.5">
                                <MapPin className="w-3 h-3 text-emerald-400 flex-shrink-0" />
                                <span className="text-emerald-400 text-[9px] font-bold tracking-wide">GPS TAGGED</span>
                              </div>
                              {displayGps ? (
                                <div className="text-white text-[10px] font-medium space-y-0.5">
                                  <p>Latitude: {displayGps.lat.toFixed(6)}  |  Longitude: {displayGps.lon.toFixed(6)}</p>
                                  {gps?.accuracy != null && <p className="text-white/70">Accuracy: {gps.accuracy.toFixed(1)} m{gps.altitude != null ? `  |  Altitude: ${gps.altitude.toFixed(1)} m` : ""}</p>}
                                  {gps?.time && <p className="text-white/60">{gps.time}</p>}
                                </div>
                              ) : (
                                <p className="text-white/60 text-[10px]">GPS unavailable</p>
                              )}
                            </div>
                          )}
                        </div>
                      ) : (
                        <button
                          onClick={() => openCamera(field)}
                          className="w-full h-20 border-2 border-dashed border-slate-200 rounded-xl flex items-center justify-center gap-2 text-slate-400 hover:border-brand-300 text-xs"
                        >
                          <Camera className="w-4 h-4" /> {hint}
                        </button>
                      )}
                    </div>
                  );
                })}

                {/* ── Documents collected ── */}
                <div className="mt-1">
                  <p className="text-sm font-medium text-slate-700 mb-2">Documents Collected</p>
                  <div className="grid grid-cols-2 gap-2">
                    {DOC_CATEGORIES.map((cat) => {
                      const uploaded = form.documents.find((d) => d.category === cat.id);
                      return (
                        <button key={cat.id} onClick={() => fileRefs.current[cat.id]?.click()} className={`flex items-center gap-2 p-2.5 rounded-xl border text-xs font-medium text-left ${uploaded ? "border-success-400 bg-success-50 text-success-700" : "border-slate-200 bg-white text-slate-600 transition-colors hover:border-brand-200 hover:bg-brand-50/50"}`}>
                          <span className="text-base flex-shrink-0">{cat.icon}</span>
                          <span className="truncate">{cat.label}</span>
                          {uploaded && <CheckCircle className="w-3.5 h-3.5 text-success-500 ml-auto flex-shrink-0" />}
                          <input ref={(el) => { fileRefs.current[cat.id] = el; }} type="file" accept="image/*,application/pdf" className="hidden" onChange={(e) => handleDocUpload(e, cat.id)} />
                        </button>
                      );
                    })}
                  </div>
                </div>
              </Section>
            </>
          )}

          {/* ══════════════════════════════════════════════════════════════════
              G. NOTES & CONSENT — always last for borrower path
          ══════════════════════════════════════════════════════════════════ */}
          {form.meetingType === "BORROWER" && (
            <Section title="G. Notes & Consent" badge="Optional">
              {/* Agent's visit report */}
              <div>
                <p className="text-sm font-medium text-slate-700 mb-1.5">Agent's Visit Report</p>
                <VoiceNoteBox
                  textValue={form.notes}
                  onTextChange={(v) => upd({ notes: v })}
                  placeholder="Your observations, customer mood, next steps, follow-up items…"
                  hasBlob={!!form.agentRecordingBlob}
                  duration={form.agentRecordingDuration}
                  onAudioComplete={(blob, dur) => upd({ agentRecordingBlob: blob, agentRecordingDuration: dur })}
                  onAudioRemove={() => upd({ agentRecordingBlob: null, agentRecordingDuration: 0 })}
                />
              </div>

              {/* Customer's verbal statement */}
              <div className="mt-4">
                <p className="text-sm font-medium text-slate-700 mb-1">Customer's Statement</p>
                <p className="text-xs text-slate-400 mb-1.5">Record what the customer said — commitments, reasons, concerns.</p>
                <VoiceNoteBox
                  textValue={form.customerStatement}
                  onTextChange={(v) => upd({ customerStatement: v })}
                  placeholder="E.g. Customer said salary credit expected on 30th, will transfer immediately after…"
                  rows={3}
                  hasBlob={!!form.borrowerRecordingBlob}
                  duration={form.borrowerRecordingDuration}
                  onAudioComplete={(blob, dur) => upd({ borrowerRecordingBlob: blob, borrowerRecordingDuration: dur })}
                  onAudioRemove={() => upd({ borrowerRecordingBlob: null, borrowerRecordingDuration: 0 })}
                />
              </div>

              {/* Signature */}
              <div className="mt-4">
                <SignaturePad onCapture={(url) => upd({ signatureUrl: url })} label="Customer's Signature (optional)" />
              </div>

              {/* RBI consent */}
              <label className="flex items-start gap-3 cursor-pointer mt-4">
                <input type="checkbox" checked={form.consentGiven} onChange={(e) => upd({ consentGiven: e.target.checked })} className="mt-0.5 w-4 h-4 rounded border-slate-300 text-brand-600 focus:ring-brand-400" />
                <span className="text-xs text-slate-600 leading-relaxed">Visit conducted within RBI Fair Practice Code — customer was informed of outstanding amount, no threats or coercion were used.</span>
              </label>
            </Section>
          )}

          {/* ── Validation summary ───────────────────────────────────────────── */}
          {!canSubmit && form.meetingType && (
            <div className="bg-amber-50 border border-amber-200 rounded-2xl p-4 text-xs text-amber-800 space-y-1">
              <p className="font-bold mb-1">Still required:</p>
              {!locationCaptured && <p>• Waiting for GPS location — enable location and hold still</p>}
              {locationCaptured && !locationVerified && <p>• Move within 100m of the customer's address to verify GPS (or select "Address Issue" if the address is wrong)</p>}
              {form.meetingType === "BORROWER" && !form.outcome && <p>• Select visit outcome</p>}
              {form.meetingType === "THIRD_PARTY" && !form.personMet && <p>• Select who you met</p>}
              {form.meetingType === "THIRD_PARTY" && !form.outcome && <p>• Select outcome</p>}
              {form.meetingType === "NOT_MET" && !form.outcome && <p>• Select outcome</p>}
              {sel?.needsPayment && (!form.amount || amountNum <= 0) && <p>• Enter payment amount</p>}
              {sel?.needsPayment && amountExceedsTarget && <p>• Payment amount exceeds target amount (max ₹{targetAmount.toLocaleString("en-IN")})</p>}
              {sel?.needsPayment && form.paymentMode === "CASH" && !form.cashCounted && <p>• Confirm cash counted</p>}
              {sel?.needsPayment && form.paymentMode === "UPI" && !form.upiRef && !qrPaidDemo && <p>• Enter UPI transaction ID (or show the QR and wait for "Payment received")</p>}
              {sel?.needsPayment && form.paymentMode === "CHEQUE" && (!form.chequeNumber || !form.chequeDate || !form.chequeBank) && <p>• Complete cheque details</p>}
              {sel?.needsPayment && paymentValid && !paymentVerified && <p>• Verify the amount with the borrower via OTP (or use the offline option + signature)</p>}
              {sel?.needsPTP && (!form.ptpAmount || !form.ptpDate) && <p>• Complete PTP commitment details</p>}
              {sel?.needsEscalation && form.escalationNotes.length < 10 && <p>• Add escalation notes (min 10 chars)</p>}
            </div>
          )}

          </div>
          )}
        </div>

        {/* ── Fixed bottom submit — constrained to mobile frame ────────── */}
        {/* No rail offset here: Record Visit is its own top-level route, outside
            AgentLayout, so there is no sidebar to clear. Offsetting it pushed
            the bar 220px right of the content it belongs to. */}
        <div className="fixed bottom-0 left-0 right-0 z-20 pointer-events-none">
          <div className="max-w-md md:max-w-none mx-auto pointer-events-auto bg-white border-t border-slate-100 p-4 lg:px-6">
            <div className="flex items-center justify-between text-xs text-slate-400 mb-2">
              <span>{withinFence ? "✓ GPS verified" : distanceM !== null ? `⚠ ${distanceM}m (unverified)` : "Getting GPS…"}</span>
              {form.outcome && <span className="font-mono font-bold text-slate-600 bg-slate-100 px-2 py-0.5 rounded">{sel?.tag ?? form.outcome}</span>}
            </div>
            <Button
              fullWidth
              onClick={handleSubmit}
              loading={submitting}
              disabled={!canSubmit}
              className={!canSubmit ? "!bg-slate-200 !text-slate-400 hover:!bg-slate-200 active:!bg-slate-200 !shadow-none" : undefined}
            >
              {canSubmit ? <CheckCircle className="w-4 h-4" /> : <Lock className="w-4 h-4" />}
              {submitting
                ? "Submitting…"
                : !locationReady
                  ? (locationCaptured ? `Move within 100m (${distanceM}m away)` : "Waiting for GPS…")
                  : "Submit Visit Record"}
            </Button>
          </div>
        </div>
      </div>
    </>
  );
}

// ─── Notes with speech-to-text ───────────────────────────────────────────────

function NotesWithSpeech({ value, onChange, placeholder, rows = 4 }: {
  value: string; onChange: (v: string) => void; placeholder?: string; rows?: number;
}) {
  const rec = useRecordAndTranscribe((text) => onChange(value + (value ? " " : "") + text));
  const handleMic = () => (rec.isRecording ? rec.stop() : rec.start());
  return (
    <div className="relative">
      <textarea
        className="w-full rounded-xl border border-slate-200 bg-white text-sm p-3 pr-12 focus:outline-none focus:ring-2 focus:ring-brand-300 resize-none placeholder-slate-400"
        placeholder={placeholder}
        rows={rows}
        value={value}
        onChange={(e) => onChange(e.target.value)}
      />
      <button
        type="button"
        onClick={handleMic}
        disabled={rec.isTranscribing}
        title={rec.isRecording ? "Stop recording" : "Record a voice note (Hindi/English → English)"}
        className={`absolute right-3 top-3 p-1.5 rounded-lg transition-colors ${rec.isRecording ? "bg-danger-100 text-danger-600 animate-pulse" : "bg-slate-100 text-slate-500 hover:bg-brand-100 hover:text-brand-600"}`}
      >
        {rec.isRecording ? <MicOff className="w-4 h-4" /> : <Mic className="w-4 h-4" />}
      </button>
      {rec.isRecording && (
        <p className="text-xs text-danger-600 mt-1 flex items-center gap-1">
          <span className="w-2 h-2 bg-danger-500 rounded-full animate-ping inline-block" /> Recording… {rec.fmt(rec.elapsed)} (Hindi or English)
        </p>
      )}
      {rec.isTranscribing && (
        <p className="text-xs text-slate-400 mt-1 flex items-center gap-1">
          <span className="w-2 h-2 bg-slate-400 rounded-full animate-pulse inline-block" /> Transcribing…
        </p>
      )}
    </div>
  );
}

// ─── Voice note box — single mic button: records audio + transcribes via Whisper ──

function VoiceNoteBox({ textValue, onTextChange, placeholder, rows = 4, hasBlob, duration, onAudioComplete, onAudioRemove }: {
  textValue: string;
  onTextChange: (v: string) => void;
  placeholder?: string;
  rows?: number;
  hasBlob: boolean;
  duration: number;
  onAudioComplete: (blob: Blob, dur: number) => void;
  onAudioRemove: () => void;
}) {
  const baseTextRef = useRef("");
  const rec = useRecordAndTranscribe(
    (text) => onTextChange(baseTextRef.current + (baseTextRef.current ? " " : "") + text),
    onAudioComplete,
  );

  function handleMic() {
    if (rec.isRecording) { rec.stop(); return; }
    // Capture current text as the baseline the transcript gets appended to
    baseTextRef.current = textValue;
    rec.start();
  }

  return (
    <div>
      <div className="relative">
        <textarea
          className="w-full rounded-xl border border-slate-200 bg-white text-sm p-3 pb-10 focus:outline-none focus:ring-2 focus:ring-brand-300 resize-none placeholder-slate-400"
          placeholder={placeholder}
          rows={rows}
          value={textValue}
          onChange={(e) => onTextChange(e.target.value)}
        />
        <button
          type="button"
          onClick={handleMic}
          disabled={rec.isTranscribing}
          className={`absolute bottom-2.5 right-2.5 flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-semibold transition-colors ${
            rec.isRecording
              ? "bg-danger-100 text-danger-700"
              : "bg-slate-100 text-slate-500 hover:bg-brand-100 hover:text-brand-600"
          }`}
          title={rec.isRecording ? "Stop recording" : "Record a voice note (Hindi/English → English)"}
        >
          {rec.isRecording
            ? <MicOff className="w-3.5 h-3.5 animate-pulse" />
            : <Mic className="w-3.5 h-3.5" />
          }
          <span>{rec.isRecording ? rec.fmt(rec.elapsed) : rec.isTranscribing ? "Transcribing…" : "Record"}</span>
          {rec.isRecording && <span className="w-1.5 h-1.5 rounded-full bg-danger-500 animate-ping" />}
        </button>
      </div>
      {hasBlob && !rec.isRecording && (
        <div className="flex items-center gap-2 mt-1.5 px-3 py-1.5 rounded-xl border border-success-200 bg-success-50">
          <Mic className="w-3.5 h-3.5 text-success-600 flex-shrink-0" />
          <span className="text-xs font-medium text-success-700 flex-1">Audio saved — {rec.fmt(duration)}</span>
          <button type="button" onClick={() => { rec.reset(); onAudioRemove(); }} className="text-danger-400 hover:text-danger-600">
            <X className="w-3.5 h-3.5" />
          </button>
        </div>
      )}
    </div>
  );
}

// ─── Section wrapper ──────────────────────────────────────────────────────────

function Section({ title, children, required, badge }: {
  title: string; children: React.ReactNode; required?: boolean; badge?: string;
}) {
  return (
    <div className="bg-white rounded-2xl border border-slate-100 shadow-sm overflow-hidden">
      <div className="flex items-center justify-between px-4 py-3 border-b border-slate-50">
        <h2 className="text-sm font-bold text-slate-800">{title}</h2>
        {badge && (
          <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${required ? "bg-danger-50 text-danger-600" : "bg-slate-100 text-slate-500"}`}>{badge}</span>
        )}
      </div>
      <div className="p-4">{children}</div>
    </div>
  );
}
