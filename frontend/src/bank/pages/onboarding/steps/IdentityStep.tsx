// Step 1 — Identity. Creates the draft (POST /bank/agencies) the first time;
// every later visit PATCHes the same agency_id. Form state is seeded from
// `agency` via lazy useState initializers, correct because the parent page
// never mounts this step until any known draft has already finished loading
// (see OnboardAgencyWizardPage's own docblock).
import { useState, type FormEvent } from "react";
import { useMutation } from "@tanstack/react-query";
import { Plus, Trash2 } from "lucide-react";
import { toast } from "react-hot-toast";
import { Card, CardContent, CardFooter, CardHeader, CardTitle, CardDescription } from "../../../ui/card";
import { Button } from "../../../ui/button";
import { Input } from "../../../ui/input";
import { Label } from "../../../ui/label";
import { Select } from "../../../ui/select";
import { Separator } from "../../../ui/separator";
import {
  createAgencyDraft, updateAgencyIdentity, ENTITY_TYPES,
  type Agency, type AgencyContact, type AgencyIdentityFields,
} from "@/api/bank";
import { errorDetail } from "@/lib/apiError";

interface Props {
  agency: Agency | null;
  onSaved: (agency: Agency) => void;
}

function emptyContact(): AgencyContact {
  return { role: "", name: "", email: "", phone: "" };
}

export function IdentityStep({ agency, onSaved }: Props) {
  const [legalName, setLegalName] = useState(() => agency?.legal_name ?? "");
  const [tradeName, setTradeName] = useState(() => agency?.trade_name ?? "");
  const [entityType, setEntityType] = useState(() => agency?.entity_type ?? "");
  const [cin, setCin] = useState(() => agency?.cin ?? "");
  const [rbiRegNo, setRbiRegNo] = useState(() => agency?.rbi_registration_no ?? "");
  const [pan, setPan] = useState(() => agency?.pan ?? "");
  const [gstin, setGstin] = useState(() => agency?.gstin ?? "");
  const [addressLine1, setAddressLine1] = useState(() => agency?.registered_address?.line1 ?? "");
  const [addressLine2, setAddressLine2] = useState(() => agency?.registered_address?.line2 ?? "");
  const [addressCity, setAddressCity] = useState(() => agency?.registered_address?.city ?? "");
  const [addressState, setAddressState] = useState(() => agency?.registered_address?.state ?? "");
  const [addressPincode, setAddressPincode] = useState(() => agency?.registered_address?.pincode ?? "");
  const [hqCity, setHqCity] = useState(() => agency?.hq_city ?? "");
  const [website, setWebsite] = useState(() => agency?.website ?? "");
  const [contacts, setContacts] = useState<AgencyContact[]>(() =>
    agency?.contacts && agency.contacts.length > 0 ? agency.contacts.map((c) => ({ ...c })) : [emptyContact()],
  );
  const [primaryName, setPrimaryName] = useState(() => agency?.contact_name ?? "");
  const [primaryEmail, setPrimaryEmail] = useState(() => agency?.contact_email ?? "");
  const [primaryPhone, setPrimaryPhone] = useState(() => agency?.contact_phone ?? "");

  const save = useMutation({
    mutationFn: (body: AgencyIdentityFields) =>
      agency ? updateAgencyIdentity(agency.agency_id, body) : createAgencyDraft(body),
    onSuccess: (result) => {
      toast.success(agency ? "Identity saved" : `${result.legal_name} created — code ${result.code}`);
      onSaved(result);
    },
    onError: (err) => toast.error(errorDetail(err, "Could not save the agency's identity")),
  });

  function updateContact(index: number, patch: Partial<AgencyContact>) {
    setContacts((rows) => rows.map((r, i) => (i === index ? { ...r, ...patch } : r)));
  }

  function submit(e: FormEvent) {
    e.preventDefault();
    if (!legalName.trim()) {
      toast.error("Legal name is required.");
      return;
    }
    const hasAddress = addressLine1 || addressLine2 || addressCity || addressState || addressPincode;
    save.mutate({
      legal_name: legalName.trim(),
      trade_name: tradeName.trim() || null,
      entity_type: entityType || null,
      cin: cin.trim() || null,
      rbi_registration_no: rbiRegNo.trim() || null,
      pan: pan.trim() || null,
      gstin: gstin.trim() || null,
      registered_address: hasAddress
        ? { line1: addressLine1, line2: addressLine2, city: addressCity, state: addressState, pincode: addressPincode }
        : null,
      hq_city: hqCity.trim() || null,
      website: website.trim() || null,
      contacts: contacts.filter((c) => (c.name ?? "").trim() || (c.email ?? "").trim() || (c.phone ?? "").trim()),
      contact_name: primaryName.trim() || null,
      contact_email: primaryEmail.trim() || null,
      contact_phone: primaryPhone.trim() || null,
    });
  }

  return (
    <form onSubmit={submit} className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>Agency identity</CardTitle>
          <CardDescription>Legal and registration details, as they appear on the agency's own documents.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-2">
            <div>
              <Label htmlFor="id-legal-name">Legal name *</Label>
              <Input id="id-legal-name" required value={legalName} onChange={(e) => setLegalName(e.target.value)} className="mt-1.5" />
            </div>
            <div>
              <Label htmlFor="id-trade-name">Trade name</Label>
              <Input id="id-trade-name" value={tradeName} onChange={(e) => setTradeName(e.target.value)} className="mt-1.5" />
            </div>
          </div>
          <div className="grid gap-4 sm:grid-cols-3">
            <div>
              <Label htmlFor="id-entity-type">Entity type</Label>
              <Select id="id-entity-type" className="mt-1.5" value={entityType} onChange={(e) => setEntityType(e.target.value)}>
                <option value="">Not specified</option>
                {ENTITY_TYPES.map((t) => <option key={t} value={t}>{t.replace(/_/g, " ")}</option>)}
              </Select>
            </div>
            <div>
              <Label htmlFor="id-cin">CIN / LLPIN</Label>
              <Input id="id-cin" value={cin} onChange={(e) => setCin(e.target.value)} className="mt-1.5" />
            </div>
            <div>
              <Label htmlFor="id-rbi">RBI registration no.</Label>
              <Input id="id-rbi" value={rbiRegNo} onChange={(e) => setRbiRegNo(e.target.value)} className="mt-1.5" />
            </div>
          </div>
          <div className="grid gap-4 sm:grid-cols-3">
            <div>
              <Label htmlFor="id-pan">PAN</Label>
              <Input id="id-pan" value={pan} onChange={(e) => setPan(e.target.value.toUpperCase())} className="mt-1.5" />
            </div>
            <div>
              <Label htmlFor="id-gstin">GSTIN</Label>
              <Input id="id-gstin" value={gstin} onChange={(e) => setGstin(e.target.value.toUpperCase())} className="mt-1.5" />
            </div>
            <div>
              <Label htmlFor="id-hq-city">HQ city</Label>
              <Input id="id-hq-city" value={hqCity} onChange={(e) => setHqCity(e.target.value)} className="mt-1.5" />
            </div>
          </div>
          <div>
            <Label htmlFor="id-website">Website</Label>
            <Input id="id-website" type="url" placeholder="https://" value={website} onChange={(e) => setWebsite(e.target.value)} className="mt-1.5" />
          </div>

          <Separator />
          <div>
            <Label>Registered address</Label>
            <div className="mt-1.5 grid gap-3 sm:grid-cols-2">
              <Input aria-label="Address line 1" placeholder="Address line 1" value={addressLine1} onChange={(e) => setAddressLine1(e.target.value)} />
              <Input aria-label="Address line 2" placeholder="Address line 2" value={addressLine2} onChange={(e) => setAddressLine2(e.target.value)} />
              <Input aria-label="City" placeholder="City" value={addressCity} onChange={(e) => setAddressCity(e.target.value)} />
              <Input aria-label="State" placeholder="State" value={addressState} onChange={(e) => setAddressState(e.target.value)} />
              <Input aria-label="Pincode" placeholder="Pincode" value={addressPincode} onChange={(e) => setAddressPincode(e.target.value)} />
            </div>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Primary contact</CardTitle>
          <CardDescription>Who the bank reaches for onboarding questions — separate from the master login (step 5).</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4 sm:grid-cols-3">
          <div>
            <Label htmlFor="id-primary-name">Name</Label>
            <Input id="id-primary-name" value={primaryName} onChange={(e) => setPrimaryName(e.target.value)} className="mt-1.5" />
          </div>
          <div>
            <Label htmlFor="id-primary-email">Email</Label>
            <Input id="id-primary-email" type="email" value={primaryEmail} onChange={(e) => setPrimaryEmail(e.target.value)} className="mt-1.5" />
          </div>
          <div>
            <Label htmlFor="id-primary-phone">Phone</Label>
            <Input id="id-primary-phone" value={primaryPhone} onChange={(e) => setPrimaryPhone(e.target.value)} className="mt-1.5" />
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Named contacts</CardTitle>
          <CardDescription>Director, operations head, compliance officer — anyone else the bank may need to reach.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {contacts.map((contact, i) => (
            <div key={i} className="grid gap-2 sm:grid-cols-[1fr_1fr_1fr_1fr_auto] items-end rounded-inner border border-border/60 p-3">
              <div>
                <Label htmlFor={`contact-role-${i}`}>Role</Label>
                <Input id={`contact-role-${i}`} value={contact.role ?? ""} onChange={(e) => updateContact(i, { role: e.target.value })} className="mt-1.5" />
              </div>
              <div>
                <Label htmlFor={`contact-name-${i}`}>Name</Label>
                <Input id={`contact-name-${i}`} value={contact.name ?? ""} onChange={(e) => updateContact(i, { name: e.target.value })} className="mt-1.5" />
              </div>
              <div>
                <Label htmlFor={`contact-email-${i}`}>Email</Label>
                <Input id={`contact-email-${i}`} type="email" value={contact.email ?? ""} onChange={(e) => updateContact(i, { email: e.target.value })} className="mt-1.5" />
              </div>
              <div>
                <Label htmlFor={`contact-phone-${i}`}>Phone</Label>
                <Input id={`contact-phone-${i}`} value={contact.phone ?? ""} onChange={(e) => updateContact(i, { phone: e.target.value })} className="mt-1.5" />
              </div>
              <Button
                type="button" variant="ghost" size="icon" aria-label="Remove contact"
                onClick={() => setContacts((rows) => rows.filter((_, idx) => idx !== i))}
                disabled={contacts.length === 1}
              >
                <Trash2 size={16} />
              </Button>
            </div>
          ))}
          <Button type="button" variant="outline" size="sm" onClick={() => setContacts((rows) => [...rows, emptyContact()])}>
            <Plus size={14} /> Add contact
          </Button>
        </CardContent>
        <CardFooter className="justify-end gap-3">
          <Button type="submit" disabled={save.isPending}>
            {save.isPending ? "Saving…" : "Save & continue"}
          </Button>
        </CardFooter>
      </Card>
    </form>
  );
}
