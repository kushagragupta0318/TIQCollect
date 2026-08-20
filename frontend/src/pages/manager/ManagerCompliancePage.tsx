import { Shield, CheckCircle, AlertTriangle, Clock, FileText, ScanSearch, MapPin, Copy, Timer, Navigation, Route, X, Check } from "lucide-react";
import { useEffect, useState } from "react";
import { getFraudAlerts, reviewFraudAlert } from "@/api/manager";
import type { FraudFinding, FraudReport } from "@/api/manager";
import { toast } from "react-hot-toast";

const EASE = "cubic-bezier(0.16,1,0.3,1)";

const AUDIT_LOG = [
  { time: "09:42 AM", agent: "Rajesh Kumar",  action: "Visit recorded",                detail: "CASE1000001 · GPS verified · Within contact hours",   ok: true  },
  { time: "10:15 AM", agent: "Priya Sharma",  action: "Payment collected",             detail: "₹45,000 cash · Receipt RCP12345678",                  ok: true  },
  { time: "11:03 AM", agent: "Amit Singh",    action: "PTP set",                       detail: "₹28,000 committed for Jan 25, 2025",                  ok: true  },
  { time: "07:52 PM", agent: "Suresh Rao",    action: "Contact hour violation attempt", detail: "Visit record blocked — outside 8AM–7PM",              ok: false },
  { time: "02:30 PM", agent: "Neha Gupta",    action: "Visit recorded",                detail: "CASE1000045 · Not met · Premises locked",             ok: true  },
  { time: "03:15 PM", agent: "Vikram Patel",  action: "Document uploaded",             detail: "Signed acknowledgement · CASE1000089",                ok: true  },
];

const RBI_RULES = [
  "Contact hours 8 AM–7 PM enforced",
  "Agent ID card verification on login",
  "Visit GPS verification (200m radius)",
  "No contact on Sundays (system block)",
  "Customer payment receipt via SMS",
  "PTP follow-up reminders automated",
  "Borrower data encrypted at rest",
  "5-year audit log retention",
];

export default function ManagerCompliancePage() {
  return (
    <div className="space-y-5">
      <div style={{ animation: `enter 420ms ${EASE} 0ms both` }}>
        <h1 className="font-bold" style={{ color: "#1C1C1F", letterSpacing: "-0.02em", fontSize: "var(--page-title)" }}>Compliance Monitor</h1>
        <p className="text-[13px] sm:text-sm mt-0.5" style={{ color: "#6B6D76" }}>RBI Fair Practices Code adherence · Contact hours · Audit trail</p>
      </div>

      {/* Compliance scorecard — one per row on a phone. Three 2xl figures share
          ~98px each at 360px, which clips the numbers and wraps every label. */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 sm:gap-4" style={{ animation: `enter 420ms ${EASE} 60ms both` }}>
        <div className="card" style={{ border: "1px solid rgba(22,163,74,0.25)", background: "rgba(22,163,74,0.06)" }}>
          <div className="flex items-center gap-3 min-w-0">
            <div className="icon-circle bg-success-600" style={{ width: 40, height: 40 }}>
              <CheckCircle className="w-5 h-5 text-white" />
            </div>
            <div>
              <p className="text-2xl font-semibold tracking-tight text-success-700">99.2%</p>
              <p className="text-xs font-semibold text-success-600">Contact Hour Compliance</p>
            </div>
          </div>
          <p className="text-xs text-success-600 mt-3">1 violation attempt blocked today</p>
        </div>

        <div className="card" style={{ border: "1px solid rgba(22,119,255,0.20)", background: "rgba(22,119,255,0.05)" }}>
          <div className="flex items-center gap-3 min-w-0">
            <div className="icon-circle bg-brand-600" style={{ width: 40, height: 40 }}>
              <Shield className="w-5 h-5 text-white" />
            </div>
            <div>
              <p className="text-2xl font-semibold tracking-tight text-brand-700">100%</p>
              <p className="text-xs font-semibold text-brand-600">Agent ID Verified</p>
            </div>
          </div>
          <p className="text-xs text-brand-600 mt-3">All 50 agents carry valid ID cards</p>
        </div>

        <div className="card" style={{ border: "1px solid rgba(217,119,6,0.20)", background: "rgba(217,119,6,0.05)" }}>
          <div className="flex items-center gap-3 min-w-0">
            <div className="icon-circle bg-warning-600" style={{ width: 40, height: 40 }}>
              <Clock className="w-5 h-5 text-white" />
            </div>
            <div>
              <p className="text-2xl font-semibold tracking-tight text-warning-700">847</p>
              <p className="text-xs font-semibold text-warning-600">Visits Logged (Today)</p>
            </div>
          </div>
          <p className="text-xs text-warning-600 mt-3">All with GPS verification</p>
        </div>
      </div>

      {/* RBI rules status */}
      <div className="card p-4 sm:p-6" style={{ animation: `enter 420ms ${EASE} 120ms both` }}>
        <h2 className="text-base font-bold mb-4" style={{ color: "#1C1C1F" }}>RBI Fair Practices Code</h2>
        <div className="space-y-1">
          {RBI_RULES.map((rule, i) => (
            <div
              key={rule}
              className="flex items-center justify-between gap-3 py-2.5"
              style={{
                borderBottom: i < RBI_RULES.length - 1 ? "1px solid #EAEBEF" : "none",
                animation: `enter 380ms ${EASE} ${120 + i * 40}ms both`,
              }}
            >
              <span className="text-[13px] sm:text-sm min-w-0" style={{ color: "#1C1C1F" }}>{rule}</span>
              <span className="flex items-center gap-1.5 text-xs font-semibold text-success-600 flex-shrink-0">
                <CheckCircle className="w-3.5 h-3.5 flex-shrink-0" />
                Active
              </span>
            </div>
          ))}
        </div>
      </div>

      {/* Audit log */}
      <VisitAnomalies />

      <div className="card p-4 sm:p-6" style={{ animation: `enter 420ms ${EASE} 180ms both` }}>
        <div className="flex items-center justify-between gap-3 mb-4">
          <h2 className="text-base font-bold min-w-0 truncate" style={{ color: "#1C1C1F" }}>Today's Audit Log</h2>
          <button className="tap-target flex items-center justify-center gap-1.5 text-sm font-semibold text-brand-600 hover:text-brand-700 transition-colors flex-shrink-0 -mr-2 px-2">
            <FileText className="w-4 h-4 flex-shrink-0" />
            Export
          </button>
        </div>
        <div className="space-y-2">
          {AUDIT_LOG.map((log, i) => (
            <div
              key={i}
              className="flex items-start gap-3 p-3 rounded-xl text-sm"
              style={{
                background: log.ok ? "#F5F6F9" : "rgba(220,38,38,0.06)",
                border: log.ok ? "none" : "1px solid rgba(220,38,38,0.18)",
                animation: `enter 380ms ${EASE} ${180 + i * 50}ms both`,
              }}
            >
              {log.ok
                ? <CheckCircle className="w-4 h-4 text-success-500 mt-0.5 flex-shrink-0" />
                : <AlertTriangle className="w-4 h-4 flex-shrink-0 mt-0.5" style={{ color: "#DC2626" }} />
              }
              <div className="flex-1 min-w-0">
                {/* On a phone the timestamp takes its own line first; `ml-auto`
                    inside a wrapping row would otherwise strand it alone and
                    right-aligned on whatever line it happened to land on. */}
                <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                  <span className="text-xs order-1 sm:order-3 w-full sm:w-auto sm:ml-auto" style={{ color: "#6B6D76" }}>{log.time}</span>
                  <span className="font-semibold order-2 sm:order-1 min-w-0" style={{ color: "#1C1C1F" }}>{log.agent}</span>
                  <span className={`badge order-3 sm:order-2 ${log.ok ? "badge-blue" : "badge-red"}`}>{log.action}</span>
                </div>
                {/* Wraps rather than truncates — on desktop this detail is fully
                    visible, so hiding it on mobile would lose information. */}
                <p className="text-xs mt-1 break-words" style={{ color: "#6B6D76" }}>{log.detail}</p>
              </div>
            </div>
          ))}
        </div>
      </div>
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
};

const SEV_STYLE: Record<string, { bg: string; fg: string; border: string }> = {
  HIGH:   { bg: "rgba(220,38,38,0.07)", fg: "#991B1B", border: "rgba(220,38,38,0.22)" },
  MEDIUM: { bg: "rgba(180,83,9,0.07)",  fg: "#7C3E00", border: "rgba(180,83,9,0.22)" },
  LOW:    { bg: "rgba(0,0,0,0.03)",     fg: "#4A5462", border: "rgba(0,0,0,0.10)" },
};

function VisitAnomalies() {
  const [data, setData] = useState<FraudReport | null>(null);
  const [error, setError] = useState(false);
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
    try {
      await reviewFraudAlert(f.visit_id, f.type, verdict);
      toast.success(verdict === "CONFIRMED" ? "Marked as confirmed" : "Dismissed");
      const fresh = await getFraudAlerts({ includeDismissed: showDismissed });
      setData(fresh);
    } catch {
      toast.error("Could not save - try again");
    } finally {
      setBusy(null);
    }
  }

  if (error || !data) return null;

  const shown = showAll ? data.findings : data.findings.slice(0, 8);
  const high = data.by_severity.HIGH ?? 0;

  return (
    <div className="card p-4 sm:p-6" style={{ animation: `enter 420ms ${EASE} 150ms both` }}>
      <div className="flex items-center justify-between gap-3 mb-1">
        <h2 className="text-base font-bold min-w-0 truncate flex items-center gap-2" style={{ color: "#1C1C1F" }}>
          <ScanSearch className="w-4 h-4 flex-shrink-0" style={{ color: "#2563EB" }} />
          Visit Anomalies
        </h2>
        {high > 0 && <span className="badge badge-red flex-shrink-0">{high} high</span>}
      </div>
      <p className="text-[13px] mb-4" style={{ color: "#6B6D76" }}>
        {data.findings.length === 0
          ? `No anomalies across ${data.visits_examined.toLocaleString()} visits since ${data.date_from}.`
          : `${data.findings.length} found across ${data.visits_examined.toLocaleString()} visits since ${data.date_from}.`}
        {data.dismissed_hidden > 0 && !showDismissed && (
          <> {data.dismissed_hidden} dismissed and hidden.</>
        )}
      </p>

      {data.findings.length === 0 && data.dismissed_hidden === 0 ? (
        <div className="flex items-center gap-2 p-3 rounded-xl text-sm"
             style={{ background: "rgba(15,123,79,0.07)", color: "#0F5132" }}>
          <CheckCircle className="w-4 h-4 flex-shrink-0" />
          Every visit in this period is consistent with the evidence recorded.
        </div>
      ) : (
        <>
          {/* Which agents account for them. One agent with twelve findings is a
              very different conversation from twelve agents with one each. */}
          {data.by_agent.length > 0 && (
            <div className="flex flex-wrap gap-2 mb-3">
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

          <div className="flex flex-wrap gap-2 mb-4">
            {Object.entries(data.counts).map(([type, n]) => (
              <span key={type} className="text-[11.5px] font-semibold px-2.5 py-1 rounded-lg"
                    style={{ background: "#F0F2F6", color: "#3A4654" }}
                    title={TYPE_META[type]?.blurb}>
                {TYPE_META[type]?.label ?? type} · {n}
              </span>
            ))}
          </div>

          <div className="space-y-2">
            {shown.map((f) => (
              <AnomalyRow key={f.visit_id + "-" + f.type} f={f}
                          busy={busy === f.visit_id + "-" + f.type}
                          onJudge={(v) => judge(f, v)} />
            ))}
          </div>

          <div className="flex flex-wrap items-center gap-4 mt-3">
            {data.findings.length > 8 && (
              <button onClick={() => setShowAll((v) => !v)}
                      className="tap-target text-sm font-semibold text-brand-600 hover:text-brand-700 transition-colors">
                {showAll ? "Show fewer" : `Show all ${data.findings.length}`}
              </button>
            )}
            {(data.dismissed_hidden > 0 || showDismissed) && (
              <button onClick={() => setShowDismissed((v) => !v)}
                      className="tap-target text-sm font-semibold transition-colors"
                      style={{ color: "#6B6D76" }}>
                {showDismissed ? "Hide dismissed" : "Include dismissed"}
              </button>
            )}
          </div>
        </>
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
