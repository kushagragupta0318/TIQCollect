// Bank Alerts (task C06): GET /bank/alerts — computed fresh per request
// from the bank's own book (tenant-scoped server side). rules_total/
// rules_failed exist so the page can say "N of 6 rules couldn't be
// evaluated" instead of reading a crashed rule as "nothing is firing".
import api from "./axios";
import type { DecisionAlert } from "../bank/components/DecisionAlerts";

export interface BankAlertsPayload {
  alerts: DecisionAlert[];
  rules_total: number;
  rules_failed: string[];
}

export async function fetchBankAlerts(): Promise<BankAlertsPayload> {
  return (await api.get<BankAlertsPayload>("/bank/alerts")).data;
}
