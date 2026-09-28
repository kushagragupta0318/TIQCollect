// Alert severity styling, verbatim from DecisionAlerts.jsx:14-27 (spec §1.6).
import { AlertCircle, AlertTriangle, Info, type LucideIcon } from "lucide-react";
import { BRAND } from "../theme/colors";

export type AlertSeverity = "critical" | "warning" | "info";

export interface SeverityStyle {
  Icon: LucideIcon;
  /** The rounded-square icon chip. */
  soft: string;
  iconColor: string;
  /** The card's fill while open. */
  bg: string;
  label: string;
  /** Bar colour in the distribution when the label is not a DPD bucket. */
  accent: string;
}

export const SEVERITY: Record<AlertSeverity, SeverityStyle> = {
  critical: {
    Icon: AlertCircle, soft: "bg-[#FDE7E6]", iconColor: "text-[#B42318]",
    bg: "bg-[#FFF8F7]", label: "Critical", accent: BRAND.destructive,
  },
  warning: {
    Icon: AlertTriangle, soft: "bg-[#FDF0DC]", iconColor: "text-[#B54708]",
    bg: "bg-[#FFFBF5]", label: "Warning", accent: BRAND.warning,
  },
  info: {
    Icon: Info, soft: "bg-[#E8F1FE]", iconColor: "text-[#2E90FA]",
    bg: "bg-[#F7FAFF]", label: "Info", accent: BRAND.primary,
  },
};

/** Unknown severities read as info, as CC's `SEV[a.severity] || SEV.info`. */
export const severityStyle = (severity: string): SeverityStyle =>
  (SEVERITY as Record<string, SeverityStyle>)[severity] ?? SEVERITY.info;
