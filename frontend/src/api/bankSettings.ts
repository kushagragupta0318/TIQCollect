// The bank portal's Admin → Settings page: GET /bank/settings and
// PATCH /bank/settings (backend/app/api/v1/endpoints/bank_settings.py,
// schemas/bank_settings.py).
//
// Editable fields are this bank's stored policy; engine constants and DPD
// buckets are read-only and come from the engine's own config — the page shows
// which is which and never lets a read-only value be edited.
import api from "./axios";

export interface BankInfo {
  id: string;
  display_name: string;
  timezone: string;
}

export interface EditableField {
  value: number;
  default: number;
  is_override: boolean;
  editable: boolean;
}

export interface ContactHours {
  start: number;
  end: number;
  default_start: number;
  default_end: number;
  is_override: boolean;
  editable: boolean;
}

export interface EngineConstant {
  key: string;
  label: string;
  value: number;
  unit: string;
  description: string;
  editable: boolean;
}

export interface DpdBucketRow {
  bucket: string;
  label: string;
  min_dpd: number;
  /** null = the open-ended top bucket (NPA). */
  max_dpd: number | null;
}

export interface BankSettings {
  bank: BankInfo;
  contact_hours: ContactHours;
  geofence_metres: EditableField;
  sla_first_visit_days: EditableField;
  engine_constants: EngineConstant[];
  dpd_buckets: DpdBucketRow[];
  note: string;
}

export interface UpdateBankSettings {
  contact_hour_start?: number;
  contact_hour_end?: number;
  geofence_metres?: number;
  sla_first_visit_days?: number;
}

export async function getBankSettings(): Promise<BankSettings> {
  const { data } = await api.get<BankSettings>("/bank/settings");
  return data;
}

export async function updateBankSettings(body: UpdateBankSettings): Promise<BankSettings> {
  const { data } = await api.patch<BankSettings>("/bank/settings", body);
  return data;
}
