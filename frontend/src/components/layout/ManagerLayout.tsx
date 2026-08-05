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
import { LayoutDashboard, Users, Briefcase, BarChart2, Shield, ShieldCheck, Bell, AlertTriangle } from "lucide-react";
import { useState, useEffect, useCallback, useRef } from "react";
import api from "@/api/axios";
import { useAuthStore } from "@/store/authStore";
import { AccountMenu } from "@/components/layout/AccountMenu";
import type { Agent } from "@/types";

const SIDEBAR_KEY   = "tiq:sidebar";
const SIDEBAR_W     = 240;   // expanded width  (px)
const SIDEBAR_ICON  = 64;    // collapsed width (px)
const BRAND         = "#0C66E4";

const NAV_ITEMS = [
  { to: "/manager/overview",   icon: LayoutDashboard, label: "Overview" },
  { to: "/manager/agents",     icon: Users,           label: "Agents" },
  { to: "/manager/cases",      icon: Briefcase,       label: "Cases" },
  { to: "/manager/analytics",  icon: BarChart2,       label: "Analytics" },
  { to: "/manager/compliance", icon: Shield,          label: "Compliance" },
];

// ── Inline style helpers ──────────────────────────────────────────────────────
// `display` is intentionally NOT set here — it comes from the `hidden lg:flex`
// classes, so an inline value cannot win over the breakpoint.
const sidebarStyle = (open: boolean): React.CSSProperties => ({
  position:   "fixed",
  top:         0,
  left:        0,
  height:      "100svh",
  zIndex:      50,
  width:       open ? `${SIDEBAR_W}px` : `${SIDEBAR_ICON}px`,
  transition:  "width 300ms ease-in-out",
  background:  BRAND,
  borderRight: "1px solid rgba(255,255,255,0.12)",
  overflow:    "hidden",
  flexDirection: "column",
  boxShadow:   "2px 0 24px rgba(0,0,0,0.18)",
});

const navItemStyle = (isActive: boolean): React.CSSProperties => ({
  display:        "flex",
  alignItems:     "center",
  gap:            12,
  padding:        "0 8px",
  height:         36,
  borderRadius:   8,
  marginBottom:   2,
  background:     isActive ? "rgba(255,255,255,0.20)" : "transparent",
  cursor:         "pointer",
  whiteSpace:     "nowrap",
  overflow:       "hidden",
  color:          isActive ? "#fff" : "rgba(255,255,255,0.78)",
  textDecoration: "none",
  transition:     "background 150ms ease",
});

export default function ManagerLayout() {
  const { user, logout } = useAuthStore();
  const navigate = useNavigate();
  const location = useLocation();

  const [sosCount,  setSosCount]  = useState(0);
  const [sosAgents, setSosAgents] = useState<Agent[]>([]);

  useEffect(() => {
    function fetchSOS() {
      api.get("/manager/dashboard")
        .then((r) => setSosCount(r.data.sos_active_count ?? 0))
        .catch(() => {});
      api.get("/manager/agents")
        .then((r) => setSosAgents((r.data as Agent[]).filter((a: Agent) => a.sos_active)))
        .catch(() => {});
    }
    fetchSOS();
    const id = setInterval(fetchSOS, 30_000);
    return () => clearInterval(id);
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

      {/* Ambient background blobs */}
      <div aria-hidden="true" style={{ position: "fixed", inset: 0, zIndex: -1, pointerEvents: "none", overflow: "hidden" }}>
        <div style={{ position: "absolute", top: "-10%", right: "-5%", width: "55%", height: "65%", background: "hsl(213 100% 54% / 0.13)", filter: "blur(80px)", borderRadius: "9999px" }} />
        <div style={{ position: "absolute", bottom: "-10%", left: "-5%", width: "45%", height: "55%", background: "hsl(258 90% 66% / 0.10)", filter: "blur(80px)", borderRadius: "9999px" }} />
      </div>

      {/* ── Fixed sidebar — desktop only; hover to expand ── */}
      <aside
        className="hidden lg:flex"
        style={sidebarStyle(open)}
        onMouseEnter={() => setOpen(true)}
        onMouseLeave={() => setOpen(false)}
      >
        {/* Logo row */}
        <div style={{ height: 64, display: "flex", alignItems: "center", padding: "0 12px 0 16px", gap: 16, borderBottom: "1px solid rgba(255,255,255,0.10)", flexShrink: 0 }}>
          <div style={{ width: 32, height: 32, borderRadius: 10, background: "rgba(255,255,255,0.20)", display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0 }}>
            <ShieldCheck size={16} color="white" />
          </div>
          <div style={{ whiteSpace: "nowrap", overflow: "hidden", opacity: open ? 1 : 0, transition: "opacity 150ms" }}>
            <p style={{ color: "white", fontWeight: 700, fontSize: 13, lineHeight: "1" }}>TIQCollect</p>
            <p style={{ color: "rgba(255,255,255,0.65)", fontWeight: 500, fontSize: 10, marginTop: 3 }}>Manager Portal</p>
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
                <div style={navItemStyle(isActive)}>
                  <Icon size={16} style={{ flexShrink: 0, color: isActive ? "#fff" : "rgba(255,255,255,0.70)" }} />
                  <span style={{ fontSize: 11.5, fontWeight: isActive ? 600 : 500, opacity: open ? 1 : 0, transition: "opacity 150ms" }}>{label}</span>
                  {isActive && (
                    <span style={{ marginLeft: "auto", width: 6, height: 6, borderRadius: "50%", background: "white", flexShrink: 0, opacity: open ? 1 : 0 }} />
                  )}
                </div>
              )}
            </NavLink>
          ))}
        </nav>

        {/* User identity only — Sign Out lives solely in the top-right account
            menu, matching the agent console, so there is one place to log out
            rather than two. */}
        <div style={{ borderTop: "1px solid rgba(255,255,255,0.10)", padding: 8, flexShrink: 0 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "6px 8px", borderRadius: 8, whiteSpace: "nowrap", overflow: "hidden" }}>
            <div style={{ width: 28, height: 28, borderRadius: "50%", background: "rgba(255,255,255,0.22)", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 12, fontWeight: 700, color: "white", flexShrink: 0 }}>
              {user?.full_name.charAt(0)}
            </div>
            <div style={{ overflow: "hidden", minWidth: 0, opacity: open ? 1 : 0, transition: "opacity 150ms" }}>
              <p style={{ color: "white", fontSize: 11.5, fontWeight: 600, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", lineHeight: "1.3" }}>{user?.full_name}</p>
              <p style={{ color: "rgba(255,255,255,0.55)", fontSize: 10, whiteSpace: "nowrap" }}>{user?.role.replace("_", " ")}</p>
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
          style={{ position: "fixed", inset: 0, zIndex: 40, background: "rgba(0,0,0,0.08)", backdropFilter: "blur(2px)", WebkitBackdropFilter: "blur(2px)" }}
          onMouseEnter={() => setOpen(false)}
          onClick={() => setOpen(false)}
        />
      )}

      {/* ── Main content — offset by the rail only where the rail exists ── */}
      <div
        className="min-w-0"
        style={{ flex: 1, paddingLeft: "var(--rail-w)", display: "flex", flexDirection: "column", minHeight: "100svh" }}
      >

        {/* Top bar */}
        <header
          className="flex items-center justify-between gap-3 px-4 lg:px-6 py-3 lg:py-3.5 sticky top-0 z-30"
          style={{
            background:           "rgba(255,255,255,0.82)",
            backdropFilter:       "blur(20px)",
            WebkitBackdropFilter: "blur(20px)",
            borderBottom:         "1px solid hsl(var(--border) / 0.5)",
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
            <NotificationBell sosCount={sosCount} sosAgents={sosAgents} />
            <AccountMenu name={user?.full_name ?? ""} role={user?.role ?? ""} onLogout={handleLogout} />
          </div>
        </header>

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
        className="lg:hidden fixed bottom-0 left-0 right-0 z-40 safe-bottom"
        style={{ background: BRAND, boxShadow: "0 -2px 24px rgba(12,102,228,0.28)", borderTop: "1px solid rgba(255,255,255,0.12)" }}
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
                    background:     isActive ? "rgba(255,255,255,0.20)" : "transparent",
                    transition:     "background 150ms ease",
                  }}
                >
                  <Icon
                    size={19}
                    style={{ color: "white", opacity: isActive ? 1 : 0.65, strokeWidth: isActive ? 2.2 : 1.8, flexShrink: 0 }}
                  />
                  <span
                    className="w-full truncate text-center"
                    style={{ fontSize: 10, fontWeight: isActive ? 700 : 500, color: "white", opacity: isActive ? 1 : 0.65, lineHeight: 1 }}
                  >
                    {label}
                  </span>
                  {isActive && (
                    <span style={{ width: 4, height: 4, borderRadius: "50%", background: "white", marginTop: 1, flexShrink: 0 }} />
                  )}
                </div>
              )}
            </NavLink>
          ))}
        </div>
      </nav>
    </div>
  );
}

function NotificationBell({ sosCount, sosAgents }: { sosCount: number; sosAgents: Agent[] }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

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
        aria-label={sosCount > 0 ? `Notifications, ${sosCount} active` : "Notifications"}
        aria-expanded={open}
        className="tap-target relative flex items-center justify-center hover:-translate-y-0.5 transition-transform duration-150 flex-shrink-0"
        style={{ width: 36, height: 36, borderRadius: 12, background: "#fff", border: "1px solid hsl(var(--border) / 0.7)", color: "hsl(var(--foreground))" }}
      >
        <Bell className="w-4 h-4" />
        {sosCount > 0 && (
          <span className="absolute" style={{ top: 7, right: 7, width: 6, height: 6, borderRadius: "50%", background: "#DC2626", border: "1.5px solid #fff" }} />
        )}
      </button>

      {open && (
        <div
          className="absolute right-0 mt-2 rounded-2xl shadow-xl overflow-hidden"
          style={{ width: "min(280px, calc(100vw - 32px))", zIndex: 100, background: "#fff", border: "1px solid hsl(var(--border) / 0.6)", top: "100%" }}
        >
          <div className="px-4 py-3 border-b" style={{ borderColor: "hsl(var(--border) / 0.5)" }}>
            <p className="text-sm font-bold" style={{ color: "#1C1C1F" }}>Notifications</p>
          </div>
          {sosAgents.length === 0 ? (
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
