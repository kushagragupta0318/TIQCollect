// P2 G04 (2026-10-01, L9) — read-only contract, commission and SLA for
// AGENCY_ADMIN (plan §10, "Also for AGENCY_ADMIN"). The backend gates this on
// the agency.profile.read capability (AGENCY_ADMIN only); an AGENCY_MANAGER
// who reaches this page by URL sees the 403 explained rather than a crash.
import { useEffect, useState } from "react";
import { Building2, FileText, Percent, ShieldCheck } from "lucide-react";
import { getAgencyProfile, type AgencyProfile } from "@/api/manager";
import { errorDetail, errorStatus } from "@/lib/apiError";
import { exactRupees } from "@/lib/money";

const humanize = (s: string) => s.replace(/_/g, " ").toLowerCase().replace(/^\w/, (c) => c.toUpperCase());

function Field({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div>
      <p className="text-[10px] uppercase tracking-wide text-slate-400">{label}</p>
      <p className="text-sm font-semibold text-slate-800">{value ?? "—"}</p>
    </div>
  );
}

export default function AgencyProfilePage() {
  const [data, setData] = useState<AgencyProfile | null>(null);
  const [loading, setLoading] = useState(true);
  const [notAllowed, setNotAllowed] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getAgencyProfile()
      .then((d) => { if (!cancelled) setData(d); })
      .catch((err) => {
        if (cancelled) return;
        if (errorStatus(err) === 403) setNotAllowed(true);
        else setError(errorDetail(err, "Could not load the agency profile"));
      })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, []);

  if (loading) {
    return <div className="p-4 lg:p-6"><div className="card animate-pulse h-48" /></div>;
  }

  if (notAllowed) {
    return (
      <div className="p-4 lg:p-6">
        <div className="card p-5 text-sm text-slate-500">
          The agency profile is visible to your agency's admin account. If you need this,
          ask your agency admin.
        </div>
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="p-4 lg:p-6">
        <div className="card p-5 text-sm text-danger-600">{error ?? "No data"}</div>
      </div>
    );
  }

  const { agency, contract, commission_terms } = data;

  return (
    <div className="p-4 lg:p-6 space-y-4 pb-6 max-w-3xl">
      <h1 className="text-xl font-bold text-slate-900">Agency Profile</h1>

      <div className="card p-4 sm:p-5 space-y-3">
        <h2 className="text-sm font-semibold text-slate-700 flex items-center gap-1.5">
          <Building2 className="w-4 h-4 text-brand-600" /> Identity
        </h2>
        <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
          <Field label="Legal name" value={agency.legal_name} />
          <Field label="Trade name" value={agency.trade_name} />
          <Field label="RBI registration" value={agency.rbi_registration_no} />
          <Field label="Status" value={humanize(agency.status)} />
          <Field label="HQ city" value={agency.hq_city} />
        </div>
        {(agency.contact_name || agency.contact_email || agency.contact_phone) && (
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-3 pt-2 border-t border-slate-100">
            <Field label="Contact" value={agency.contact_name} />
            <Field label="Email" value={agency.contact_email} />
            <Field label="Phone" value={agency.contact_phone} />
          </div>
        )}
      </div>

      {!contract ? (
        <div className="card p-5 text-sm text-slate-500">No contract on file for this agency.</div>
      ) : (
        <div className="card p-4 sm:p-5 space-y-3">
          <h2 className="text-sm font-semibold text-slate-700 flex items-center gap-1.5">
            <FileText className="w-4 h-4 text-brand-600" /> Contract {contract.contract_no}
            <span className="badge badge-slate">{humanize(contract.status)}</span>
          </h2>
          {!contract.is_current && (
            <p className="text-xs text-warning-600 bg-warning-50 rounded-lg px-2.5 py-1.5">
              This agency has no ACTIVE contract — showing the most recent one on file. These terms are not currently in force.
            </p>
          )}
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
            <Field label="Start" value={contract.start_date} />
            <Field label="End" value={contract.end_date} />
            <Field label="Seat limit (agents)" value={contract.max_agents} />
            <Field label="Max placed cases" value={contract.max_placed_cases} />
            <Field label="Max visits / month" value={contract.max_visits_per_month} />
            <Field label="SLA — first visit" value={`${contract.sla_first_visit_days} days`} />
          </div>
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-3 pt-2 border-t border-slate-100">
            <Field label="Recall — no activity" value={contract.recall_no_activity_days != null ? `${contract.recall_no_activity_days} days` : "Not set"} />
            <Field label="Recall on SLA breach" value={contract.recall_on_sla_breach ? "Yes" : "No"} />
            <Field label="Recall at contract end" value={contract.recall_at_contract_end ? "Yes" : "No"} />
          </div>
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-3 pt-2 border-t border-slate-100">
            <Field label="Performance bonus" value={contract.performance_bonus_pct != null ? `${contract.performance_bonus_pct}%` : "Not set"} />
            <Field label="Performance target" value={contract.performance_target_pct != null ? `${contract.performance_target_pct}%` : "Not set"} />
            <Field label="Security deposit" value={contract.security_deposit != null ? exactRupees(contract.security_deposit) : "Not set"} />
          </div>
        </div>
      )}

      {commission_terms.length > 0 && (
        <div className="card p-4 sm:p-5 space-y-3">
          <h2 className="text-sm font-semibold text-slate-700 flex items-center gap-1.5">
            <Percent className="w-4 h-4 text-brand-600" /> Commission
          </h2>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-[10px] uppercase tracking-wide text-slate-400">
                  <th className="pb-2 pr-3">Loan type</th>
                  <th className="pb-2 pr-3">DPD bucket</th>
                  <th className="pb-2 pr-3">Commission</th>
                  <th className="pb-2">Fixed fee</th>
                </tr>
              </thead>
              <tbody>
                {commission_terms.map((t, i) => (
                  <tr key={i} className="border-t border-slate-100">
                    <td className="py-2 pr-3 font-medium text-slate-700">{humanize(t.loan_type)}</td>
                    <td className="py-2 pr-3 text-slate-600">{humanize(t.dpd_bucket)}</td>
                    <td className="py-2 pr-3 font-semibold text-brand-600">{t.commission_pct}%</td>
                    <td className="py-2 text-slate-600">{t.fixed_fee_per_resolution != null ? exactRupees(t.fixed_fee_per_resolution) : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <p className="text-[10px] text-slate-400 flex items-center gap-1">
        <ShieldCheck className="w-3 h-3" /> Read-only — contact your bank relationship manager to change contract terms.
      </p>
    </div>
  );
}
