// Command Center `components/ui/table.jsx`, ported to TypeScript (spec §3.3).
// Class strings verbatim — including the ones that do not render: CC's default
// table rules (bank.css "Clean Default Tables") outrank `py-3`, `pb-2.5`,
// `font-medium` and the row hover here, exactly as they do in CC (spec §0.3).
import type { ComponentProps } from "react";
import { cn } from "../lib/cn";

export function Table({ className, ...props }: ComponentProps<"table">) {
  return (
    <div className="relative w-full overflow-auto">
      <table className={cn("w-full caption-bottom text-sm border-collapse", className)} {...props} />
    </div>
  );
}

export function TableHeader({ className, ...props }: ComponentProps<"thead">) {
  return <thead className={cn("bg-muted", className)} {...props} />;
}

export function TableBody({ className, ...props }: ComponentProps<"tbody">) {
  return <tbody className={cn("[&_tr:last-child]:border-0", className)} {...props} />;
}

export function TableFooter({ className, ...props }: ComponentProps<"tfoot">) {
  return <tfoot className={cn("border-t bg-muted/50 font-medium [&>tr]:last:border-b-0", className)} {...props} />;
}

export function TableRow({ className, ...props }: ComponentProps<"tr">) {
  return (
    <tr
      className={cn(
        "border-b border-border/60 transition-colors hover:bg-accent/40 data-[state=selected]:bg-muted even:bg-muted/30",
        className,
      )}
      {...props}
    />
  );
}

export function TableHead({ className, ...props }: ComponentProps<"th">) {
  return (
    <th
      className={cn(
        "bg-muted text-left align-middle text-[10px] font-bold uppercase tracking-wider text-muted-foreground px-4 py-2.5 [&:has([role=checkbox])]:pr-0",
        className,
      )}
      {...props}
    />
  );
}

export function TableCell({ className, ...props }: ComponentProps<"td">) {
  return (
    <td
      className={cn(
        "px-4 py-3 align-middle text-xs font-medium text-foreground [&:has([role=checkbox])]:pr-0",
        className,
      )}
      {...props}
    />
  );
}

export function TableCaption({ className, ...props }: ComponentProps<"caption">) {
  return <caption className={cn("mt-4 text-sm text-muted-foreground", className)} {...props} />;
}
