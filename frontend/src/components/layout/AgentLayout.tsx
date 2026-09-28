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
import { Home, Briefcase, User, Map, LogOut, WifiOff, MapPin, LocateFixed, RotateCcw } from "lucide-react";
import { BrandLogo } from "@/components/ui/BrandLogo";
import { useEffect, useState, useCallback } from "react";
import { toast } from "react-hot-toast";
import { useAuthStore } from "@/store/authStore";
import { useSOSStore } from "@/store/sosStore";
import { SOSButton } from "@/components/ui/SOSButton";
import { AccountMenu } from "@/components/layout/AccountMenu";
import { BeatProvider } from "@/contexts/BeatContext";
import { useBeat } from "@/contexts/useBeat";
import { refreshLocation, useLiveLocation } from "@/hooks/useLiveLocation";
import { reportNow, startLocationReporting, stopLocationReporting } from "@/lib/locationReporter";
import { logout as apiLogout } from "@/api/auth";

const SIDEBAR_KEY  = "tiq:agent-sidebar";
const SIDEBAR_W    = 252;
const SIDEBAR_ICON = 72;

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
  top:            16,
  left:           16,
  height:        "calc(100svh - 32px)",
  zIndex:         50,
  width:          open ? `${SIDEBAR_W}px` : `${SIDEBAR_ICON}px`,
  transition:    "width 200ms cubic-bezier(0.2,0,0,1)",
  background:     "#FFFFFF",
  border:        "1px solid #ECEDF1",
  borderRadius:  16,
  overflow:      "hidden",
  flexDirection: "column",
  boxShadow:     "0 1px 2px rgba(16,24,40,0.04)",
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

/** Live GPS address in the header — shows the agent's real location anywhere on
 *  earth, so the demo reads as live rather than pinned to a fixed city. */
function LiveLocationLine({ tone = "light" }: { tone?: "light" | "dark" }) {
  const loc = useLiveLocation();
  const [refreshing, setRefreshing] = useState(false);
  const text =
    loc.status === "ready"   ? loc.address :
    loc.status === "locating" ? "Locating…" :
    loc.status === "denied"  ? "Location off — enable GPS" :
                               "Location unavailable";
  const Icon = loc.status === "ready" ? MapPin : LocateFixed;
  const color = tone === "light" ? "rgba(255,255,255,0.85)" : "hsl(var(--muted-foreground))";

  // 2026-09-14 — the refresh button. The watcher runs on its own, so this is
  // the agent's retry when the line is stuck ("Locating…" indoors, "Location
  // off" after they re-enabled GPS, an address that lags a lane behind), and
  // it pushes the fresh fix to the manager's map at once rather than on the
  // next 15 s tick.
  const onRefresh = async () => {
    if (refreshing) return;
    setRefreshing(true);
    try {
      const ok = await refreshLocation();
      if (!ok) { toast.error("Couldn't get a fix — check GPS and try again"); return; }
      // `flush()` was wrong here: the refreshed fix only reaches the queue if
      // it clears the reporter's 50 m / 15 s gate, so flushing usually sent
      // nothing while the toast said otherwise. reportNow() bypasses the gate
      // — the agent asked to be seen — and reports whether it could.
      toast.success(reportNow() ? "Location updated" : "Location updated on this device only");
    } finally {
      setRefreshing(false);
    }
  };

  return (
    <div
      title={loc.coords ? `${loc.coords.lat.toFixed(5)}, ${loc.coords.lon.toFixed(5)}` : undefined}
      style={{ display: "flex", alignItems: "flex-start", gap: 4, marginTop: 2, maxWidth: 300 }}
    >
      <Icon size={11} color={color} style={{ marginTop: 2, flexShrink: 0 }}
            className={loc.status === "locating" ? "animate-pulse" : undefined} />
      <span style={{ color, fontSize: 10.5, fontWeight: 500, lineHeight: 1.25,
                     display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical", overflow: "hidden" }}>
        {text}
      </span>
      <button
        type="button"
        onClick={() => { void onRefresh(); }}
        disabled={refreshing}
        aria-label="Refresh location"
        title="Refresh location"
        style={{ background: "none", border: 0, padding: 2, marginTop: 0, marginLeft: 2, cursor: refreshing ? "default" : "pointer",
                 color, display: "inline-flex", flexShrink: 0, borderRadius: 4 }}
      >
        <RotateCcw size={11} className={refreshing ? "animate-spin" : undefined} />
      </button>
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

  // Upload the location trail for as long as the agent is logged in — this
  // layout is only mounted for an authenticated agent, so mount/unmount IS
  // the login boundary.
  //
  // This block used to read: "ONLY while the agent is checked in. Gated on
  // duty status rather than merely on being logged in: continuously recording
  // an employee's movements outside their working hours is employee
  // monitoring we have no business doing, and the retention sweep in
  // workers/tasks/location_retention.py cannot un-collect it afterwards."
  // That is no longer what the code does, and the two statements sat one
  // above the other for a day. The concern it raises is real and is now the
  // deployment's to answer: agents must be told that the app reports position
  // whenever it is open, and closing the app — not going off duty — is what
  // stops it. LOCATION_RETENTION_DAYS still bounds how long it is kept.
  //
  // 2026-09-14: this used to start only when `beat?.check_in_status ===
  // "ON_DUTY"`, on the reasoning that checking in is the agent's own action
  // and therefore the consent boundary. In practice that made the live map
  // blind on any day without a plan: no beat, no check-in, no trail, and the
  // manager saw "6 d ago" beside an agent who was logged in and working.
  // Decided 2026-09-14 that a logged-in agent is a tracked agent; the consent
  // boundary is the login. Beat check-in still marks the working day.
  //
  // Mounted in the layout rather than per-page so the trail does not develop
  // holes every time the agent navigates between screens. It rides on the
  // single watchPosition subscription LiveLocationLine below already holds —
  // no second GPS watcher, which would double power draw on a phone that has
  // to last a full shift.
  useEffect(() => {
    startLocationReporting();
    return () => stopLocationReporting();
  }, []);

  useEffect(() => {
    const onOnline  = () => { setIsOnline(true);  toast.success("Back online — you can submit now"); };
    // The copy here used to promise "actions will queue" and "visits will sync
    // when reconnected". Neither was true: there is no service worker, no
    // outbox and no background sync anywhere in this app, so a visit submitted
    // on a dead connection was simply lost — while the banner told the agent it
    // was safe. A false reassurance is worse than no banner at all, because it
    // is the reason someone keeps working instead of walking to find signal.
    //
    // RecordVisitPage now also blocks submission while offline and keeps the
    // typed part of the form on the device, so the promise below is one the app
    // can actually keep.
    const onOffline = () => { setIsOnline(false); toast.error("You're offline — you can't submit visits until you reconnect"); };
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
    apiLogout().finally(() => {
      logout();
      navigate("/login", { replace: true });
    });
  }, [logout, navigate]);

  return (
    <div
      className="overflow-x-clip-safe"
      style={{ background: "hsl(var(--background))", display: "flex", minHeight: "100svh", width: "100%" }}
    >

      {/* ── Fixed rail — desktop only; hover to expand ── */}
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
            <p style={{ color: "#667085", fontWeight: 400, fontSize: 11, marginTop: 4 }}>Field Agent</p>
          </div>
        </div>

        {/* Nav */}
        <nav style={{ flex: 1, padding: "12px 8px", overflowY: "auto", overflowX: "hidden" }}>
          {NAV_ITEMS.map(({ to, icon: Icon, label }) => (
            <NavLink key={to} to={to} title={!open ? label : undefined}>
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
            menu, so there is one place to log out rather than two. */}
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

      {/* ── Backdrop — desktop only; catches the mouse leaving the expanded rail ── */}
      {open && (
        <div
          aria-hidden="true"
          className="hidden lg:block"
          style={{ position: "fixed", inset: 0, zIndex: 40, background: "rgba(16,24,40,0.04)" }}
          onMouseEnter={() => setOpen(false)}
          onClick={() => setOpen(false)}
        />
      )}

      {/* ── Main column — offset by the rail only where the rail exists.
             Below lg this is the untouched 448px phone shell. ── */}
      <div
        className="min-w-0 w-full max-w-md md:max-w-none mx-auto md:mx-0 flex flex-col relative tiq-ambient"
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
            className="mx-2 mt-2 flex items-center justify-between rounded-card px-4 py-3 lg:hidden tiq-glass-bar"
          >
            <div className="flex items-center gap-2.5 min-w-0">
              <BrandLogo size={32} />
              <div className="min-w-0">
                {/* Upstream swapped the static "TIQCollect · Field Agent" label
                    for the agent's live GPS address — kept. */}
                <p className="truncate" style={{ color: "#101828", fontSize: 13, fontWeight: 600, lineHeight: 1.3 }}>{user?.full_name}</p>
                <LiveLocationLine tone="dark" />
              </div>
            </div>
            <SOSButton />
          </div>

          {/* Desktop header — a real glass bar (2026-09-22): the case list
              scrolls under it, which is what makes the blur legible. */}
          <div className="mx-4 mt-4 hidden items-center justify-between gap-3 rounded-card px-5 py-3 lg:flex tiq-glass-bar">
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
              Offline — notes are saved on this device. Reconnect to submit.
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
                    padding:        "6px 4px",
                    borderRadius:   10,
                    background:     isActive ? "#EFF6FF" : "transparent",
                    transition:     "background 120ms cubic-bezier(0.2,0,0,1)",
                  }}
                >
                  <Icon size={19} style={{ color: isActive ? "#2563EB" : "#98A2B3", strokeWidth: isActive ? 2.2 : 1.8, flexShrink: 0 }} />
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
            <LogOut size={19} style={{ color: "#98A2B3", strokeWidth: 1.8, flexShrink: 0 }} />
            <span className="w-full truncate text-center" style={{ fontSize: 10, fontWeight: 500, color: "#667085", lineHeight: 1 }}>Logout</span>
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
