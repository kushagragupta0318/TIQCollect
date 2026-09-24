// Command Center `components/ui/dialog.jsx`, ported to TypeScript (spec §3.3).
// Class strings verbatim. Two changes. The portal target is the `.bank-root`
// portal container, not document.body — outside the wrapper the dialog would
// lose every bank variable and rule (spec §7.3, lib/portal.ts). And focus is
// managed (lib/useModalFocus.ts): it moves into the dialog, Tab stays inside,
// Escape closes, focus returns to the opener, and the dialog is named by its
// title (aria-labelledby) — CC does none of these (a recorded deviation,
// UI spec §9). CC's own scroll lock and Escape listener live in that hook now.
import { createContext, useContext, useId, useMemo, useRef, type ComponentProps, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { cn } from "../lib/cn";
import { getBankPortalRoot } from "../lib/portal";
import { useModalFocus } from "../lib/useModalFocus";

interface DialogContextValue {
  close: () => void;
  titleId: string;
  descriptionId: string;
}

const DialogContext = createContext<DialogContextValue | null>(null);

export interface DialogProps {
  open: boolean;
  onOpenChange?: (open: boolean) => void;
  children?: ReactNode;
}

export function Dialog({ open, onOpenChange, children }: DialogProps) {
  const id = useId();
  const value = useMemo<DialogContextValue>(
    () => ({ close: () => onOpenChange?.(false), titleId: `${id}-title`, descriptionId: `${id}-description` }),
    [id, onOpenChange],
  );

  if (!open) return null;
  return createPortal(
    <DialogContext.Provider value={value}>
      <div className="fixed inset-0 z-[9999] flex items-center justify-center p-4">
        <div className="fixed inset-0 bg-[#101828]/40" onClick={() => onOpenChange?.(false)} />
        {children}
      </div>
    </DialogContext.Provider>,
    getBankPortalRoot(),
  );
}

export function DialogContent({ className, children, ...props }: ComponentProps<"div">) {
  const ctx = useContext(DialogContext);
  const ref = useRef<HTMLDivElement>(null);
  useModalFocus(ref, ctx?.close);
  return (
    <div
      ref={ref}
      role="dialog"
      aria-modal="true"
      aria-labelledby={ctx?.titleId}
      aria-describedby={ctx?.descriptionId}
      className={cn(
        "relative z-50 bg-card border border-border rounded-modal shadow-modal w-full max-w-[560px] max-h-[88vh] flex flex-col overflow-hidden",
        className,
      )}
      onClick={(e) => e.stopPropagation()}
      {...props}
    >
      {children}
    </div>
  );
}

export function DialogHeader({ className, ...props }: ComponentProps<"div">) {
  return <div className={cn("px-6 pt-6 pb-4 border-b border-border flex-shrink-0", className)} {...props} />;
}

export function DialogTitle({ className, ...props }: ComponentProps<"h2">) {
  const ctx = useContext(DialogContext);
  return <h2 id={ctx?.titleId} className={cn("text-[19px] font-semibold text-foreground tracking-tight", className)} {...props} />;
}

export function DialogDescription({ className, ...props }: ComponentProps<"p">) {
  const ctx = useContext(DialogContext);
  return <p id={ctx?.descriptionId} className={cn("text-[14px] text-muted-foreground mt-1", className)} {...props} />;
}

export function DialogFooter({ className, ...props }: ComponentProps<"div">) {
  return (
    <div
      className={cn(
        "px-6 py-4 border-t border-border flex-shrink-0 bg-card flex items-center justify-end gap-3",
        className,
      )}
      {...props}
    />
  );
}

export function DialogClose({ className, onClose, ...props }: ComponentProps<"button"> & { onClose?: () => void }) {
  return (
    <button
      className={cn(
        "absolute top-4 right-4 w-10 h-10 rounded-control flex items-center justify-center text-muted-foreground hover:text-foreground hover:bg-muted transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/20",
        className,
      )}
      onClick={onClose}
      aria-label="Close dialog"
      {...props}
    >
      <svg
        width="14"
        height="14"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <line x1="18" y1="6" x2="6" y2="18" />
        <line x1="6" y1="6" x2="18" y2="18" />
      </svg>
    </button>
  );
}
