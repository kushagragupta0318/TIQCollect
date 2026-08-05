import { create } from "zustand";

interface SOSState {
  sosActive: boolean;
  sosLoading: boolean;
  setSosActive: (v: boolean) => void;
  setSosLoading: (v: boolean) => void;
}

export const useSOSStore = create<SOSState>((set) => ({
  sosActive: false,
  sosLoading: false,
  setSosActive: (v) => set({ sosActive: v }),
  setSosLoading: (v) => set({ sosLoading: v }),
}));
