// ─── CHANGELOG (standalone plan) ─────────────────────────────────────────────
// 2026-09-28 — NEW (P2 G02). "Add Agent" drawer, wired to
//   POST /manager/agents (backend/app/api/v1/endpoints/manager_agents_admin.py).
//   No territory_region_id field: there is no regions-list endpoint yet, and
//   the backend treats it as optional and skips the coverage check when
//   absent — adding a picker here would be a UI for data nobody can supply.
//   Deliberately conditional-render (`if (!open) return null`) with no slide
//   transition at all — an entrance animation needs a "shown" flag flipped a
//   frame after mount, and doing that from a useEffect trips this repo's own
//   react-hooks/set-state-in-effect gate (issue 1 in CLAUDE.md: 7 of 7 lint
//   errors are this exact rule). The brief calls a plain mount/unmount "a
//   perfectly fine first cut... correctness over polish", so that is what
//   this is: the drawer appears instantly rather than sliding in.
// ────────────────────────────────────────────────────────────────────────────
import { useRef, useState, type FormEvent } from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";
import { toast } from "react-hot-toast";
import { Input } from "@/components/ui/Input";
import { Button } from "@/components/ui/Button";
import { BaseLocationPicker } from "@/components/map/BaseLocationPicker";
import { useModalA11y } from "@/hooks/useModalA11y";
import { createAgent } from "@/api/manager";
import type { CreateAgentBody } from "@/api/manager";
import { errorDetail } from "@/lib/apiError";

// AgentSpecialization / VEHICLE_TYPES / AGENT_GENDER_VALUES, verbatim from
// backend/app/models/agent.py — there is no frontend copy of these enums to
// import, so they are restated here rather than left as free text.
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
  open: boolean;
  onClose: () => void;
  onCreated: () => void;
}

export function CreateAgentDrawer({ open, onClose, onCreated }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  useModalA11y(open, ref, onClose);

  const [fullName, setFullName] = useState("");
  const [email, setEmail] = useState("");
  const [phone, setPhone] = useState("");
  const [employeeCode, setEmployeeCode] = useState("");
  const [idCardNumber, setIdCardNumber] = useState("");
  const [territory, setTerritory] = useState("");
  const [gender, setGender] = useState("");
  const [specialization, setSpecialization] = useState<(typeof SPECIALIZATION_OPTIONS)[number]>("BOTH");
  const [vehicleType, setVehicleType] = useState<(typeof VEHICLE_OPTIONS)[number]>("TWO_WHEELER");
  const [maxCasesPerDay, setMaxCasesPerDay] = useState(15);
  const [languages, setLanguages] = useState("");
  const [lat, setLat] = useState<number | null>(null);
  const [lon, setLon] = useState<number | null>(null);
  const [locationError, setLocationError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  function reset() {
    setFullName(""); setEmail(""); setPhone(""); setEmployeeCode(""); setIdCardNumber("");
    setTerritory(""); setGender(""); setSpecialization("BOTH"); setVehicleType("TWO_WHEELER");
    setMaxCasesPerDay(15); setLanguages(""); setLat(null); setLon(null); setLocationError(null);
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (lat === null || lon === null) {
      setLocationError("Click the map to set the agent's base location.");
      return;
    }
    setLocationError(null);
    setBusy(true);
    const body: CreateAgentBody = {
      full_name: fullName.trim(),
      email: email.trim(),
      phone: phone.trim(),
      employee_code: employeeCode.trim(),
      id_card_number: idCardNumber.trim(),
      base_latitude: lat,
      base_longitude: lon,
      territory: territory.trim(),
      specialization,
      vehicle_type: vehicleType,
      max_cases_per_day: maxCasesPerDay,
      languages_spoken: languages.split(",").map((l) => l.trim()).filter(Boolean),
    };
    if (gender) body.gender = gender;
    try {
      const result = await createAgent(body);
      toast.success(
        result.activation.sent
          ? `${result.full_name} created — activation link sent by SMS`
          : `${result.full_name} created — activation SMS could not be sent${result.activation.error ? ` (${result.activation.error})` : ""}. Use Reset Login to send one later.`
      );
      reset();
      onCreated();
      onClose();
    } catch (err) {
      // Drawer stays open, form keeps whatever the manager typed — see
      // brief: "keep the drawer open so the manager doesn't lose their input".
      toast.error(errorDetail(err, "Could not create agent"));
    } finally {
      setBusy(false);
    }
  }

  if (!open) return null;

  return createPortal(
    <>
      <div className="fixed inset-0 z-50 bg-black/40" onClick={onClose} />
      <div
        ref={ref}
        role="dialog"
        aria-modal="true"
        aria-labelledby="create-agent-title"
        className="fixed inset-y-0 right-0 z-50 flex w-full max-w-md flex-col bg-white shadow-premium"
      >
        <div className="flex items-center justify-between px-5 py-4 border-b" style={{ borderColor: "#EAEBEF" }}>
          <h2 id="create-agent-title" className="text-base font-bold" style={{ color: "#1C1C1F" }}>Add Agent</h2>
          <button type="button" onClick={onClose} aria-label="Close" className="tap-target" style={{ color: "#6B6D76" }}>
            <X className="w-4 h-4" />
          </button>
        </div>

        <form onSubmit={submit} className="flex-1 overflow-y-auto px-5 py-4 space-y-3">
          <Input label="Full name" value={fullName} onChange={(e) => setFullName(e.target.value)} required />
          <Input label="Email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
          <Input label="Phone" value={phone} onChange={(e) => setPhone(e.target.value)} required />
          <div className="grid grid-cols-2 gap-2">
            <Input label="Employee code" value={employeeCode} onChange={(e) => setEmployeeCode(e.target.value)} required />
            <Input label="ID card number" value={idCardNumber} onChange={(e) => setIdCardNumber(e.target.value)} required />
          </div>
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
            <Button type="submit" loading={busy}>Create agent</Button>
          </div>
        </form>
      </div>
    </>,
    document.body,
  );
}
