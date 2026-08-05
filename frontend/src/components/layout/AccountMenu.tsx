import { useEffect, useRef, useState } from "react";
import { LogOut } from "lucide-react";

/**
 * Avatar button that opens a small account menu carrying Sign Out.
 *
 * Shared by both shells so the manager and agent headers behave identically —
 * it was written for ManagerLayout first and extracted here when AgentLayout
 * was brought in line with it.
 */
export function AccountMenu({
  name,
  role,
  onLogout,
}: {
  name: string;
  role: string;
  onLogout: () => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function handleClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    function handleKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", handleClick);
    document.addEventListener("keydown", handleKey);
    return () => {
      document.removeEventListener("mousedown", handleClick);
      document.removeEventListener("keydown", handleKey);
    };
  }, [open]);

  return (
    <div ref={ref} style={{ position: "relative" }}>
      <button
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label="Account menu"
        className="tap-target w-9 h-9 rounded-full bg-primary flex items-center justify-center text-white text-sm font-bold flex-shrink-0"
      >
        {name.charAt(0)}
      </button>

      {open && (
        <div
          role="menu"
          className="absolute right-0 mt-2 rounded-2xl shadow-xl overflow-hidden"
          style={{
            width: "min(220px, calc(100vw - 32px))",
            zIndex: 100,
            background: "#fff",
            border: "1px solid hsl(var(--border) / 0.6)",
            top: "100%",
          }}
        >
          <div className="px-4 py-3 border-b" style={{ borderColor: "hsl(var(--border) / 0.5)" }}>
            <p className="text-sm font-bold truncate" style={{ color: "#1C1C1F" }}>{name}</p>
            <p className="text-xs mt-0.5 capitalize" style={{ color: "#6B6D76" }}>
              {role.replace(/_/g, " ").toLowerCase()}
            </p>
          </div>
          <button
            role="menuitem"
            onClick={() => { setOpen(false); onLogout(); }}
            className="tap-target w-full flex items-center gap-2.5 px-4 py-3 text-sm font-semibold transition-colors hover:bg-slate-50"
            style={{ color: "#DC2626", background: "none", border: "none", cursor: "pointer" }}
          >
            <LogOut className="w-4 h-4 flex-shrink-0" />
            Sign out
          </button>
        </div>
      )}
    </div>
  );
}
