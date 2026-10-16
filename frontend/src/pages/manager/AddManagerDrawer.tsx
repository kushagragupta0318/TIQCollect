// Add an AGENCY_MANAGER to the agency (agency-admin only). A slim sibling of
// CreateAgentDrawer: managers aren't field agents, so no territory/map/vehicle —
// just name, email, phone. In DEMO_MODE the create returns a known password,
// shown in a persistent toast with Copy (same as the agent flow); in production
// an invite link is created and the manager sets their own password.
import { useRef, useState, type FormEvent } from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";
import { toast } from "react-hot-toast";
import { Input } from "@/components/ui/Input";
import { Button } from "@/components/ui/Button";
import { useModalA11y } from "@/hooks/useModalA11y";
import { createManager } from "@/api/manager";
import type { CreateManagerResult } from "@/api/manager";
import { errorDetail } from "@/lib/apiError";

interface Props {
  open: boolean;
  onClose: () => void;
  onCreated: (result: CreateManagerResult) => void;
}

export function AddManagerDrawer({ open, onClose, onCreated }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  useModalA11y(open, ref, onClose);

  const [fullName, setFullName] = useState("");
  const [email, setEmail] = useState("");
  const [phone, setPhone] = useState("");
  const [busy, setBusy] = useState(false);

  function reset() { setFullName(""); setEmail(""); setPhone(""); }

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      const result = await createManager({ full_name: fullName.trim(), email: email.trim(), phone: phone.trim() });
      if (result.demo_login) {
        const cred = result.demo_login;
        toast(
          (t) => (
            <div className="flex flex-col gap-2">
              <span className="font-bold">{result.full_name} created — manager login</span>
              <div className="text-xs leading-5">
                <div>Email: <span className="font-semibold">{cred.email}</span></div>
                <div>Password: <span className="font-semibold">{cred.password}</span></div>
              </div>
              <div className="flex gap-2 mt-1">
                <button
                  type="button"
                  onClick={() => { void navigator.clipboard?.writeText(`${cred.email}\n${cred.password}`); toast.success("Copied", { duration: 1500 }); }}
                  className="px-2.5 py-1 rounded-lg text-xs font-semibold"
                  style={{ background: "#1677FF", color: "#fff" }}
                >
                  Copy
                </button>
                <button
                  type="button"
                  onClick={() => toast.dismiss(t.id)}
                  className="px-2.5 py-1 rounded-lg text-xs font-semibold"
                  style={{ border: "1px solid #ECEDF1", color: "#475467" }}
                >
                  Dismiss
                </button>
              </div>
            </div>
          ),
          { duration: Infinity, id: `mgr-cred-${result.id ?? result.email}` },
        );
      } else {
        // Production: an invitation was created; the manager sets their own password.
        toast.success(`${result.full_name} invited — share the set-password link.`);
      }
      reset();
      onCreated(result);
      onClose();
    } catch (err) {
      // Keep the drawer open so the admin doesn't lose their input.
      toast.error(errorDetail(err, "Could not add manager"));
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
        aria-labelledby="add-manager-title"
        className="fixed inset-y-0 right-0 z-50 flex w-full max-w-md flex-col bg-white shadow-premium"
      >
        <div className="flex items-center justify-between px-5 py-4 border-b" style={{ borderColor: "#EAEBEF" }}>
          <h2 id="add-manager-title" className="text-base font-bold" style={{ color: "#1C1C1F" }}>Add Manager</h2>
          <button type="button" onClick={onClose} aria-label="Close" className="tap-target" style={{ color: "#6B6D76" }}>
            <X className="w-4 h-4" />
          </button>
        </div>
        <form onSubmit={submit} className="flex-1 overflow-y-auto px-5 py-4 space-y-3">
          <Input label="Full name" value={fullName} onChange={(e) => setFullName(e.target.value)} placeholder="e.g. Rohit Sharma" required />
          <Input label="Email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="name@agency.test" required />
          <Input label="Phone" value={phone} onChange={(e) => setPhone(e.target.value)} placeholder="9810000000" required />
          <p className="text-xs" style={{ color: "#98A2B3" }}>
            The manager joins your agency and can build their own team of field agents.
          </p>
          <div className="flex justify-end gap-2 pt-2">
            <button type="button" onClick={onClose} className="tap-target text-xs font-semibold px-3 py-2 rounded-xl" style={{ color: "#6B6D76" }}>Cancel</button>
            <Button type="submit" loading={busy}>Add manager</Button>
          </div>
        </form>
      </div>
    </>,
    document.body,
  );
}
