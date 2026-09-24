import { create } from "zustand";
import { persist } from "zustand/middleware";
import type { AuthUser } from "@/types";
import { slotKey } from "@/lib/sessionSlot";

// Both keys are slot-namespaced (lib/sessionSlot.ts) so the simulator can hold
// an agent and a manager session in one tab. Outside a slot they are the
// historical "tiq_auth" / "tiq_device_id", so existing sessions survive.
const AUTH_KEY = slotKey("tiq_auth");
const DEVICE_KEY = slotKey("tiq_device_id");

interface AuthState {
  accessToken: string | null;
  refreshToken: string | null;
  user: AuthUser | null;
  deviceId: string;
  isAuthenticated: boolean;
  setTokens: (access: string, refresh: string) => void;
  setUser: (user: AuthUser) => void;
  logout: () => void;
}

function generateDeviceId(): string {
  const existing = localStorage.getItem(DEVICE_KEY);
  if (existing) return existing;
  const id = `${Date.now()}-${Math.random().toString(36).substring(2, 15)}`;
  localStorage.setItem(DEVICE_KEY, id);
  return id;
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      accessToken: null,
      refreshToken: null,
      user: null,
      deviceId: generateDeviceId(),
      isAuthenticated: false,

      setTokens: (access, refresh) =>
        set({ accessToken: access, refreshToken: refresh, isAuthenticated: true }),

      setUser: (user) => set({ user }),

      logout: () =>
        set({ accessToken: null, refreshToken: null, user: null, isAuthenticated: false }),
    }),
    {
      name: AUTH_KEY,
      partialize: (state) => ({
        accessToken: state.accessToken,
        refreshToken: state.refreshToken,
        user: state.user,
        deviceId: state.deviceId,
        isAuthenticated: state.isAuthenticated,
      }),
    }
  )
);
