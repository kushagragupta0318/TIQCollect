// 2026-09-28 (P1 A11, d4) — one way to act on a login-shaped answer
// (/auth/login, invite accept, the MFA ticket confirm): store the session and
// go home, or go to the step the server says is still owed. The decision is
// lib/authFlow.afterLogin (tested); this is only the wiring.
import { useNavigate } from "react-router";
import { toast } from "react-hot-toast";
import { afterLogin, isNextStep, type LoginResult } from "@/lib/authFlow";
import { useAuthStore } from "@/store/authStore";
import type { UserRole } from "@/types";

export function useFinishLogin() {
  const navigate = useNavigate();
  const { setTokens, setUser } = useAuthStore();
  return (result: LoginResult, email: string) => {
    if (!isNextStep(result)) {
      setTokens(result.access_token, result.refresh_token);
      // role is a plain string on the wire: bank roles exist before src/types names them.
      setUser({ id: result.user_id, email, full_name: result.full_name, role: result.role as UserRole,
                is_active: true });
      toast.success(`Welcome, ${result.full_name.split(" ")[0]}!`);
    } else {
      toast(result.message);
    }
    const { to, state } = afterLogin(result);
    navigate(to, { replace: true, state });
  };
}
