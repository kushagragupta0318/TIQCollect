import { useEffect, useLayoutEffect, lazy, Suspense } from "react";
import { BrowserRouter, Routes, Route, Navigate, useLocation } from "react-router";
import { Toaster } from "react-hot-toast";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { ProtectedRoute } from "@/components/layout/ProtectedRoute";
import AgentLayout from "@/components/layout/AgentLayout";
import ManagerLayout from "@/components/layout/ManagerLayout";

import { useAuthStore } from "@/store/authStore";
import { AGENT_ROLES, MANAGER_ROLES, homeFor } from "@/lib/roles";

// Route-level code-splitting: separate heavy bundles (RecordVisit, Analytics, Maps)
const LandingPage = lazy(() => import("@/pages/LandingPage"));
const LoginPage = lazy(() => import("@/pages/auth/LoginPage"));
const QuickLoginPage = lazy(() => import("@/pages/auth/QuickLoginPage")); // link minted by scripts/generate_quick_login_link.py
// 2026-09-28 (P1 A11, d4): invitations, password reset, two-factor, account security.
const SetPasswordPage = lazy(() => import("@/pages/auth/SetPasswordPage"));
const ResetPasswordPage = lazy(() => import("@/pages/auth/ResetPasswordPage"));
const ForgotPasswordPage = lazy(() => import("@/pages/auth/ForgotPasswordPage"));
const MfaSetupPage = lazy(() => import("@/pages/auth/MfaSetupPage"));
const AccountSecurityPage = lazy(() => import("@/pages/auth/AccountSecurityPage"));

const AgentHomePage = lazy(() => import("@/pages/agent/AgentHomePage"));
const AgentCasesPage = lazy(() => import("@/pages/agent/AgentCasesPage"));
const AgentProfilePage = lazy(() => import("@/pages/agent/AgentProfilePage"));
const AgentCaseDetailPage = lazy(() => import("@/pages/agent/AgentCaseDetailPage"));
const RecordVisitPage = lazy(() => import("@/pages/agent/RecordVisitPage"));
const BeatMapPage = lazy(() => import("@/pages/agent/BeatMapPage"));

const ManagerOverviewPage = lazy(() => import("@/pages/manager/ManagerOverviewPage"));
const ManagerAgentsPage = lazy(() => import("@/pages/manager/ManagerAgentsPage"));
const ManagerLiveMapPage = lazy(() => import("@/pages/manager/ManagerLiveMapPage"));
const ManagerBeatPlanPage = lazy(() => import("@/pages/manager/ManagerBeatPlanPage"));
const ManagerCasesPage = lazy(() => import("@/pages/manager/ManagerCasesPage"));
const ManagerAnalyticsPage = lazy(() => import("@/pages/manager/ManagerAnalyticsPage"));
const ManagerCompliancePage = lazy(() => import("@/pages/manager/ManagerCompliancePage"));
const AgencyProfilePage = lazy(() => import("@/pages/manager/AgencyProfilePage"));

// Mobile app simulator (standalone plan P0). A demo/dev tool: on in the Vite
// dev server, off in a production build unless VITE_ENABLE_SIMULATOR=1 — a
// page that puts two sessions side by side has no business on a field
// deployment. It grants nothing: each frame logs in normally and the server
// enforces every rule against what the frame submits.
const SIMULATOR_ENABLED =
  import.meta.env.DEV || import.meta.env.VITE_ENABLE_SIMULATOR === "1";
const SimulatorPage = lazy(() => import("@/pages/simulator/SimulatorPage"));

// Bank portal (standalone plan §2.4–2.5, tasks UI02–UI05). One lazy module
// owns the whole /bank/* tree — its role guard, the Command Center shell and
// the scoped bank.css — so none of it is in the agency or agent bundles.
const BankApp = lazy(() => import("@/bank/BankApp"));

// Perf (2026-10-06, enriched demo book): a tab switch or an alt-tab back into
// the browser must not restart the whole manager/agent dashboard. staleTime
// keeps a freshly-visited tab's data usable without a refetch; the pages that
// genuinely need live data drive it with their own refetchInterval (overview,
// live map), so window-focus and reconnect refetches are pure redundant load
// on the API and pure redundant re-renders. Measured: with focus-refetch on,
// every return to the browser refetched all active queries at once
// (~5-6 per manager page). Off by default; a page opts back in explicitly.
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 60_000,
      gcTime: 5 * 60_000,
      retry: 1,
      refetchOnWindowFocus: false,
      refetchOnReconnect: false,
    },
  },
});

function PageLoader() {
  return (
    <div className="flex min-h-[50vh] w-full items-center justify-center">
      <div className="flex flex-col items-center gap-3">
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-primary-500 border-t-transparent" />
        <span className="text-xs font-medium text-slate-400">Loading view...</span>
      </div>
    </div>
  );
}

function RootRedirect() {
  const { isAuthenticated, user } = useAuthStore();
  if (!isAuthenticated || !user) return <LandingPage />;
  return <Navigate to={homeFor(user.role)} replace />;
}

/**
 * React Router keeps the document's scroll position between client-side route
 * changes. Reset it before the next route paints so every page opens from its
 * top instead of briefly rendering at the previous page's scroll position.
 */
function ScrollToTop() {
  const location = useLocation();

  useEffect(() => {
    const previous = window.history.scrollRestoration;
    window.history.scrollRestoration = "manual";
    return () => {
      window.history.scrollRestoration = previous;
    };
  }, []);

  useLayoutEffect(() => {
    document.documentElement.scrollTop = 0;
    document.body.scrollTop = 0;
    window.scrollTo({ top: 0, left: 0, behavior: "auto" });
  }, [location.key]);

  return null;
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <ScrollToTop />
        <Suspense fallback={<PageLoader />}>
          <Routes>
            <Route path="/" element={<RootRedirect />} />
            <Route path="/login" element={<LoginPage />} />
            {/* Single-use link token → real session (core/security.py). */}
            <Route path="/quick-login" element={<QuickLoginPage />} />
            {/* A11: public credential pages; tokens arrive in state or ?token= and never stay in the URL. */}
            <Route path="/set-password" element={<SetPasswordPage />} />
            <Route path="/reset-password" element={<ResetPasswordPage />} />
            <Route path="/forgot-password" element={<ForgotPasswordPage />} />
            <Route path="/mfa-setup" element={<MfaSetupPage />} />
            {/* Any signed-in role, bank roles included; the page sends a signed-out visitor to /login. */}
            <Route path="/account/security" element={<AccountSecurityPage />} />
            {/* /manager-bridge was here until 2026-09-24 (A10): a public route that
                logged ANY visitor in as manager1 with a password compiled into the
                bundle. Removed with public/collection_dashboard/, its only caller. */}
            {SIMULATOR_ENABLED && <Route path="/simulator" element={<SimulatorPage />} />}

            {/* Record Visit — follows the same width ladder as AgentLayout:
                phone shell below md, wider column at md, full width at lg where
                the page itself splits into two columns. */}
            <Route
              path="/agent/visit/:caseId"
              element={
                <ProtectedRoute allowedRoles={AGENT_ROLES}>
                  <div className="relative mx-auto flex min-h-svh max-w-md flex-col overflow-x-clip bg-background md:max-w-none">
                    <RecordVisitPage />
                  </div>
                </ProtectedRoute>
              }
            />

            {/* Field Agent — mobile PWA shell */}
            <Route
              path="/agent"
              element={
                <ProtectedRoute allowedRoles={AGENT_ROLES}>
                  <AgentLayout />
                </ProtectedRoute>
              }
            >
              <Route index element={<Navigate to="home" replace />} />
              <Route path="home" element={<AgentHomePage />} />
              <Route path="cases" element={<AgentCasesPage />} />
              <Route path="cases/:id" element={<AgentCaseDetailPage />} />
              <Route path="beat" element={<BeatMapPage />} />
              <Route path="profile" element={<AgentProfilePage />} />
            </Route>

            {/* Agency Manager — desktop */}
            <Route
              path="/manager"
              element={
                <ProtectedRoute allowedRoles={MANAGER_ROLES}>
                  <ManagerLayout />
                </ProtectedRoute>
              }
            >
              <Route index element={<Navigate to="overview" replace />} />
              <Route path="overview" element={<ManagerOverviewPage />} />
              <Route path="agents" element={<ManagerAgentsPage />} />
              <Route path="live-map" element={<ManagerLiveMapPage />} />
              <Route path="beat-plan" element={<ManagerBeatPlanPage />} />
              <Route path="cases" element={<ManagerCasesPage />} />
              <Route path="analytics" element={<ManagerAnalyticsPage />} />
              <Route path="compliance" element={<ManagerCompliancePage />} />
              <Route path="agency-profile" element={<AgencyProfilePage />} />
            </Route>

            {/* Bank portal — roles BANK_ADMIN / BANK_ANALYST / BANK_TECHOPS /
                PLATFORM_ADMIN, guarded inside BankApp. */}
            <Route path="/bank/*" element={<BankApp />} />

            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </Suspense>
      </BrowserRouter>

      <Toaster
        position="top-center"
        toastOptions={{
          duration: 4000,
          style: { borderRadius: "12px", border: "1px solid #ECEDF1", boxShadow: "0 8px 24px rgba(16,24,40,0.10)", color: "#101828", fontSize: "14px", fontWeight: 500 },
        }}
      />
    </QueryClientProvider>
  );
}