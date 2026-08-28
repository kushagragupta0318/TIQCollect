import { clsx } from "clsx";
import type { DPDBucket, CasePriority, AgentTier, CaseStatus, RiskCategory, RecoveryPotential } from "@/types";

interface BadgeProps {
  children: React.ReactNode;
  variant?: "red" | "orange" | "yellow" | "green" | "purple" | "blue" | "gray";
  className?: string;
}

export function Badge({ children, variant = "gray", className }: BadgeProps) {
  return (
    <span className={clsx(`badge badge-${variant}`, className)}>{children}</span>
  );
}

export function DPDBadge({ bucket }: { bucket: DPDBucket }) {
  const map: Record<DPDBucket, { label: string; variant: BadgeProps["variant"] }> = {
    CURRENT: { label: "Current", variant: "green" },
    BUCKET_1: { label: "1–30 DPD", variant: "yellow" },
    BUCKET_2: { label: "31–60 DPD", variant: "orange" },
    BUCKET_3: { label: "61–90 DPD", variant: "red" },
    NPA: { label: "NPA 90+", variant: "purple" },
  };
  const { label, variant } = map[bucket];
  return <Badge variant={variant}>{label}</Badge>;
}

/**
 * How much of this loan we expect to get back — HIGH / MEDIUM / LOW.
 *
 * `null` renders "Not scored" rather than falling back to LOW. An unscored loan
 * is unknown, and a grey chip that reads as the worst band would quietly write
 * off money nobody has looked at.
 *
 * Deliberately NOT coloured on the same scale as PriorityBadge: there, HIGH is
 * bad and red. Here HIGH is good, so it is green. Sharing a palette across two
 * opposite meanings is how a manager ends up reading the wrong column.
 */
export function RecoveryBadge({ potential, compact = false }: {
  potential: RecoveryPotential | null | undefined;
  /** Table cells are one column wide; the column header supplies the noun. */
  compact?: boolean;
}) {
  if (!potential) return <Badge variant="gray">{compact ? "—" : "Not scored"}</Badge>;
  const map: Record<RecoveryPotential, { label: string; short: string; variant: BadgeProps["variant"] }> = {
    HIGH: { label: "High recovery", short: "High", variant: "green" },
    MEDIUM: { label: "Medium recovery", short: "Medium", variant: "yellow" },
    LOW: { label: "Low recovery", short: "Low", variant: "red" },
  };
  const { label, short, variant } = map[potential];
  return <Badge variant={variant}>{compact ? short : label}</Badge>;
}

export function PriorityBadge({ priority }: { priority: CasePriority }) {
  const map: Record<CasePriority, { label: string; variant: BadgeProps["variant"] }> = {
    LOW: { label: "Low", variant: "green" },
    MEDIUM: { label: "Medium", variant: "blue" },
    HIGH: { label: "High", variant: "orange" },
    CRITICAL: { label: "Critical", variant: "red" },
  };
  const { label, variant } = map[priority];
  return <Badge variant={variant}>{label}</Badge>;
}

export function TierBadge({ tier }: { tier: AgentTier }) {
  const map: Record<AgentTier, { label: string; variant: BadgeProps["variant"] }> = {
    TIER_1: { label: "Tier 1", variant: "green" },
    TIER_2: { label: "Tier 2", variant: "blue" },
    TIER_3: { label: "Tier 3", variant: "gray" },
  };
  const { label, variant } = map[tier];
  return <Badge variant={variant}>{label}</Badge>;
}

export function CaseStatusBadge({ status, ptpDueToday }: { status: CaseStatus; ptpDueToday?: boolean }) {
  const map: Record<CaseStatus, { label: string; variant: BadgeProps["variant"] }> = {
    UNASSIGNED: { label: "Unassigned", variant: "gray" },
    ASSIGNED: { label: "Assigned", variant: "blue" },
    IN_PROGRESS: { label: "In Progress", variant: "yellow" },
    PTP_SET: { label: "PTP Set", variant: "orange" },
    PARTIALLY_PAID: { label: "Partial", variant: "blue" },
    PAID: { label: "Paid", variant: "green" },
    ESCALATED: { label: "Escalated", variant: "red" },
    CLOSED: { label: "Closed", variant: "gray" },
    WRITTEN_OFF: { label: "Written Off", variant: "purple" },
  };
  if (status === "PTP_SET" && ptpDueToday) {
    return <Badge variant="red">PTP Due</Badge>;
  }
  const { label, variant } = map[status];
  return <Badge variant={variant}>{label}</Badge>;
}

export function RiskBadge({ risk }: { risk: RiskCategory }) {
  const map: Record<RiskCategory, { label: string; variant: BadgeProps["variant"] }> = {
    LOW: { label: "Low Risk", variant: "green" },
    MEDIUM: { label: "Med Risk", variant: "yellow" },
    HIGH: { label: "High Risk", variant: "orange" },
    CRITICAL: { label: "Critical", variant: "red" },
  };
  const { label, variant } = map[risk];
  return <Badge variant={variant}>{label}</Badge>;
}
