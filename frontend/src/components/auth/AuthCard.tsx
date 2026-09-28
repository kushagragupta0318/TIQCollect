// 2026-09-28 (P1 A11, d4) — the one frame for the credential pages
// (set password, reset, forgot, two-factor setup, account security). The
// login page keeps its own two-panel layout; these are short single tasks,
// so they share a centred card in the same tokens.
import type { ReactNode } from "react";
import { Link } from "react-router";
import { BrandLogo } from "@/components/ui/BrandLogo";

export function AuthCard({ title, subtitle, children, footer }: {
  title: string;
  subtitle?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
}) {
  return (
    <main className="min-h-dvh bg-[#EEF0F4] px-4 py-8 font-sans">
      <div className="mx-auto w-full max-w-md">
        <Link to="/" className="mb-6 inline-flex items-center gap-3" aria-label="Go to TIQCollect homepage">
          <BrandLogo size={32} />
          <span className="text-lg font-bold tracking-[-0.02em] text-[#101828]">TIQCollect</span>
        </Link>
        <section className="rounded-card border border-white bg-white p-6 shadow-[0_8px_32px_rgba(16,24,40,0.08)] sm:p-8">
          <header className="mb-5">
            <h1 className="text-2xl font-extrabold tracking-[-0.03em] text-[#101828]">{title}</h1>
            {subtitle && <p className="mt-2 text-sm text-[#667085]">{subtitle}</p>}
          </header>
          {children}
        </section>
        {footer && <div className="mt-4 text-center text-sm text-[#667085]">{footer}</div>}
      </div>
    </main>
  );
}

/** A new-password pair with the live rule (lib/authFlow.passwordProblem). */
export function PasswordHint({ problem, mismatch }: { problem: string | null; mismatch: boolean }) {
  const text = problem ?? (mismatch ? "The two passwords don't match." : null);
  return text ? <p className="text-xs text-danger-600" role="alert">{text}</p> : (
    <p className="text-xs text-[#667085]">At least 10 characters, with a letter and a number.</p>
  );
}
