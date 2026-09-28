import { Navigate } from "react-router";
import { useAuthStore } from "@/store/authStore";
import { guardRedirect } from "@/lib/roles";

interface Props {
  children: React.ReactNode;
  allowedRoles: readonly string[];
}

export function ProtectedRoute({ children, allowedRoles }: Props) {
  const { isAuthenticated, user } = useAuthStore();
  // Signed out → /login; wrong portal → the role's own home (lib/roles.ts).
  const redirect = guardRedirect(user, isAuthenticated, allowedRoles);
  if (redirect) return <Navigate to={redirect} replace />;
  return <>{children}</>;
}
