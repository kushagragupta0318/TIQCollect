import api from "./axios";
import type { LoginResponse } from "@/types";

export async function login(email: string, password: string, deviceId: string): Promise<LoginResponse> {
  const { data } = await api.post<LoginResponse>("/auth/login", {
    email,
    password,
    device_id: deviceId,
  });
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
