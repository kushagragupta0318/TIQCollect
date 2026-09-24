// The bank portal's route tree — the one module App.tsx lazy-loads for
// `/bank/*` (plan §2.4). It guards the roles, mounts the Command Center shell
// and registers every page in navigation.ts, so the sidebar and the router
// cannot list different screens.
import { Suspense, useCallback } from "react";
import { Navigate, Route, Routes, useNavigate } from "react-router";
import api from "@/api/axios";
import { useAuthStore } from "@/store/authStore";
import { AnalyticsLoading } from "./components/analytics";
// Null unless the gallery is enabled (galleryFlag.ts); lazy when it is, so
// Recharts and the sample data never sit in the shell's chunk.
import { BankComponentGalleryPage } from "./galleryFlag";
import { BankLayout } from "./layout/BankLayout";
import { guardRedirect } from "@/lib/roles";
import { BANK_PORTAL_ROLES, BANK_ROLE_LABELS, isBankPortalRole } from "./layout/bankRoles";
import { BANK_NAV_ITEMS } from "./layout/navigation";
import { BankPlaceholderPage } from "./pages/BankPlaceholderPage";


export default function BankApp() {
  const { isAuthenticated, user, logout } = useAuthStore();
  const navigate = useNavigate();

  const signOut = useCallback(() => {
    api.post("/auth/logout").finally(() => {
      logout();
      navigate("/login", { replace: true });
    });
  }, [logout, navigate]);

  // The same guard decision as ProtectedRoute (lib/roles.ts): signed out →
  // /login, another portal's role → its own home, never a second rejection.
  const redirect = guardRedirect(user, isAuthenticated, BANK_PORTAL_ROLES);
  if (redirect || !user || !isBankPortalRole(user.role)) return <Navigate to={redirect ?? "/login"} replace />;

  const persona = {
    role: BANK_ROLE_LABELS[user.role],
    // Until the global filter bar (task C02) scopes the view, it is the whole book.
    product: "All Products",
    geography: "All India",
  };

  return (
    <Routes>
      <Route element={<BankLayout persona={persona} onSignOut={signOut} />}>
        <Route index element={<Navigate to="overview" replace />} />
        {BANK_NAV_ITEMS.map((item) => (
          <Route key={item.path} path={item.path} element={<BankPlaceholderPage item={item} />} />
        ))}
        {BankComponentGalleryPage && (
          <Route
            path="_gallery"
            element={
              <Suspense fallback={<AnalyticsLoading label="Loading gallery…" />}>
                <BankComponentGalleryPage />
              </Suspense>
            }
          />
        )}
        <Route path="*" element={<Navigate to="overview" replace />} />
      </Route>
    </Routes>
  );
}
