// ─── CHANGELOG (prototype → product) ───────────────────────────────────────
// 2026-07-31 — Responsive pass (agent flow). The shell was hardcoded to
//   max-w-md (448px), so on a laptop it rendered as a phone-width column in
//   empty grey — 35% of a 1280px screen. Phone view below lg is unchanged.
// 2026-07-31 (later) — Desktop shell rebuilt to match ManagerLayout exactly:
//   the same 64px hover-to-expand rail, the same white glass top bar, the same
//   shared AccountMenu carrying Sign Out, full-width content via --rail-w.
// 2026-08-03 — Merged origin/main's live-GPS header line. LiveLocationLine
//   gained a `tone` prop: upstream hardcoded it white for the blue phone
//   header, which is invisible on the desktop glass bar. Both shells now show
//   the agent's live address.
// ─────────────────────────────────────────────────────────────────────────
import { NavLink, Outlet, useNavigate, useLocation } from "react-router";
import { Home, Briefcase, User, Map, LogOut, WifiOff, ShieldCheck, MapPin, LocateFixed } from "lucide-react";
import { useEffect, useState, useCallback } from "react";
import { toast } from "react-hot-toast";
import { useAuthStore } from "@/store/authStore";
import { useSOSStore } from "@/store/sosStore";
import { SOSButton } from "@/components/ui/SOSButton";
import { AccountMenu } from "@/components/layout/AccountMenu";
import { BeatProvider, useBeat } from "@/contexts/BeatContext";
import { useLiveLocation } from "@/hooks/useLiveLocation";
import api from "@/api/axios";

const SIDEBAR_KEY  = "tiq:agent-sidebar";
const SIDEBAR_W    = 240;  // expanded width  (px)
const SIDEBAR_ICON = 64;   // collapsed width (px)
const BRAND        = "#0C66E4";

const NAV_ITEMS = [
  { to: "/agent/home",    icon: Home,      label: "Home" },
  { to: "/agent/cases",   icon: Briefcase, label: "Cases" },
  { to: "/agent/beat",    icon: Map,       label: "Beat" },
  { to: "/agent/profile", icon: User,      label: "Profile" },
];

// ── Inline style helpers — mirrored from ManagerLayout ───────────────────────
// `display` is intentionally NOT set here — it comes from the `hidden lg:flex`
// classes, so an inline value cannot win over the breakpoint.
const sidebarStyle = (open: boolean): React.CSSProperties => ({
  position:      "fixed",
  top:            0,
  left:           0,
  height:        "100svh",
  zIndex:         50,
  width:          open ? `${SIDEBAR_W}px` : `${SIDEBAR_ICON}px`,
  transition:    "width 300ms ease-in-out",
  background:     BRAND,
  borderRight:   "1px solid rgba(255,255,255,0.12)",
  overflow:      "hidden",
  flexDirection: "column",
  boxShadow:     "2px 0 24px rgba(0,0,0,0.18)",
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

/** Live GPS address in the header — shows the agent's real location anywhere on
 *  earth, so the demo reads as live rather than pinned to a fixed city. */
function LiveLocationLine({ tone = "light" }: { tone?: "light" | "dark" }) {
  const loc = useLiveLocation();
  const text =
    loc.status === "ready"   ? loc.address :
    loc.status === "locating" ? "Locating…" :
    loc.status === "denied"  ? "Location off — enable GPS" :
                               "Location unavailable";
  const Icon = loc.status === "ready" ? MapPin : LocateFixed;
  const color = tone === "light" ? "rgba(255,255,255,0.85)" : "hsl(var(--muted-foreground))";
  return (
    <div
      title={loc.coords ? `${loc.coords.lat.toFixed(5)}, ${loc.coords.lon.toFixed(5)}` : undefined}
      style={{ display: "flex", alignItems: "flex-start", gap: 4, marginTop: 2, maxWidth: 280 }}
    >
      <Icon size={11} color={color} style={{ marginTop: 2, flexShrink: 0 }}
            className={loc.status === "locating" ? "animate-pulse" : undefined} />
      <span style={{ color, fontSize: 10.5, fontWeight: 500, lineHeight: 1.25,
                     display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical", overflow: "hidden" }}>
        {text}
      </span>
    </div>
  );
}

export default function AgentLayout() {
  return (
    <BeatProvider>
      <AgentLayoutInner />
    </BeatProvider>
  );
}

function AgentLayoutInner() {
  const { user, logout } = useAuthStore();
  const { setSosActive } = useSOSStore();
  const { beat } = useBeat();
  const navigate = useNavigate();
  const location = useLocation();
  const [isOnline, setIsOnline] = useState(navigator.onLine);

  useEffect(() => {
    if (beat != null) setSosActive(beat.sos_active);
  }, [beat?.sos_active, setSosActive]);

  useEffect(() => {
    const onOnline  = () => { setIsOnline(true);  toast.success("Back online — syncing data"); };
    const onOffline = () => { setIsOnline(false); toast.error("You're offline — actions will queue"); };
    window.addEventListener("online",  onOnline);
    window.addEventListener("offline", onOffline);
    return () => {
      window.removeEventListener("online",  onOnline);
      window.removeEventListener("offline", onOffline);
    };
  }, []);

  // Defaults to COLLAPSED, same as the manager rail: an expanded rail puts the
  // backdrop over the page on load, and that backdrop swallows the first click.
  const [open, setOpenState] = useState<boolean>(() => {
    try { return localStorage.getItem(SIDEBAR_KEY) === "open"; } catch { return false; }
  });

  const setOpen = useCallback((v: boolean) => {
    setOpenState(v);
    try {
      localStorage.setItem(SIDEBAR_KEY, v ? "open" : "closed");
    } catch {
      // localStorage unavailable (Safari private mode) — rail state just won't
      // persist across reloads, which is not worth failing over.
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

      {/* ── Fixed rail — desktop only; hover to expand ── */}
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
            <p style={{ color: "rgba(255,255,255,0.65)", fontWeight: 500, fontSize: 10, marginTop: 3 }}>Field Agent</p>
          </div>
        </div>

        {/* Nav */}
        <nav style={{ flex: 1, padding: "12px 8px", overflowY: "auto", overflowX: "hidden" }}>
          {NAV_ITEMS.map(({ to, icon: Icon, label }) => (
            <NavLink key={to} to={to} title={!open ? label : undefined}>
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
            menu, so there is one place to log out rather than two. */}
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

      {/* ── Backdrop — desktop only; catches the mouse leaving the expanded rail ── */}
      {open && (
        <div
          aria-hidden="true"
          className="hidden lg:block"
          style={{ position: "fixed", inset: 0, zIndex: 40, background: "rgba(0,0,0,0.08)", backdropFilter: "blur(2px)", WebkitBackdropFilter: "blur(2px)" }}
          onMouseEnter={() => setOpen(false)}
          onClick={() => setOpen(false)}
        />
      )}

      {/* ── Main column — offset by the rail only where the rail exists.
             Below lg this is the untouched 448px phone shell. ── */}
      <div
        className="min-w-0 w-full max-w-md md:max-w-none mx-auto md:mx-0 flex flex-col relative"
        style={{ flex: 1, paddingLeft: "var(--rail-w)", minHeight: "100svh" }}
      >

        {/* ── Top bar ──
            Below lg: the brand-blue agent header it ships with today.
            At lg: the same white glass bar as the manager console. */}
        <header
          className="sticky top-0 z-30 safe-top"
        >
          {/* Phone header — the brand blue lives here, not on <header>, so it
              cannot bleed through behind the desktop glass bar. */}
          <div
            className="lg:hidden flex items-center justify-between px-4 py-3 text-white"
            style={{ background: BRAND, boxShadow: "0 2px 16px rgba(12,102,228,0.22)" }}
          >
            <div className="flex items-center gap-2.5 min-w-0">
              <div
                className="flex items-center justify-center flex-shrink-0"
                style={{ width: 30, height: 30, borderRadius: 9, background: "rgba(255,255,255,0.18)" }}
              >
                <ShieldCheck size={15} color="white" />
              </div>
              <div className="min-w-0">
                {/* Upstream swapped the static "TIQCollect · Field Agent" label
                    for the agent's live GPS address — kept. */}
                <p className="truncate" style={{ color: "white", fontSize: 13, fontWeight: 700, lineHeight: 1.3 }}>{user?.full_name}</p>
                <LiveLocationLine tone="light" />
              </div>
            </div>
            <SOSButton />
          </div>

          {/* Desktop header — same glass bar as the manager console */}
          <div
            className="hidden lg:flex items-center justify-between gap-3 px-4 lg:px-6 py-3 lg:py-3.5"
            style={{
              background:           "rgba(255,255,255,0.82)",
              backdropFilter:       "blur(20px)",
              WebkitBackdropFilter: "blur(20px)",
              borderBottom:         "1px solid hsl(var(--border) / 0.5)",
            }}
          >
            <div className="min-w-0">
              <h1 className="text-[15px] font-bold text-foreground truncate" style={{ letterSpacing: "-0.02em" }}>{user?.full_name ?? "Field Agent"}</h1>
              {/* Live address rather than the date: on a field app the agent's
                  current location is the more useful context, and it is what
                  upstream put in the phone header. */}
              <LiveLocationLine tone="dark" />
            </div>
            <div className="flex items-center gap-2.5 flex-shrink-0">
              <SOSButton />
              <AccountMenu name={user?.full_name ?? ""} role={user?.role ?? ""} onLogout={handleLogout} />
            </div>
          </div>

          <ContactHourBanner />
          {!isOnline && (
            <div className="bg-slate-800 text-white text-xs text-center py-1.5 px-4 font-medium flex items-center justify-center gap-1.5">
              <WifiOff className="w-3 h-3" />
              Offline — visits will sync when reconnected
            </div>
          )}
        </header>

        {/* ── Page content — full width at lg, exactly like the manager console ── */}
        <main key={location.pathname} className="flex-1 pb-20 lg:pb-6 min-w-0 page-fade-in">
          <Outlet />
        </main>
      </div>

      {/* ── Bottom navigation — phone and tablet only, unchanged ── */}
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
                    padding:        "6px 4px",
                    borderRadius:   10,
                    background:     isActive ? "rgba(255,255,255,0.20)" : "transparent",
                    transition:     "background 150ms ease",
                  }}
                >
                  <Icon size={19} style={{ color: "white", opacity: isActive ? 1 : 0.65, strokeWidth: isActive ? 2.2 : 1.8, flexShrink: 0 }} />
                  <span
                    className="w-full truncate text-center"
                    style={{ fontSize: 10, fontWeight: isActive ? 700 : 500, color: "white", opacity: isActive ? 1 : 0.65, lineHeight: 1 }}
                  >
                    {label}
                  </span>
                  {isActive && <span style={{ width: 4, height: 4, borderRadius: "50%", background: "white", marginTop: 1 }} />}
                </div>
              )}
            </NavLink>
          ))}

          {/* Logout */}
          <button
            onClick={handleLogout}
            className="tap-target"
            style={{
              flex: 1, display: "flex", flexDirection: "column", alignItems: "center",
              justifyContent: "center", gap: 3, padding: "6px 4px", borderRadius: 10,
              background: "transparent", border: "none", cursor: "pointer", minWidth: 0,
            }}
          >
            <LogOut size={19} style={{ color: "white", opacity: 0.65, strokeWidth: 1.8, flexShrink: 0 }} />
            <span className="w-full truncate text-center" style={{ fontSize: 10, fontWeight: 500, color: "white", opacity: 0.65, lineHeight: 1 }}>Logout</span>
          </button>
        </div>
      </nav>
    </div>
  );
}

function ContactHourBanner() {
  const hour = new Date().getHours();
  if (hour >= 8 && hour < 19) return null;
  return (
    <div className="bg-danger-600 text-white text-xs text-center py-1.5 px-4 font-medium">
      Outside RBI contact hours (8AM–7PM IST). Visit recording is disabled.
    </div>
  );
}
