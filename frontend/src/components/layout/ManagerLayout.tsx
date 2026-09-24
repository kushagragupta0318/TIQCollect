// ─── CHANGELOG (prototype → product) ───────────────────────────────────────
// 2026-07-31 — Responsive pass (manager flow). The sidebar expanded on
//   onMouseEnter, an event a touch device never fires, so on every phone and
//   tablet it sat as 64 permanently-collapsed pixels — 17% of a 360px screen,
//   with its labels unreachable. It is now desktop-only (`hidden lg:flex`,
//   behaviour unchanged there) and below lg a bottom tab bar takes over,
//   reusing AgentLayout's visual language so both flows read as one product.
//   All five destinations stay visible rather than hiding behind a drawer.
//   Sign Out moved into the header avatar menu (6 items is one too many for a
//   comfortable 360px tab bar). Also: 100vh → 100svh in all three places
//   (mobile browser chrome made the old value taller than the visible
//   viewport), rail width driven by --rail-w so the inline style can respond,
//   and responsive header/main padding. See docs/frontend-guide.md.
// 2026-08-05 — Merged tiq-demo. Its EmbeddedManagerLayout (a chrome-less
//   shell for framing inside the Collections Command Center) is dropped
//   along with the rest of the offline build: nothing produces that bundle
//   any more since its build script was deleted upstream.
// ─────────────────────────────────────────────────────────────────────────
import { NavLink, Outlet, useNavigate, useLocation } from "react-router";
import { AlertTriangle, BarChart2, Bell, Briefcase, CalendarOff, Compass, LayoutDashboard, MapPin, Shield, Users } from "lucide-react";
import type { LeaveRequest } from "@/api/agent";
import { BrandLogo } from "@/components/ui/BrandLogo";
import { useState, useEffect, useCallback, useRef } from "react";
import api from "@/api/axios";
import { useAuthStore } from "@/store/authStore";
import { AccountMenu } from "@/components/layout/AccountMenu";
import type { Agent } from "@/types";
import { useQueryClient } from "@tanstack/react-query";
import { useLiveEvents, WORK_EVENTS } from "@/hooks/useLiveEvents";

const SIDEBAR_KEY   = "tiq:sidebar";
const SIDEBAR_W     = 252;
const SIDEBAR_ICON  = 72;

const NAV_ITEMS = [
  { to: "/manager/overview",   icon: LayoutDashboard, label: "Overview" },
  { to: "/manager/agents",     icon: Users,           label: "Agents" },
  { to: "/manager/live-map",   icon: MapPin,          label: "Live Map" },
  // "Field Plan" in the manager's words; the path keeps the code's word.
  { to: "/manager/beat-plan",  icon: Compass,         label: "Field Plan" },
  { to: "/manager/cases",      icon: Briefcase,       label: "Cases" },
  { to: "/manager/analytics",  icon: BarChart2,       label: "Analytics" },
  { to: "/manager/compliance", icon: Shield,          label: "Compliance" },
];

// ── Inline style helpers ──────────────────────────────────────────────────────
// `display` is intentionally NOT set here — it comes from the `hidden lg:flex`
// classes, so an inline value cannot win over the breakpoint.
const sidebarStyle = (open: boolean): React.CSSProperties => ({
  position:   "fixed",
  top:         16,
  left:        16,
  height:      "calc(100svh - 32px)",
  zIndex:      50,
  width:       open ? `${SIDEBAR_W}px` : `${SIDEBAR_ICON}px`,
  transition:  "width 200ms cubic-bezier(0.2,0,0,1)",
  background:  "#FFFFFF",
  border:      "1px solid #ECEDF1",
  borderRadius: 16,
  overflow:    "hidden",
  flexDirection: "column",
  boxShadow:   "0 1px 2px rgba(16,24,40,0.04)",
});

const navItemStyle = (isActive: boolean, open: boolean): React.CSSProperties => ({
  display:        "flex",
  alignItems:     "center",
  justifyContent: open ? "flex-start" : "center",
  gap:            open ? 12 : 0,
  padding:        open ? "0 8px" : 0,
  height:         44,
  borderRadius:   12,
  marginBottom:   2,
  background:     isActive ? "#EFF6FF" : "transparent",
  cursor:         "pointer",
  whiteSpace:     "nowrap",
  overflow:       "hidden",
  color:          isActive ? "#2563EB" : "#667085",
  textDecoration: "none",
  transition:     "background 120ms cubic-bezier(0.2,0,0,1), color 120ms cubic-bezier(0.2,0,0,1)",
});

export default function ManagerLayout() {
  const { user, logout } = useAuthStore();
  const navigate = useNavigate();
  const location = useLocation();

  const [sosCount,  setSosCount]  = useState(0);
  const [sosAgents, setSosAgents] = useState<Agent[]>([]);
  // 2026-09-21 — pending leave requests ride the same 30 s poll, so a request
  // filed from a phone reaches the bell on whatever page the manager is on.
  const [leavePending, setLeavePending] = useState<LeaveRequest[]>([]);

  const fetchSOS = useCallback(() => {
    api.get("/manager/dashboard")
      .then((r) => setSosCount(r.data.sos_active_count ?? 0))
      .catch(() => {});
    api.get("/manager/agents")
      .then((r) => setSosAgents((r.data as Agent[]).filter((a: Agent) => a.sos_active)))
      .catch(() => {});
    api.get("/manager/leave-requests", { params: { status: "REQUESTED" } })
      .then((r) => setLeavePending((r.data?.requests as LeaveRequest[]) ?? []))
      .catch(() => {});
  }, []);

  useEffect(() => {
    fetchSOS();
    const id = setInterval(fetchSOS, 30_000);
    return () => clearInterval(id);
  }, [fetchSOS]);

  // 2026-09-24 (P0-07) — live events. An SOS used to reach the bell on the
  // next 30 s poll; it now reaches it as soon as the agent's phone commits it.
  // Recorded work (visit, payment, PTP) marks every cached query stale, so
  // whichever manager page is open re-reads its figures now instead of on its
  // 60 s LIVE tick. Debounced: a burst of events is one refetch, not ten. The
  // polls above stay — they are what still works if the stream never connects.
  const queryClient = useQueryClient();
  const invalidateTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  useLiveEvents((e) => {
    if (e.type.startsWith("sos.")) fetchSOS();
    if (WORK_EVENTS.has(e.type)) {
      if (invalidateTimer.current) clearTimeout(invalidateTimer.current);
      invalidateTimer.current = setTimeout(() => {
        void queryClient.invalidateQueries();
      }, 1_500);
    }
  });
  useEffect(() => () => {
    if (invalidateTimer.current) clearTimeout(invalidateTimer.current);
  }, []);

  // Persist sidebar state across page loads.
  // Defaults to COLLAPSED. It used to default to expanded, which put the
  // full-screen backdrop over the page on every first load — and the backdrop
  // swallows the first click, because it only clears on mouseenter/click.
  // A hover-to-expand rail resting collapsed is also what every subsequent
  // load already showed, since the first hover writes "closed" and it sticks.
  const [open, setOpenState] = useState<boolean>(() => {
    try { return localStorage.getItem(SIDEBAR_KEY) === "open"; } catch { return false; }
  });

  const setOpen = useCallback((v: boolean) => {
    setOpenState(v);
    try {
      localStorage.setItem(SIDEBAR_KEY, v ? "open" : "closed");
    } catch {
      // localStorage unavailable (Safari private mode) — sidebar state just
      // won't persist across reloads, which is not worth failing over.
    }
  }, []);

  const handleLogout = useCallback(() => {
    api.post("/auth/logout").finally(() => {
      logout();
      navigate("/login", { replace: true });
    });
  }, [logout, navigate]);

  return (
    <div
      className="overflow-x-clip-safe"
      style={{ background: "hsl(var(--background))", display: "flex", minHeight: "100svh", width: "100%" }}
    >

      {/* ── Fixed sidebar — desktop only; hover to expand ── */}
      <aside
        className="hidden lg:flex"
        style={sidebarStyle(open)}
        onMouseEnter={() => setOpen(true)}
        onMouseLeave={() => setOpen(false)}
      >
        {/* Logo row */}
        <div style={{ height: 64, display: "flex", alignItems: "center", padding: "0 12px 0 18px", gap: 18, borderBottom: "1px solid #ECEDF1", flexShrink: 0 }}>
          <BrandLogo size={34} />
          <div style={{ whiteSpace: "nowrap", overflow: "hidden", opacity: open ? 1 : 0, transition: "opacity 150ms" }}>
            <p style={{ color: "#101828", fontWeight: 600, fontSize: 14, lineHeight: "1" }}>TIQCollect</p>
            <p style={{ color: "#667085", fontWeight: 400, fontSize: 11, marginTop: 4 }}>Manager Portal</p>
          </div>
        </div>

        {/* Nav */}
        <nav style={{ flex: 1, padding: "12px 8px", overflowY: "auto", overflowX: "hidden" }}>
          {NAV_ITEMS.map(({ to, icon: Icon, label }) => (
            <NavLink
              key={to}
              to={to}
              title={!open ? label : undefined}
            >
              {({ isActive }) => (
                <div style={navItemStyle(isActive, open)}>
                  <Icon size={17} style={{ flexShrink: 0, color: isActive ? "#2563EB" : "#98A2B3" }} />
                  <span style={{ fontSize: 11.5, fontWeight: isActive ? 600 : 500, opacity: open ? 1 : 0, maxWidth: open ? 160 : 0, overflow: "hidden", transition: "opacity 150ms, max-width 200ms" }}>{label}</span>
                </div>
              )}
            </NavLink>
          ))}
        </nav>

        {/* User identity only — Sign Out lives solely in the top-right account
            menu, matching the agent console, so there is one place to log out
            rather than two. */}
        <div style={{ borderTop: "1px solid #ECEDF1", padding: 8, flexShrink: 0 }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: open ? "flex-start" : "center", gap: open ? 10 : 0, padding: open ? "6px 8px" : "6px 0", borderRadius: 8, whiteSpace: "nowrap", overflow: "hidden" }}>
            <div style={{ width: 32, height: 32, borderRadius: "50%", background: "#EFF6FF", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 12, fontWeight: 600, color: "#2563EB", flexShrink: 0 }}>
              {user?.full_name.charAt(0)}
            </div>
            <div style={{ overflow: "hidden", minWidth: 0, maxWidth: open ? 160 : 0, opacity: open ? 1 : 0, transition: "opacity 150ms, max-width 200ms" }}>
              <p style={{ color: "#101828", fontSize: 12, fontWeight: 600, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", lineHeight: "1.3" }}>{user?.full_name}</p>
              <p style={{ color: "#667085", fontSize: 10.5, whiteSpace: "nowrap" }}>{user?.role.replace("_", " ")}</p>
            </div>
          </div>
        </div>
      </aside>

      {/* ── Backdrop — desktop only; it exists to catch the mouse leaving the
             expanded sidebar, so it has no purpose on a touch device. ── */}
      {open && (
        <div
          aria-hidden="true"
          className="hidden lg:block"
          style={{ position: "fixed", inset: 0, zIndex: 40, background: "rgba(16,24,40,0.04)" }}
          onMouseEnter={() => setOpen(false)}
          onClick={() => setOpen(false)}
        />
      )}

      {/* ── Main content — offset by the rail only where the rail exists ── */}
      <div
        className="min-w-0"
        style={{ flex: 1, paddingLeft: "var(--rail-w)", display: "flex", flexDirection: "column", minHeight: "100svh" }}
      >

        {/* Top bar.
            The header is a floating card, so it used to be the sticky element
            itself, with `top-4` and margins — and page content scrolled
            through the 16px gap above it and the gutters beside it, visibly
            (2026-09-17). The STICKY element is now this full-width wrapper,
            at top-0, painted in the page background: it owns the gap and the
            gutters, and the card sits inside it exactly where it was. Same
            look at rest; nothing shows above or beside the card while
            scrolling. `pb-1` keeps the card's 1px shadow inside the wrapper. */}
        <div className="sticky top-0 z-30 px-2 pt-2 pb-1 lg:px-4 lg:pt-4" style={{ background: "hsl(var(--background))" }}>
        <header
          className="flex items-center justify-between gap-3 rounded-card border px-4 py-3 lg:px-5"
          style={{
            background: "#FFFFFF",
            borderColor: "#ECEDF1",
            boxShadow: "0 1px 2px rgba(16,24,40,0.04)",
          }}
        >
          <div className="min-w-0">
            <h1 className="text-[15px] font-bold text-foreground truncate" style={{ letterSpacing: "-0.02em" }}>Agency Manager</h1>
            {/* Short date on phones, full date once there is room for it. */}
            <p className="text-[11px] mt-0.5 text-muted-foreground font-medium truncate">
              <span className="sm:hidden">
                {new Date().toLocaleDateString("en-IN", { weekday: "short", day: "numeric", month: "short" })}
              </span>
              <span className="hidden sm:inline">
                {new Date().toLocaleDateString("en-IN", { weekday: "long", day: "numeric", month: "long", year: "numeric" })}
              </span>
            </p>
          </div>
          <div className="flex items-center gap-2 sm:gap-2.5 flex-shrink-0">
            <SOSAlertBadge sosCount={sosCount} />
            <NotificationBell sosCount={sosCount} sosAgents={sosAgents} leavePending={leavePending} />
            <AccountMenu name={user?.full_name ?? ""} role={user?.role ?? ""} onLogout={handleLogout} />
          </div>
        </header>
        </div>

        {/* Page content — extra bottom padding on mobile so the last card
            clears the fixed tab bar. */}
        <main
          key={location.pathname}
          className="flex-1 p-3 sm:p-4 lg:p-6 pb-24 lg:pb-6 page-fade-in min-w-0"
        >
          <Outlet />
        </main>
      </div>

      {/* ── Bottom tab bar — below lg, where the hover sidebar cannot work.
             Same visual language as AgentLayout so the two flows match. ── */}
      <nav
        className="safe-bottom fixed bottom-2 left-2 right-2 z-40 rounded-card border lg:hidden"
        style={{ background: "#FFFFFF", boxShadow: "0 8px 24px rgba(16,24,40,0.10)", borderColor: "#ECEDF1" }}
      >
        <div className="flex items-stretch px-1 py-1.5">
          {NAV_ITEMS.map(({ to, icon: Icon, label }) => (
            <NavLink key={to} to={to} style={{ flex: 1, textDecoration: "none", minWidth: 0 }}>
              {({ isActive }) => (
                <div
                  className="tap-target"
                  style={{
                    display:        "flex",
                    flexDirection:  "column",
                    alignItems:     "center",
                    justifyContent: "center",
                    gap:            3,
                    padding:        "6px 2px",
                    borderRadius:   10,
                    background:     isActive ? "#EFF6FF" : "transparent",
                    transition:     "background 120ms cubic-bezier(0.2,0,0,1)",
                  }}
                >
                  <Icon
                    size={19}
                    style={{ color: isActive ? "#2563EB" : "#98A2B3", strokeWidth: isActive ? 2.2 : 1.8, flexShrink: 0 }}
                  />
                  <span
                    className="w-full truncate text-center"
                    style={{ fontSize: 10, fontWeight: isActive ? 700 : 500, color: isActive ? "#2563EB" : "#667085", lineHeight: 1 }}
                  >
                    {label}
                  </span>
                </div>
              )}
            </NavLink>
          ))}
        </div>
      </nav>
    </div>
  );
}

function NotificationBell({ sosCount, sosAgents, leavePending }: { sosCount: number; sosAgents: Agent[]; leavePending: LeaveRequest[] }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const navigate = useNavigate();
  const total = sosCount + leavePending.length;
  const fmt = (d: string) => new Date(`${d}T00:00:00`).toLocaleDateString("en-IN", { day: "numeric", month: "short" });

  useEffect(() => {
    if (!open) return;
    function handleClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [open]);

  return (
    <div ref={ref} style={{ position: "relative" }}>
      <button
        onClick={() => setOpen((v) => !v)}
        aria-label={total > 0 ? `Notifications, ${total} active` : "Notifications"}
        aria-expanded={open}
        className="tap-target relative flex flex-shrink-0 items-center justify-center transition-colors duration-150 hover:bg-muted"
        style={{ width: 40, height: 40, borderRadius: 10, background: "#fff", border: "1px solid #E1E3E9", color: "#667085" }}
      >
        <Bell className="w-4 h-4" />
        {total > 0 && (
          <span className="absolute" style={{ top: 7, right: 7, width: 6, height: 6, borderRadius: "50%", background: sosCount > 0 ? "#DC2626" : "#D97706", border: "1.5px solid #fff" }} />
        )}
      </button>

      {open && (
        <div
          className="absolute right-0 mt-2 overflow-hidden rounded-card shadow-premium"
          style={{ width: "min(280px, calc(100vw - 32px))", zIndex: 100, background: "#fff", border: "1px solid #ECEDF1", top: "100%" }}
        >
          <div className="px-4 py-3 border-b" style={{ borderColor: "hsl(var(--border) / 0.5)" }}>
            <p className="text-sm font-bold" style={{ color: "#1C1C1F" }}>Notifications</p>
          </div>
          {sosAgents.length === 0 && leavePending.length === 0 ? (
            <div className="px-4 py-6 text-center">
              <Bell className="w-6 h-6 mx-auto mb-2 opacity-25" />
              <p className="text-xs" style={{ color: "#6B6D76" }}>No active alerts</p>
            </div>
          ) : (
            <div className="py-1 max-h-[60svh] overflow-y-auto">
              {sosAgents.map((a) => (
                <div key={a.id} className="flex items-start gap-3 px-4 py-3 hover:bg-red-50 transition-colors">
                  <AlertTriangle className="w-4 h-4 flex-shrink-0 mt-0.5 animate-pulse" style={{ color: "#DC2626" }} />
                  <div className="min-w-0">
                    <p className="text-xs font-semibold truncate" style={{ color: "#991B1B" }}>SOS — {a.full_name}</p>
                    <p className="text-xs mt-0.5" style={{ color: "#6B6D76" }}>{a.employee_code} · Requires immediate attention</p>
                  </div>
                </div>
              ))}
              {leavePending.map((r) => (
                <button
                  key={r.id}
                  type="button"
                  onClick={() => { setOpen(false); navigate("/manager/agents?leave=1"); }}
                  className="w-full text-left flex items-start gap-3 px-4 py-3 hover:bg-amber-50 transition-colors"
                >
                  <CalendarOff className="w-4 h-4 flex-shrink-0 mt-0.5" style={{ color: "#D97706" }} />
                  <div className="min-w-0">
                    <p className="text-xs font-semibold truncate" style={{ color: "#92400E" }}>Leave request — {r.agent_name}</p>
                    <p className="text-xs mt-0.5" style={{ color: "#6B6D76" }}>
                      {r.leave_type.replace("_", " ").toLowerCase()} · {fmt(r.from_date)}{r.to_date !== r.from_date ? ` – ${fmt(r.to_date)}` : ""} · tap to review
                    </p>
                  </div>
                </button>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function SOSAlertBadge({ sosCount }: { sosCount: number }) {
  if (sosCount === 0) return null;
  return (
    <div
      className="flex items-center gap-1.5 px-2 sm:px-3 py-1.5 rounded-xl animate-pulse flex-shrink-0"
      style={{ background: "rgba(220,38,38,0.10)", border: "1px solid rgba(220,38,38,0.25)" }}
    >
      <AlertTriangle className="w-4 h-4 flex-shrink-0" style={{ color: "#DC2626" }} />
      <span className="text-xs font-bold whitespace-nowrap" style={{ color: "#991B1B" }}>
        {sosCount}<span className="hidden xs:inline"> SOS</span><span className="hidden sm:inline"> ACTIVE</span>
      </span>
    </div>
  );
}
