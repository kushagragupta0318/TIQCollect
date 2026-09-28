// 2026-09-28 — NEW (P1 A11 / A08, d4). Two-factor setup for bank users, in
//   two modes:
//   - WITH A TICKET (router state): login withheld the session because
//     BANK_MFA_REQUIRED is on; confirming a code opens it.
//   - SIGNED IN (no ticket): from Account security; confirming a code signs
//     out the user's other devices, which were opened without the factor.
//   The secret is shown once — as a QR code and as text for manual entry —
//   and is never stored in the browser.
import { useState, type FormEvent } from "react";
import { Link, useLocation, useNavigate } from "react-router";
import { useQuery } from "@tanstack/react-query";
import QRCode from "react-qr-code";
import { toast } from "react-hot-toast";
import { confirmMfaSetup, confirmMfaWithTicket, startMfaSetup, startMfaWithTicket } from "@/api/auth";
import { AuthCard } from "@/components/auth/AuthCard";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { errorDetail } from "@/lib/apiError";
import { useAuthStore } from "@/store/authStore";
import { useFinishLogin } from "./useFinishLogin";

export default function MfaSetupPage() {
  const location = useLocation();
  const navigate = useNavigate();
  const { deviceId, isAuthenticated } = useAuthStore();
  const finish = useFinishLogin();
  const [ticket] = useState(() => {
    const t = (location.state as { ticket?: unknown } | null)?.ticket;
    return typeof t === "string" ? t : null;
  });
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const secret = useQuery({
    queryKey: ["mfa-setup", ticket],
    queryFn: () => (ticket ? startMfaWithTicket(ticket) : startMfaSetup()),
    enabled: !!ticket || isAuthenticated,
    retry: false,
    staleTime: Infinity,
    gcTime: 0,                     // the secret does not outlive this page
  });

  async function confirm(e: FormEvent) {
    e.preventDefault();
    if (code.length !== 6) return;
    setBusy(true);
    setError(null);
    try {
      if (ticket) {
        finish(await confirmMfaWithTicket(ticket, code, deviceId), "");
      } else {
        toast.success((await confirmMfaSetup(code)).message);
        navigate("/account/security", { replace: true });
      }
    } catch (err) {
      setCode("");
      setError(errorDetail(err, "That code is not right. Check the time on your phone."));
    } finally {
      setBusy(false);
    }
  }

  if ((!ticket && !isAuthenticated) || secret.isError) {
    return (
      <AuthCard title="Two-factor setup unavailable" subtitle={
        secret.isError ? errorDetail(secret.error, "This setup link has expired. Sign in again.")
                       : "Sign in first to set up two-factor sign-in."}>
        <Link to="/login" className="text-sm font-medium text-brand-600">Go to sign in</Link>
      </AuthCard>
    );
  }

  return (
    <AuthCard
      title="Set up two-factor sign-in"
      subtitle="Scan the code with an authenticator app (Google Authenticator, Microsoft Authenticator, Authy), then enter the 6 digits it shows."
    >
      {secret.data ? (
        <div className="mb-4 flex flex-col items-center gap-3">
          <div className="rounded-control border border-[#ECEDF1] bg-white p-3">
            <QRCode value={secret.data.otpauth_uri} size={176} />
          </div>
          <p className="text-center text-xs text-[#667085]">
            Can't scan? Enter this key:{" "}
            <code className="select-all break-all font-mono text-[#101828]">{secret.data.secret}</code>
          </p>
        </div>
      ) : (
        <p className="mb-4 text-sm text-[#667085]">Preparing your key…</p>
      )}
      <form onSubmit={confirm} className="space-y-3">
        <Input label="6-digit code" inputMode="numeric" autoComplete="one-time-code" maxLength={6}
               value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 6))}
               className="h-10 px-3 tracking-[0.3em]" required />
        {error && <p className="text-sm text-danger-600" role="alert">{error}</p>}
        <Button type="submit" fullWidth loading={busy} disabled={!secret.data || code.length !== 6}>
          Turn on two-factor sign-in
        </Button>
      </form>
    </AuthCard>
  );
}
