// Command Center `components/ui/sidebar.jsx:11-18` — the context and hook,
// split out of sidebar.tsx so that file exports only components
// (react-refresh/only-export-components; spec §7.5).
import { createContext, useContext } from "react";

export interface SidebarContextValue {
  state: "expanded" | "collapsed";
  open: boolean;
  setOpen: (value: boolean | ((open: boolean) => boolean)) => void;
  toggleSidebar: () => void;
}

export const SidebarContext = createContext<SidebarContextValue | null>(null);

/** Same persistence key as CC, so the parity harness seeds both apps alike (spec §8). */
export const SIDEBAR_STORAGE_KEY = "sidebar:state:v2";

export function useSidebar(): SidebarContextValue {
  const ctx = useContext(SidebarContext);
  if (!ctx) throw new Error("useSidebar must be used within a <SidebarProvider />");
  return ctx;
}
