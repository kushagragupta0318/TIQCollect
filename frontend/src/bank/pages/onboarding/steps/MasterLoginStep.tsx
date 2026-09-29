// Step 5 — Master Login. Invites the agency's AGENCY_ADMIN. EMAIL delivery
// is left out of the channel choice on purpose: invite_service.create_invite
// always answers EMAIL with a 503 ("Email delivery is not configured") in
// this environment, so offering it here would be an option that only ever
// fails — the same honesty this codebase applies to Twilio's sandbox number
// and the live-equivalent model figures elsewhere.
import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Copy, Send } from "lucide-react";
import { toast } from "react-hot-toast";
import { Card, CardContent, CardFooter, CardHeader, CardTitle, CardDescription } from "../../../ui/card";
import { Button } from "../../../ui/button";
import { Input } from "../../../ui/input";
import { Label } from "../../../ui/label";
import { Select } from "../../../ui/select";
import { Badge, type BadgeProps } from "../../../ui/badge";
import { inviteAgencyMasterLogin, type Agency, type InviteMasterLoginResult, type InviteSummary } from "@/api/bank";
import { errorDetail } from "@/lib/apiError";

interface Props {
  agencyId: string;
  agency: Agency;
  invite: InviteSummary | null;
  onInvited: () => void;
  onContinue: () => void;
}

const INVITE_STATUS_BADGE: Record<string, NonNullable<BadgeProps["variant"]>> = {
  OPEN: "warning", ACCEPTED: "success", REVOKED: "destructive", EXPIRED: "destructive",
};

export function MasterLoginStep({ agencyId, agency, invite, onInvited, onContinue }: Props) {
  const [fullName, setFullName] = useState(() => agency.contact_name ?? "");
  const [email, setEmail] = useState(() => agency.contact_email ?? "");
  const [phone, setPhone] = useState(() => agency.contact_phone ?? "");
  const [channel, setChannel] = useState<"LINK" | "SMS">("LINK");
  const [result, setResult] = useState<InviteMasterLoginResult | null>(null);

  const send = useMutation({
    mutationFn: () => inviteAgencyMasterLogin(agencyId, { full_name: fullName.trim(), email: email.trim(), phone: phone.trim(), channel }),
    onSuccess: (res) => {
      toast.success(res.delivered ? "Invitation sent" : "Invitation created");
      setResult(res);
      onInvited();
    },
    onError: (err) => toast.error(errorDetail(err, "Could not send the invitation")),
  });

  const linkToCopy = result?.path ? `${window.location.origin}${result.path}` : null;

  return (
    <Card>
      <CardHeader>
        <CardTitle>Master login</CardTitle>
        <CardDescription>
          Invites this person as the agency's AGENCY_ADMIN — the account that signs in and manages the agency's own
          users once it activates.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {invite && (
          <div className="flex flex-wrap items-center gap-2 rounded-inner border border-border/60 px-3 py-2 text-[12.5px]">
            <span className="text-muted-foreground">Current invite:</span>
            <span className="font-medium text-foreground">{invite.full_name} · {invite.email}</span>
            <Badge variant={INVITE_STATUS_BADGE[invite.status] ?? "outline"}>{invite.status}</Badge>
          </div>
        )}

        <form
          className="grid gap-4 sm:grid-cols-3"
          onSubmit={(e) => { e.preventDefault(); send.mutate(); }}
        >
          <div>
            <Label htmlFor="ml-name">Full name</Label>
            <Input id="ml-name" required value={fullName} onChange={(e) => setFullName(e.target.value)} className="mt-1.5" />
          </div>
          <div>
            <Label htmlFor="ml-email">Email</Label>
            <Input id="ml-email" type="email" required value={email} onChange={(e) => setEmail(e.target.value)} className="mt-1.5" />
          </div>
          <div>
            <Label htmlFor="ml-phone">Phone</Label>
            <Input id="ml-phone" required value={phone} onChange={(e) => setPhone(e.target.value)} className="mt-1.5" />
          </div>
          <div>
            <Label htmlFor="ml-channel">Delivery</Label>
            <Select id="ml-channel" className="mt-1.5" value={channel} onChange={(e) => setChannel(e.target.value as "LINK" | "SMS")}>
              <option value="LINK">Link — I'll share it myself</option>
              <option value="SMS">SMS</option>
            </Select>
          </div>
          <div className="flex items-end sm:col-span-2">
            <Button type="submit" disabled={send.isPending}>
              <Send size={14} /> {send.isPending ? "Sending…" : "Send invite"}
            </Button>
          </div>
        </form>

        {linkToCopy && (
          <div className="flex flex-wrap items-center gap-2 rounded-inner bg-accent px-3 py-2 text-[12.5px]">
            <span className="min-w-0 flex-1 truncate text-accent-foreground">{linkToCopy}</span>
            <Button
              type="button" variant="outline" size="xs"
              onClick={() => { navigator.clipboard.writeText(linkToCopy); toast.success("Link copied"); }}
            >
              <Copy size={12} /> Copy
            </Button>
          </div>
        )}
        {result && result.delivered === false && !linkToCopy && (
          <p className="text-[12px] text-muted-foreground">
            The invitation was created but could not be delivered automatically. Ask the agency to check with the bank.
          </p>
        )}
      </CardContent>
      <CardFooter className="justify-end">
        <Button variant="outline" onClick={onContinue}>Continue to review</Button>
      </CardFooter>
    </Card>
  );
}
