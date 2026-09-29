// The bank portal's agency-onboarding API (P2 D01/D02 backend, D01-D03 UI).
//
// PLACEMENT. This lives in src/api/ (alongside manager.ts, agent.ts), not in
// src/bank/lib/ — bank/lib/ today holds only UI-primitive support (cn, cva,
// portal, useModalFocus), nothing API-shaped, and src/api/ is this repo's one
// established home for typed request/response wrappers around `api` from
// api/axios. Keeping API modules in one place regardless of which portal
// calls them matches the "one definition per rule" convention (ADR 0001)
// better than starting a second api/ directory under bank/.
//
// CONTRACT SOURCE. Every shape below is read off
// backend/app/api/v1/endpoints/bank_agencies_admin.py and
// backend/app/services/bank/agency_service.py (P2 D01/D02, already merged —
// this file does not touch backend/). DOC_TYPES / REQUIRED_DOC_TYPES are
// copied verbatim from backend/app/models/tenancy.py; LOAN_TYPES and
// DPD_BUCKETS from backend/app/models/loan.py's LoanType / DPDBucket enums.
import api from "./axios";

export const DOC_TYPES = [
  "INCORPORATION_CERT", "REGISTRATION_CERT", "AGREEMENT", "INSURANCE",
  "POLICE_VERIFICATION_POLICY", "PAN", "GST", "DRA_REGISTER", "OTHER",
] as const;
export type DocType = (typeof DOC_TYPES)[number];

/** The four document types verify_document/_maybe_activate require before an
 *  agency can go ACTIVE — see agency_service.REQUIRED_DOC_TYPES. */
export const REQUIRED_DOC_TYPES: readonly DocType[] = [
  "REGISTRATION_CERT", "AGREEMENT", "INSURANCE", "POLICE_VERIFICATION_POLICY",
];

export const DOC_TYPE_LABELS: Record<DocType, string> = {
  INCORPORATION_CERT: "Certificate of Incorporation",
  REGISTRATION_CERT: "Registration Certificate",
  AGREEMENT: "Signed Agreement",
  INSURANCE: "Insurance Policy",
  POLICE_VERIFICATION_POLICY: "Police Verification Policy",
  PAN: "PAN Card",
  GST: "GST Certificate",
  DRA_REGISTER: "DRA Register Entry",
  OTHER: "Other Document",
};

/** backend/app/models/loan.py LoanType. */
export const LOAN_TYPES = [
  "HOME", "AUTO", "PERSONAL", "BUSINESS", "GOLD", "CREDIT_CARD", "EDUCATION", "MICROFINANCE",
] as const;

/** backend/app/models/loan.py DPDBucket. */
export const DPD_BUCKETS = ["CURRENT", "BUCKET_1", "BUCKET_2", "BUCKET_3", "NPA"] as const;

/** Agency.entity_type has no DB check constraint (String(20), free text) —
 *  these are the values its own column comment names, offered as a Select
 *  with room to type something else via "OTHER". */
export const ENTITY_TYPES = ["PVT_LTD", "LLP", "PARTNERSHIP", "PROPRIETORSHIP", "PUBLIC_LTD", "OTHER"] as const;

/** backend/app/models/tenancy.py AGENCY_STATUSES — the directory's status filter. */
export const AGENCY_STATUSES = ["PENDING", "ACTIVE", "SUSPENDED", "OFFBOARDED"] as const;

/** backend/app/models/tenancy.py CONTRACT_STATUSES. */
export const CONTRACT_STATUSES = ["DRAFT", "ACTIVE", "EXPIRED", "TERMINATED"] as const;

export interface AgencyContact {
  role?: string;
  name?: string;
  email?: string;
  phone?: string;
}

export interface RegisteredAddress {
  line1?: string;
  line2?: string;
  city?: string;
  state?: string;
  pincode?: string;
}

/** The identity fields shared by create (step 1 submit) and update (step 1
 *  revisited) — CreateAgencyRequest / UpdateIdentityRequest are the same
 *  shape server-side. */
export interface AgencyIdentityFields {
  legal_name: string;
  trade_name?: string | null;
  entity_type?: string | null;
  cin?: string | null;
  rbi_registration_no?: string | null;
  pan?: string | null;
  gstin?: string | null;
  registered_address?: RegisteredAddress | null;
  hq_city?: string | null;
  website?: string | null;
  contacts?: AgencyContact[] | null;
  contact_name?: string | null;
  contact_email?: string | null;
  contact_phone?: string | null;
}

export interface Agency extends AgencyIdentityFields {
  agency_id: string;
  code: string;
  status: "PENDING" | "ACTIVE" | string;
  activated_at: string | null;
}

export interface AgencyContract {
  contract_id: string;
  contract_no: string;
  status: string;
  start_date: string;
  end_date: string;
  max_placed_cases: number | null;
  max_agents: number | null;
  max_visits_per_month: number | null;
  sla_first_visit_days: number | null;
  recall_no_activity_days: number | null;
  recall_on_sla_breach: boolean | null;
  recall_at_contract_end: boolean | null;
}

export interface ContractTerm {
  loan_type: string;
  dpd_bucket: string;
  commission_pct: number;
  fixed_fee_per_resolution?: number | null;
  is_authorised?: boolean;
}

export interface AgencyDocument {
  document_id: string;
  doc_type: string;
  file_name: string | null;
  content_type: string | null;
  size_bytes: number | null;
  status: "UPLOADED" | "VERIFIED" | "REJECTED" | "EXPIRED" | "SUPERSEDED";
  scan_status: "PENDING" | "CLEAN" | "INFECTED" | "ERROR";
  issued_on: string | null;
  expires_on: string | null;
  rejection_reason: string | null;
  uploaded_by: string | null;
  verified_by: string | null;
  verified_at: string | null;
}

/** GET /bank/agencies/{id} — the resume/review data source: everything
 *  collected so far, in one call. `documents` is newest-first per
 *  get_agency_detail's own ordering. */
export interface AgencyDetail extends Agency {
  contract: AgencyContract | null;
  region_ids: string[];
  documents: AgencyDocument[];
  required_doc_types: string[];
}

/**
 * GET /bank/agencies-directory row (D05, `agency_service.list_agency_directory`)
 * — one Agency plus its latest contract, covered regions and authorised
 * products, joined server-side so the directory table and coverage map need
 * one call, not one per agency.
 *
 * `score` IS DELIBERATELY ABSENT. Agency Performance Index is D06's, reads a
 * materialised view that does not exist yet — the backend does not null-fill
 * or fake it, and neither does this type. Render that column as pending.
 */
export interface AgencyDirectoryRow extends Agency {
  contract: AgencyContract | null;
  covered_regions: {
    region_id: string;
    name: string;
    /** ZONE | REGION | STATE | CITY */
    level: string;
    latitude: number | null;
    longitude: number | null;
  }[];
  /** Sorted LoanType values this agency is authorised for, server-side. */
  authorised_products: string[];
}

export interface AgencyDirectoryFilters {
  /** Hierarchy-aware server-side: a ZONE id also matches agencies covering
   *  anything beneath it (REGION/STATE/CITY) — see list_agency_directory. */
  region_id?: string;
  status?: string;
  loan_type?: string;
  /** YYYY-MM-DD. */
  contract_expiring_before?: string;
}

export interface Region {
  region_id: string;
  parent_id: string | null;
  /** ZONE | REGION | STATE | CITY */
  level: string;
  code: string;
  name: string;
  /** Materialised path, e.g. "/ncr/haryana/gurugram/" — usable to group/indent. */
  path: string;
  latitude: number | null;
  longitude: number | null;
}

export interface UpdateCoverageContractBody {
  start_date?: string;
  end_date?: string;
  max_placed_cases?: number;
  max_agents?: number;
  max_visits_per_month?: number;
  sla_first_visit_days?: number;
  recall_no_activity_days?: number;
  recall_on_sla_breach?: boolean;
  recall_at_contract_end?: boolean;
  performance_bonus_pct?: number;
  performance_target_pct?: number;
  security_deposit?: number;
  /** Replaces the whole set when sent — see the module docblock in
   *  bank_agencies_admin.py: omit the key entirely to leave it untouched. */
  region_ids?: string[];
  /** Same wholesale-replace rule as region_ids. */
  contract_terms?: ContractTerm[];
}

export interface PresignDocumentResult {
  upload_url: string;
  key: string;
  doc_type: string;
  /** Opaque, short-lived, signed — pass back exactly as received to
   *  confirmAgencyDocument. Binds this key to this (bank, agency, doc_type);
   *  confirm_document verifies it server-side rather than trusting a prefix
   *  match on the key alone (coordinator audit HIGH, backend 51d165b). */
  upload_token: string;
}

export interface ConfirmDocumentBody {
  doc_type: string;
  key: string;
  /** The upload_token from presignAgencyDocument's response — required. */
  upload_token: string;
  file_name?: string;
  issued_on?: string;
  expires_on?: string;
}

export interface InviteMasterLoginBody {
  full_name: string;
  email: string;
  phone: string;
  channel?: "LINK" | "SMS" | "EMAIL";
}

export interface InviteSummary {
  id: string;
  email: string;
  full_name: string;
  phone: string;
  role: string;
  purpose: string;
  bank_id: string | null;
  agency_id: string | null;
  delivery_channel: string;
  invited_by: string | null;
  created_at: string | null;
  expires_at: string;
  status: "OPEN" | "ACCEPTED" | "REVOKED" | "EXPIRED" | string;
}

export interface InviteMasterLoginResult {
  invite: InviteSummary;
  delivered: boolean | null;
  /** Present only for channel LINK — the one-time set-password link, shown
   *  once so the bank user can copy/share it. */
  token?: string;
  path?: string;
}

export async function createAgencyDraft(body: AgencyIdentityFields): Promise<Agency> {
  const { data } = await api.post<Agency>("/bank/agencies", body);
  return data;
}

export async function updateAgencyIdentity(agencyId: string, body: Partial<AgencyIdentityFields>): Promise<Agency> {
  const { data } = await api.patch<Agency>(`/bank/agencies/${agencyId}`, body);
  return data;
}

/** Steps 2 (Coverage) and 3 (Contract) both call this — they are one PATCH
 *  server-side. Send only the keys the current step owns; an omitted key
 *  (undefined, not present in the body) leaves that value untouched, so the
 *  two UI steps can save independently without clobbering each other. */
export async function updateCoverageContract(agencyId: string, body: UpdateCoverageContractBody): Promise<Agency> {
  const { data } = await api.patch<Agency>(`/bank/agencies/${agencyId}/coverage-contract`, body);
  return data;
}

export async function presignAgencyDocument(
  agencyId: string, docType: DocType, contentType: "application/pdf" | "image/jpeg" | "image/png",
): Promise<PresignDocumentResult> {
  const { data } = await api.post<PresignDocumentResult>(`/bank/agencies/${agencyId}/documents/presign`, {
    doc_type: docType, content_type: contentType,
  });
  return data;
}

export async function confirmAgencyDocument(agencyId: string, body: ConfirmDocumentBody): Promise<AgencyDocument> {
  const { data } = await api.post<AgencyDocument>(`/bank/agencies/${agencyId}/documents`, body);
  return data;
}

export async function inviteAgencyMasterLogin(agencyId: string, body: InviteMasterLoginBody): Promise<InviteMasterLoginResult> {
  const { data } = await api.post<InviteMasterLoginResult>(`/bank/agencies/${agencyId}/invite-master-login`, body);
  return data;
}

export async function getAgencyDetail(agencyId: string): Promise<AgencyDetail> {
  const { data } = await api.get<AgencyDetail>(`/bank/agencies/${agencyId}`);
  return data;
}

export async function listRegions(): Promise<Region[]> {
  const { data } = await api.get<Region[]>("/bank/regions");
  return data;
}

/**
 * The directory table's source (D05). All filters are optional and
 * server-side (region_id is hierarchy-aware, see AgencyDirectoryFilters) —
 * axios drops undefined params, so an unset filter is never sent, matching
 * the route's "omit entirely to mean no filter" contract. Callers should not
 * pass an empty string for an unset filter; leave the key out instead.
 */
export async function listAgencyDirectory(filters: AgencyDirectoryFilters = {}): Promise<AgencyDirectoryRow[]> {
  const { data } = await api.get<AgencyDirectoryRow[]>("/bank/agencies-directory", { params: filters });
  return data;
}

/**
 * GET /bank/agencies/{id} does not carry the master-login invite (confirmed
 * against agency_service.get_agency_detail, which returns agency + contract +
 * region_ids + documents + required_doc_types only — no invite). The only
 * route that reports an invite's status is the account-admin listing
 * (accounts.py's admin_router, mounted at /admin/invites), scoped server-side
 * to the caller's own bank. The Review step fetches this and filters to the
 * one whose agency_id matches and whose purpose is AGENCY_MASTER_LOGIN,
 * taking the most recently created if more than one exists (e.g. a withdrawn
 * invite followed by a fresh one).
 */
export async function listBankInvites(): Promise<InviteSummary[]> {
  const { data } = await api.get<InviteSummary[]>("/admin/invites");
  return data;
}
