// Edit the six Identity fields the agency profile shows, in place.
//
// Scope is deliberately the CARD, not the whole identity record: onboarding's
// IdentityStep already owns the full form (CIN, PAN, GSTIN, registered
// address, the contacts list). This edits what is on screen and PATCHes only
// those keys, so a field the card does not show can never be blanked by a save
// from here — UpdateIdentityRequest drops nulls server-side, but sending a key
// the user could not see is how that kind of data loss starts.
//
// Status is read-only here on purpose: it moves through
// /bank/agencies/{id}/suspend (agency.suspend, a separate sensitive
// capability), not through an identity PATCH.
import { useState, type FormEvent } from "react";
import { useMutation } from "@tanstack/react-query";
import { toast } from "react-hot-toast";
import { Dialog, DialogClose, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "../../ui/dialog";
import { Button } from "../../ui/button";
import { Input } from "../../ui/input";
import { Label } from "../../ui/label";
import { updateAgencyIdentity, type AgencyIdentityFields } from "@/api/bank";
import { errorDetail } from "@/lib/apiError";

export interface EditableIdentity {
  legal_name: string;
  rbi_registration_no?: string | null;
  hq_city?: string | null;
  contact_name?: string | null;
  contact_email?: string | null;
  contact_phone?: string | null;
}

interface Props {
  agencyId: string;
  agency: EditableIdentity;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSaved: () => void;
}

export function EditIdentityDialog({ agencyId, agency, open, onOpenChange, onSaved }: Props) {
  // Keyed by `open` at the call site, so each opening remounts with fresh
  // values — a cancelled edit never leaks into the next one.
  const [rbiRegNo, setRbiRegNo] = useState(() => agency.rbi_registration_no ?? "");
  const [hqCity, setHqCity] = useState(() => agency.hq_city ?? "");
  const [contactName, setContactName] = useState(() => agency.contact_name ?? "");
  const [contactEmail, setContactEmail] = useState(() => agency.contact_email ?? "");
  const [contactPhone, setContactPhone] = useState(() => agency.contact_phone ?? "");

  const save = useMutation({
    mutationFn: (body: Partial<AgencyIdentityFields>) => updateAgencyIdentity(agencyId, body),
    onSuccess: () => {
      toast.success("Identity updated");
      onSaved();
      onOpenChange(false);
    },
    onError: (err) => toast.error(errorDetail(err, "Could not update this agency's identity")),
  });

  function submit(e: FormEvent) {
    e.preventDefault();
    // legal_name is required by CreateAgencyRequest's shape and is not edited
    // here, so it is sent back unchanged rather than omitted.
    save.mutate({
      legal_name: agency.legal_name,
      rbi_registration_no: rbiRegNo.trim() || null,
      hq_city: hqCity.trim() || null,
      contact_name: contactName.trim() || null,
      contact_email: contactEmail.trim() || null,
      contact_phone: contactPhone.trim() || null,
    });
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogClose onClose={() => onOpenChange(false)} />
        <DialogHeader>
          <DialogTitle>Edit identity</DialogTitle>
          <DialogDescription>
            {agency.legal_name} — registration, location and primary contact. Status is changed from the
            agency's own actions, not here.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={submit}>
          <div className="grid gap-4 overflow-y-auto px-6 py-5 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="edit-rbi">RBI registration</Label>
              <Input id="edit-rbi" value={rbiRegNo} onChange={(e) => setRbiRegNo(e.target.value)} placeholder="e.g. REG/2024/0188" />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="edit-hq">HQ city</Label>
              <Input id="edit-hq" value={hqCity} onChange={(e) => setHqCity(e.target.value)} placeholder="e.g. Gurugram" />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="edit-contact">Contact</Label>
              <Input id="edit-contact" value={contactName} onChange={(e) => setContactName(e.target.value)} placeholder="Primary contact's name" />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="edit-phone">Phone</Label>
              <Input id="edit-phone" value={contactPhone} onChange={(e) => setContactPhone(e.target.value)} placeholder="10-digit mobile" />
            </div>
            <div className="space-y-1.5 sm:col-span-2">
              <Label htmlFor="edit-email">Email</Label>
              {/* type=email so the browser rejects a malformed address before
                  the server's AccountEmail does, with the field still focused. */}
              <Input id="edit-email" type="email" value={contactEmail} onChange={(e) => setContactEmail(e.target.value)} placeholder="name@agency.test" />
            </div>
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)} disabled={save.isPending}>
              Cancel
            </Button>
            <Button type="submit" disabled={save.isPending}>
              {save.isPending ? "Saving…" : "Save changes"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export default EditIdentityDialog;
