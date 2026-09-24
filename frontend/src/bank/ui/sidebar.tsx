// Command Center `components/ui/sidebar.jsx`, ported to TypeScript (spec §2.2, §3.3).
// Widths 252 / 76px, persisted open state, Ctrl/Cmd+B. Class strings verbatim.
import {
  cloneElement,
  isValidElement,
  useCallback,
  useEffect,
  useMemo,
  useState,
  type ComponentProps,
  type CSSProperties,
  type ReactElement,
  type ReactNode,
} from "react";
import { PanelLeft } from "lucide-react";
import { cn } from "../lib/cn";
import { Button } from "./button";
import { SIDEBAR_STORAGE_KEY, SidebarContext, useSidebar, type SidebarContextValue } from "./sidebarContext";

const SIDEBAR_WIDTH = "252px";
const SIDEBAR_WIDTH_ICON = "76px";
const SIDEBAR_KEYBOARD_SHORTCUT = "b";

function readSavedOpen(defaultOpen: boolean): boolean {
  if (typeof window === "undefined") return defaultOpen;
  try {
    const saved = window.localStorage.getItem(SIDEBAR_STORAGE_KEY);
    if (saved === "open") return true;
    if (saved === "closed") return false;
  } catch {
    /* storage blocked: fall back to the default, as CC does when nothing is saved */
  }
  return defaultOpen;
}

export function SidebarProvider({
  defaultOpen = true,
  children,
  className,
  style,
  ...props
}: ComponentProps<"div"> & { defaultOpen?: boolean }) {
  const [open, setOpenState] = useState<boolean>(() => readSavedOpen(defaultOpen));

  const setOpen = useCallback<SidebarContextValue["setOpen"]>(
    (value) => {
      const next = typeof value === "function" ? value(open) : value;
      setOpenState(next);
      try {
        window.localStorage.setItem(SIDEBAR_STORAGE_KEY, next ? "open" : "closed");
      } catch {
        /* storage blocked: the state still toggles for this session */
      }
    },
    [open],
  );

  const toggleSidebar = useCallback(() => setOpen((v) => !v), [setOpen]);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === SIDEBAR_KEYBOARD_SHORTCUT && (e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        toggleSidebar();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [toggleSidebar]);

  const state = open ? "expanded" : "collapsed";

  const value = useMemo<SidebarContextValue>(
    () => ({ state, open, setOpen, toggleSidebar }),
    [state, open, setOpen, toggleSidebar],
  );

  return (
    <SidebarContext.Provider value={value}>
      <div
        data-state={state}
        style={
          {
            "--sidebar-width": SIDEBAR_WIDTH,
            "--sidebar-width-icon": SIDEBAR_WIDTH_ICON,
            ...style,
          } as CSSProperties
        }
        className={cn("group/sidebar-wrapper flex min-h-screen w-full", className)}
        {...props}
      >
        {children}
      </div>
    </SidebarContext.Provider>
  );
}

export function Sidebar({ className, children, ...props }: ComponentProps<"aside">) {
  const { state } = useSidebar();
  return (
    <aside
      data-state={state}
      data-collapsible={state === "collapsed" ? "icon" : ""}
      className={cn(
        "fixed top-5 bottom-5 left-5 z-50 flex flex-col",
        "rounded-card border border-border bg-card text-foreground shadow-resting overflow-visible",
        "transition-[width] duration-300 ease-in-out",
        // Collapsed = slim icon rail (hover flyouts); expanded = full labels.
        state === "expanded" ? "w-[var(--sidebar-width)]" : "w-[var(--sidebar-width-icon)]",
        className,
      )}
      {...props}
    >
      {children}
    </aside>
  );
}

export function SidebarHeader({ className, ...props }: ComponentProps<"div">) {
  return <div className={cn("h-16 flex items-center border-border shrink-0 px-3 gap-2", className)} {...props} />;
}

export function SidebarContent({ className, ...props }: ComponentProps<"div">) {
  // Overflow is set by the caller: collapsed rail needs overflow-visible so
  // hover-flyouts escape; expanded needs overflow-y-auto to scroll pages.
  return <div className={cn("flex-1 min-h-0 py-3 flex flex-col gap-3", className)} {...props} />;
}

export function SidebarFooter({ className, ...props }: ComponentProps<"div">) {
  return <div className={cn("border-t border-border shrink-0 p-2", className)} {...props} />;
}

export function SidebarGroup({ className, ...props }: ComponentProps<"div">) {
  return <div className={cn("flex flex-col gap-1 px-2", className)} {...props} />;
}

export function SidebarGroupLabel({ className, ...props }: ComponentProps<"div">) {
  const { state } = useSidebar();
  return (
    <div
      className={cn(
        "text-[11px] font-semibold tracking-wider text-muted-foreground uppercase px-2 h-7 flex items-center",
        "transition-opacity duration-150",
        state === "collapsed" && "opacity-0 pointer-events-none h-0 -mt-1",
        className,
      )}
      {...props}
    />
  );
}

export function SidebarMenu({ className, ...props }: ComponentProps<"ul">) {
  return <ul className={cn("flex flex-col gap-0.5 list-none p-0 m-0", className)} {...props} />;
}

export function SidebarMenuItem({ className, ...props }: ComponentProps<"li">) {
  return <li className={cn("relative group/menu-item", className)} {...props} />;
}

const MENU_TOOLTIP = cn(
  "pointer-events-none absolute left-full top-1/2 -translate-y-1/2 ml-2 z-50",
  "whitespace-nowrap rounded-md bg-foreground text-background px-2 py-1 text-xs font-medium",
  "opacity-0 group-hover/menu-item:opacity-100 transition-opacity shadow-md",
);

/**
 * SidebarMenuButton — primary nav item.
 * Polymorphic via `asChild`: when true, clones the single child (e.g. a <Link/>)
 * and applies the styling to it so router state still works.
 */
export function SidebarMenuButton({
  asChild = false,
  isActive = false,
  tooltip,
  className,
  children,
  ...props
}: ComponentProps<"button"> & { asChild?: boolean; isActive?: boolean; tooltip?: ReactNode }) {
  const { state } = useSidebar();
  const collapsed = state === "collapsed";

  const classes = cn(
    "flex items-center gap-3 w-full rounded-control px-3 h-11 text-sm",
    "transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
    isActive
      ? "bg-accent text-primary font-semibold"
      : "text-muted-foreground font-medium hover:bg-muted hover:text-foreground",
    className,
  );

  if (asChild && isValidElement(children)) {
    const child = children as ReactElement<{ className?: string } & Record<string, unknown>>;
    return (
      <div className="relative">
        {cloneElement(child, {
          className: cn(classes, child.props.className),
          "data-active": isActive ? "true" : undefined,
          ...props,
        })}
        {collapsed && tooltip && (
          <span role="tooltip" className={MENU_TOOLTIP}>
            {tooltip}
          </span>
        )}
      </div>
    );
  }

  return (
    <div className="relative">
      <button className={classes} {...props}>
        {children}
      </button>
      {collapsed && tooltip && (
        <span role="tooltip" className={MENU_TOOLTIP}>
          {tooltip}
        </span>
      )}
    </div>
  );
}

export function SidebarMenuBadge({ className, ...props }: ComponentProps<"span">) {
  const { state } = useSidebar();
  return (
    <span
      className={cn(
        "ml-auto inline-flex items-center justify-center rounded text-[9px] font-bold leading-none px-1.5 py-0.5",
        state === "collapsed" && "hidden",
        className,
      )}
      {...props}
    />
  );
}

/**
 * SidebarInset — main content area that auto-offsets for the fixed sidebar.
 */
export function SidebarInset({ className, ...props }: ComponentProps<"div">) {
  const { state } = useSidebar();
  return (
    <div
      className={cn(
        "relative flex flex-1 flex-col min-w-0 min-h-screen transition-[padding] duration-300 ease-in-out",
        state === "expanded" ? "pl-[calc(var(--sidebar-width)+2.5rem)]" : "pl-[calc(var(--sidebar-width-icon)+2.5rem)]",
        className,
      )}
      {...props}
    />
  );
}

/**
 * SidebarTrigger — collapse/expand toggle; drop into the Header.
 */
export function SidebarTrigger({ className, onClick, ...props }: ComponentProps<"button">) {
  const { toggleSidebar } = useSidebar();
  return (
    <Button
      variant="ghost"
      size="icon"
      className={cn("h-8 w-8 text-muted-foreground hover:text-foreground", className)}
      onClick={(e) => {
        onClick?.(e);
        toggleSidebar();
      }}
      aria-label="Toggle sidebar"
      title="Toggle sidebar (Ctrl/Cmd + B)"
      {...props}
    >
      <PanelLeft />
    </Button>
  );
}
