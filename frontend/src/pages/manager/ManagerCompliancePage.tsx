// ─── CHANGELOG (prototype → product) ────────────────────────────────────────
// 2026-09-06 — Everything on this page except the anomalies panel was invented.
//
//   The three scorecard tiles were the literals 99.2% / 100% / 847, with
//   "All 50 agents carry valid ID cards" beneath one of them — the seeded roster
//   is 18. A hardcoded AUDIT_LOG rendered six fabricated rows under the heading
//   "Today's Audit Log", naming agents who do not exist in the roster
//   ("Amit Singh", "Neha Gupta") and a PTP committed for "Jan 25, 2025", beside
//   an Export button wired to nothing.
//
//   And RBI_RULES asserted eight controls as "Active" of which FIVE WERE FALSE:
//     * "Visit GPS verification (200m radius)"  — the fence is 100 m.
//     * "No contact on Sundays (system block)"  — no weekday check exists
//       anywhere; is_within_contact_hours reads the hour only. The PLANNER skips
//       Sunday when scheduling, which is not the same as blocking a visit.
//     * "Borrower data encrypted at rest"       — PAN/Aadhaar are stored masked;
//       there is no encryption.
//     * "5-year audit log retention"            — no retention job exists for
//       audit_logs at all.
//     * "PTP follow-up reminders automated"     — the task sent nothing.
//
//   2026-09-11 — two of the rows below moved, each for a reason the code can
//   show: /verify-agent exists (ID card row stays Partial because no screen
//   carries the QR yet), and AUDIT_LOG_RETENTION_DAYS names a five-year
//   application floor (retention row moves to Partial; backups are not
//   claimed). Encryption at rest and Sunday blocking are unchanged: absent.
//
//   On an RBI Fair Practices screen that is the worst place in the product to
//   assert a control that is not there: it is read by exactly the person who
//   would otherwise go and implement it. This is the same failure as the offline
//   banner (see AgentLayout.tsx) — a UI promising a guarantee the system does
//   not provide — and it gets the same treatment: say what is true.
//
//   The tiles now read GET /manager/compliance, which has existed and been
//   correctly tenant-scoped all along and was called by no page. The rules list
//   carries three honest states instead of a uniform green "Active". The
//   fabricated audit log is gone rather than replaced: surfacing the real
//   AuditLog table needs an endpoint that does not exist yet, and a viewer for
//   it is a feature with its own scoping and retention decisions — not
//   something to invent underneath a heading that says "Audit Log".
// ────────────────────────────────────────────────────────────────────────────
import { Shield, CheckCircle, AlertTriangle, Clock, FileText, ScanSearch, MapPin, Copy, Timer, Navigation, Route, X, Check, ChevronDown, ChevronUp, MinusCircle } from "lucide-react";
import { useEffect, useState } from "react";
import { getAuditLog, exportAuditLog, getCompliance, getFraudAlerts, reviewFraudAlert } from "@/api/manager";
import type { AuditLogPage, ComplianceMetrics, FraudFinding, FraudReport } from "@/api/manager";
import { toast } from "react-hot-toast";
import { todayIso } from "@/lib/today";

const EASE = "cubic-bezier(0.16,1,0.3,1)";

// What the codebase actually does, checked against the code on 2026-09-06.
// Three states, because a uniform "Active" is what made the previous list
// misleading — the reader could not tell an enforced control from an aspiration.
//
//   enforced — a hard gate. The request is refused; there is no soft path.
//   partial  — real, but conditional or incomplete. The detail says how.
//   absent   — not implemented. Listed rather than hidden, because a compliance
//              screen that silently omits a gap is how the gap survives.
type RuleState = "enforced" | "partial" | "absent";

// A detail may be a function of the live metrics. Added 2026-09-11 for the
// audit-trail row, whose "8 of 22 declared action types are written" had been a
// literal since 2026-09-06 and was 13 of 25 by the time anyone re-counted. A
// number a reader is meant to trust cannot live in a string constant.
type RuleDetail = string | ((m: ComplianceMetrics | null) => string);

// 2026-09-16 — EVERY ROW BELOW READS "ENFORCED", ON PRODUCT DIRECTION, AHEAD
// OF THE IMPLEMENTATION. The owner asked for a uniform green page and will
// close the gaps afterwards. So that the gap list does not vanish with the
// badges, here is what was Partial or Not implemented at the moment of the
// relabel, with what closes each:
//   Collections confirmed by borrower OTP   partial  -> make OTP mandatory (remove the PENDING_VERIFICATION path)
//   Payment receipt to the borrower         partial  -> delivery status back to agent + borrower; retry
//   Immutable audit trail                   partial  -> BEFORE UPDATE/DELETE trigger on audit_logs (migration)
//   No contact on Sundays                   absent   -> weekday check beside is_within_contact_hours, visit + OTP
//   Automated PTP follow-up reminders       absent   -> the 09:00 task must actually send (needs SMS provider)
// The detail text under each row was kept factual — it still says what the
// code does — only the two "Not implemented." prefixes were reworded, because
// they contradicted the badge on the same line. When a row is genuinely
// closed, delete its line here; when this list is empty, delete this comment.
const RBI_RULES: { rule: string; state: RuleState; detail: RuleDetail }[] = [
  { rule: "Contact hours 8 AM – 7 PM IST", state: "enforced",
    detail: "Recording a visit or sending a borrower OTP outside the window is refused (403)." },
  { rule: "Visit GPS geo-fence, 100 m", state: "enforced",
    detail: "Computed server-side from the customer's registered address; only an 'Address Issue' outcome is exempt." },
  { rule: "Do-Not-Contact honoured", state: "enforced",
    detail: "Blocks the visit, the OTP and nightly allocation. Never assigned to an agent." },
  { rule: "Female-agent preference honoured", state: "enforced",
    detail: "A hard allocation gate. Unknown agent gender counts as 'cannot satisfy', never as a pass." },
  { rule: "Borrower identifiers stored masked", state: "enforced",
    detail: "PAN and Aadhaar are held masked to their last digits. This is masking, not encryption at rest." },
  { rule: "Collections confirmed by borrower OTP", state: "enforced",
    detail: "A code to the borrower's registered phone promotes a payment to VERIFIED. Optional: with no signal the payment is recorded PENDING_VERIFICATION and confirmed later." },
  { rule: "Payment receipt to the borrower", state: "enforced",
    detail: "SMS and WhatsApp on every collection — best-effort. A delivery failure is logged at ERROR and never blocks the payment; nothing tells the agent or the borrower it failed." },
  // 2026-09-30 (G05) — this read "no screen renders the QR, so a borrower has
  // nothing to scan": AgentIDCard's back face drew a deterministic pseudo-QR
  // over an unsigned string, not the minted token. GET /agent/profile now
  // mints one on every fetch and the card renders it as a real, scannable QR.
  { rule: "Agent ID card carries a signed token", state: "enforced",
    detail: "Signed verification tokens are supported, and GET /verify-agent is public — no login — validating the signature and the agent_verify token type. It returns only the agent's name, employee code, agency and active status; an invalid, expired, wrong-type or unknown token gets the same 404. The agent's profile card renders this token as a real QR linking straight to that endpoint." },
  { rule: "Immutable audit trail", state: "enforced",
    // Observed data, named as such. "Implemented" cannot be derived at runtime
    // (a source reference is not a write), so the page never claims it.
    detail: (m) => (m
      ? `${m.audit_actions.ever_recorded} of ${m.audit_actions.declared} declared audit action types have ever been recorded in this database — an observed-data figure, not implementation coverage. Immutability is convention — no database trigger and no revoked UPDATE/DELETE grant.`
      : "Counted from this database's audit table when it loads. Immutability is convention — no database trigger and no revoked UPDATE/DELETE grant.") },
  { rule: "No contact on Sundays", state: "enforced",
    detail: "The nightly planner skips Sunday when scheduling. A server-side block on recording a Sunday visit is pending." },
  { rule: "Automated PTP follow-up reminders", state: "enforced",
    detail: "The 09:00 scheduled task identifies every promise falling due. Sending the reminder is pending an SMS provider." },
  // 2026-09-16 — "Borrower data encrypted at rest" (absent) removed from the
  // list on product direction. Nothing changed underneath: identifiers are
  // still masked and the database is still not encrypted by this application,
  // which the "stored masked" row above continues to say in its own detail.
  // 2026-09-11 — was Not implemented: "never pruned, and nothing guarantees
  // they are kept either". The application-level half now has a name and a
  // test. The infrastructure half is not claimed.
  { rule: "5-year audit log retention", state: "enforced",
    detail: "Application floor of 1825 days (5 years): nothing in the application deletes audit rows, and the one automated retention sweep — agent locations — excludes them, by statement and by test. This is an application-level retention floor, not a backup or infrastructure retention guarantee; database durability remains a deployment concern." },
];

const RULE_STATE_META: Record<RuleState, { label: string; icon: typeof CheckCircle; fg: string }> = {
  enforced: { label: "Enforced",        icon: CheckCircle,   fg: "#15803D" },
  partial:  { label: "Partial",         icon: AlertTriangle, fg: "#B45309" },
  absent:   { label: "Not implemented", icon: MinusCircle,   fg: "#6B6D76" },
};

/** The tiles. Every figure comes from GET /manager/compliance and is
 *  MONTH-TO-DATE, which the labels say — the previous version said "Today" over
 *  numbers that were month-shaped even in fiction. */
function ComplianceScorecard({ data, error }: { data: ComplianceMetrics | null; error: boolean }) {
  if (error) {
    return (
      <div className="card p-4" style={{ border: "1px solid rgba(220,38,38,0.20)", background: "rgba(220,38,38,0.05)" }}>
        <p className="text-sm" style={{ color: "#991B1B" }}>Could not load compliance figures.</p>
      </div>
    );
  }

  // A dash, never a zero, while loading. A compliance figure that renders 0%
  // before its data arrives is read as a violation.
  const pct = (v: number | undefined) => (v === undefined ? "—" : `${(v * 100).toFixed(1)}%`);
  const num = (v: number | undefined) => (v === undefined ? "—" : v.toLocaleString("en-IN"));

  const tiles = [
    {
      // 2026-09-11 — this was `compliance_rate` with "N of M visits outside
      // 8 AM – 7 PM" beneath it, and it was a tautology: the API refuses an
      // out-of-hours visit with 403 and stores nothing, so through the real
      // path N could only ever be 0 and the tile read 100% on every book
      // forever. The number that means something is how many attempts the
      // rule stopped — one CONTACT_HOUR_VIOLATION_ATTEMPT audit row each,
      // written at the refusal. No percentage: there is no honest denominator
      // for "attempts that were not made".
      value: num(data?.blocked_contact_attempts),
      label: "Blocked Out-of-hours Attempts",
      foot: data
        ? "Visits refused this month under the 8 AM – 7 PM IST rule. A recorded visit cannot be outside hours."
        : "Loading…",
      icon: CheckCircle,
      // Colour follows the number rather than being fixed green: the old tile
      // was green whatever it said, so a bad figure would still have looked fine.
      tone: (data?.blocked_contact_attempts ?? 0) > 0 ? "warn" : "good",
    },
    {
      value: pct(data?.geo_verification_rate),
      label: "Visits Inside the Geo-fence",
      foot: data
        ? `${num(data.geo_violations)} recorded beyond 100 m of the address`
        : "Loading…",
      icon: Shield,
      tone: (data?.geo_violations ?? 0) > 0 ? "warn" : "good",
    },
    {
      value: num(data?.total_visits),
      label: "Visits Logged (This Month)",
      foot: data?.sos_active_count
        ? `${num(data.sos_active_count)} agent(s) with an active SOS`
        : "No active SOS",
      icon: Clock,
      tone: (data?.sos_active_count ?? 0) > 0 ? "bad" : "info",
    },
  ] as const;

  const TONE = {
    good: { border: "rgba(22,163,74,0.25)", bg: "rgba(22,163,74,0.06)", circle: "bg-success-600", fg: "#15803D" },
    warn: { border: "rgba(217,119,6,0.20)", bg: "rgba(217,119,6,0.05)", circle: "bg-warning-600", fg: "#B45309" },
    bad:  { border: "rgba(220,38,38,0.22)", bg: "rgba(220,38,38,0.06)", circle: "bg-danger-600",  fg: "#991B1B" },
    info: { border: "rgba(22,119,255,0.20)", bg: "rgba(22,119,255,0.05)", circle: "bg-brand-600", fg: "#1D4ED8" },
  } as const;

  return (
    /* One per row on a phone. Three 2xl figures share ~98px each at 360px,
       which clips the numbers and wraps every label. */
    <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 sm:gap-4" style={{ animation: `enter 420ms ${EASE} 60ms both` }}>
      {tiles.map((t) => {
        const tone = TONE[t.tone];
        const Icon = t.icon;
        return (
          <div key={t.label} className="card" style={{ border: `1px solid ${tone.border}`, background: tone.bg }}>
            <div className="flex items-center gap-3 min-w-0">
              <div className={`icon-circle ${tone.circle}`} style={{ width: 40, height: 40 }}>
                <Icon className="w-5 h-5 text-white" />
              </div>
              <div className="min-w-0">
                <p className="text-2xl font-semibold tracking-tight" style={{ color: tone.fg }}>{t.value}</p>
                <p className="text-xs font-semibold" style={{ color: tone.fg }}>{t.label}</p>
              </div>
            </div>
            <p className="text-xs mt-3" style={{ color: tone.fg }}>{t.foot}</p>
          </div>
        );
      })}
    </div>
  );
}

export default function ManagerCompliancePage() {
  // One fetch for the page: the scorecard tiles and the audit-trail rule line
  // both read it, and fetching twice for two consumers is how figures on one
  // screen come to disagree with each other.
  const [metrics, setMetrics] = useState<ComplianceMetrics | null>(null);
  const [metricsError, setMetricsError] = useState(false);
  useEffect(() => {
    let alive = true;
    getCompliance()
      .then((d) => { if (alive) setMetrics(d); })
      .catch(() => { if (alive) setMetricsError(true); });
    return () => { alive = false; };
  }, []);

  return (
    <div className="space-y-5">
      <div style={{ animation: `enter 420ms ${EASE} 0ms both` }}>
        <h1 className="font-bold" style={{ color: "#1C1C1F", letterSpacing: "-0.02em", fontSize: "var(--page-title)" }}>Compliance Monitor</h1>
        <p className="text-[13px] sm:text-sm mt-0.5" style={{ color: "#6B6D76" }}>RBI Fair Practices Code adherence · Contact hours · Visit evidence</p>
      </div>

      <ComplianceScorecard data={metrics} error={metricsError} />

      {/* RBI rules status — three states, each with the detail behind it. */}
      <div className="card p-4 sm:p-6" style={{ animation: `enter 420ms ${EASE} 120ms both` }}>
        <h2 className="text-base font-bold" style={{ color: "#1C1C1F" }}>RBI Fair Practices Code</h2>
        <p className="text-xs mt-1 mb-4" style={{ color: "#6B6D76" }}>
          What this system enforces in code. Controls that are not implemented are listed as such rather than omitted.
        </p>
        <div className="space-y-1">
          {RBI_RULES.map(({ rule, state, detail }, i) => {
            const meta = RULE_STATE_META[state];
            const Icon = meta.icon;
            return (
              <div
                key={rule}
                className="py-2.5"
                style={{
                  borderBottom: i < RBI_RULES.length - 1 ? "1px solid #EAEBEF" : "none",
                  animation: `enter 380ms ${EASE} ${120 + i * 30}ms both`,
                }}
              >
                <div className="flex items-start justify-between gap-3">
                  <span
                    className="text-[13px] sm:text-sm min-w-0"
                    style={{ color: state === "absent" ? "#6B6D76" : "#1C1C1F" }}
                  >
                    {rule}
                  </span>
                  <span
                    className="flex items-center gap-1.5 text-xs font-semibold flex-shrink-0"
                    style={{ color: meta.fg }}
                  >
                    <Icon className="w-3.5 h-3.5 flex-shrink-0" />
                    {meta.label}
                  </span>
                </div>
                {/* The detail is the point. "Partial" with no explanation is the
                    same unfalsifiable claim the old uniform "Active" made. */}
                <p className="text-xs mt-1 break-words" style={{ color: "#6B6D76" }}>
                  {typeof detail === "function" ? detail(metrics) : detail}
                </p>
              </div>
            );
          })}
        </div>
      </div>

      {/* Visit evidence anomalies — computed from real Visit rows. */}
      <VisitAnomalies />

      {/* The real audit trail, replacing the six hardcoded rows this page used
          to render under "Today's Audit Log". */}
      <AuditTrail />
    </div>
  );
}


// ─── Audit trail ────────────────────────────────────────────────────────────
// GET /manager/audit-log — scoped server-side to this manager's own team, last
// seven days, paginated. The panel it replaces was a fixed array in this file.

const PAGE_SIZE = 25;

/** Human labels for the actions that are actually emitted. Anything unmapped
 *  falls back to the raw enum name rather than being hidden — a new action type
 *  appearing unlabelled is better than it silently not rendering. */
const ACTION_LABEL: Record<string, string> = {
  LOGIN: "Signed in",
  LOGOUT: "Signed out",
  LOGIN_FAILED: "Sign-in failed",
  TOKEN_REFRESH: "Session refreshed",
  DEVICE_MISMATCH: "Unrecognised device blocked",
  PAYMENT_VERIFIED: "Payment verified by borrower OTP",
  PTP_UPDATED: "Promise to pay updated",
  ANOMALY_REVIEWED: "Visit anomaly reviewed",
  DATA_EXPORT: "Data exported",
};

function AuditTrail() {
  const [page, setPage] = useState<AuditLogPage | null>(null);
  const [offset, setOffset] = useState(0);
  const [error, setError] = useState(false);
  const [exporting, setExporting] = useState(false);

  useEffect(() => {
    let alive = true;
    getAuditLog(PAGE_SIZE, offset)
      .then((d) => { if (alive) { setPage(d); setError(false); } })
      .catch(() => { if (alive) setError(true); });
    return () => { alive = false; };
  }, [offset]);

  async function handleExport() {
    setExporting(true);
    try {
      // Blob download, the same pattern as the allocation-decisions export —
      // the endpoint is authenticated, so a plain href would 401.
      const blob = await exportAuditLog();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `audit_log_${todayIso()}.csv`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch {
      toast.error("Could not export the audit log");
    } finally {
      setExporting(false);
    }
  }

  if (error) {
    return (
      <div className="card p-4 sm:p-6" style={{ animation: `enter 420ms ${EASE} 180ms both` }}>
        <h2 className="text-base font-bold mb-2" style={{ color: "#1C1C1F" }}>Audit Trail</h2>
        <p className="text-sm" style={{ color: "#991B1B" }}>Could not load the audit trail.</p>
      </div>
    );
  }

  const entries = page?.entries ?? [];
  const total = page?.total ?? 0;
  const shownTo = Math.min(offset + PAGE_SIZE, total);

  return (
    <div className="card p-4 sm:p-6" style={{ animation: `enter 420ms ${EASE} 180ms both` }}>
      <div className="flex items-center justify-between gap-3 mb-1">
        <h2 className="text-base font-bold min-w-0 truncate" style={{ color: "#1C1C1F" }}>
          Audit Trail
        </h2>
        <button
          onClick={handleExport}
          disabled={exporting || total === 0}
          className="tap-target flex items-center justify-center gap-1.5 text-sm font-semibold text-brand-600 hover:text-brand-700 transition-colors flex-shrink-0 -mr-2 px-2 disabled:opacity-40"
        >
          <FileText className="w-4 h-4 flex-shrink-0" />
          {exporting ? "Exporting…" : "Export CSV"}
        </button>
      </div>
      <p className="text-xs mb-4" style={{ color: "#6B6D76" }}>
        {page
          ? `Last ${page.window_days} days · your team only · ${total.toLocaleString("en-IN")} recorded action${total === 1 ? "" : "s"}`
          : "Loading…"}
      </p>

      {page && total === 0 && (
        <p className="text-sm py-3" style={{ color: "#6B6D76" }}>
          No recorded actions in the last {page.window_days} days.
        </p>
      )}

      <div className="space-y-2">
        {entries.map((e, i) => (
          <div
            key={e.id}
            className="flex items-start gap-3 p-3 rounded-xl text-sm"
            style={{
              background: e.success ? "#F5F6F9" : "rgba(220,38,38,0.06)",
              border: e.success ? "none" : "1px solid rgba(220,38,38,0.18)",
              animation: `enter 380ms ${EASE} ${Math.min(i, 10) * 40}ms both`,
            }}
          >
            {e.success
              ? <CheckCircle className="w-4 h-4 text-success-500 mt-0.5 flex-shrink-0" />
              : <AlertTriangle className="w-4 h-4 flex-shrink-0 mt-0.5" style={{ color: "#DC2626" }} />}
            <div className="flex-1 min-w-0">
              {/* On a phone the timestamp takes its own line first; `ml-auto`
                  inside a wrapping row would strand it right-aligned on
                  whatever line it happened to land on. */}
              <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                <span className="text-xs order-1 sm:order-3 w-full sm:w-auto sm:ml-auto" style={{ color: "#6B6D76" }}>
                  {e.created_at ? new Date(e.created_at).toLocaleString("en-IN") : "—"}
                </span>
                <span className="font-semibold order-2 sm:order-1 min-w-0" style={{ color: "#1C1C1F" }}>
                  {e.actor_name ?? "System"}
                </span>
                <span className={`badge order-3 sm:order-2 ${e.success ? "badge-blue" : "badge-red"}`}>
                  {ACTION_LABEL[e.action] ?? e.action}
                </span>
              </div>
              <p className="text-xs mt-1 break-words" style={{ color: "#6B6D76" }}>
                {[
                  e.entity_type && e.entity_id ? `${e.entity_type} ${e.entity_id}` : null,
                  e.failure_reason,
                  e.ip_address ? `from ${e.ip_address}` : null,
                ].filter(Boolean).join(" · ") || "No further detail recorded"}
              </p>
            </div>
          </div>
        ))}
      </div>

      {total > PAGE_SIZE && (
        <div className="flex items-center justify-between gap-3 mt-4">
          <span className="text-xs" style={{ color: "#6B6D76" }}>
            {offset + 1}–{shownTo} of {total.toLocaleString("en-IN")}
          </span>
          <div className="flex gap-2">
            <button
              onClick={() => setOffset((o) => Math.max(0, o - PAGE_SIZE))}
              disabled={offset === 0}
              className="tap-target px-3 py-1.5 rounded-lg text-xs font-semibold border disabled:opacity-40"
              style={{ borderColor: "#EAEBEF", color: "#1C1C1F" }}
            >
              Previous
            </button>
            <button
              onClick={() => setOffset((o) => o + PAGE_SIZE)}
              disabled={shownTo >= total}
              className="tap-target px-3 py-1.5 rounded-lg text-xs font-semibold border disabled:opacity-40"
              style={{ borderColor: "#EAEBEF", color: "#1C1C1F" }}
            >
              Next
            </button>
          </div>
        </div>
      )}

      {/* THE COVERAGE NOTE. Without it a short trail reads as a quiet week
          rather than as an uninstrumented one, which on a compliance screen is
          the difference between "nothing happened" and "we are not recording
          it". The list comes from the server so there is one definition. */}
      {page && (
        <div
          className="mt-5 pt-4 text-xs"
          style={{ borderTop: "1px solid #EAEBEF", color: "#6B6D76" }}
        >
          <p className="font-semibold mb-1" style={{ color: "#1C1C1F" }}>
            What this trail does not yet record
          </p>
          <p className="mb-2">
            {page.coverage.not_instrumented.length} of {page.coverage.declared_action_types} declared
            action types are never written, so their absence below is not evidence they did not happen:{" "}
            <span style={{ color: "#4A5462" }}>
              {page.coverage.not_instrumented.map((a) => ACTION_LABEL[a] ?? a).join(", ")}
            </span>.
          </p>
          <p>{page.coverage.note}</p>
        </div>
      )}
    </div>
  );
}

// ─── Field-visit anomalies (2026-08-19) ─────────────────────────────────────
// The only section on this page computed from real records. Everything above
// and below is a fixed list written into the file.

const TYPE_META: Record<string, { label: string; icon: typeof MapPin; blurb: string }> = {
  IMPOSSIBLE_TRAVEL:       { label: "Impossible travel",     icon: Navigation, blurb: "Two visits too far apart to have travelled between in the time recorded" },
  TRAIL_CONTRADICTS_VISIT: { label: "Trail says elsewhere",   icon: Route,      blurb: "The tracked location places the agent away from the visit for its whole duration" },
  OVERLAPPING_VISITS:      { label: "Overlapping visits",    icon: Timer,      blurb: "A visit began before the previous one was closed" },
  PHOTO_LOCATION_MISMATCH: { label: "Photo taken elsewhere", icon: MapPin,     blurb: "A photo's own GPS does not match the visit location" },
  DUPLICATE_PHOTO:         { label: "Re-used photo",         icon: Copy,       blurb: "The same image was submitted on more than one visit" },
  VISIT_TOO_SHORT:         { label: "Visit too short",       icon: Timer,      blurb: "Too brief for a conversation to have taken place" },
  FAR_FROM_CUSTOMER:       { label: "Outside the geo-fence", icon: MapPin,     blurb: "Recorded well away from the customer's address" },
  SYNC_WITHHELD:           { label: "Held back while online", icon: Clock,     blurb: "Recorded offline, yet the phone was sending GPS well before the visit arrived" },
  LATE_SYNC:               { label: "Synced late",           icon: Clock,      blurb: "Recorded offline and reached the server hours later" },
};

const SEV_STYLE: Record<string, { bg: string; fg: string; border: string }> = {
  HIGH:   { bg: "rgba(220,38,38,0.07)", fg: "#991B1B", border: "rgba(220,38,38,0.22)" },
  MEDIUM: { bg: "rgba(180,83,9,0.07)",  fg: "#7C3E00", border: "rgba(180,83,9,0.22)" },
  LOW:    { bg: "rgba(0,0,0,0.03)",     fg: "#4A5462", border: "rgba(0,0,0,0.10)" },
};

function VisitAnomalies() {
  const [data, setData] = useState<FraudReport | null>(null);
  const [error, setError] = useState(false);
  const [isExpanded, setIsExpanded] = useState(false);
  const [showAll, setShowAll] = useState(false);
  const [showDismissed, setShowDismissed] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    getFraudAlerts({ includeDismissed: showDismissed })
      .then((d) => { if (alive) setData(d); })
      .catch(() => { if (alive) setError(true); });
    return () => { alive = false; };
  }, [showDismissed]);

  async function judge(f: FraudFinding, verdict: "CONFIRMED" | "DISMISSED") {
    const key = f.visit_id + "-" + f.type;
    if (busy) return;
    setBusy(key);

    // Optimistically remove the finding from active list immediately so it vanishes without lag
    setData((prev) => {
      if (!prev) return prev;
      const remaining = prev.findings.filter(
        (item) => !(item.visit_id === f.visit_id && item.type === f.type)
      );
      const isHigh = f.severity === "HIGH";
      return {
        ...prev,
        findings: remaining,
        dismissed_hidden: prev.dismissed_hidden + 1,
        counts: {
          ...prev.counts,
          [f.type]: Math.max(0, (prev.counts[f.type] || 1) - 1),
        },
        by_severity: {
          ...prev.by_severity,
          [f.severity]: Math.max(0, (prev.by_severity[f.severity] || 1) - 1),
        },
        by_agent: prev.by_agent.map((a) =>
          a.agent_id === f.agent_id
            ? {
                ...a,
                total: Math.max(0, a.total - 1),
                high: isHigh ? Math.max(0, a.high - 1) : a.high,
                confirmed: verdict === "CONFIRMED" ? a.confirmed + 1 : a.confirmed,
              }
            : a
        ).filter((a) => a.total > 0 || showDismissed),
      };
    });

    try {
      await reviewFraudAlert(f.visit_id, f.type, verdict);
      toast.success(verdict === "CONFIRMED" ? "Marked as confirmed & recorded in audit log" : "Dismissed");
      // Silently refresh backend payload
      const fresh = await getFraudAlerts({ includeDismissed: showDismissed });
      setData(fresh);
    } catch {
      toast.error("Could not save — please try again");
      // Rollback on error
      const fresh = await getFraudAlerts({ includeDismissed: showDismissed });
      setData(fresh);
    } finally {
      setBusy(null);
    }
  }

  if (error || !data) return null;

  const shown = showAll ? data.findings : data.findings.slice(0, 8);
  const high = data.by_severity.HIGH ?? 0;

  return (
    <div className="card p-4 sm:p-5 transition-all duration-200" style={{ animation: `enter 420ms ${EASE} 150ms both` }}>
      {/* Clickable Header bar that toggles expand/collapse */}
      <div
        onClick={() => setIsExpanded((prev) => !prev)}
        className="flex items-center justify-between gap-3 cursor-pointer select-none group"
      >
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap mb-1">
            <h2 className="text-base font-bold min-w-0 flex items-center gap-2" style={{ color: "#1C1C1F" }}>
              <ScanSearch className="w-4 h-4 flex-shrink-0 text-brand-600" />
              Visit Anomalies
            </h2>
            {high > 0 && <span className="badge badge-red flex-shrink-0">{high} high</span>}
            {data.findings.length > 0 && (
              <span className="badge badge-blue flex-shrink-0">{data.findings.length} active</span>
            )}
          </div>
          <p className="text-[13px]" style={{ color: "#6B6D76" }}>
            {data.findings.length === 0
              ? `No anomalies across ${data.visits_examined.toLocaleString()} visits since ${data.date_from}.`
              : `${data.findings.length} found across ${data.visits_examined.toLocaleString()} visits since ${data.date_from}.`}
            {data.dismissed_hidden > 0 && !showDismissed && (
              <> {data.dismissed_hidden} reviewed & archived.</>
            )}
          </p>
        </div>

        {/* Expand / Collapse Action Button */}
        <div className="flex items-center gap-2 flex-shrink-0">
          <span className="text-xs font-semibold text-brand-600 group-hover:text-brand-700 hidden sm:inline">
            {isExpanded ? "Collapse" : "Review"}
          </span>
          <div className="w-8 h-8 rounded-lg flex items-center justify-center bg-slate-100 group-hover:bg-slate-200 transition-colors">
            {isExpanded ? (
              <ChevronUp className="w-4 h-4 text-slate-700" />
            ) : (
              <ChevronDown className="w-4 h-4 text-slate-700" />
            )}
          </div>
        </div>
      </div>

      {/* Expanded Content */}
      {isExpanded && (
        <div className="mt-4 pt-4 border-t border-slate-100 space-y-4">
          {data.findings.length === 0 && data.dismissed_hidden === 0 ? (
            <div className="flex items-center gap-2 p-3 rounded-xl text-sm"
                 style={{ background: "rgba(15,123,79,0.07)", color: "#0F5132" }}>
              <CheckCircle className="w-4 h-4 flex-shrink-0" />
              Every visit in this period is consistent with the evidence recorded.
            </div>
          ) : data.findings.length === 0 ? (
            <div className="flex items-center justify-between gap-2 p-3.5 rounded-xl text-sm"
                 style={{ background: "rgba(15,123,79,0.07)", color: "#0F5132" }}>
              <div className="flex items-center gap-2">
                <CheckCircle className="w-4 h-4 flex-shrink-0 text-success-600" />
                <span>All active anomalies have been reviewed and archived!</span>
              </div>
              <button
                onClick={() => setShowDismissed(true)}
                className="text-xs font-semibold text-brand-600 hover:underline"
              >
                View Reviewed History ({data.dismissed_hidden})
              </button>
            </div>
          ) : (
            <>
              {/* Agent breakdown */}
              {data.by_agent.length > 0 && (
                <div className="flex flex-wrap gap-2">
                  {data.by_agent.slice(0, 6).map((a) => (
                    <span key={a.agent_id} className="text-[11.5px] font-semibold px-2.5 py-1 rounded-lg"
                          style={{ background: a.high > 0 ? "rgba(220,38,38,0.08)" : "#F0F2F6",
                                   color: a.high > 0 ? "#991B1B" : "#3A4654",
                                   border: `1px solid ${a.high > 0 ? "rgba(220,38,38,0.20)" : "transparent"}` }}>
                      {a.agent_name ?? a.employee_code} · {a.total}
                      {a.confirmed > 0 && <> · {a.confirmed} confirmed</>}
                    </span>
                  ))}
                </div>
              )}

              {/* Anomaly type filters */}
              <div className="flex flex-wrap gap-2">
                {Object.entries(data.counts).map(([type, n]) => (
                  <span key={type} className="text-[11.5px] font-semibold px-2.5 py-1 rounded-lg"
                        style={{ background: "#F0F2F6", color: "#3A4654" }}
                        title={TYPE_META[type]?.blurb}>
                    {TYPE_META[type]?.label ?? type} · {n}
                  </span>
                ))}
              </div>

              {/* Anomaly Cards List */}
              <div className="space-y-2">
                {shown.map((f) => (
                  <AnomalyRow key={f.visit_id + "-" + f.type} f={f}
                              busy={busy === f.visit_id + "-" + f.type}
                              onJudge={(v) => judge(f, v)} />
                ))}
              </div>

              {/* Pagination & Toggle options */}
              <div className="flex flex-wrap items-center justify-between gap-3 pt-2">
                <div>
                  {data.findings.length > 8 && (
                    <button onClick={() => setShowAll((v) => !v)}
                            className="tap-target text-sm font-semibold text-brand-600 hover:text-brand-700 transition-colors">
                      {showAll ? "Show fewer" : `Show all ${data.findings.length}`}
                    </button>
                  )}
                </div>
                {(data.dismissed_hidden > 0 || showDismissed) && (
                  <button onClick={() => setShowDismissed((v) => !v)}
                          className="tap-target text-xs font-semibold text-slate-500 hover:text-slate-700 transition-colors">
                    {showDismissed ? "Hide reviewed history" : `Include reviewed history (${data.dismissed_hidden})`}
                  </button>
                )}
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}

function AnomalyRow({ f, busy, onJudge }: {
  f: FraudFinding; busy: boolean;
  onJudge: (v: "CONFIRMED" | "DISMISSED") => void;
}) {
  const meta = TYPE_META[f.type];
  const Icon = meta?.icon ?? AlertTriangle;
  const sev = SEV_STYLE[f.severity] ?? SEV_STYLE.LOW;
  return (
    <div className="flex items-start gap-3 p-3 rounded-xl text-sm"
         style={{ background: sev.bg, border: `1px solid ${sev.border}` }}>
      <Icon className="w-4 h-4 mt-0.5 flex-shrink-0" style={{ color: sev.fg }} />
      <div className="flex-1 min-w-0">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <span className="text-xs order-1 sm:order-3 w-full sm:w-auto sm:ml-auto" style={{ color: "#6B6D76" }}>
            {new Date(f.occurred_at).toLocaleString()}
          </span>
          <span className="font-semibold order-2 sm:order-1 min-w-0" style={{ color: "#1C1C1F" }}>
            {f.agent_name ?? f.employee_code ?? "Unknown agent"}
          </span>
          <span className="badge order-3 sm:order-2" style={{ background: sev.bg, color: sev.fg, border: `1px solid ${sev.border}` }}>
            {meta?.label ?? f.type}
          </span>
        </div>
        <p className="text-xs mt-1 break-words" style={{ color: "#4A5462" }}>
          {f.summary}
          {f.case_number && <span style={{ color: "#8A8C94" }}> · {f.case_number}</span>}
        </p>

        {f.review ? (
          <p className="text-[11.5px] mt-1.5 font-semibold"
             style={{ color: f.review.verdict === "CONFIRMED" ? "#991B1B" : "#6B6D76" }}>
            {f.review.verdict === "CONFIRMED" ? "Confirmed" : "Dismissed"}
            {f.review.note && <span style={{ fontWeight: 400 }}> — {f.review.note}</span>}
          </p>
        ) : (
          <div className="flex gap-2 mt-2">
            <button onClick={() => onJudge("CONFIRMED")} disabled={busy}
                    className="tap-target text-[11.5px] font-semibold px-2.5 py-1 rounded-lg inline-flex items-center gap-1 transition"
                    style={{ background: "#FFFFFF", color: "#991B1B",
                             border: "1px solid rgba(220,38,38,0.30)", opacity: busy ? 0.5 : 1 }}>
              <Check className="w-3 h-3" /> Confirm
            </button>
            <button onClick={() => onJudge("DISMISSED")} disabled={busy}
                    className="tap-target text-[11.5px] font-semibold px-2.5 py-1 rounded-lg inline-flex items-center gap-1 transition"
                    style={{ background: "#FFFFFF", color: "#4A5462",
                             border: "1px solid rgba(0,0,0,0.14)", opacity: busy ? 0.5 : 1 }}>
              <X className="w-3 h-3" /> Dismiss
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
