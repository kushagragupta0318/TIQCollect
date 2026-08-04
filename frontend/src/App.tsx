import { BrowserRouter, HashRouter, Routes, Route, Navigate } from "react-router";
import { Toaster } from "react-hot-toast";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { ProtectedRoute } from "@/components/layout/ProtectedRoute";
import AgentLayout from "@/components/layout/AgentLayout";
import ManagerLayout from "@/components/layout/ManagerLayout";

import LandingPage from "@/pages/LandingPage";
import LoginPage from "@/pages/auth/LoginPage";
import QuickLoginPage from "@/pages/auth/QuickLoginPage"; // collection_dashboard
import ManagerBridgePage from "@/pages/auth/ManagerBridgePage"; // collection_dashboard
import AgentHomePage from "@/pages/agent/AgentHomePage";
import AgentCasesPage from "@/pages/agent/AgentCasesPage";
import AgentProfilePage from "@/pages/agent/AgentProfilePage";
import AgentCaseDetailPage from "@/pages/agent/AgentCaseDetailPage";
import RecordVisitPage from "@/pages/agent/RecordVisitPage";
import BeatMapPage from "@/pages/agent/BeatMapPage";
import ManagerOverviewPage from "@/pages/manager/ManagerOverviewPage";
import ManagerAgentsPage from "@/pages/manager/ManagerAgentsPage";
import ManagerCasesPage from "@/pages/manager/ManagerCasesPage";
import ManagerAnalyticsPage from "@/pages/manager/ManagerAnalyticsPage";
import ManagerCompliancePage from "@/pages/manager/ManagerCompliancePage";

import { useAuthStore } from "@/store/authStore";
import { IS_OFFLINE_BUILD } from "@/api/axios";

// The offline build is served as plain static files out of another app's public
// folder, so it routes on the hash — deep links then need no server rewrites and
// work under any sub-path. Dev and normal builds keep BrowserRouter unchanged.
const Router = IS_OFFLINE_BUILD ? HashRouter : BrowserRouter;

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { staleTime: 30_000, retry: 1 },
  },
});

function RootRedirect() {
  const { isAuthenticated, user } = useAuthStore();
  if (!isAuthenticated || !user) return <LandingPage />;
  if (user.role === "FIELD_AGENT") return <Navigate to="/agent/home" replace />;
  return <Navigate to="/manager/overview" replace />;
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <Router>
        <Routes>
          <Route path="/" element={<RootRedirect />} />
          <Route path="/login" element={<LoginPage />} />
          {/* collection_dashboard: public deep-link, bypasses login form */}
          <Route path="/quick-login" element={<QuickLoginPage />} />
          {/* collection_dashboard: always force a Manager 1 session, then land on analytics */}
          <Route path="/manager-bridge" element={<ManagerBridgePage />} />
          {/* Record Visit — constrained to max-w-md to match mobile PWA shell */}
          <Route
            path="/agent/visit/:caseId"
            element={
              <ProtectedRoute allowedRoles={["FIELD_AGENT"]}>
                <div className="min-h-screen bg-slate-50 flex flex-col max-w-md mx-auto relative">
                  <RecordVisitPage />
                </div>
              </ProtectedRoute>
            }
          />

          {/* Field Agent — mobile PWA shell */}
          <Route
            path="/agent"
            element={
              <ProtectedRoute allowedRoles={["FIELD_AGENT"]}>
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
              <ProtectedRoute allowedRoles={["AGENCY_MANAGER", "AGENCY_ADMIN"]}>
                <ManagerLayout />
              </ProtectedRoute>
            }
          >
            <Route index element={<Navigate to="overview" replace />} />
            <Route path="overview" element={<ManagerOverviewPage />} />
            <Route path="agents" element={<ManagerAgentsPage />} />
            <Route path="cases" element={<ManagerCasesPage />} />
            <Route path="analytics" element={<ManagerAnalyticsPage />} />
            <Route path="compliance" element={<ManagerCompliancePage />} />
          </Route>

          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Router>

      <Toaster
        position="top-center"
        toastOptions={{
          duration: 4000,
          style: { borderRadius: "12px", fontSize: "14px", fontWeight: 500 },
        }}
      />
    </QueryClientProvider>
  );
}
