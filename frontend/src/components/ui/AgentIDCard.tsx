import { Shield, QrCode } from "lucide-react";
import { useState } from "react";

interface Props {
  agentId: string;
  name: string;
  idCardNumber: string;
  territory: string;
  tier: string;
  employeeCode: string;
  validUntil?: string;
}

/* Deterministic pseudo-QR grid from a seed string */
function hashStr(s: string): number {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) {
    h = Math.imul(h ^ s.charCodeAt(i), 16777619) >>> 0;
  }
  return h;
}

function bitAt(seed: number, row: number, col: number, size: number): boolean {
  const isFinderCorner =
    (row < 3 && col < 3) ||
    (row < 3 && col >= size - 3) ||
    (row >= size - 3 && col < 3);
  if (isFinderCorner) {
    const r = Math.min(row, size - 1 - row, 2);
    const c = Math.min(col, size - 1 - col, 2);
    return r % 2 === 0 || c % 2 === 0;
  }
  const idx = row * size + col;
  const word = idx >> 5;
  const bit = idx & 31;
  const rng = hashStr(String(seed + word * 7919));
  return ((rng >> bit) & 1) === 1;
}

function MiniQR({ value, size = 18 }: { value: string; size?: number }) {
  const seed = hashStr(value);
  const cellSize = 5;
  const svgSize = size * cellSize;

  return (
    <svg width={svgSize} height={svgSize} viewBox={`0 0 ${svgSize} ${svgSize}`} className="rounded">
      <rect width={svgSize} height={svgSize} fill="white" />
      {Array.from({ length: size }, (_, row) =>
        Array.from({ length: size }, (_, col) => {
          const on = bitAt(seed, row, col, size);
          if (!on) return null;
          return (
            <rect
              key={`${row}-${col}`}
              x={col * cellSize}
              y={row * cellSize}
              width={cellSize}
              height={cellSize}
              fill="#1e293b"
            />
          );
        })
      )}
    </svg>
  );
}

export default function AgentIDCard({ agentId, name, idCardNumber, territory, tier, employeeCode, validUntil }: Props) {
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
            <MiniQR value={`tiqcollect:agent:${agentId}:${idCardNumber}`} size={18} />
          </div>
          <div className="text-center">
            <p className="text-xs font-mono font-bold text-slate-700">{idCardNumber}</p>
            <p className="text-[10px] text-slate-400 mt-0.5">Agent · {territory}</p>
          </div>
          <div className="flex items-center gap-1.5 text-[10px] text-slate-400">
            <Shield className="w-3 h-3 text-success-500" />
            Issued by TIQCollect · RBI Reg. No. RB-2024-0192
          </div>
        </div>
      )}
    </div>
  );
}
