// 2026-09-28 — NEW (P1 A11 / A07, d4). Self-service reset by SMS code. It
//   replaces a toast that said "managed by your administrator". The first
//   step shows the same message whether or not the account exists — the
//   server answers identically, and so does this page.
import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router";
import { forgotPassword, verifyResetCode } from "@/api/auth";
import { AuthCard } from "@/components/auth/AuthCard";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { errorDetail } from "@/lib/apiError";

export default function ForgotPasswordPage() {
  const navigate = useNavigate();
  const [identifier, setIdentifier] = useState("");
  const [requestId, setRequestId] = useState<string | null>(null);
  const [message, setMessage] = useState("");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function send(e: FormEvent) {
    e.preventDefault();
    if (!identifier.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const res = await forgotPassword(identifier.trim());
      setRequestId(res.request_id);
      setMessage(res.message);
    } catch (err) {
      setError(errorDetail(err, "Could not send a code. Try again in a minute."));
    } finally {
      setBusy(false);
    }
  }

  async function verify(e: FormEvent) {
    e.preventDefault();
    if (!requestId || code.length !== 6) return;
    setBusy(true);
    setError(null);
    try {
      const { reset_token } = await verifyResetCode(requestId, code);
      navigate("/reset-password", { replace: true, state: { token: reset_token } });
    } catch (err) {
      setCode("");
      setError(errorDetail(err, "That code is not right, or it has expired."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthCard
      title="Reset your password"
      subtitle={requestId ? message : "Enter your email or phone. We'll text a code to the phone on your account."}
      footer={<Link to="/login" className="font-medium text-brand-600">Back to sign in</Link>}
    >
      {!requestId ? (
        <form onSubmit={send} className="space-y-3">
          <Input label="Email or phone" value={identifier} autoComplete="username"
                 onChange={(e) => setIdentifier(e.target.value)} className="h-10 px-3" required />
          {error && <p className="text-sm text-danger-600" role="alert">{error}</p>}
          <Button type="submit" fullWidth loading={busy}>Send code</Button>
        </form>
      ) : (
        <form onSubmit={verify} className="space-y-3">
          <Input label="6-digit code" inputMode="numeric" autoComplete="one-time-code" maxLength={6}
                 value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 6))}
                 className="h-10 px-3 tracking-[0.3em]" autoFocus required />
          {error && <p className="text-sm text-danger-600" role="alert">{error}</p>}
          <Button type="submit" fullWidth loading={busy} disabled={code.length !== 6}>Continue</Button>
          <button type="button" className="w-full text-sm text-brand-600"
                  onClick={() => { setRequestId(null); setCode(""); setError(null); }}>
            Didn't get it? Start again
          </button>
        </form>
      )}
    </AuthCard>
  );
}
