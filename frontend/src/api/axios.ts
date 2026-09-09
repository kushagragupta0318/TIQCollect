import axios, { type AxiosError, type InternalAxiosRequestConfig } from "axios";
import { useAuthStore } from "@/store/authStore";

const api = axios.create({
  baseURL: "/api/v1",
  timeout: 15000,
  headers: { "Content-Type": "application/json" },
});

/**
 * Timeout for the handful of endpoints that do real work before answering.
 *
 * 2026-09-09 — the 15s default above is right for a read and badly wrong for
 * nightly allocation. `POST /manager/allocation/plan` filters the pool, runs a
 * Hungarian solve over a case x capacity-slot matrix, calls OSRM for a distance
 * matrix and a route per agent, then writes fifteen beats: measured on the demo
 * book (921 cases evaluated, 214 allocated, 15 agents) it took over 40 seconds
 * end to end and returned 200.
 *
 * The browser had already given up at 15. The user saw "Failed to generate plan
 * — please try again" WHILE THE PLAN WAS BEING WRITTEN, which is how a
 * screenshot came to show that toast on top of a fully populated PLANNED beat
 * plan. Worse, "please try again" is an instruction to do the one thing that
 * makes it worse: the second click collides with the first request, which is
 * still running, and that is the concurrent-planning collision the advisory
 * lock in planner_service now answers with a 409. The timeout was the root
 * cause; the collision was its symptom.
 *
 * Three minutes, not sixty seconds: the work scales with the size of the pool
 * and the number of agents, and a bigger book must not reintroduce this.
 */
export const LONG_RUNNING_MS = 180_000;

/** True when the request was aborted client-side rather than refused. */
export function isTimeout(err: unknown): boolean {
  if (typeof err !== "object" || err === null) return false;
  const e = err as { code?: unknown; response?: unknown };
  return (e.code === "ECONNABORTED" || e.code === "ETIMEDOUT") && !e.response;
}

// Attach access token to every request
api.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  const token = useAuthStore.getState().accessToken;
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});

// Auto-refresh on 401
let isRefreshing = false;
let failedQueue: Array<{ resolve: (v: string) => void; reject: (e: unknown) => void }> = [];

const processQueue = (error: unknown, token: string | null) => {
  failedQueue.forEach((p) => (error ? p.reject(error) : p.resolve(token!)));
  failedQueue = [];
};

api.interceptors.response.use(
  (res) => res,
  async (error: AxiosError) => {
    const originalRequest = error.config as InternalAxiosRequestConfig & { _retry?: boolean };

    if (error.response?.status === 401 && !originalRequest._retry) {
      if (isRefreshing) {
        return new Promise((resolve, reject) => {
          failedQueue.push({ resolve, reject });
        }).then((token) => {
          originalRequest.headers.Authorization = `Bearer ${token}`;
          return api(originalRequest);
        });
      }

      originalRequest._retry = true;
      isRefreshing = true;

      const refreshToken = useAuthStore.getState().refreshToken;
      if (!refreshToken) {
        useAuthStore.getState().logout();
        return Promise.reject(error);
      }

      try {
        const { data } = await axios.post("/api/v1/auth/refresh", { refresh_token: refreshToken });
        useAuthStore.getState().setTokens(data.access_token, data.refresh_token);
        processQueue(null, data.access_token);
        originalRequest.headers.Authorization = `Bearer ${data.access_token}`;
        return api(originalRequest);
      } catch (err) {
        processQueue(err, null);
        useAuthStore.getState().logout();
        return Promise.reject(err);
      } finally {
        isRefreshing = false;
      }
    }
    return Promise.reject(error);
  }
);

export default api;
