// 2026-09-28 — NEW (P1 A11 / A06, d4). Accept an invitation: the invitee
//   sees who invited them to what, chooses their own password, and is signed
//   in. The token arrives in the link's ?token=; it is read once and removed
//   from the address bar so it does not sit in history.
import { useEffect, useState, type FormEvent } from "react";
import { Link, useLocation, useNavigate } from "react-router";
import { useQuery } from "@tanstack/react-query";
import { acceptInvite, previewInvite } from "@/api/auth";
import { AuthCard, PasswordHint } from "@/components/auth/AuthCard";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { errorDetail } from "@/lib/apiError";
import { passwordProblem, tokenFrom } from "@/lib/authFlow";
import { useAuthStore } from "@/store/authStore";
import { useFinishLogin } from "./useFinishLogin";

const ROLE_NAMES: Record<string, string> = {
  BANK_ADMIN: "Bank administrator", BANK_ANALYST: "Bank analyst", BANK_TECHOPS: "Bank tech ops",
  AGENCY_ADMIN: "Agency administrator", AGENCY_MANAGER: "Agency manager",
};

export default function SetPasswordPage() {
  const location = useLocation();
  const navigate = useNavigate();
  const { deviceId } = useAuthStore();
  const finish = useFinishLogin();
  const [token] = useState(() => tokenFrom(location.search, location.state));
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (location.search) navigate(location.pathname, { replace: true });
  }, [location.search, location.pathname, navigate]);

  const preview = useQuery({
    queryKey: ["invite-preview", token],
    queryFn: () => previewInvite(token as string),
    enabled: !!token,
    retry: false,
    staleTime: Infinity,
  });

  const problem = password ? passwordProblem(password, preview.data?.email) : null;
  const mismatch = !!confirm && confirm !== password;

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!token || problem || mismatch || !password) return;
    setBusy(true);
    setError(null);
    try {
      finish(await acceptInvite(token, password, deviceId), preview.data?.email ?? "");
    } catch (err) {
      setError(errorDetail(err, "Could not set your password. Try again."));
    } finally {
      setBusy(false);
    }
  }

  if (!token || preview.isError) {
    return (
      <AuthCard title="This link can't be used" subtitle={
        "The invitation may have expired (they last 72 hours), been used, or been withdrawn. "
        + "Ask whoever invited you to send a new one."}>
        <Link to="/login" className="text-sm font-medium text-brand-600">Go to sign in</Link>
      </AuthCard>
    );
  }

  const inv = preview.data;
  return (
    <AuthCard
      title="Set your password"
      subtitle={inv ? <>You have been invited as <b>{ROLE_NAMES[inv.role] ?? inv.role}</b>
        {inv.organisation ? <> at <b>{inv.organisation}</b></> : null}.</> : "Checking your invitation…"}
      footer={<Link to="/login" className="font-medium text-brand-600">Already have an account? Sign in</Link>}
    >
      <form onSubmit={submit} className="space-y-3">
        <Input label="Email" value={inv?.email ?? ""} readOnly className="h-10 px-3" autoComplete="username" />
        <Input label="New password" type="password" value={password} autoComplete="new-password"
               onChange={(e) => setPassword(e.target.value)} className="h-10 px-3" required />
        <Input label="Confirm password" type="password" value={confirm} autoComplete="new-password"
               onChange={(e) => setConfirm(e.target.value)} className="h-10 px-3" required />
        <PasswordHint problem={problem} mismatch={mismatch} />
        {error && <p className="text-sm text-danger-600" role="alert">{error}</p>}
        <Button type="submit" fullWidth loading={busy} disabled={!inv || !!problem || mismatch || !password}>
          Set password and sign in
        </Button>
      </form>
    </AuthCard>
  );
}
