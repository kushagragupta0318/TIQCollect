// ─── CHANGELOG (prototype → product) ────────────────────────────────────────
// 2026-08-19 — New file. Six features could serve a written-in answer instead
// of an AI one, and nothing on screen distinguished them. A manager reading an
// "AI insight" had no way to tell whether a model produced it.
//
// Deliberately silent on success: a badge on every AI answer would be noise,
// and the thing worth surfacing is the exception. Same reasoning as the SOS
// location-quality labels — say something only when the truth differs from
// what the user would assume.
// ────────────────────────────────────────────────────────────────────────────
import { Info } from "lucide-react";

const REASON: Record<string, string> = {
  NOT_CONFIGURED: "AI is not configured",
  AUTH_FAILED: "AI key was rejected",
  RATE_LIMITED: "AI is rate limited right now",
  MODEL_NOT_FOUND: "AI model is unavailable",
  TIMEOUT: "AI did not respond in time",
  BAD_RESPONSE: "AI returned an unusable answer",
  UPSTREAM_ERROR: "AI service is unavailable",
};

/** Renders nothing when the answer genuinely came from the model. */
export function AiBadge({
  aiGenerated,
  status,
  className = "",
}: {
  aiGenerated?: boolean | null;
  status?: string | null;
  className?: string;
}) {
  if (aiGenerated !== false) return null;
  const why = (status && REASON[status]) || "AI unavailable";
  return (
    <span
      title={why}
      className={`inline-flex items-center gap-1 text-[10.5px] font-semibold px-2 py-0.5 rounded-md ${className}`}
      style={{
        background: "rgba(180,83,9,0.10)",
        color: "#7C3E00",
        border: "1px solid rgba(180,83,9,0.22)",
      }}
    >
      <Info className="w-3 h-3 flex-shrink-0" />
      Standard summary · {why}
    </span>
  );
}
