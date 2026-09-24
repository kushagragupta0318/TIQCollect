// The top bar's "Raise a Query" dialog, built from the ported primitives. The
// bank portal has no support desk to send it to yet (plan §5.1 has none; spec
// §8 S5 lists no bank counterpart), so the form is complete and Submit says
// honestly that it is not connected, rather than pretending to send.
import { useState } from "react";
import { Button } from "../ui/button";
import { Dialog, DialogClose, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "../ui/dialog";
import { Input } from "../ui/input";
import { Label } from "../ui/label";
import { Select } from "../ui/select";
import { Textarea } from "../ui/textarea";

const CATEGORIES = ["Figure looks wrong", "Access or permissions", "Report or export", "Agency or placement", "Something else"];

export function RaiseQueryDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const [category, setCategory] = useState(CATEGORIES[0]);
  const [subject, setSubject] = useState("");
  const [details, setDetails] = useState("");

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogClose onClose={() => onOpenChange(false)} />
        <DialogHeader>
          <DialogTitle>Raise a Query</DialogTitle>
          <DialogDescription>Tell the platform team what you need and which screen it is about.</DialogDescription>
        </DialogHeader>
        <div className="px-6 py-5 space-y-4 overflow-y-auto">
          <div className="space-y-1.5">
            <Label htmlFor="bank-query-category">Category</Label>
            <Select id="bank-query-category" value={category} onChange={(e) => setCategory(e.target.value)}>
              {CATEGORIES.map((c) => (
                <option key={c}>{c}</option>
              ))}
            </Select>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="bank-query-subject">Subject</Label>
            <Input id="bank-query-subject" value={subject} onChange={(e) => setSubject(e.target.value)} placeholder="e.g. GNPA differs from the monthly MIS" />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="bank-query-details">Details</Label>
            <Textarea id="bank-query-details" value={details} onChange={(e) => setDetails(e.target.value)} placeholder="What you expected, what you saw, and the filters you had on." />
          </div>
        </div>
        <DialogFooter>
          <span className="mr-auto text-[12px] text-muted-foreground">Query routing is not connected yet.</span>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button disabled title="Query routing is not connected yet">
            Submit query
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
