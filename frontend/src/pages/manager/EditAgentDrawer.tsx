// ─── CHANGELOG (standalone plan) ─────────────────────────────────────────────
// 2026-09-28 — NEW (P2 G02). "Edit Agent" drawer, wired to
//   PATCH /manager/agents/{agent_id}. email / employee_code / id_card_number
//   are not fields here — the backend's EditAgentRequest deliberately leaves
//   them out (identity documents and the account's login identity are rarer,
//   separate actions; see manager_agents_admin.py's own docstring). Every
//   field is sent on submit rather than only the changed ones: edit_agent()
//   safely no-ops anything that did not actually change, and diffing
//   client-side would just be restating that same logic a second place.
//
//   The form is seeded from `agent` via lazy useState initializers, not a
//   resync effect — ManagerAgentsPage mounts this with `key={agent?.id ??
//   "closed"}` (see its own comment), so a different agent opening is a full
//   remount with fresh initial state rather than a value this component has
//   to notice changed. Syncing via an effect would trip this repo's own
//   react-hooks/set-state-in-effect lint gate (issue 1 in CLAUDE.md); the key
//   sidesteps it entirely rather than working around it.
// ────────────────────────────────────────────────────────────────────────────
import { useRef, useState, type FormEvent } from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";
import { toast } from "react-hot-toast";
import { Input } from "@/components/ui/Input";
import { Button } from "@/components/ui/Button";
import { BaseLocationPicker } from "@/components/map/BaseLocationPicker";
import { useModalA11y } from "@/hooks/useModalA11y";
import { editAgent } from "@/api/manager";
import type { EditAgentBody } from "@/api/manager";
import { errorDetail } from "@/lib/apiError";
import type { Agent } from "@/types";

const GENDER_OPTIONS: Array<{ value: string; label: string }> = [
  { value: "", label: "Not specified" },
  { value: "F", label: "Female" },
  { value: "M", label: "Male" },
  { value: "OTHER", label: "Other" },
];
const SPECIALIZATION_OPTIONS = ["SECURED", "UNSECURED", "BOTH"] as const;
const VEHICLE_OPTIONS = ["TWO_WHEELER", "FOUR_WHEELER", "PUBLIC_TRANSPORT"] as const;

const FIELD_LABEL = "mb-1.5 block text-[13px] font-normal text-[#98A2B3]";

interface Props {
  agent: Agent | null;
  onClose: () => void;
  onSaved: () => void;
}

function validSpecialization(value: string | undefined): (typeof SPECIALIZATION_OPTIONS)[number] {
  return (SPECIALIZATION_OPTIONS as readonly string[]).includes(value ?? "")
    ? (value as (typeof SPECIALIZATION_OPTIONS)[number]) : "BOTH";
}
function validVehicleType(value: string | undefined): (typeof VEHICLE_OPTIONS)[number] {
  return (VEHICLE_OPTIONS as readonly string[]).includes(value ?? "")
    ? (value as (typeof VEHICLE_OPTIONS)[number]) : "TWO_WHEELER";
}

/** Renders nothing when `agent` is null, so the parent can always mount this
 *  and just pass null/an Agent to close/open it — same idea as the create
 *  drawer's `open` boolean, adapted for "open on THIS agent". */
export function EditAgentDrawer({ agent, onClose, onSaved }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const open = agent !== null;
  useModalA11y(open, ref, onClose);

  // Lazy initializers, read once per mount — correct because the PARENT
  // remounts this component (via `key={agent?.id ?? "closed"}`) whenever a
  // different agent opens, so "once per mount" already means "once per
  // agent". agent can still be null here: the parent keeps one closed
  // instance mounted between opens.
  const [fullName, setFullName] = useState(() => agent?.full_name ?? "");
  const [phone, setPhone] = useState(() => agent?.phone ?? "");
  const [territory, setTerritory] = useState(() => agent?.territory ?? "");
  const [gender, setGender] = useState(() => agent?.gender ?? "");
  const [specialization, setSpecialization] = useState(() => validSpecialization(agent?.specialization));
  const [vehicleType, setVehicleType] = useState(() => validVehicleType(agent?.vehicle_type));
  const [maxCasesPerDay, setMaxCasesPerDay] = useState(() => agent?.max_cases_per_day ?? 15);
  const [languages, setLanguages] = useState(() => (agent?.languages_spoken ?? []).join(", "));
  const [lat, setLat] = useState<number | null>(() => agent?.base_latitude ?? null);
  const [lon, setLon] = useState<number | null>(() => agent?.base_longitude ?? null);
  const [locationError, setLocationError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (!agent) return null;
  // Captured so the closure below never carries a stale `Agent | null` type
  // through TypeScript's narrowing-in-closures gap.
  const agentId = agent.id;
  const agentName = agent.full_name;

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (lat === null || lon === null) {
      setLocationError("Click the map to set the agent's base location.");
      return;
    }
    setLocationError(null);
    setBusy(true);
    const body: EditAgentBody = {
      full_name: fullName.trim(),
      phone: phone.trim(),
      territory: territory.trim(),
      gender: gender || undefined,
      specialization,
      vehicle_type: vehicleType,
      max_cases_per_day: maxCasesPerDay,
      languages_spoken: languages.split(",").map((l) => l.trim()).filter(Boolean),
      base_latitude: lat,
      base_longitude: lon,
    };
    try {
      await editAgent(agentId, body);
      toast.success(`${agentName} updated`);
      onSaved();
      onClose();
    } catch (err) {
      toast.error(errorDetail(err, "Could not update agent"));
    } finally {
      setBusy(false);
    }
  }

  return createPortal(
    <>
      <div
        className="fixed inset-0 z-50 bg-black/40"
        onClick={onClose}
      />
      <div
        ref={ref}
        role="dialog"
        aria-modal="true"
        aria-labelledby="edit-agent-title"
        className="fixed inset-y-0 right-0 z-50 flex w-full max-w-md flex-col bg-white shadow-premium"
      >
        <div className="flex items-center justify-between px-5 py-4 border-b" style={{ borderColor: "#EAEBEF" }}>
          <h2 id="edit-agent-title" className="text-base font-bold" style={{ color: "#1C1C1F" }}>Edit {agentName}</h2>
          <button type="button" onClick={onClose} aria-label="Close" className="tap-target" style={{ color: "#6B6D76" }}>
            <X className="w-4 h-4" />
          </button>
        </div>

        <form onSubmit={submit} className="flex-1 overflow-y-auto px-5 py-4 space-y-3">
          <p className="text-xs" style={{ color: "#8A8F9C" }}>
            Email, employee code and ID card number are not editable here.
          </p>
          <Input label="Full name" value={fullName} onChange={(e) => setFullName(e.target.value)} required />
          <Input label="Phone" value={phone} onChange={(e) => setPhone(e.target.value)} required />
          <Input label="Territory" value={territory} onChange={(e) => setTerritory(e.target.value)} required />

          <div className="grid grid-cols-3 gap-2">
            <label>
              <span className={FIELD_LABEL}>Gender</span>
              <select className="input w-full" value={gender} onChange={(e) => setGender(e.target.value)}>
                {GENDER_OPTIONS.map((g) => <option key={g.value} value={g.value}>{g.label}</option>)}
              </select>
            </label>
            <label>
              <span className={FIELD_LABEL}>Specialization</span>
              <select className="input w-full" value={specialization} onChange={(e) => setSpecialization(e.target.value as (typeof SPECIALIZATION_OPTIONS)[number])}>
                {SPECIALIZATION_OPTIONS.map((s) => <option key={s} value={s}>{s}</option>)}
              </select>
            </label>
            <label>
              <span className={FIELD_LABEL}>Vehicle</span>
              <select className="input w-full" value={vehicleType} onChange={(e) => setVehicleType(e.target.value as (typeof VEHICLE_OPTIONS)[number])}>
                {VEHICLE_OPTIONS.map((v) => <option key={v} value={v}>{v.replace(/_/g, " ")}</option>)}
              </select>
            </label>
          </div>

          <Input
            label="Max cases per day" type="number" min={1} max={50}
            value={maxCasesPerDay}
            onChange={(e) => setMaxCasesPerDay(Math.max(1, Math.min(50, Number(e.target.value) || 1)))}
          />
          <Input
            label="Languages spoken (comma-separated)"
            placeholder="Hindi, English"
            value={languages}
            onChange={(e) => setLanguages(e.target.value)}
          />

          <div>
            <span className={FIELD_LABEL}>Base location</span>
            <BaseLocationPicker
              latitude={lat} longitude={lon}
              onChange={(la, lo) => { setLat(la); setLon(lo); setLocationError(null); }}
            />
            {locationError && <p className="mt-1 text-xs text-danger-600">{locationError}</p>}
          </div>

          <div className="flex justify-end gap-2 pt-2">
            <button type="button" onClick={onClose} className="tap-target text-xs font-semibold px-3 py-2 rounded-xl" style={{ color: "#6B6D76" }}>
              Cancel
            </button>
            <Button type="submit" loading={busy}>Save changes</Button>
          </div>
        </form>
      </div>
    </>,
    document.body,
  );
}
