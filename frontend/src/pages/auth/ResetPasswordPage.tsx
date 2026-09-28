// 2026-09-28 — NEW (P1 A11 / A07, d4). Set a new password with a reset
//   token — from an admin's link (?token=), from the forgot-password flow, or
//   from a first login that must change its password (both in router state).
//   A reset opens no session: the person signs in afterwards, and a second
//   factor, if they have one, is still asked for.
import { useEffect, useState, type FormEvent } from "react";
import { Link, useLocation, useNavigate } from "react-router";
import { toast } from "react-hot-toast";
import { resetPassword } from "@/api/auth";
import { AuthCard, PasswordHint } from "@/components/auth/AuthCard";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { errorDetail } from "@/lib/apiError";
import { passwordProblem, tokenFrom } from "@/lib/authFlow";

export default function ResetPasswordPage() {
  const location = useLocation();
  const navigate = useNavigate();
  const [token] = useState(() => tokenFrom(location.search, location.state));
  const [firstLogin] = useState(() => (location.state as { reason?: string } | null)?.reason === "first-login");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (location.search) navigate(location.pathname, { replace: true });
  }, [location.search, location.pathname, navigate]);

  const problem = password ? passwordProblem(password) : null;
  const mismatch = !!confirm && confirm !== password;

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!token || problem || mismatch || !password) return;
    setBusy(true);
    setError(null);
    try {
      const res = await resetPassword(token, password);
      toast.success(res.message);
      navigate("/login", { replace: true });
    } catch (err) {
      setError(errorDetail(err, "Could not update your password."));
    } finally {
      setBusy(false);
    }
  }

  if (!token) {
    return (
      <AuthCard title="This link can't be used" subtitle="Reset links work once and expire. Request a new one.">
        <Link to="/forgot-password" className="text-sm font-medium text-brand-600">Reset my password</Link>
      </AuthCard>
    );
  }

  return (
    <AuthCard
      title={firstLogin ? "Choose a new password" : "Reset your password"}
      subtitle={firstLogin ? "Your account needs a new password before you can continue." : undefined}
      footer={<Link to="/login" className="font-medium text-brand-600">Back to sign in</Link>}
    >
      <form onSubmit={submit} className="space-y-3">
        <Input label="New password" type="password" value={password} autoComplete="new-password"
               onChange={(e) => setPassword(e.target.value)} className="h-10 px-3" required />
        <Input label="Confirm password" type="password" value={confirm} autoComplete="new-password"
               onChange={(e) => setConfirm(e.target.value)} className="h-10 px-3" required />
        <PasswordHint problem={problem} mismatch={mismatch} />
        {error && <p className="text-sm text-danger-600" role="alert">{error}</p>}
        <Button type="submit" fullWidth loading={busy} disabled={!!problem || mismatch || !password}>
          Save new password
        </Button>
      </form>
    </AuthCard>
  );
}
