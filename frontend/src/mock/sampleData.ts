import type { Agent, Case, Customer, Loan, Beat, PTP } from "@/types";

// ─── Agent Users ────────────────────────────────────────────────────────────
export const DEMO_USERS = [
  { email: "agent001@tiqcollect.in", password: "Agent@123", role: "FIELD_AGENT" as const, user_id: "user-agent-001", full_name: "Rajesh Kumar" },
  { email: "agent002@tiqcollect.in", password: "Agent@123", role: "FIELD_AGENT" as const, user_id: "user-agent-002", full_name: "Priya Sharma" },
  { email: "manager1@tiqcollect.in", password: "Manager@123", role: "AGENCY_MANAGER" as const, user_id: "user-mgr-001", full_name: "Sanjay Mehta" },
  { email: "admin@tiqcollect.in", password: "Admin@123", role: "AGENCY_ADMIN" as const, user_id: "user-admin-001", full_name: "System Admin" },
];

// ─── Sample Customers ────────────────────────────────────────────────────────
export const SAMPLE_CUSTOMERS: Customer[] = [
  { id: "cust-001", customer_ref: "CUST000001", full_name: "Arvind Sharma",     phone_primary: "9821034567", city: "Gurugram", state: "Haryana", latitude: 28.4595, longitude: 77.0266, risk_category: "CRITICAL", risk_score: 88, cibil_score: 412, is_hostile: false, language_preference: "HINDI" },
  { id: "cust-002", customer_ref: "CUST000002", full_name: "Sunita Devi",       phone_primary: "9765432109", city: "Gurugram", state: "Haryana", latitude: 28.4748, longitude: 77.0820, risk_category: "HIGH",     risk_score: 72, cibil_score: 498, is_hostile: false, language_preference: "HINDI" },
  { id: "cust-003", customer_ref: "CUST000003", full_name: "Mohammed Rafiq",    phone_primary: "9654321098", city: "Gurugram", state: "Haryana", latitude: 28.4621, longitude: 77.0409, risk_category: "CRITICAL", risk_score: 91, cibil_score: 385, is_hostile: true,  language_preference: "HINDI" },
  { id: "cust-004", customer_ref: "CUST000004", full_name: "Geeta Kapoor",      phone_primary: "9543210987", city: "Gurugram", state: "Haryana", latitude: 28.4765, longitude: 77.0673, risk_category: "HIGH",     risk_score: 68, cibil_score: 521, is_hostile: false, language_preference: "HINDI" },
  { id: "cust-005", customer_ref: "CUST000005", full_name: "Ramesh Yadav",      phone_primary: "9432109876", city: "Gurugram", state: "Haryana", latitude: 28.4689, longitude: 77.0855, risk_category: "MEDIUM",   risk_score: 54, cibil_score: 601, is_hostile: false, language_preference: "HINDI" },
  { id: "cust-006", customer_ref: "CUST000006", full_name: "Rekha Malhotra",    phone_primary: "9321098765", city: "Gurugram", state: "Haryana", latitude: 28.4543, longitude: 77.0362, risk_category: "HIGH",     risk_score: 76, cibil_score: 477, is_hostile: false, language_preference: "HINDI" },
  { id: "cust-007", customer_ref: "CUST000007", full_name: "Deepak Verma",      phone_primary: "9210987654", city: "Gurugram", state: "Haryana", latitude: 28.4523, longitude: 77.1005, risk_category: "MEDIUM",   risk_score: 48, cibil_score: 635, is_hostile: false, language_preference: "HINDI" },
  { id: "cust-008", customer_ref: "CUST000008", full_name: "Anjali Singh",      phone_primary: "9109876543", city: "Gurugram", state: "Haryana", latitude: 28.4550, longitude: 77.1044, risk_category: "MEDIUM",   risk_score: 42, cibil_score: 658, is_hostile: false, language_preference: "HINDI" },
  { id: "cust-009", customer_ref: "CUST000009", full_name: "Suresh Rawat",      phone_primary: "9098765432", city: "Gurugram", state: "Haryana", latitude: 28.4471, longitude: 77.0789, risk_category: "CRITICAL", risk_score: 85, cibil_score: 421, is_hostile: false, language_preference: "HINDI" },
  { id: "cust-010", customer_ref: "CUST000010", full_name: "Meena Agarwal",     phone_primary: "8987654321", city: "Gurugram", state: "Haryana", latitude: 28.5155, longitude: 76.9908, risk_category: "LOW",      risk_score: 28, cibil_score: 714, is_hostile: false, language_preference: "HINDI" },
  { id: "cust-011", customer_ref: "CUST000011", full_name: "Vikram Malhotra",   phone_primary: "8876543210", city: "Gurugram", state: "Haryana", latitude: 28.4777, longitude: 77.0736, risk_category: "HIGH",     risk_score: 71, cibil_score: 503, is_hostile: false, language_preference: "HINDI" },
  { id: "cust-012", customer_ref: "CUST000012", full_name: "Pooja Chauhan",     phone_primary: "8765432109", city: "Gurugram", state: "Haryana", latitude: 28.4612, longitude: 77.0305, risk_category: "LOW",      risk_score: 22, cibil_score: 742, is_hostile: false, language_preference: "HINDI" },
  { id: "cust-013", customer_ref: "CUST000013", full_name: "Ravi Shankar",      phone_primary: "8654321098", city: "Gurugram", state: "Haryana", latitude: 28.4497, longitude: 77.1101, risk_category: "MEDIUM",   risk_score: 51, cibil_score: 612, is_hostile: false, language_preference: "HINDI" },
  { id: "cust-014", customer_ref: "CUST000014", full_name: "Fatima Ansari",     phone_primary: "8543210987", city: "Gurugram", state: "Haryana", latitude: 28.5043, longitude: 77.0824, risk_category: "LOW",      risk_score: 31, cibil_score: 698, is_hostile: false, language_preference: "HINDI" },
];

// ─── Sample Loans ────────────────────────────────────────────────────────────
export const SAMPLE_LOANS: Loan[] = [
  { id: "loan-001", loan_account_number: "LN100234567", loan_type: "PERSONAL", bank_name: "HDFC Bank", total_outstanding: 385000, overdue_amount: 72500, emi_amount: 8200, dpd: 127, dpd_bucket: "NPA", status: "NPA", next_due_date: "2024-10-01", collection_priority_score: 94 },
  { id: "loan-002", loan_account_number: "LN100345678", loan_type: "HOME", bank_name: "SBI", total_outstanding: 2150000, overdue_amount: 126000, emi_amount: 42000, dpd: 97, dpd_bucket: "NPA", status: "NPA", next_due_date: "2024-10-05", collection_priority_score: 91 },
  { id: "loan-003", loan_account_number: "LN100456789", loan_type: "PERSONAL", bank_name: "ICICI Bank", total_outstanding: 210000, overdue_amount: 54000, emi_amount: 6800, dpd: 143, dpd_bucket: "NPA", status: "NPA", next_due_date: "2024-09-28", collection_priority_score: 96 },
  { id: "loan-004", loan_account_number: "LN100567890", loan_type: "AUTO", bank_name: "Axis Bank", total_outstanding: 485000, overdue_amount: 38500, emi_amount: 12500, dpd: 72, dpd_bucket: "BUCKET_3", status: "ACTIVE", next_due_date: "2024-11-10", collection_priority_score: 78 },
  { id: "loan-005", loan_account_number: "LN100678901", loan_type: "BUSINESS", bank_name: "Kotak Mahindra Bank", total_outstanding: 950000, overdue_amount: 85000, emi_amount: 31000, dpd: 68, dpd_bucket: "BUCKET_3", status: "ACTIVE", next_due_date: "2024-11-12", collection_priority_score: 82 },
  { id: "loan-006", loan_account_number: "LN100789012", loan_type: "PERSONAL", bank_name: "IndusInd Bank", total_outstanding: 145000, overdue_amount: 29000, emi_amount: 4500, dpd: 82, dpd_bucket: "BUCKET_3", status: "ACTIVE", next_due_date: "2024-11-08", collection_priority_score: 75 },
  { id: "loan-007", loan_account_number: "LN100890123", loan_type: "PERSONAL", bank_name: "HDFC Bank", total_outstanding: 88000, overdue_amount: 17600, emi_amount: 3200, dpd: 45, dpd_bucket: "BUCKET_2", status: "ACTIVE", next_due_date: "2024-11-20", collection_priority_score: 52 },
  { id: "loan-008", loan_account_number: "LN100901234", loan_type: "GOLD", bank_name: "SBI", total_outstanding: 55000, overdue_amount: 11000, emi_amount: 2800, dpd: 38, dpd_bucket: "BUCKET_2", status: "ACTIVE", next_due_date: "2024-11-18", collection_priority_score: 44 },
  { id: "loan-009", loan_account_number: "LN101012345", loan_type: "PERSONAL", bank_name: "ICICI Bank", total_outstanding: 320000, overdue_amount: 48000, emi_amount: 9800, dpd: 91, dpd_bucket: "NPA", status: "NPA", next_due_date: "2024-10-02", collection_priority_score: 89 },
  { id: "loan-010", loan_account_number: "LN101123456", loan_type: "MICROFINANCE", bank_name: "Bandhan Bank", total_outstanding: 18500, overdue_amount: 3700, emi_amount: 1850, dpd: 22, dpd_bucket: "BUCKET_1", status: "ACTIVE", next_due_date: "2024-12-01", collection_priority_score: 28 },
  { id: "loan-011", loan_account_number: "LN101234567", loan_type: "HOME", bank_name: "HDFC Bank", total_outstanding: 1250000, overdue_amount: 62500, emi_amount: 28000, dpd: 65, dpd_bucket: "BUCKET_3", status: "ACTIVE", next_due_date: "2024-11-15", collection_priority_score: 74 },
  { id: "loan-012", loan_account_number: "LN101345678", loan_type: "PERSONAL", bank_name: "Axis Bank", total_outstanding: 42000, overdue_amount: 8400, emi_amount: 2100, dpd: 18, dpd_bucket: "BUCKET_1", status: "ACTIVE", next_due_date: "2024-12-05", collection_priority_score: 24 },
  { id: "loan-013", loan_account_number: "LN101456789", loan_type: "CREDIT_CARD", bank_name: "ICICI Bank", total_outstanding: 68000, overdue_amount: 13600, emi_amount: 5000, dpd: 55, dpd_bucket: "BUCKET_2", status: "ACTIVE", next_due_date: "2024-11-22", collection_priority_score: 58 },
  { id: "loan-014", loan_account_number: "LN101567890", loan_type: "EDUCATION", bank_name: "SBI", total_outstanding: 125000, overdue_amount: 12500, emi_amount: 4200, dpd: 14, dpd_bucket: "BUCKET_1", status: "ACTIVE", next_due_date: "2024-12-08", collection_priority_score: 19 },
];

// ─── Sample Cases for agent-001 (Rajesh Kumar) ───────────────────────────────
export const SAMPLE_CASES: Case[] = [
  { id: "case-001", case_number: "CASE0010001", customer: SAMPLE_CUSTOMERS[0], loan: SAMPLE_LOANS[0], agent_id: "agent-001", status: "ESCALATED", priority: "CRITICAL", target_amount: 72500, collected_amount: 0, allocation_date: new Date().toISOString().split("T")[0], visit_count: 3, is_escalated: true },
  { id: "case-002", case_number: "CASE0010002", customer: SAMPLE_CUSTOMERS[1], loan: SAMPLE_LOANS[1], agent_id: "agent-001", status: "PTP_SET", priority: "CRITICAL", target_amount: 126000, collected_amount: 0, allocation_date: new Date().toISOString().split("T")[0], visit_count: 2, is_escalated: false },
  { id: "case-003", case_number: "CASE0010003", customer: SAMPLE_CUSTOMERS[2], loan: SAMPLE_LOANS[2], agent_id: "agent-001", status: "IN_PROGRESS", priority: "CRITICAL", target_amount: 54000, collected_amount: 0, allocation_date: new Date().toISOString().split("T")[0], visit_count: 1, is_escalated: false },
  { id: "case-004", case_number: "CASE0010004", customer: SAMPLE_CUSTOMERS[3], loan: SAMPLE_LOANS[3], agent_id: "agent-001", status: "ASSIGNED", priority: "HIGH", target_amount: 38500, collected_amount: 0, allocation_date: new Date().toISOString().split("T")[0], visit_count: 0, is_escalated: false },
  { id: "case-005", case_number: "CASE0010005", customer: SAMPLE_CUSTOMERS[4], loan: SAMPLE_LOANS[4], agent_id: "agent-001", status: "IN_PROGRESS", priority: "HIGH", target_amount: 85000, collected_amount: 0, allocation_date: new Date().toISOString().split("T")[0], visit_count: 2, is_escalated: false },
  { id: "case-006", case_number: "CASE0010006", customer: SAMPLE_CUSTOMERS[5], loan: SAMPLE_LOANS[5], agent_id: "agent-001", status: "ASSIGNED", priority: "HIGH", target_amount: 29000, collected_amount: 0, allocation_date: new Date().toISOString().split("T")[0], visit_count: 0, is_escalated: false },
  { id: "case-007", case_number: "CASE0010007", customer: SAMPLE_CUSTOMERS[6], loan: SAMPLE_LOANS[6], agent_id: "agent-001", status: "PARTIALLY_PAID", priority: "MEDIUM", target_amount: 17600, collected_amount: 8800, allocation_date: new Date().toISOString().split("T")[0], visit_count: 1, is_escalated: false },
  { id: "case-008", case_number: "CASE0010008", customer: SAMPLE_CUSTOMERS[7], loan: SAMPLE_LOANS[7], agent_id: "agent-001", status: "ASSIGNED", priority: "MEDIUM", target_amount: 11000, collected_amount: 0, allocation_date: new Date().toISOString().split("T")[0], visit_count: 0, is_escalated: false },
  { id: "case-009", case_number: "CASE0010009", customer: SAMPLE_CUSTOMERS[8], loan: SAMPLE_LOANS[8], agent_id: "agent-001", status: "PTP_SET", priority: "CRITICAL", target_amount: 48000, collected_amount: 0, allocation_date: new Date().toISOString().split("T")[0], visit_count: 2, is_escalated: false },
  { id: "case-010", case_number: "CASE0010010", customer: SAMPLE_CUSTOMERS[9], loan: SAMPLE_LOANS[9], agent_id: "agent-001", status: "PAID", priority: "LOW", target_amount: 3700, collected_amount: 3700, allocation_date: new Date().toISOString().split("T")[0], visit_count: 1, is_escalated: false },
  { id: "case-011", case_number: "CASE0010011", customer: SAMPLE_CUSTOMERS[10], loan: SAMPLE_LOANS[10], agent_id: "agent-001", status: "ASSIGNED", priority: "HIGH", target_amount: 62500, collected_amount: 0, allocation_date: new Date().toISOString().split("T")[0], visit_count: 0, is_escalated: false },
  { id: "case-012", case_number: "CASE0010012", customer: SAMPLE_CUSTOMERS[11], loan: SAMPLE_LOANS[11], agent_id: "agent-001", status: "IN_PROGRESS", priority: "LOW", target_amount: 8400, collected_amount: 0, allocation_date: new Date().toISOString().split("T")[0], visit_count: 1, is_escalated: false },
  { id: "case-013", case_number: "CASE0010013", customer: SAMPLE_CUSTOMERS[12], loan: SAMPLE_LOANS[12], agent_id: "agent-001", status: "ASSIGNED", priority: "MEDIUM", target_amount: 13600, collected_amount: 0, allocation_date: new Date().toISOString().split("T")[0], visit_count: 0, is_escalated: false },
  { id: "case-014", case_number: "CASE0010014", customer: SAMPLE_CUSTOMERS[13], loan: SAMPLE_LOANS[13], agent_id: "agent-001", status: "ASSIGNED", priority: "LOW", target_amount: 12500, collected_amount: 0, allocation_date: new Date().toISOString().split("T")[0], visit_count: 0, is_escalated: false },
];

// ─── Sample Beat (today's route) ─────────────────────────────────────────────
export const SAMPLE_BEAT: Beat = {
  id: "beat-today-001",
  beat_date: new Date().toISOString().split("T")[0],
  beat_number: `BEAT-${new Date().toISOString().split("T")[0].replace(/-/g, "")}-AGT001`,
  ordered_case_ids: ["case-003", "case-001", "case-002", "case-004", "case-009", "case-005", "case-006", "case-011", "case-007", "case-008", "case-013", "case-012", "case-014", "case-010"],
  total_cases: 14,
  estimated_distance_km: 34.6,
  estimated_duration_minutes: 310,
  total_target_amount: 527200,
  status: "PLANNED",
  cases_completed: 3,
  amount_collected: 12500,
};

// ─── Sample PTPs ──────────────────────────────────────────────────────────────
export const SAMPLE_PTPS: PTP[] = [
  { id: "ptp-001", committed_amount: 126000, committed_date: new Date().toISOString().split("T")[0], status: "ACTIVE", customer_reason: "Salary pending from employer, will pay by end of month" },
  { id: "ptp-002", committed_amount: 48000, committed_date: new Date(Date.now() + 2 * 86400000).toISOString().split("T")[0], status: "ACTIVE", customer_reason: "Family function expenses, will arrange funds in 2 days" },
];

// ─── Sample Visits ────────────────────────────────────────────────────────────
export const SAMPLE_VISITS = [
  { id: "visit-001", case_id: "case-001", check_in_time: new Date(Date.now() - 3 * 86400000).toISOString(), outcome: "CUSTOMER_REFUSED", customer_met: true, notes: "Customer refused to pay. Claims bank gave wrong EMI amount. Needs manager attention.", visit_number: 1, geo_verified: true },
  { id: "visit-002", case_id: "case-001", check_in_time: new Date(Date.now() - 2 * 86400000).toISOString(), outcome: "CUSTOMER_HOSTILE", customer_met: true, notes: "Customer became aggressive. Neighbours present. Recommend police complaint.", visit_number: 2, geo_verified: true },
  { id: "visit-003", case_id: "case-001", check_in_time: new Date(Date.now() - 1 * 86400000).toISOString(), outcome: "CUSTOMER_REFUSED", customer_met: true, notes: "Still refusing. Escalated to manager.", visit_number: 3, geo_verified: true },
  { id: "visit-004", case_id: "case-002", check_in_time: new Date(Date.now() - 2 * 86400000).toISOString(), outcome: "PTP_SET", customer_met: true, notes: "Customer cooperative. Promised to pay today.", visit_number: 1, geo_verified: true },
  { id: "visit-005", case_id: "case-002", check_in_time: new Date(Date.now() - 1 * 86400000).toISOString(), outcome: "PTP_SET", customer_met: true, notes: "PTP broken. Rescheduled to today. Husband to arrange funds.", visit_number: 2, geo_verified: true },
  { id: "visit-006", case_id: "case-003", check_in_time: new Date(Date.now() - 1 * 86400000).toISOString(), outcome: "CUSTOMER_NOT_MET", customer_met: false, notes: "Premises locked. Neighbor says family went out. Will revisit tomorrow.", visit_number: 1, geo_verified: true },
  { id: "visit-007", case_id: "case-005", check_in_time: new Date(Date.now() - 3 * 86400000).toISOString(), outcome: "CUSTOMER_NOT_MET", customer_met: false, notes: "Shop closed. Left notice.", visit_number: 1, geo_verified: true },
  { id: "visit-008", case_id: "case-005", check_in_time: new Date(Date.now() - 1 * 86400000).toISOString(), outcome: "REVISIT_REQUIRED", customer_met: true, notes: "Met owner. Claims business losses. Will pay partial amount this week.", visit_number: 2, geo_verified: true },
  { id: "visit-009", case_id: "case-007", check_in_time: new Date(Date.now() - 2 * 86400000).toISOString(), outcome: "PARTIAL_PAYMENT_PTP", customer_met: true, notes: "Collected ₹8,800 cash. Balance PTP set for next week.", visit_number: 1, geo_verified: true },
  { id: "visit-010", case_id: "case-009", check_in_time: new Date(Date.now() - 3 * 86400000).toISOString(), outcome: "CUSTOMER_NOT_MET", customer_met: false, notes: "House locked.", visit_number: 1, geo_verified: false },
  { id: "visit-011", case_id: "case-009", check_in_time: new Date(Date.now() - 1 * 86400000).toISOString(), outcome: "PTP_SET", customer_met: true, notes: "Customer met. Son to arrange payment. PTP set for 2 days.", visit_number: 2, geo_verified: true },
  { id: "visit-012", case_id: "case-010", check_in_time: new Date(Date.now() - 1 * 86400000).toISOString(), outcome: "PAYMENT_COLLECTED", customer_met: true, notes: "Full amount collected ₹3,700 via UPI. Case closed.", visit_number: 1, geo_verified: true },
  { id: "visit-013", case_id: "case-012", check_in_time: new Date(Date.now() - 1 * 86400000).toISOString(), outcome: "REVISIT_REQUIRED", customer_met: true, notes: "Customer on medical leave. Has hospital bills. Needs 1 week.", visit_number: 1, geo_verified: true },
];

// ─── Sample Payments ──────────────────────────────────────────────────────────
export const SAMPLE_PAYMENTS = [
  { id: "pay-001", case_id: "case-007", amount: 8800, mode: "CASH", status: "VERIFIED", receipt_number: "RCP88457821", payment_date: new Date(Date.now() - 2 * 86400000).toISOString(), agent_id: "agent-001" },
  { id: "pay-002", case_id: "case-010", amount: 3700, mode: "UPI", status: "VERIFIED", receipt_number: "RCP99241637", payment_date: new Date(Date.now() - 1 * 86400000).toISOString(), agent_id: "agent-001", upi_reference: "TXN241115002341" },
];

// ─── Manager View: All 50 Agents ─────────────────────────────────────────────
export const ALL_AGENTS: Agent[] = [
  { id: "agent-001", user_id: "user-agent-001", employee_code: "EMP0001", id_card_number: "TIQID00001", full_name: "Rajesh Kumar", territory: "Gurugram Central", status: "ON_DUTY", tier: "TIER_1", ranking_score: 87.4, current_month_visits: 147, current_month_collections: 485000, current_month_ptps_set: 38, current_month_ptps_honored: 29, lifetime_collection_rate: 0.74, last_known_latitude: 28.4595, last_known_longitude: 77.0266, sos_active: false, max_cases_per_day: 15, specialization: "BOTH", languages_spoken: ["HINDI", "ENGLISH"] },
  { id: "agent-002", user_id: "user-agent-002", employee_code: "EMP0002", id_card_number: "TIQID00002", full_name: "Priya Sharma", territory: "Delhi West", status: "ON_DUTY", tier: "TIER_1", ranking_score: 91.2, current_month_visits: 165, current_month_collections: 542000, current_month_ptps_set: 42, current_month_ptps_honored: 36, lifetime_collection_rate: 0.81, last_known_latitude: 28.6139, last_known_longitude: 77.2090, sos_active: false, max_cases_per_day: 15, specialization: "UNSECURED", languages_spoken: ["HINDI", "ENGLISH"] },
  { id: "agent-003", user_id: "user-agent-003", employee_code: "EMP0003", id_card_number: "TIQID00003", full_name: "Amit Singh", territory: "Pune East", status: "ON_DUTY", tier: "TIER_2", ranking_score: 67.8, current_month_visits: 132, current_month_collections: 378000, current_month_ptps_set: 28, current_month_ptps_honored: 21, lifetime_collection_rate: 0.68, last_known_latitude: 18.5204, last_known_longitude: 73.8567, sos_active: false, max_cases_per_day: 14, specialization: "SECURED", languages_spoken: ["HINDI", "MARATHI"] },
  { id: "agent-004", user_id: "user-agent-004", employee_code: "EMP0004", id_card_number: "TIQID00004", full_name: "Sunita Devi", territory: "Lucknow Central", status: "ON_DUTY", tier: "TIER_2", ranking_score: 61.3, current_month_visits: 98, current_month_collections: 291000, current_month_ptps_set: 22, current_month_ptps_honored: 15, lifetime_collection_rate: 0.61, last_known_latitude: 26.8467, last_known_longitude: 80.9462, sos_active: false, max_cases_per_day: 12, specialization: "BOTH", languages_spoken: ["HINDI"] },
  { id: "agent-005", user_id: "user-agent-005", employee_code: "EMP0005", id_card_number: "TIQID00005", full_name: "Vikram Patel", territory: "Ahmedabad South", status: "ON_DUTY", tier: "TIER_1", ranking_score: 84.6, current_month_visits: 156, current_month_collections: 481000, current_month_ptps_set: 35, current_month_ptps_honored: 28, lifetime_collection_rate: 0.79, last_known_latitude: 23.0225, last_known_longitude: 72.5714, sos_active: false, max_cases_per_day: 15, specialization: "BOTH", languages_spoken: ["GUJARATI", "HINDI"] },
  { id: "agent-006", user_id: "user-agent-006", employee_code: "EMP0006", id_card_number: "TIQID00006", full_name: "Neha Gupta", territory: "Kolkata North", status: "OFF_DUTY", tier: "TIER_3", ranking_score: 34.1, current_month_visits: 67, current_month_collections: 145000, current_month_ptps_set: 14, current_month_ptps_honored: 9, lifetime_collection_rate: 0.42, last_known_latitude: 22.5726, last_known_longitude: 88.3639, sos_active: false, max_cases_per_day: 10, specialization: "UNSECURED", languages_spoken: ["BENGALI", "HINDI"] },
  { id: "agent-007", user_id: "user-agent-007", employee_code: "EMP0007", id_card_number: "TIQID00007", full_name: "Suresh Rao", territory: "Hyderabad Central", status: "ON_DUTY", tier: "TIER_2", ranking_score: 55.7, current_month_visits: 112, current_month_collections: 312000, current_month_ptps_set: 26, current_month_ptps_honored: 19, lifetime_collection_rate: 0.65, last_known_latitude: 17.3850, last_known_longitude: 78.4867, sos_active: false, max_cases_per_day: 13, specialization: "BOTH", languages_spoken: ["TELUGU", "HINDI"] },
  { id: "agent-008", user_id: "user-agent-008", employee_code: "EMP0008", id_card_number: "TIQID00008", full_name: "Anjali Mishra", territory: "Chennai West", status: "ON_DUTY", tier: "TIER_1", ranking_score: 78.9, current_month_visits: 143, current_month_collections: 425000, current_month_ptps_set: 33, current_month_ptps_honored: 27, lifetime_collection_rate: 0.74, last_known_latitude: 13.0827, last_known_longitude: 80.2707, sos_active: false, max_cases_per_day: 14, specialization: "SECURED", languages_spoken: ["TAMIL", "ENGLISH"] },
  ...Array.from({ length: 42 }, (_, i) => ({
    id: `agent-${(i + 9).toString().padStart(3, "0")}`,
    user_id: `user-agent-${(i + 9).toString().padStart(3, "0")}`,
    employee_code: `EMP${(i + 9).toString().padStart(4, "0")}`,
    id_card_number: `TIQID${(i + 9).toString().padStart(5, "0")}`,
    full_name: ["Ravi Verma", "Kavitha Nair", "Mohan Das", "Lakshmi Reddy", "Arjun Mehta", "Divya Singh", "Kiran Rao", "Sanjay Gupta", "Meena Iyer", "Rahul Joshi", "Deepak Yadav", "Smita More", "Nitin Kulkarni", "Aisha Khan", "Renu Tiwari", "Satish Pawar", "Geetha Krishnan", "Rajiv Nair", "Seema Yadav", "Pavan Kumar", "Sushma Reddy", "Ashok Mishra", "Babita Singh", "Chetan Sharma", "Dinesh Patel", "Ekta Verma", "Farhan Sheikh", "Geeta Rao", "Harish Kumar", "Indira Gandhi", "Jagdish Singh", "Kamala Devi", "Lalit Mehta", "Mona Joshi", "Neeraj Gupta", "Omkar Patil", "Puja Agarwal", "Qasim Ali", "Rashmi Nair", "Santosh Yadav", "Tanvi Shah", "Uma Sharma"][i % 42],
    territory: ["Bangalore North", "Jaipur East", "Bhopal", "Nagpur", "Surat", "Vadodara", "Indore", "Coimbatore", "Visakhapatnam", "Kochi", "Patna", "Ranchi"][i % 12],
    status: i % 5 === 0 ? "OFF_DUTY" as const : "ON_DUTY" as const,
    tier: (["TIER_1", "TIER_2", "TIER_3", "TIER_2", "TIER_1"] as const)[i % 5],
    ranking_score: Math.round((30 + (i % 65)) * 10) / 10,
    current_month_visits: 50 + (i % 130),
    current_month_collections: 80000 + (i % 450000),
    current_month_ptps_set: 8 + (i % 35),
    current_month_ptps_honored: 5 + (i % 28),
    lifetime_collection_rate: Math.round((0.35 + (i % 50) / 100) * 100) / 100,
    last_known_latitude: 18 + (i % 10),
    last_known_longitude: 73 + (i % 15),
    sos_active: false,
    max_cases_per_day: 10 + (i % 6),
    specialization: "BOTH" as const,
    languages_spoken: ["HINDI", "ENGLISH"],
  })),
];
