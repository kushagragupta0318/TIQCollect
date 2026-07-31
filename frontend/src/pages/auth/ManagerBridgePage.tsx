// ─── CHANGELOG ─────────────────────────────────────────────────────────────
// 2026-07-30 — collection_dashboard: new public bridge route. Clicking an agency
//   on the collection dashboard now ALWAYS lands on the Manager 1 analytics view,
//   regardless of who (if anyone) is currently logged in. This page force-logs-in
//   as Manager 1 using the demo credentials (same pair LoginPage's quick-login
//   uses), overwrites any existing session, then redirects to /manager/analytics.
//   Doing the login INSIDE the app (not in the static dashboard) means it works
//   cross-origin and produces real, working tokens for the analytics API calls.
// ─────────────────────────────────────────────────────────────────────────────
import { useEffect, useRef } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "react-hot-toast";
import { login as apiLogin } from "@/api/auth";
import { useAuthStore } from "@/store/authStore";

// Demo Manager 1 credentials — same pair LoginPage's "Manager" quick-login uses.
const MANAGER_EMAIL = "manager1@tiqcollect.in";
const MANAGER_PASSWORD = "Manager@123";

export default function ManagerBridgePage() {
  const navigate = useNavigate();
  const { setTokens, setUser, deviceId } = useAuthStore();
  const ranOnce = useRef(false);

  useEffect(() => {
    if (ranOnce.current) return;
    ranOnce.current = true;

    // Always re-login as Manager 1, overwriting whatever session exists
    // (field agent, a different manager, or none at all).
    apiLogin(MANAGER_EMAIL, MANAGER_PASSWORD, deviceId)
      .then((data) => {
        setTokens(data.access_token, data.refresh_token);
        setUser({
          id: data.user_id,
          email: MANAGER_EMAIL,
          full_name: data.full_name,
          role: data.role,
          is_active: true,
        });
        navigate("/manager/analytics", { replace: true });
      })
      .catch(() => {
        toast.error("Could not open the manager dashboard.");
        navigate("/login", { replace: true });
      });
  }, []);

  return (
    <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-brand-900 via-brand-700 to-brand-500">
      <p className="text-white text-sm">Opening ABC Collections…</p>
    </div>
  );
}
