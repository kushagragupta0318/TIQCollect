// ─── CHANGELOG (prototype → product) ───
// 2026-07-30 — New file. The familiar N-box OTP entry (one digit per box,
//   auto-advance, backspace-to-previous, paste-to-fill). Used by the borrower
//   payment-verification OTP gate in RecordVisitPage and the deferred
//   "Verify now" flow in AgentCaseDetailPage. See prototype_to_product/30.07.md.
// ───────────────────────────────────────────────────────────────────────────
import { useRef, type KeyboardEvent, type ClipboardEvent } from "react";

interface Props {
  value: string;
  onChange: (v: string) => void;
  length?: number;
  disabled?: boolean;
  autoFocus?: boolean;
}

// Controlled OTP box group. `value` is the plain digit string (e.g. "482"),
// rendered across `length` boxes.
export default function OtpInput({ value, onChange, length = 4, disabled, autoFocus }: Props) {
  const refs = useRef<Array<HTMLInputElement | null>>([]);
  const digits = Array.from({ length }, (_, i) => value[i] ?? "");

  function setAt(i: number, d: string) {
    const next = (value.slice(0, i) + d + value.slice(i + 1)).slice(0, length);
    onChange(next.replace(/\D/g, ""));
    if (d && i < length - 1) refs.current[i + 1]?.focus();
  }

  function handleKeyDown(i: number, e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Backspace" && !digits[i] && i > 0) {
      refs.current[i - 1]?.focus();
    }
  }

  function handlePaste(e: ClipboardEvent<HTMLInputElement>) {
    e.preventDefault();
    const pasted = e.clipboardData.getData("text").replace(/\D/g, "").slice(0, length);
    if (pasted) {
      onChange(pasted);
      refs.current[Math.min(pasted.length, length - 1)]?.focus();
    }
  }

  return (
    <div className="flex gap-2 justify-center" role="group" aria-label="OTP">
      {digits.map((d, i) => (
        <input
          key={i}
          ref={(el) => { refs.current[i] = el; }}
          type="text"
          inputMode="numeric"
          autoComplete={i === 0 ? "one-time-code" : "off"}
          maxLength={1}
          disabled={disabled}
          autoFocus={autoFocus && i === 0}
          value={d}
          onChange={(e) => setAt(i, e.target.value.replace(/\D/g, "").slice(-1))}
          onKeyDown={(e) => handleKeyDown(i, e)}
          onPaste={handlePaste}
          onFocus={(e) => e.target.select()}
          className="w-12 h-14 text-center text-2xl font-bold rounded-xl border border-slate-300 focus:border-brand-500 focus:ring-2 focus:ring-brand-200 focus:outline-none disabled:bg-slate-100 disabled:text-slate-400"
        />
      ))}
    </div>
  );
}
