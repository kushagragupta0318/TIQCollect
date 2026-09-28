// 2026-09-28 — NEW (P1 A11 / A07 / A08, d4). Account security for any
//   signed-in person: change the password (other devices are signed out), and
//   for bank users, two-factor sign-in (set it up, or turn it off with a
//   current code unless the deployment requires it).
import { useState, type FormEvent } from "react";
import { Link, Navigate, useNavigate } from "react-router";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "react-hot-toast";
import { changePassword, disableMfa, getMfaStatus } from "@/api/auth";
import { AuthCard, PasswordHint } from "@/components/auth/AuthCard";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { errorDetail } from "@/lib/apiError";
import { passwordProblem } from "@/lib/authFlow";
import { homeFor } from "@/lib/roles";
import { useAuthStore } from "@/store/authStore";

export default function AccountSecurityPage() {
  const { isAuthenticated, user } = useAuthStore();
  if (!isAuthenticated || !user) return <Navigate to="/login" replace />;
  return <Signed email={user.email} role={user.role} />;
}

function Signed({ email, role }: { email: string; role: string }) {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const mfa = useQuery({ queryKey: ["mfa-status"], queryFn: getMfaStatus, retry: false });
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [offCode, setOffCode] = useState("");

  const problem = next ? passwordProblem(next, email) : null;
  const mismatch = !!confirm && confirm !== next;

  async function change(e: FormEvent) {
    e.preventDefault();
    if (!current || !next || problem || mismatch) return;
    setBusy(true);
    setError(null);
    try {
      toast.success((await changePassword(current, next)).message);
      setCurrent(""); setNext(""); setConfirm("");
    } catch (err) {
      setError(errorDetail(err, "Could not change your password."));
    } finally {
      setBusy(false);
    }
  }

  async function turnOff(e: FormEvent) {
    e.preventDefault();
    try {
      toast.success((await disableMfa(offCode)).message);
      setOffCode("");
      await qc.invalidateQueries({ queryKey: ["mfa-status"] });
    } catch (err) {
      toast.error(errorDetail(err, "That code is not right."));
    }
  }

  return (
    <AuthCard title="Account security" subtitle={email}
              footer={<Link to={homeFor(role)} className="font-medium text-brand-600">Back</Link>}>
      <form onSubmit={change} className="space-y-3">
        <h2 className="text-sm font-semibold text-[#101828]">Change password</h2>
        <Input label="Current password" type="password" value={current} autoComplete="current-password"
               onChange={(e) => setCurrent(e.target.value)} className="h-10 px-3" required />
        <Input label="New password" type="password" value={next} autoComplete="new-password"
               onChange={(e) => setNext(e.target.value)} className="h-10 px-3" required />
        <Input label="Confirm new password" type="password" value={confirm} autoComplete="new-password"
               onChange={(e) => setConfirm(e.target.value)} className="h-10 px-3" required />
        <PasswordHint problem={problem} mismatch={mismatch} />
        {error && <p className="text-sm text-danger-600" role="alert">{error}</p>}
        <Button type="submit" fullWidth loading={busy} disabled={!current || !next || !!problem || mismatch}>
          Change password
        </Button>
      </form>

      {mfa.data?.allowed && (
        <div className="mt-6 space-y-3 border-t border-[#ECEDF1] pt-5">
          <h2 className="text-sm font-semibold text-[#101828]">Two-factor sign-in</h2>
          {mfa.data.enabled ? (
            mfa.data.required ? (
              <p className="text-sm text-[#667085]">On. Your organisation requires it for bank users.</p>
            ) : (
              <form onSubmit={turnOff} className="space-y-3">
                <p className="text-sm text-[#667085]">On. Enter a current code to turn it off.</p>
                <Input label="6-digit code" inputMode="numeric" maxLength={6} value={offCode}
                       onChange={(e) => setOffCode(e.target.value.replace(/\D/g, "").slice(0, 6))}
                       className="h-10 px-3 tracking-[0.3em]" />
                <Button type="submit" variant="secondary" fullWidth disabled={offCode.length !== 6}>
                  Turn off
                </Button>
              </form>
            )
          ) : mfa.data.configured ? (
            <Button type="button" fullWidth onClick={() => navigate("/mfa-setup")}>
              Set up two-factor sign-in
            </Button>
          ) : (
            <p className="text-sm text-[#667085]">Not available on this server yet. Ask your administrator.</p>
          )}
        </div>
      )}
    </AuthCard>
  );
}
