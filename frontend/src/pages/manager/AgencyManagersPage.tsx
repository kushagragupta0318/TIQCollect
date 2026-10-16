// Agency-admin only: the agency's managers (list + add). The nav entry is
// shown to AGENCY_ADMIN only (ManagerLayout), and the API is AGENCY_ADMIN-only
// server-side (403 otherwise) — this page handles that 403 rather than showing
// a misleading empty state to a non-admin who typed the URL.
import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, UserCog } from "lucide-react";
import { getAgencyManagers } from "@/api/manager";
import { AddManagerDrawer } from "./AddManagerDrawer";

export default function AgencyManagersPage() {
  const queryClient = useQueryClient();
  const [createOpen, setCreateOpen] = useState(false);
  const { data: managers = [], isLoading, isError } = useQuery({
    queryKey: ["manager", "managers"],
    queryFn: getAgencyManagers,
    staleTime: 30_000,
    retry: false,
  });

  if (isError) {
    return (
      <p className="text-sm p-6" style={{ color: "#6B6D76" }}>
        Only an agency administrator can manage managers.
      </p>
    );
  }

  return (
    <div className="space-y-5">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-xl font-bold" style={{ color: "#101828", letterSpacing: "-0.02em" }}>Managers</h1>
          <p className="text-sm mt-0.5" style={{ color: "#667085" }}>
            The agency managers in your agency. Add a manager, and they build their own team of field agents.
          </p>
        </div>
        <button
          type="button"
          onClick={() => setCreateOpen(true)}
          className="tap-target inline-flex items-center gap-2 px-3 sm:px-4 py-2 rounded-xl font-semibold text-xs sm:text-sm flex-shrink-0 transition hover:brightness-95"
          style={{ background: "#1677FF", border: "1px solid #1677FF", color: "#fff" }}
        >
          <Plus className="w-4 h-4" /> <span className="hidden sm:inline">Add Manager</span>
        </button>
      </div>

      <div className="card p-0 overflow-hidden">
        {isLoading ? (
          <p className="text-sm p-6" style={{ color: "#667085" }}>Loading…</p>
        ) : managers.length === 0 ? (
          <div className="text-center py-14 px-4">
            <div className="mx-auto mb-4 flex items-center justify-center rounded-2xl" style={{ width: 56, height: 56, background: "#EAF2FF", color: "#1677FF" }}>
              <UserCog className="w-6 h-6" />
            </div>
            <p className="text-sm font-semibold" style={{ color: "#101828" }}>No managers yet</p>
            <p className="text-sm mt-1" style={{ color: "#667085" }}>Add a manager to start building out your agency.</p>
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr style={{ textAlign: "left", borderBottom: "1px solid #ECEDF1" }}>
                <th className="font-semibold px-4 py-2.5" style={{ color: "#98A2B3" }}>Manager</th>
                <th className="font-semibold px-4 py-2.5" style={{ color: "#98A2B3" }}>Agents</th>
                <th className="font-semibold px-4 py-2.5" style={{ color: "#98A2B3" }}>Status</th>
              </tr>
            </thead>
            <tbody>
              {managers.map((m) => (
                <tr key={m.id} style={{ borderTop: "1px solid #F2F3F5" }}>
                  <td className="px-4 py-3">
                    <p className="font-semibold" style={{ color: "#101828" }}>{m.full_name}</p>
                    <p className="text-xs" style={{ color: "#667085" }}>{m.email}</p>
                  </td>
                  <td className="px-4 py-3" style={{ color: "#475467" }}>{m.agents_count}</td>
                  <td className="px-4 py-3">
                    <span
                      className="text-xs font-semibold px-2 py-0.5 rounded-full"
                      style={{ background: m.is_active ? "#E7F6EC" : "#FEECEC", color: m.is_active ? "#067647" : "#B42318" }}
                    >
                      {m.is_active ? "Active" : "Inactive"}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <AddManagerDrawer
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={() => void queryClient.invalidateQueries({ queryKey: ["manager", "managers"] })}
      />
    </div>
  );
}
