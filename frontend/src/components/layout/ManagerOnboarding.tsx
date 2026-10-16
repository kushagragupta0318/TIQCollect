// Day-1 onboarding — shown by ManagerLayout when the signed-in manager has no
// agents yet (a brand-new agency manager). It replaces the whole dashboard with
// a first-run "set up your team" flow: add agents one at a time (the same Add
// Agent drawer), each lands in a roster with its login, and the manager clicks
// Continue when done.
//
// The gate in ManagerLayout keys off the ["manager","agents"] query. We DO NOT
// invalidate it as agents are added — so the cached (empty) count holds and the
// manager stays on this screen through the whole setup — and invalidate ONLY on
// Continue, which flips the gate and renders the full app. A refresh mid-setup
// takes them straight to the dashboard (agents now exist), by design.
//
// Credentials: in DEMO_MODE every agent shares one password (returned as
// demo_login), shown once in a banner plus a per-row copy. In a real deployment
// there is no password — rows show the SMS activation status instead.
import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "react-hot-toast";
import { Users, Plus, LogOut, Copy, ArrowRight, Check, AlertCircle } from "lucide-react";
import { BrandLogo } from "@/components/ui/BrandLogo";
import { CreateAgentDrawer } from "@/pages/manager/CreateAgentDrawer";
import type { CreateAgentResult } from "@/api/manager";

export default function ManagerOnboarding({ userName, agencyName, onLogout }: {
  userName?: string;
  agencyName?: string;
  onLogout: () => void;
}) {
  const queryClient = useQueryClient();
  const [createOpen, setCreateOpen] = useState(false);
  const [roster, setRoster] = useState<CreateAgentResult[]>([]);
  const firstName = (userName ?? "").trim().split(/\s+/)[0] || "there";

  // All demo agents share one password; take it from the first that has one.
  const demoPassword = roster.find((r) => r.demo_login)?.demo_login?.password;

  function copy(text: string, label: string) {
    try {
      void navigator.clipboard?.writeText(text);
      toast.success(label, { duration: 1500 });
    } catch {
      toast.error("Couldn't copy to clipboard");
    }
  }

  function copyAll() {
    const emails = roster.map((r) => r.email).join("\n");
    const text = demoPassword ? `${emails}\n\nPassword (all agents): ${demoPassword}` : emails;
    copy(text, "All logins copied");
  }

  function continueToDashboard() {
    // Flip the gate: the agents query refetches, returns the new agents, and
    // ManagerLayout renders the full app in place of this screen.
    void queryClient.invalidateQueries({ queryKey: ["manager", "agents"] });
  }

  return (
    <div style={{ minHeight: "100svh", display: "flex", flexDirection: "column", background: "hsl(var(--background))" }}>
      {/* Minimal top bar — brand + agency, and a way out, so the manager is
          never trapped on a dashboard-less screen. */}
      <header className="flex items-center justify-between px-4 sm:px-6 py-3" style={{ borderBottom: "1px solid #ECEDF1" }}>
        <div className="flex items-center gap-2.5 min-w-0">
          <BrandLogo size={30} />
          <p className="text-[15px] font-bold truncate" style={{ color: "#101828", letterSpacing: "-0.02em" }}>
            {agencyName ?? "TIQCollect"}
          </p>
        </div>
        <button
          type="button"
          onClick={onLogout}
          className="tap-target flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold transition hover:brightness-95"
          style={{ color: "#667085", border: "1px solid #ECEDF1", background: "#fff" }}
        >
          <LogOut className="w-3.5 h-3.5" /> Sign out
        </button>
      </header>

      <main className="flex-1 flex items-start sm:items-center justify-center px-4 py-8 sm:py-10">
        {roster.length === 0 ? (
          // ── Empty state: the welcome + the first action ──
          <div className="w-full max-w-md text-center">
            <div
              className="mx-auto mb-5 flex items-center justify-center rounded-2xl"
              style={{ width: 64, height: 64, background: "#EAF2FF", color: "#1677FF" }}
            >
              <Users className="w-7 h-7" />
            </div>
            <h1 className="text-2xl font-bold mb-2" style={{ color: "#101828", letterSpacing: "-0.02em" }}>
              Welcome, {firstName}
            </h1>
            <p className="text-sm mb-1" style={{ color: "#475467" }}>
              Your agency workspace is ready.
            </p>
            <p className="text-sm mb-7" style={{ color: "#667085" }}>
              You don&apos;t have any agents yet. Add your field agents to start
              allocating cases, planning beats, and tracking recovery.
            </p>
            <button
              type="button"
              onClick={() => setCreateOpen(true)}
              className="tap-target inline-flex items-center gap-2 px-5 py-2.5 rounded-xl font-semibold text-sm transition hover:brightness-95"
              style={{ background: "#1677FF", border: "1px solid #1677FF", color: "#fff" }}
            >
              <Plus className="w-4 h-4" /> Add your first agent
            </button>
            <p className="text-xs mt-6" style={{ color: "#98A2B3" }}>
              Dashboards, analytics, the live map and compliance appear once you
              have at least one agent.
            </p>
          </div>
        ) : (
          // ── Roster: agents added so far + credentials, add more, or continue ──
          <div className="w-full max-w-lg">
            <h1 className="text-xl font-bold mb-1" style={{ color: "#101828", letterSpacing: "-0.02em" }}>
              Set up your team
            </h1>
            <p className="text-sm mb-5" style={{ color: "#667085" }}>
              Add the agents who&apos;ll work your cases. Add as many as you like,
              then continue.
            </p>

            {/* Roster list */}
            <div className="flex items-center justify-between mb-2">
              <span className="text-xs font-semibold" style={{ color: "#475467" }}>
                Agents added ({roster.length})
              </span>
              <button
                type="button"
                onClick={copyAll}
                className="tap-target inline-flex items-center gap-1 text-xs font-semibold transition hover:brightness-95"
                style={{ color: "#1677FF" }}
              >
                <Copy className="w-3.5 h-3.5" /> Copy all
              </button>
            </div>

            <div className="rounded-xl overflow-hidden mb-5" style={{ border: "1px solid #ECEDF1" }}>
              {roster.map((r, i) => (
                <div
                  key={r.agent_id}
                  className="flex items-center justify-between gap-3 px-3 py-2.5"
                  style={{ borderTop: i === 0 ? "none" : "1px solid #F2F3F5", background: "#fff" }}
                >
                  <div className="min-w-0">
                    <p className="text-sm font-semibold truncate" style={{ color: "#101828" }}>
                      {r.full_name} <span className="font-normal" style={{ color: "#98A2B3" }}>· {r.employee_code}</span>
                    </p>
                    <p className="text-xs truncate" style={{ color: "#667085" }}>{r.email}</p>
                  </div>
                  {r.demo_login ? (
                    <button
                      type="button"
                      onClick={() => copy(`${r.email}\n${r.demo_login!.password}`, "Login copied")}
                      className="tap-target inline-flex items-center gap-1 px-2.5 py-1 rounded-lg text-xs font-semibold flex-shrink-0 transition hover:brightness-95"
                      style={{ background: "#EAF2FF", color: "#1677FF" }}
                    >
                      <Copy className="w-3.5 h-3.5" /> Copy login
                    </button>
                  ) : (
                    <span
                      className="inline-flex items-center gap-1 text-xs flex-shrink-0"
                      style={{ color: r.activation.sent ? "#067647" : "#B42318" }}
                    >
                      {r.activation.sent ? <Check className="w-3.5 h-3.5" /> : <AlertCircle className="w-3.5 h-3.5" />}
                      {r.activation.sent ? "Activation link sent" : "Link not sent"}
                    </span>
                  )}
                </div>
              ))}
            </div>

            <div className="flex flex-col-reverse sm:flex-row sm:items-center sm:justify-between gap-3">
              <button
                type="button"
                onClick={() => setCreateOpen(true)}
                className="tap-target inline-flex items-center justify-center gap-2 px-4 py-2.5 rounded-xl font-semibold text-sm transition hover:bg-slate-50"
                style={{ color: "#1677FF", border: "1px solid #CFE0FF", background: "#fff" }}
              >
                <Plus className="w-4 h-4" /> Add another agent
              </button>
              <button
                type="button"
                onClick={continueToDashboard}
                className="tap-target inline-flex items-center justify-center gap-2 px-5 py-2.5 rounded-xl font-semibold text-sm transition hover:brightness-95"
                style={{ background: "#1677FF", border: "1px solid #1677FF", color: "#fff" }}
              >
                Continue <ArrowRight className="w-4 h-4" />
              </button>
            </div>

            {!demoPassword && (
              <p className="text-xs mt-4" style={{ color: "#98A2B3" }}>
                Each agent receives a one-time link by SMS to set their own
                password. Use Reset Login on the Agents page to resend one.
              </p>
            )}
          </div>
        )}
      </main>

      <CreateAgentDrawer
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        // Collect the created agent into the roster; the drawer's own success
        // toast is suppressed because the roster shows the credentials instead.
        showSuccessToast={false}
        onCreated={(result) => setRoster((prev) => [...prev, result])}
      />
    </div>
  );
}
