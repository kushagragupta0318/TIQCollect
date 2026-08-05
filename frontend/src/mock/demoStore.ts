import { create } from "zustand";
import { persist } from "zustand/middleware";
import type { Case, Agent } from "@/types";
import {
  SAMPLE_CASES, SAMPLE_VISITS, SAMPLE_PAYMENTS, SAMPLE_PTPS,
  SAMPLE_BEAT, ALL_AGENTS,
} from "./sampleData";

export interface VisitRecord {
  id: string;
  case_id: string;
  check_in_time: string;
  check_out_time?: string;
  outcome: string;
  customer_met: boolean;
  notes: string;
  visit_number: number;
  geo_verified: boolean;
  not_met_reason?: string;
  selfie_url?: string;
  premises_photo_url?: string;
  receipt_photo_url?: string;
}

export interface PaymentRecord {
  id: string;
  case_id: string;
  amount: number;
  mode: string;
  status: string;
  receipt_number: string;
  payment_date: string;
  agent_id: string;
  upi_reference?: string;
  receipt_photo_url?: string;
}

export interface PTPRecord {
  id: string;
  case_id: string;
  committed_amount: number;
  committed_date: string;
  status: string;
  customer_reason: string;
  agent_notes?: string;
}

interface DemoState {
  // Agent state
  checkedIn: boolean;
  checkInTime: string | null;
  checkedOut: boolean;
  onDuty: boolean;
  selfieUrl: string | null;
  sosActive: boolean;
  currentLocation: { lat: number; lon: number } | null;

  // Case data (mutable)
  cases: Case[];
  visits: VisitRecord[];
  payments: PaymentRecord[];
  ptps: PTPRecord[];
  beat: typeof SAMPLE_BEAT;

  // Alerts
  alerts: Array<{ id: string; type: string; message: string; case_id?: string; read: boolean }>;

  // Actions
  checkIn: (selfieUrl?: string) => void;
  checkOut: () => void;
  setSosActive: (active: boolean, location?: { lat: number; lon: number }) => void;
  setLocation: (lat: number, lon: number) => void;
  recordVisit: (visit: VisitRecord, caseUpdate: Partial<Case>) => void;
  addPayment: (payment: PaymentRecord, caseUpdate: Partial<Case>) => void;
  addPTP: (ptp: PTPRecord, caseUpdate: Partial<Case>) => void;
  markAlertRead: (id: string) => void;
  resetDemo: () => void;
}

const initialAlerts = [
  { id: "alert-001", type: "PTP_DUE", message: "PTP due today: Sunita Devi — ₹1,26,000", case_id: "case-002", read: false },
  { id: "alert-002", type: "PTP_DUE", message: "PTP due today: Suresh Babu — ₹48,000", case_id: "case-009", read: false },
  { id: "alert-003", type: "ESCALATION", message: "Case escalated: Arvind Sharma — Manager review required", case_id: "case-001", read: false },
  { id: "alert-004", type: "NEW_ALLOCATION", message: "14 cases allocated for today", read: false },
];

export const useDemoStore = create<DemoState>()(
  persist(
    (set) => ({
      checkedIn: false,
      checkInTime: null,
      checkedOut: false,
      onDuty: false,
      selfieUrl: null,
      sosActive: false,
      currentLocation: null,

      cases: SAMPLE_CASES,
      visits: SAMPLE_VISITS as VisitRecord[],
      payments: SAMPLE_PAYMENTS as PaymentRecord[],
      ptps: SAMPLE_PTPS as PTPRecord[],
      beat: SAMPLE_BEAT,
      alerts: initialAlerts,

      checkIn: (selfieUrl) =>
        set({ checkedIn: true, checkInTime: new Date().toISOString(), onDuty: true, selfieUrl: selfieUrl ?? null }),

      checkOut: () =>
        set({ checkedIn: false, checkedOut: true, onDuty: false }),

      setSosActive: (active, location) =>
        set({ sosActive: active, currentLocation: location ?? null }),

      setLocation: (lat, lon) =>
        set({ currentLocation: { lat, lon } }),

      recordVisit: (visit, caseUpdate) =>
        set((state) => ({
          visits: [...state.visits, visit],
          cases: state.cases.map((c) =>
            c.id === visit.case_id ? { ...c, ...caseUpdate, visit_count: c.visit_count + 1 } : c
          ),
        })),

      addPayment: (payment, caseUpdate) =>
        set((state) => ({
          payments: [...state.payments, payment],
          cases: state.cases.map((c) =>
            c.id === payment.case_id
              ? { ...c, ...caseUpdate, collected_amount: c.collected_amount + payment.amount }
              : c
          ),
        })),

      addPTP: (ptp, caseUpdate) =>
        set((state) => ({
          ptps: [...state.ptps, ptp],
          cases: state.cases.map((c) =>
            c.id === ptp.case_id ? { ...c, ...caseUpdate } : c
          ),
        })),

      markAlertRead: (id) =>
        set((state) => ({
          alerts: state.alerts.map((a) => (a.id === id ? { ...a, read: true } : a)),
        })),

      resetDemo: () =>
        set({
          checkedIn: false, checkInTime: null, checkedOut: false, onDuty: false,
          selfieUrl: null, sosActive: false, currentLocation: null,
          cases: SAMPLE_CASES, visits: SAMPLE_VISITS as VisitRecord[],
          payments: SAMPLE_PAYMENTS as PaymentRecord[], ptps: SAMPLE_PTPS as PTPRecord[],
          beat: SAMPLE_BEAT, alerts: initialAlerts,
        }),
    }),
    {
      name: "tiq_demo_store",
      partialize: (s) => ({
        checkedIn: s.checkedIn, checkInTime: s.checkInTime, onDuty: s.onDuty,
        selfieUrl: s.selfieUrl, cases: s.cases, visits: s.visits,
        payments: s.payments, ptps: s.ptps, alerts: s.alerts,
      }),
    }
  )
);

// Manager view helpers
export function getAllAgents(): Agent[] { return ALL_AGENTS; }
