import { Shield, QrCode } from "lucide-react";
import { useState } from "react";
import QRCode from "react-qr-code";

interface Props {
  name: string;
  idCardNumber: string;
  territory: string;
  tier: string;
  employeeCode: string;
  validUntil?: string;
  /** The issuing agency and its RBI registration, from the agency record.
   *  Either may be absent; nothing is printed in its place (A14). */
  issuer?: string | null;
  rbiRegistrationNo?: string | null;
  /** Signed core/security.create_agent_verify_token, from GET /agent/profile
   *  (G05). Encoded as a link to the public GET /verify-agent so a borrower's
   *  camera can scan it directly. Absent only on a stale cached profile —
   *  the back face says so rather than showing a placeholder. */
  verifyToken?: string | null;
}

export default function AgentIDCard({ name, idCardNumber, territory, tier, employeeCode, validUntil, issuer, rbiRegistrationNo, verifyToken }: Props) {
  const [flipped, setFlipped] = useState(false);

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-slate-700 flex items-center gap-1.5">
          <Shield className="w-4 h-4 text-brand-600" />
          Digital Agent ID
        </h3>
        <button
          onClick={() => setFlipped((f) => !f)}
          className="tap-target text-xs text-brand-600 font-medium inline-flex items-center justify-center gap-1 hover:underline"
        >
          <QrCode className="w-3.5 h-3.5" />
          {flipped ? "Show ID" : "Show QR"}
        </button>
      </div>

      {!flipped ? (
        /* Front face */
        <div className="relative overflow-hidden rounded-card bg-primary p-4 text-white">
          {/* Background pattern */}
          <div className="absolute inset-0 opacity-5">
            <svg viewBox="0 0 100 100" preserveAspectRatio="none" className="w-full h-full">
              <circle cx="80" cy="20" r="50" fill="none" stroke="white" strokeWidth="20" />
              <circle cx="20" cy="80" r="40" fill="none" stroke="white" strokeWidth="15" />
            </svg>
          </div>

          <div className="relative z-10">
            <div className="flex items-start justify-between mb-4">
              <div>
                <p className="text-[10px] font-semibold tracking-widest opacity-70 uppercase">TIQCollect</p>
                <p className="text-[10px] opacity-60 mt-0.5">Field Recovery Agent</p>
              </div>
              <div className={`bg-white/20 text-white text-[10px] font-bold px-2 py-0.5 rounded-full border border-white/30`}>{tier.replace("_", " ")}</div>
            </div>

            <div className="mb-4">
              <p className="text-lg font-bold leading-tight">{name}</p>
              <p className="text-sm font-mono opacity-90 mt-0.5">{idCardNumber}</p>
            </div>

            <div className="flex items-end justify-between">
              <div className="space-y-1.5">
                <div>
                  <p className="text-[10px] opacity-60 uppercase tracking-wide">Employee Code</p>
                  <p className="text-xs font-semibold">{employeeCode}</p>
                </div>
                <div>
                  <p className="text-[10px] opacity-60 uppercase tracking-wide">Territory</p>
                  <p className="text-xs font-semibold">{territory}</p>
                </div>
                {validUntil && (
                  <div>
                    <p className="text-[10px] opacity-60 uppercase tracking-wide">Valid Until</p>
                    <p className="text-xs font-semibold">{validUntil}</p>
                  </div>
                )}
              </div>
              {/* Small chip decoration */}
              <div className="w-10 h-7 rounded bg-yellow-300/30 border border-yellow-200/30 grid grid-cols-3 gap-px p-1">
                {Array.from({ length: 6 }).map((_, i) => <div key={i} className="bg-yellow-100/40 rounded-sm" />)}
              </div>
            </div>

            <div className="mt-4 pt-3 border-t border-white/20 flex items-center gap-1.5">
              <div className="w-1.5 h-1.5 rounded-full bg-green-300 animate-pulse" />
              <p className="text-[10px] opacity-70">RBI Compliant · Geo-Verified Agent</p>
            </div>
          </div>
        </div>
      ) : (
        /* Back face — QR code */
        <div className="rounded-2xl bg-white border-2 border-slate-100 p-4 shadow-lg flex flex-col items-center gap-3">
          <p className="text-xs text-slate-500 font-medium">Scan to verify agent identity</p>
          <div className="p-3 bg-white rounded-xl border border-slate-100 shadow-inner">
            {verifyToken ? (
              <QRCode
                value={`${window.location.origin}/api/v1/verify-agent?token=${encodeURIComponent(verifyToken)}`}
                size={120}
              />
            ) : (
              <p className="w-[120px] h-[120px] flex items-center justify-center text-center text-[10px] text-slate-400 px-2">
                QR unavailable — reload this page
              </p>
            )}
          </div>
          <div className="text-center">
            <p className="text-xs font-mono font-bold text-slate-700">{idCardNumber}</p>
            <p className="text-[10px] text-slate-400 mt-0.5">Agent · {territory}</p>
          </div>
          {(issuer || rbiRegistrationNo) && (
            <div className="flex items-center gap-1.5 text-[10px] text-slate-400">
              <Shield className="w-3 h-3 text-success-500" />
              {[issuer && `Issued by ${issuer}`, rbiRegistrationNo && `RBI Reg. No. ${rbiRegistrationNo}`]
                .filter(Boolean).join(" · ")}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
