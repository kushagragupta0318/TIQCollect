// Bank Alerts (task C06): GET /bank/alerts — a typed list of
// DecisionAlerts.tsx's own DecisionAlert shape, computed fresh per request
// from the bank's own book (tenant-scoped server side).
import api from "./axios";
import type { DecisionAlert } from "../bank/components/DecisionAlerts";

export async function fetchBankAlerts(): Promise<DecisionAlert[]> {
  return (await api.get<DecisionAlert[]>("/bank/alerts")).data;
}
