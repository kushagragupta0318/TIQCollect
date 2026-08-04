import { NavLink, Outlet, useNavigate } from "react-router";
import { Home, Briefcase, User, Map, LogOut, WifiOff, ShieldCheck } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "react-hot-toast";
import { useAuthStore } from "@/store/authStore";
import { useSOSStore } from "@/store/sosStore";
import { SOSButton } from "@/components/ui/SOSButton";
import { BeatProvider, useBeat } from "@/contexts/BeatContext";
import api from "@/api/axios";

const NAV_ITEMS = [
  { to: "/agent/home",    icon: Home,      label: "Home" },
  { to: "/agent/cases",   icon: Briefcase, label: "Cases" },
  { to: "/agent/beat",    icon: Map,       label: "Beat" },
  { to: "/agent/profile", icon: User,      label: "Profile" },
];

const BRAND = "#0C66E4";

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

  function handleLogout() {
    api.post("/auth/logout").finally(() => {
      logout();
      navigate("/login", { replace: true });
    });
  }

  return (
    <div className="flex flex-col min-h-screen bg-slate-50 max-w-md mx-auto relative">

      {/* ── Top header ── */}
      <header className="sticky top-0 z-40 text-white safe-top" style={{ background: BRAND, boxShadow: "0 2px 16px rgba(12,102,228,0.22)" }}>
        <div className="flex items-center justify-between px-4 py-3">
          <div className="flex items-center gap-2.5">
            <div style={{ width: 30, height: 30, borderRadius: 9, background: "rgba(255,255,255,0.18)", display: "flex", alignItems: "center", justifyContent: "center" }}>
              <ShieldCheck size={15} color="white" />
            </div>
            <div>
              <p style={{ color: "rgba(255,255,255,0.65)", fontSize: 10, fontWeight: 500, lineHeight: 1 }}>TIQCollect · Field Agent</p>
              <p style={{ color: "white", fontSize: 13, fontWeight: 700, lineHeight: 1.3 }}>{user?.full_name}</p>
            </div>
          </div>
          <SOSButton />
        </div>

        <ContactHourBanner />
        {!isOnline && (
          <div className="bg-slate-800 text-white text-xs text-center py-1.5 px-4 font-medium flex items-center justify-center gap-1.5">
            <WifiOff className="w-3 h-3" />
            Offline — visits will sync when reconnected
          </div>
        )}
      </header>

      {/* ── Page content ── */}
      <main className="flex-1 overflow-y-auto pb-20">
        <Outlet />
      </main>

      {/* ── Bottom navigation — always expanded, never collapses ── */}
      <nav
        className="fixed bottom-0 left-1/2 -translate-x-1/2 w-full max-w-md z-40 safe-bottom"
        style={{ background: BRAND, boxShadow: "0 -2px 24px rgba(12,102,228,0.28)", borderTop: "1px solid rgba(255,255,255,0.12)" }}
      >
        <div className="flex items-stretch px-1 py-1.5">
          {NAV_ITEMS.map(({ to, icon: Icon, label }) => (
            <NavLink
              key={to}
              to={to}
              style={{ flex: 1, textDecoration: "none" }}
            >
              {({ isActive }) => (
                <div
                  style={{
                    display:        "flex",
                    flexDirection:  "column",
                    alignItems:     "center",
                    gap:            3,
                    padding:        "6px 4px",
                    borderRadius:   10,
                    background:     isActive ? "rgba(255,255,255,0.20)" : "transparent",
                    transition:     "background 150ms ease",
                  }}
                >
                  <Icon
                    size={19}
                    style={{
                      color:       "white",
                      opacity:     isActive ? 1 : 0.65,
                      strokeWidth: isActive ? 2.2 : 1.8,
                      flexShrink:  0,
                    }}
                  />
                  <span
                    style={{
                      fontSize:   10,
                      fontWeight: isActive ? 700 : 500,
                      color:      "white",
                      opacity:    isActive ? 1 : 0.65,
                      lineHeight: 1,
                    }}
                  >
                    {label}
                  </span>
                  {isActive && (
                    <span style={{ width: 4, height: 4, borderRadius: "50%", background: "white", marginTop: 1 }} />
                  )}
                </div>
              )}
            </NavLink>
          ))}

          {/* Logout */}
          <button
            onClick={handleLogout}
            style={{
              flex:          1,
              display:       "flex",
              flexDirection: "column",
              alignItems:    "center",
              gap:           3,
              padding:       "6px 4px",
              borderRadius:  10,
              background:    "transparent",
              border:        "none",
              cursor:        "pointer",
              transition:    "background 150ms ease",
            }}
            onMouseEnter={e => (e.currentTarget.style.background = "rgba(255,255,255,0.10)")}
            onMouseLeave={e => (e.currentTarget.style.background = "transparent")}
          >
            <LogOut size={19} style={{ color: "white", opacity: 0.65, strokeWidth: 1.8 }} />
            <span style={{ fontSize: 10, fontWeight: 500, color: "white", opacity: 0.65, lineHeight: 1 }}>Logout</span>
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
