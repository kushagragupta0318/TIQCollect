// Admin > Bank Users (K01). A bank administrator's view of its own bank's
// staff accounts: invite a new one, move an analyst/techops role, deactivate
// or reactivate an account, and send a reset-login link. Every action is
// BANK_ADMIN-only server-side (require_perm("bank.users.manage")) — this page
// also disables the controls that the server would refuse, so a bank admin is
// never offered an action that only ever 403/404/409s:
//   · a BANK_ADMIN row (which includes the viewer themselves — only an admin
//     reaches this page) has no actions: a fellow admin, and your own account,
//     are the platform admin's / Account Security's to manage, not this page's.
//   · role change is offered only between Analyst and Tech Ops. Promotion to
//     Administrator is by invite, never a live-account flip.
//
// The shapes come from src/api/bankUsers.ts, read off the K01 backend.
import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Ban, Copy, KeyRound, RotateCcw, Send, ShieldCheck, UserPlus } from "lucide-react";
import { toast } from "react-hot-toast";
import { ExecutiveHeader, PageFailure, PageLoading, PageRoot } from "../../components/PageTemplate";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../ui/card";
import { Button } from "../../ui/button";
import { Input } from "../../ui/input";
import { Label } from "../../ui/label";
import { Select } from "../../ui/select";
import { Textarea } from "../../ui/textarea";
import { Badge, type BadgeProps } from "../../ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../../ui/table";
import { Dialog, DialogClose, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "../../ui/dialog";
import {
  BANK_USER_ROLE_LABELS, ROLE_CHANGE_CHOICES, changeBankUserRole, deactivateBankUser, inviteBankUser,
  listBankUsers, reactivateBankUser, resetBankUserLogin,
  type BankUser, type BankUserRole, type InviteBankUserResult,
} from "@/api/bankUsers";
import { errorDetail } from "@/lib/apiError";

const ROLE_BADGE: Record<string, NonNullable<BadgeProps["variant"]>> = {
  BANK_ADMIN: "default",
  BANK_ANALYST: "secondary",
  BANK_TECHOPS: "outline",
};

/** A readable last-login — a date, or a plain "Never" rather than a blank. */
function lastLogin(iso: string | null): string {
  if (!iso) return "Never";
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? "Never"
    : d.toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" });
}

function MfaCell({ user }: { user: BankUser }) {
  if (user.mfa_enabled) return <Badge variant="success">On</Badge>;
  if (user.mfa_required) return <Badge variant="warning">Required</Badge>;
  return <span className="text-[12px] text-muted-foreground">Off</span>;
}

export default function BankUsersPage() {
  const qc = useQueryClient();
  const usersQuery = useQuery({ queryKey: ["bank-users"], queryFn: listBankUsers });
  const [inviteOpen, setInviteOpen] = useState(false);
  const [deactivating, setDeactivating] = useState<BankUser | null>(null);

  const users = useMemo(() => usersQuery.data ?? [], [usersQuery.data]);
  const refresh = () => qc.invalidateQueries({ queryKey: ["bank-users"] });

  const roleMutation = useMutation({
    mutationFn: ({ id, role }: { id: string; role: BankUserRole }) => changeBankUserRole(id, role),
    onSuccess: () => { toast.success("Role updated"); refresh(); },
    onError: (err) => toast.error(errorDetail(err, "Could not change the role")),
  });
  const reactivateMutation = useMutation({
    mutationFn: (id: string) => reactivateBankUser(id),
    onSuccess: () => { toast.success("Account reactivated"); refresh(); },
    onError: (err) => toast.error(errorDetail(err, "Could not reactivate the account")),
  });
  const resetMutation = useMutation({
    mutationFn: (id: string) => resetBankUserLogin(id),
    onSuccess: (res) => toast.success(res.sent ? "A reset link was texted to the user" : "Reset started"),
    onError: (err) => toast.error(errorDetail(err, "Could not start a reset")),
  });

  const header = (
    <ExecutiveHeader
      title="Bank Users"
      meta={["Administration", "Task K01", usersQuery.data ? `${users.length} user${users.length === 1 ? "" : "s"}` : "Loading…"]}
      scopeNote="Your bank's own staff accounts. A bank administrator invites, changes a role, deactivates or resets login — all within your bank."
    />
  );

  if (usersQuery.isLoading) {
    return <PageRoot>{header}<PageLoading label="Loading bank users…" /></PageRoot>;
  }
  if (usersQuery.isError) {
    return (
      <PageRoot>
        {header}
        <PageFailure>{errorDetail(usersQuery.error, "Bank users could not be loaded.")}</PageFailure>
        <div className="flex justify-center">
          <Button variant="outline" onClick={() => usersQuery.refetch()}>Retry</Button>
        </div>
      </PageRoot>
    );
  }

  return (
    <PageRoot>
      {header}

      <Card>
        <CardHeader className="flex flex-row items-start justify-between gap-4">
          <div>
            <CardTitle>Staff accounts</CardTitle>
            {usersQuery.isFetching && <CardDescription>Updating…</CardDescription>}
          </div>
          <Button onClick={() => setInviteOpen(true)}><UserPlus size={14} /> Invite user</Button>
        </CardHeader>
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>User</TableHead>
                <TableHead>Role</TableHead>
                <TableHead>Status</TableHead>
                <TableHead title="Two-factor sign-in">MFA</TableHead>
                <TableHead>Last login</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {users.map((u) => {
                const isAdmin = u.role === "BANK_ADMIN";       // no actions: fellow admin / yourself
                const otherRole: BankUserRole = u.role === "BANK_ANALYST" ? "BANK_TECHOPS" : "BANK_ANALYST";
                const busy = roleMutation.isPending || reactivateMutation.isPending || resetMutation.isPending;
                return (
                  <TableRow key={u.user_id}>
                    <TableCell>
                      <div className="font-semibold text-foreground">{u.full_name}</div>
                      <div className="text-[11px] text-muted-foreground">{u.email}</div>
                    </TableCell>
                    <TableCell>
                      <Badge variant={ROLE_BADGE[u.role] ?? "outline"}>{BANK_USER_ROLE_LABELS[u.role] ?? u.role}</Badge>
                    </TableCell>
                    <TableCell>
                      {u.is_active
                        ? <Badge variant="success">Active</Badge>
                        : <Badge variant="destructive">Deactivated</Badge>}
                    </TableCell>
                    <TableCell><MfaCell user={u} /></TableCell>
                    <TableCell className="text-[12px] text-muted-foreground tabular-nums">{lastLogin(u.last_login_at)}</TableCell>
                    <TableCell>
                      {isAdmin ? (
                        <span className="block text-right text-[11px] text-muted-foreground">Administrator</span>
                      ) : (
                        <div className="flex flex-wrap items-center justify-end gap-1.5">
                          {ROLE_CHANGE_CHOICES.includes(u.role as (typeof ROLE_CHANGE_CHOICES)[number]) && u.is_active && (
                            <Button
                              size="xs" variant="outline" disabled={busy}
                              onClick={() => roleMutation.mutate({ id: u.user_id, role: otherRole })}
                              title={`Make this user ${BANK_USER_ROLE_LABELS[otherRole]}`}
                            >
                              <ShieldCheck size={12} /> Make {BANK_USER_ROLE_LABELS[otherRole]}
                            </Button>
                          )}
                          {u.is_active ? (
                            <Button
                              size="xs" variant="destructive" disabled={busy}
                              onClick={() => setDeactivating(u)}
                            >
                              <Ban size={12} /> Deactivate
                            </Button>
                          ) : (
                            <Button
                              size="xs" variant="success" disabled={busy}
                              onClick={() => reactivateMutation.mutate(u.user_id)}
                            >
                              <RotateCcw size={12} /> Reactivate
                            </Button>
                          )}
                          <Button
                            size="xs" variant="outline" disabled={busy || !u.is_active}
                            onClick={() => resetMutation.mutate(u.user_id)}
                            title={u.is_active ? "Text a set-password link to this user" : "Reactivate the account first"}
                          >
                            <KeyRound size={12} /> Reset login
                          </Button>
                        </div>
                      )}
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
          {users.length === 0 && (
            <p className="py-6 text-center text-[13px] text-muted-foreground">No bank users yet. Invite your first.</p>
          )}
        </CardContent>
      </Card>

      <InviteDialog open={inviteOpen} onClose={() => setInviteOpen(false)} onInvited={refresh} />
      <DeactivateDialog
        user={deactivating}
        onClose={() => setDeactivating(null)}
        onDone={() => { setDeactivating(null); refresh(); }}
      />
    </PageRoot>
  );
}

function InviteDialog({ open, onClose, onInvited }: { open: boolean; onClose: () => void; onInvited: () => void }) {
  const [fullName, setFullName] = useState("");
  const [email, setEmail] = useState("");
  const [phone, setPhone] = useState("");
  const [role, setRole] = useState<BankUserRole>("BANK_ANALYST");
  const [channel, setChannel] = useState<"LINK" | "SMS">("LINK");
  const [result, setResult] = useState<InviteBankUserResult | null>(null);

  const send = useMutation({
    mutationFn: () => inviteBankUser({ full_name: fullName.trim(), email: email.trim(), phone: phone.trim(), role, channel }),
    onSuccess: (res) => {
      toast.success(res.delivered ? "Invitation sent" : "Invitation created");
      setResult(res);
      onInvited();
    },
    onError: (err) => toast.error(errorDetail(err, "Could not send the invitation")),
  });

  const link = result?.path ? `${window.location.origin}${result.path}` : null;

  function reset() {
    setFullName(""); setEmail(""); setPhone(""); setRole("BANK_ANALYST"); setChannel("LINK"); setResult(null);
  }

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) { reset(); onClose(); } }}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Invite a bank user</DialogTitle>
          <DialogDescription>
            They set their own password from the link — you never see or choose it. Email delivery is not configured;
            share the link yourself, or send it by SMS.
          </DialogDescription>
          <DialogClose onClose={() => { reset(); onClose(); }} />
        </DialogHeader>
        <form
          className="grid gap-4 px-6 py-5 sm:grid-cols-2"
          onSubmit={(e) => { e.preventDefault(); send.mutate(); }}
        >
          <div className="sm:col-span-2">
            <Label htmlFor="bu-name">Full name</Label>
            <Input id="bu-name" required value={fullName} onChange={(e) => setFullName(e.target.value)} className="mt-1.5" />
          </div>
          <div>
            <Label htmlFor="bu-email">Email</Label>
            <Input id="bu-email" type="email" required value={email} onChange={(e) => setEmail(e.target.value)} className="mt-1.5" />
          </div>
          <div>
            <Label htmlFor="bu-phone">Phone</Label>
            <Input id="bu-phone" required value={phone} onChange={(e) => setPhone(e.target.value)} className="mt-1.5" />
          </div>
          <div>
            <Label htmlFor="bu-role">Role</Label>
            <Select id="bu-role" className="mt-1.5" value={role} onChange={(e) => setRole(e.target.value as BankUserRole)}>
              <option value="BANK_ANALYST">{BANK_USER_ROLE_LABELS.BANK_ANALYST}</option>
              <option value="BANK_TECHOPS">{BANK_USER_ROLE_LABELS.BANK_TECHOPS}</option>
              <option value="BANK_ADMIN">{BANK_USER_ROLE_LABELS.BANK_ADMIN}</option>
            </Select>
          </div>
          <div>
            <Label htmlFor="bu-channel">Delivery</Label>
            <Select id="bu-channel" className="mt-1.5" value={channel} onChange={(e) => setChannel(e.target.value as "LINK" | "SMS")}>
              <option value="LINK">Link — I'll share it myself</option>
              <option value="SMS">SMS</option>
            </Select>
          </div>
          <div className="sm:col-span-2">
            <Button type="submit" disabled={send.isPending}>
              <Send size={14} /> {send.isPending ? "Sending…" : "Send invite"}
            </Button>
          </div>
        </form>

        {link && (
          <div className="mx-6 mb-5 flex flex-wrap items-center gap-2 rounded-inner bg-accent px-3 py-2 text-[12.5px]">
            <span className="min-w-0 flex-1 truncate text-accent-foreground">{link}</span>
            <Button
              type="button" variant="outline" size="xs"
              onClick={() => { navigator.clipboard?.writeText(link); toast.success("Link copied"); }}
            >
              <Copy size={12} /> Copy
            </Button>
          </div>
        )}
        <DialogFooter>
          <Button variant="outline" onClick={() => { reset(); onClose(); }}>Close</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function DeactivateDialog(
  { user, onClose, onDone }: { user: BankUser | null; onClose: () => void; onDone: () => void },
) {
  const [reason, setReason] = useState("");
  const mutate = useMutation({
    mutationFn: () => deactivateBankUser(user!.user_id, reason.trim()),
    onSuccess: () => { toast.success("Account deactivated"); setReason(""); onDone(); },
    onError: (err) => toast.error(errorDetail(err, "Could not deactivate the account")),
  });

  return (
    <Dialog open={user !== null} onOpenChange={(o) => { if (!o) { setReason(""); onClose(); } }}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Deactivate {user?.full_name}</DialogTitle>
          <DialogDescription>
            They are signed out of every device at once and cannot sign in until reactivated. Give a reason — it is
            recorded in the audit trail.
          </DialogDescription>
          <DialogClose onClose={() => { setReason(""); onClose(); }} />
        </DialogHeader>
        <form
          className="px-6 py-5"
          onSubmit={(e) => { e.preventDefault(); if (reason.trim()) mutate.mutate(); }}
        >
          <Label htmlFor="bu-reason">Reason</Label>
          <Textarea id="bu-reason" required value={reason} onChange={(e) => setReason(e.target.value)} className="mt-1.5" rows={3} />
        </form>
        <DialogFooter>
          <Button variant="outline" onClick={() => { setReason(""); onClose(); }}>Cancel</Button>
          <Button
            variant="destructive"
            disabled={mutate.isPending || !reason.trim()}
            onClick={() => mutate.mutate()}
          >
            <Ban size={14} /> {mutate.isPending ? "Deactivating…" : "Deactivate"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
