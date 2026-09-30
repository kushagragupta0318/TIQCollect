// What a bank user reads for the API's enum codes. Pure (type-only import of
// api/bank.ts), so page logic can use it without pulling in axios.
import type { AGENCY_STATUSES, LOAN_TYPES } from "@/api/bank";

export const LOAN_TYPE_LABELS: Record<(typeof LOAN_TYPES)[number], string> = {
  HOME: "Home loan",
  AUTO: "Auto loan",
  PERSONAL: "Personal loan",
  BUSINESS: "Business loan",
  GOLD: "Gold loan",
  CREDIT_CARD: "Credit card",
  EDUCATION: "Education loan",
  MICROFINANCE: "Microfinance",
};

export const AGENCY_STATUS_LABELS: Record<(typeof AGENCY_STATUSES)[number], string> = {
  PENDING: "Onboarding",
  ACTIVE: "Active",
  SUSPENDED: "Suspended",
  OFFBOARDED: "Offboarded",
};

/** AgencyContract.status (backend/app/models/tenancy.py CONTRACT_STATUSES). */
export const CONTRACT_STATUS_LABELS: Record<string, string> = {
  DRAFT: "Draft",
  ACTIVE: "Active",
  EXPIRED: "Expired",
  TERMINATED: "Terminated",
};

/** A label for any code, falling back to the code itself rather than hiding it. */
export function labelFor(map: Record<string, string>, code: string): string {
  return map[code] ?? code;
}
