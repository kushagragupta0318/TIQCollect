// Command Center `components/Sidebar.jsx`, ported to TypeScript (spec §2.2).
// Class strings verbatim; the sections are the bank's (plan §5.1, navigation.ts).
// Floating card rail, 252px expanded / 76px collapsed, collapsed by default.
//
// Accessibility additions over CC (recorded in UI spec §9), none of which
// changes the resting look: the page list is a navigation landmark, the
// current page carries aria-current, the rail toggles carry aria-expanded, and
// the collapsed rail's flyouts are keyboard disclosures (see SectionRail). The
// rail STAYS collapsed by default — CC's default, and the S1 parity state
// (spec §8) — because with the disclosure it is now fully operable from a
// keyboard; the choice persists per user (Ctrl/Cmd+B).
import { useId, useRef, useState, type FocusEvent, type KeyboardEvent } from "react";
import { Link, useLocation } from "react-router";
import { LogOut, PanelLeft, PanelLeftClose } from "lucide-react";
import { Sidebar as SidebarRoot, SidebarContent, SidebarFooter, SidebarHeader } from "../ui/sidebar";
import { useSidebar } from "../ui/sidebarContext";
import { BANK_HOME_PATH, BUILT_BANK_SECTIONS, bankHref, type BankNavItem, type BankNavSection } from "./navigation";
import logo from "../assets/logo.png";
import IQ from "../assets/IQ_Logo.png";

export interface BankPersona {
  role: string;
  product: string;
  geography: string;
}

function ItemLink({ item, active, onNavigate }: { item: BankNavItem; active: boolean; onNavigate?: () => void }) {
  const Icon = item.icon;
  return (
    <Link
      to={bankHref(item)}
      aria-current={active ? "page" : undefined}
      onClick={onNavigate}
      className={[
        "relative flex items-center gap-2.5 pl-3 pr-2.5 py-2 rounded-control text-[12.5px] font-medium transition-colors group/i",
        active ? "bg-accent text-primary font-semibold" : "text-muted-foreground hover:bg-muted hover:text-foreground",
      ].join(" ")}
    >
      <Icon
        aria-hidden="true"
        className={[
          "size-[17px] shrink-0",
          active ? "text-primary" : "text-muted-foreground group-hover/i:text-foreground",
        ].join(" ")}
      />
      <span className="truncate">{item.name}</span>
    </Link>
  );
}

// Collapsed rail: section icon + flyout of its pages. CC opens the flyout on
// hover only and leaves its links focusable while invisible, so a keyboard
// user tabs through twenty hidden links and a screen reader hears them all.
// Here it is a disclosure (a recorded deviation, UI spec §9): hover still
// opens it exactly as in CC; the button also opens it (click, Enter, Space)
// and says so with aria-expanded; closed, the flyout is `invisible`, so its
// links leave the tab order; Escape or focus leaving the section closes it.
function SectionRail({ section, activePath }: { section: BankNavSection; activePath: string }) {
  const SectionIcon = section.icon;
  const sectionActive = section.items.some((i) => bankHref(i) === activePath);
  const [open, setOpen] = useState(false);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const flyoutId = useId();

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key === "Escape" && open) {
      e.stopPropagation();
      setOpen(false);
      buttonRef.current?.focus();
    }
  };
  const onBlur = (e: FocusEvent<HTMLDivElement>) => {
    if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setOpen(false);
  };

  return (
    <div className="relative group px-2" onKeyDown={onKeyDown} onBlur={onBlur}>
      <button
        ref={buttonRef}
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        aria-controls={flyoutId}
        className={[
          "relative flex items-center justify-center w-11 h-11 mx-auto rounded-control transition-colors",
          sectionActive ? "bg-accent text-primary" : "text-muted-foreground group-hover:bg-muted group-hover:text-foreground",
        ].join(" ")}
        aria-label={section.label}
      >
        <SectionIcon className="size-[19px]" aria-hidden="true" />
      </button>
      <div
        id={flyoutId}
        className={
          open
            ? "absolute left-full top-0 pl-3 z-50 opacity-100 translate-x-0 pointer-events-auto visible transition-all duration-150 ease-out"
            : // CC's string verbatim, plus `invisible group-hover:visible`.
              "absolute left-full top-0 pl-3 z-50 opacity-0 -translate-x-1 pointer-events-none invisible " +
              "group-hover:opacity-100 group-hover:translate-x-0 group-hover:pointer-events-auto group-hover:visible " +
              "transition-all duration-150 ease-out"
        }
      >
        <div className="w-60 rounded-inner border border-border bg-card shadow-menu p-2">
          <p className="text-[11px] font-semibold uppercase tracking-[0.06em] text-muted-foreground px-2.5 pt-1.5 pb-2">{section.label}</p>
          <div className="space-y-0.5">
            {section.items.map((item) => (
              <ItemLink key={item.path} item={item} active={bankHref(item) === activePath} onNavigate={() => setOpen(false)} />
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

export function BankSidebar({ persona, onSignOut }: { persona?: BankPersona; onSignOut?: () => void }) {
  const loc = useLocation();
  const { state, toggleSidebar } = useSidebar();
  const expanded = state === "expanded";

  return (
    <SidebarRoot>
      <SidebarHeader className={`border-b border-border pb-3 ${expanded ? "" : "justify-center"}`}>
        {expanded ? (
          <div className="flex items-center justify-between w-full px-1">
            <img className="h-[2rem]" src={logo} alt="TransOrgIQ" />
            <button onClick={toggleSidebar} aria-label="Collapse sidebar" aria-expanded={true}
              className="text-muted-foreground hover:text-foreground hover:bg-muted rounded-control p-2 transition-colors">
              <PanelLeftClose size={16} />
            </button>
          </div>
        ) : (
          <Link to={BANK_HOME_PATH} title="Overview" className="mx-auto flex items-center justify-center">
            <img className="h-7" src={IQ} alt="TransOrgIQ" />
          </Link>
        )}
      </SidebarHeader>

      {/* Expand affordance in the collapsed rail */}
      {!expanded && (
        <button onClick={toggleSidebar} aria-label="Expand sidebar" aria-expanded={false}
          className="mx-auto mt-2 text-muted-foreground hover:text-foreground hover:bg-muted rounded-control p-2 transition-colors">
          <PanelLeft size={16} />
        </button>
      )}

      {/* Persona card (expanded only) */}
      {persona && expanded && (
        <div className="mx-3 mt-3 mb-1 px-3 py-3 bg-muted rounded-inner">
          <p className="text-[11px] font-semibold text-muted-foreground uppercase tracking-[0.06em] mb-1">{persona.role}</p>
          <p className="text-[14px] font-semibold text-foreground truncate">{persona.product}</p>
          <p className="text-[12px] text-muted-foreground mt-0.5">{persona.geography}</p>
        </div>
      )}

      <SidebarContent
        role="navigation"
        aria-label="Bank portal"
        className={expanded ? "pt-2 overflow-y-auto sidebar-scrollbar-hide" : "pt-3 overflow-visible gap-1.5"}
      >
        {expanded
          ? BUILT_BANK_SECTIONS.map((section) => (
              <div key={section.label} className="px-3 py-1">
                <p className="text-[11px] font-semibold uppercase tracking-[0.06em] text-muted-foreground px-2 mb-1.5 mt-3">{section.label}</p>
                <div className="space-y-0.5">
                  {section.items.map((item) => (
                    <ItemLink key={item.path} item={item} active={loc.pathname === bankHref(item)} />
                  ))}
                </div>
              </div>
            ))
          : BUILT_BANK_SECTIONS.map((section) => (
              <SectionRail key={section.label} section={section} activePath={loc.pathname} />
            ))}
      </SidebarContent>

      <SidebarFooter className="border-t border-border pt-2 pb-2">
        {onSignOut && (
          expanded ? (
            <button onClick={onSignOut}
              className="flex min-h-10 items-center gap-2 w-full px-4 rounded-control text-[13px] font-medium text-muted-foreground hover:text-foreground hover:bg-muted transition-colors">
              <LogOut className="size-3.5 shrink-0" /> Sign out
            </button>
          ) : (
            <div className="relative group px-2">
              <button onClick={onSignOut} aria-label="Sign out"
                className="w-11 h-11 mx-auto flex items-center justify-center rounded-control text-muted-foreground hover:bg-muted hover:text-foreground transition-colors">
                <LogOut className="size-4" />
              </button>
              <div aria-hidden="true" className="absolute left-full bottom-1 pl-3 z-50 opacity-0 -translate-x-1 pointer-events-none
                              group-hover:opacity-100 group-hover:translate-x-0 group-hover:pointer-events-auto transition-all duration-150">
                <div className="px-3 py-2 rounded-inner border border-border shadow-menu text-[13px] font-medium text-foreground bg-card whitespace-nowrap">
                  Sign out
                </div>
              </div>
            </div>
          )
        )}
      </SidebarFooter>
    </SidebarRoot>
  );
}
