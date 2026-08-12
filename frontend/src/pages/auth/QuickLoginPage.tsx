// collection_dashboard: public bridge route — exchanges a link token for a real session, no login form shown
import { useEffect, useRef } from "react";
import { useNavigate, useSearchParams } from "react-router";
import { toast } from "react-hot-toast";
import { quickLogin } from "@/api/auth";
import { useAuthStore } from "@/store/authStore";

export default function QuickLoginPage() {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const { setTokens, setUser } = useAuthStore();
  const ranOnce = useRef(false);

  useEffect(() => {
    if (ranOnce.current) return;
    ranOnce.current = true;

    const token = params.get("token");
    if (!token) {
      navigate("/login", { replace: true });
      return;
    }

    quickLogin(token)
      .then((data) => {
        setTokens(data.access_token, data.refresh_token);
        setUser({ id: data.user_id, email: "", full_name: data.full_name, role: data.role, is_active: true });
        navigate(data.role === "FIELD_AGENT" ? "/agent/home" : "/manager/analytics", { replace: true });
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
