import { NavLink, Outlet, useNavigate, useLocation } from "react-router-dom";
import { LayoutDashboard, Users, Briefcase, BarChart2, Shield, LogOut, ShieldCheck, Bell, AlertTriangle } from "lucide-react";
import { useState, useEffect, useCallback, useRef } from "react";
import api, { IS_OFFLINE_BUILD } from "@/api/axios";
import { useAuthStore } from "@/store/authStore";
import type { Agent } from "@/types";

const SIDEBAR_KEY   = "tiq:sidebar";
const SIDEBAR_W     = 240;   // expanded width  (px)
const SIDEBAR_ICON  = 64;    // collapsed width (px)

const NAV_ITEMS = [
  { to: "/manager/overview",   icon: LayoutDashboard, label: "Overview" },
  { to: "/manager/agents",     icon: Users,           label: "Agents" },
  { to: "/manager/cases",      icon: Briefcase,       label: "Cases" },
  { to: "/manager/analytics",  icon: BarChart2,       label: "Analytics" },
  { to: "/manager/compliance", icon: Shield,          label: "Compliance" },
];

// ── Inline style helpers ──────────────────────────────────────────────────────
const sidebarStyle = (open: boolean): React.CSSProperties => ({
  position:   "fixed",
  top:         0,
  left:        0,
  height:      "100vh",
  zIndex:      50,
  width:       open ? `${SIDEBAR_W}px` : `${SIDEBAR_ICON}px`,
  transition:  "width 300ms ease-in-out",
  background:  "#0C66E4",
  borderRight: "1px solid rgba(255,255,255,0.12)",
  overflow:    "hidden",
  display:     "flex",
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

// The offline build is framed inside the Collections Command Center, which
// already provides the surrounding chrome. So there it drops this layout's
// sidebar, top bar, notifications and account/sign-out entirely, and offers just
// the two destinations that build is for — as buttons across the top.
// Running normally, FullManagerLayout below is used, unchanged.
export default function ManagerLayout() {
  return IS_OFFLINE_BUILD ? <EmbeddedManagerLayout /> : <FullManagerLayout />;
}

// Analytics first — it is where the agency click lands — then Cases beside it.
const EMBED_NAV_ITEMS = ["/manager/analytics", "/manager/cases"].flatMap(
  (path) => NAV_ITEMS.filter(({ to }) => to === path)
);

function EmbeddedManagerLayout() {
  const location = useLocation();

  return (
    <div style={{ background: "hsl(var(--background))", minHeight: "100vh", display: "flex", flexDirection: "column", width: "100%" }}>
      {/* Ambient background blobs — kept so the framed page still reads as this app */}
      <div aria-hidden="true" style={{ position: "fixed", inset: 0, zIndex: -1, pointerEvents: "none", overflow: "hidden" }}>
        <div style={{ position: "absolute", top: "-10%", right: "-5%", width: "55%", height: "65%", background: "hsl(213 100% 54% / 0.13)", filter: "blur(80px)", borderRadius: "9999px" }} />
        <div style={{ position: "absolute", bottom: "-10%", left: "-5%", width: "45%", height: "55%", background: "hsl(258 90% 66% / 0.10)", filter: "blur(80px)", borderRadius: "9999px" }} />
      </div>

      <nav
        className="flex items-center gap-2 px-6 py-3 sticky top-0 z-30"
        style={{
          background: "rgba(255,255,255,0.82)",
          backdropFilter: "blur(20px)",
          WebkitBackdropFilter: "blur(20px)",
          borderBottom: "1px solid hsl(var(--border) / 0.5)",
        }}
      >
        {EMBED_NAV_ITEMS.map(({ to, icon: Icon, label }) => (
          <NavLink key={to} to={to} style={{ textDecoration: "none" }}>
            {({ isActive }) => (
              <span
                className="flex items-center gap-2"
                style={{
                  padding: "7px 14px",
                  borderRadius: 10,
                  fontSize: 12,
                  fontWeight: 600,
                  background: isActive ? "#1677FF" : "#fff",
                  color: isActive ? "#fff" : "#6B6D76",
                  border: `1px solid ${isActive ? "#1677FF" : "hsl(var(--border) / 0.7)"}`,
                  transition: "background 150ms ease, color 150ms ease",
                }}
              >
                <Icon size={14} style={{ flexShrink: 0 }} />
                {label}
              </span>
            )}
          </NavLink>
        ))}
      </nav>

      <main key={location.pathname} className="flex-1 overflow-y-auto p-6 page-fade-in">
        <Outlet />
      </main>
    </div>
  );
}

function FullManagerLayout() {
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

  // Persist sidebar state across page loads (like the team does)
  const [open, setOpenState] = useState<boolean>(() => {
    try { return localStorage.getItem(SIDEBAR_KEY) !== "closed"; } catch { return true; }
  });

  const setOpen = useCallback((v: boolean) => {
    setOpenState(v);
    try { localStorage.setItem(SIDEBAR_KEY, v ? "open" : "closed"); } catch {}
  }, []);

  function handleLogout() {
    api.post("/auth/logout").finally(() => {
      logout();
      navigate("/login", { replace: true });
    });
  }

  return (
    <div style={{ background: "hsl(var(--background))", display: "flex", minHeight: "100vh", width: "100%" }}>

      {/* Ambient background blobs */}
      <div aria-hidden="true" style={{ position: "fixed", inset: 0, zIndex: -1, pointerEvents: "none", overflow: "hidden" }}>
        <div style={{ position: "absolute", top: "-10%", right: "-5%", width: "55%", height: "65%", background: "hsl(213 100% 54% / 0.13)", filter: "blur(80px)", borderRadius: "9999px" }} />
        <div style={{ position: "absolute", bottom: "-10%", left: "-5%", width: "45%", height: "55%", background: "hsl(258 90% 66% / 0.10)", filter: "blur(80px)", borderRadius: "9999px" }} />
      </div>

      {/* ── Fixed sidebar — hover to expand (matches team) ── */}
      <aside
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

        {/* User + logout */}
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
          <button
            onClick={handleLogout}
            style={{ display: "flex", alignItems: "center", gap: 10, padding: "6px 8px", borderRadius: 8, width: "100%", background: "none", border: "none", color: "rgba(255,255,255,0.55)", whiteSpace: "nowrap", cursor: "pointer", transition: "color 150ms", fontSize: 11.5, fontWeight: 500 }}
            onMouseEnter={e => (e.currentTarget.style.color = "rgba(255,255,255,0.90)")}
            onMouseLeave={e => (e.currentTarget.style.color = "rgba(255,255,255,0.55)")}
          >
            <LogOut size={14} style={{ flexShrink: 0 }} />
            <span style={{ opacity: open ? 1 : 0, transition: "opacity 150ms" }}>Sign Out</span>
          </button>
        </div>
      </aside>

      {/* ── Backdrop — when expanded, covers main content and closes sidebar on hover ── */}
      {open && (
        <div
          aria-hidden="true"
          style={{ position: "fixed", inset: 0, zIndex: 40, background: "rgba(0,0,0,0.08)", backdropFilter: "blur(2px)", WebkitBackdropFilter: "blur(2px)" }}
          onMouseEnter={() => setOpen(false)}
          onClick={() => setOpen(false)}
        />
      )}

      {/* ── Main content — always offset by icon width (64 px), never pushes ── */}
      <div style={{ flex: 1, paddingLeft: `${SIDEBAR_ICON}px`, display: "flex", flexDirection: "column", minHeight: "100vh", minWidth: 0 }}>

        {/* Top bar */}
        <header
          className="flex items-center justify-between px-6 py-3.5 sticky top-0 z-30"
          style={{
            background:           "rgba(255,255,255,0.82)",
            backdropFilter:       "blur(20px)",
            WebkitBackdropFilter: "blur(20px)",
            borderBottom:         "1px solid hsl(var(--border) / 0.5)",
          }}
        >
          <div>
            <h1 className="text-[15px] font-bold text-foreground" style={{ letterSpacing: "-0.02em" }}>Agency Manager</h1>
            <p className="text-[11px] mt-0.5 text-muted-foreground font-medium">
              {new Date().toLocaleDateString("en-IN", { weekday: "long", day: "numeric", month: "long", year: "numeric" })}
            </p>
          </div>
          <div className="flex items-center gap-2.5">
            <SOSAlertBadge sosCount={sosCount} />
            <NotificationBell sosCount={sosCount} sosAgents={sosAgents} />
            <div className="w-9 h-9 rounded-full bg-primary flex items-center justify-center text-white text-sm font-bold">
              {user?.full_name.charAt(0)}
            </div>
          </div>
        </header>

        {/* Page content */}
        <main key={location.pathname} className="flex-1 overflow-y-auto p-6 page-fade-in">
          <Outlet />
        </main>
      </div>
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
        className="relative flex items-center justify-center hover:-translate-y-0.5 transition-transform duration-150"
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
          style={{ width: 280, zIndex: 100, background: "#fff", border: "1px solid hsl(var(--border) / 0.6)", top: "100%" }}
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
            <div className="py-1">
              {sosAgents.map((a) => (
                <div key={a.id} className="flex items-start gap-3 px-4 py-3 hover:bg-red-50 transition-colors">
                  <AlertTriangle className="w-4 h-4 flex-shrink-0 mt-0.5 animate-pulse" style={{ color: "#DC2626" }} />
                  <div>
                    <p className="text-xs font-semibold" style={{ color: "#991B1B" }}>SOS — {a.full_name}</p>
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
      className="flex items-center gap-1.5 px-3 py-1.5 rounded-xl animate-pulse"
      style={{ background: "rgba(220,38,38,0.10)", border: "1px solid rgba(220,38,38,0.25)" }}
    >
      <AlertTriangle className="w-4 h-4 flex-shrink-0" style={{ color: "#DC2626" }} />
      <span className="text-xs font-bold" style={{ color: "#991B1B" }}>{sosCount} SOS ACTIVE</span>
    </div>
  );
}
