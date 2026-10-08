// collection_dashboard: public bridge route — exchanges a link token for a real session, no login form shown
import { useEffect } from "react";
import { useNavigate, useSearchParams } from "react-router";
import { toast } from "react-hot-toast";
import { quickLogin } from "@/api/auth";
import { useAuthStore } from "@/store/authStore";
import { quickLoginLanding } from "@/lib/roles";

// The token is single-use: the backend 401s the second exchange. A useRef guard
// is NOT enough — React StrictMode unmounts and remounts this component in dev,
// giving a fresh ref each time, so the effect's exchange fired twice and the
// second 401 bounced a just-succeeded login to /login. A module-scoped set
// survives the remount and keys by token, so a distinct link later in the same
// session still exchanges.
const exchanged = new Set<string>();

export default function QuickLoginPage() {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const { setTokens, setUser } = useAuthStore();

  useEffect(() => {
    const token = params.get("token");
    if (token && exchanged.has(token)) return;
    if (token) exchanged.add(token);
    if (!token) {
      navigate("/login", { replace: true });
      return;
    }

    quickLogin(token)
      .then((data) => {
        setTokens(data.access_token, data.refresh_token);
        setUser({ id: data.user_id, email: "", full_name: data.full_name, role: data.role, is_active: true });
        navigate(quickLoginLanding(data.role), { replace: true });
      })
      .catch(() => {
        toast.error("This link is invalid or has expired.");
        navigate("/login", { replace: true });
      });
  }, []);

  return (
    <div className="flex min-h-screen items-center justify-center bg-background p-4">
      <div className="rounded-card border border-border bg-white px-6 py-5 text-sm font-medium text-foreground shadow-resting">Signing you in…</div>
    </div>
  );
}
