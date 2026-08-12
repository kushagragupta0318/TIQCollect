import { clsx } from "clsx";
import { Loader2 } from "lucide-react";
import type { ButtonHTMLAttributes, ReactNode } from "react";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: "primary" | "secondary" | "danger" | "ghost";
  size?: "sm" | "md" | "lg";
  loading?: boolean;
  fullWidth?: boolean;
  children: ReactNode;
}

export function Button({ variant = "primary", size = "md", loading, fullWidth, children, className, disabled, ...props }: ButtonProps) {
  const base = "inline-flex min-h-10 items-center justify-center gap-2 rounded-control border font-medium transition-colors duration-150 disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/20 focus-visible:ring-offset-2";
  const variants = {
    primary: "border-primary bg-white text-primary hover:bg-brand-100",
    secondary: "border-[#E1E3E9] bg-white text-[#101828] hover:bg-[#F7F8FA]",
    danger: "border-danger-500 bg-white text-danger-700 hover:bg-danger-100",
    ghost: "border-transparent bg-transparent text-[#667085] hover:bg-[#F7F8FA] hover:text-[#101828]",
  };
  const sizes = {
    sm: "h-9 min-h-9 px-3 text-[13px]",
    md: "h-10 px-4 text-sm",
    lg: "h-11 px-5 text-sm",
  };

  return (
    <button
      className={clsx(base, variants[variant], sizes[size], fullWidth && "w-full", className)}
      disabled={disabled || loading}
      {...props}
    >
      {loading && <Loader2 className="w-4 h-4 animate-spin" />}
      {children}
    </button>
  );
}
