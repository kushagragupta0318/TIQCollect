import api from "./axios";
import type { LoginResponse } from "@/types";
import { slotKey } from "@/lib/sessionSlot";

// A09b (2026-09-28): the server issues a secret when it binds a field agent's
// device, and a later login from that device must present it (the device id
// alone is client-chosen, so anyone who learnt it could replay it). Kept per
// session slot, like the device id, so the simulator's agent and manager
// sessions in one tab each keep their own.
const DEVICE_SECRET_KEY = slotKey("tiq_device_secret");

export function readDeviceSecret(): string | undefined {
  try {
    return localStorage.getItem(DEVICE_SECRET_KEY) ?? undefined;
  } catch {
    return undefined;
  }
}

export function storeDeviceSecret(secret: string): void {
  try {
    localStorage.setItem(DEVICE_SECRET_KEY, secret);
  } catch {
    /* storage blocked: the next login re-binds (demo) or asks for a manager reset */
  }
}

export async function login(email: string, password: string, deviceId: string): Promise<LoginResponse> {
  const { data } = await api.post<LoginResponse>("/auth/login", {
    email,
    password,
    device_id: deviceId,
    device_secret: readDeviceSecret(),
  });
  if (data.device_secret) storeDeviceSecret(data.device_secret);
  return data;
}

// collection_dashboard: exchanges a quick-login link token for a real session
export async function quickLogin(token: string): Promise<LoginResponse> {
  const { data } = await api.post<LoginResponse>("/auth/quick-login", { token });
  return data;
}

export async function logout(): Promise<void> {
  await api.post("/auth/logout");
}

export async function getMe() {
  const { data } = await api.get("/auth/me");
  return data;
}
