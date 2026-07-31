import { Shield, CheckCircle, AlertTriangle, Clock, FileText } from "lucide-react";

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
        <h1 className="text-xl font-bold" style={{ color: "#1C1C1F", letterSpacing: "-0.02em" }}>Compliance Monitor</h1>
        <p className="text-sm mt-0.5" style={{ color: "#6B6D76" }}>RBI Fair Practices Code adherence · Contact hours · Audit trail</p>
      </div>

      {/* Compliance scorecard */}
      <div className="grid grid-cols-3 gap-4" style={{ animation: `enter 420ms ${EASE} 60ms both` }}>
        <div className="card" style={{ border: "1px solid rgba(22,163,74,0.25)", background: "rgba(22,163,74,0.06)" }}>
          <div className="flex items-center gap-3">
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
          <div className="flex items-center gap-3">
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
          <div className="flex items-center gap-3">
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
      <div className="card p-6" style={{ animation: `enter 420ms ${EASE} 120ms both` }}>
        <h2 className="text-base font-bold mb-4" style={{ color: "#1C1C1F" }}>RBI Fair Practices Code</h2>
        <div className="space-y-1">
          {RBI_RULES.map((rule, i) => (
            <div
              key={rule}
              className="flex items-center justify-between py-2.5"
              style={{
                borderBottom: i < RBI_RULES.length - 1 ? "1px solid #EAEBEF" : "none",
                animation: `enter 380ms ${EASE} ${120 + i * 40}ms both`,
              }}
            >
              <span className="text-sm" style={{ color: "#1C1C1F" }}>{rule}</span>
              <span className="flex items-center gap-1.5 text-xs font-semibold text-success-600">
                <CheckCircle className="w-3.5 h-3.5" />
                Active
              </span>
            </div>
          ))}
        </div>
      </div>

      {/* Audit log */}
      <div className="card p-6" style={{ animation: `enter 420ms ${EASE} 180ms both` }}>
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-base font-bold" style={{ color: "#1C1C1F" }}>Today's Audit Log</h2>
          <button className="flex items-center gap-1.5 text-sm font-semibold text-brand-600 hover:text-brand-700 transition-colors">
            <FileText className="w-4 h-4" />
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
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="font-semibold" style={{ color: "#1C1C1F" }}>{log.agent}</span>
                  <span className={`badge ${log.ok ? "badge-blue" : "badge-red"}`}>{log.action}</span>
                  <span className="text-xs ml-auto" style={{ color: "#6B6D76" }}>{log.time}</span>
                </div>
                <p className="text-xs mt-0.5 truncate" style={{ color: "#6B6D76" }}>{log.detail}</p>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
