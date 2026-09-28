// Step 4 — Documents. Each of the nine doc types gets its own upload row: the
// four in REQUIRED_DOC_TYPES are marked, the rest are optional. Upload is the
// two-call flow used elsewhere in this app (RecordVisitPage's photo/recording
// uploads) — presign, PUT the file straight to MinIO, then confirm — because
// nothing here trusts the client for the object's real size or content type;
// confirm_document HEADs and hashes what MinIO actually received.
//
// No verify/reject controls here on purpose — that is a bank-admin review
// screen for a later task (brief: "No document verify/reject UI"). This step
// only uploads and shows status.
import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, Clock, FileWarning, Upload } from "lucide-react";
import { toast } from "react-hot-toast";
import { Card, CardContent, CardFooter, CardHeader, CardTitle, CardDescription } from "../../../ui/card";
import { Button } from "../../../ui/button";
import { Input } from "../../../ui/input";
import { Label } from "../../../ui/label";
import { Badge, type BadgeProps } from "../../../ui/badge";
import {
  confirmAgencyDocument, presignAgencyDocument, DOC_TYPES, DOC_TYPE_LABELS, REQUIRED_DOC_TYPES,
  type AgencyDetail, type DocType,
} from "@/api/bank";
import { latestDocumentsByType, missingRequiredDocs } from "../onboardingLogic";
import { errorDetail } from "@/lib/apiError";

interface Props {
  agencyId: string;
  detail: AgencyDetail;
  onUploaded: () => void;
  onContinue: () => void;
}

const ALLOWED_CONTENT_TYPES: Record<string, "application/pdf" | "image/jpeg" | "image/png"> = {
  "application/pdf": "application/pdf",
  "image/jpeg": "image/jpeg",
  "image/png": "image/png",
};

const STATUS_BADGE: Record<string, { variant: NonNullable<BadgeProps["variant"]>; label: string }> = {
  UPLOADED: { variant: "warning", label: "Uploaded · awaiting review" },
  VERIFIED: { variant: "success", label: "Verified" },
  REJECTED: { variant: "destructive", label: "Rejected" },
  EXPIRED: { variant: "destructive", label: "Expired" },
  SUPERSEDED: { variant: "outline", label: "Superseded" },
};

function DocumentRow({
  agencyId, docType, required, current, onUploaded,
}: {
  agencyId: string;
  docType: DocType;
  required: boolean;
  current: AgencyDetail["documents"][number] | undefined;
  onUploaded: () => void;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [issuedOn, setIssuedOn] = useState("");
  const [expiresOn, setExpiresOn] = useState("");

  const upload = useMutation({
    mutationFn: async () => {
      if (!file) throw new Error("Choose a file first.");
      const contentType = ALLOWED_CONTENT_TYPES[file.type];
      if (!contentType) throw new Error("Only PDF, JPEG or PNG files are accepted.");
      const presigned = await presignAgencyDocument(agencyId, docType, contentType);
      const put = await fetch(presigned.upload_url, { method: "PUT", body: file, headers: { "Content-Type": contentType } });
      if (!put.ok) throw new Error(`Upload to storage failed (${put.status}).`);
      return confirmAgencyDocument(agencyId, {
        doc_type: docType, key: presigned.key, file_name: file.name,
        issued_on: issuedOn || undefined, expires_on: expiresOn || undefined,
      });
    },
    onSuccess: () => {
      toast.success(`${DOC_TYPE_LABELS[docType]} uploaded`);
      setFile(null); setIssuedOn(""); setExpiresOn("");
      onUploaded();
    },
    // errorDetail reads err.response.data.detail when there is one (a failed
    // presign/confirm call) and otherwise falls back — here, to the plain
    // Error thrown above for a missing file or an unsupported type.
    onError: (err) => toast.error(errorDetail(err, err instanceof Error ? err.message : "Upload failed")),
  });

  const badge = current ? STATUS_BADGE[current.status] : null;

  return (
    <div className="rounded-inner border border-border/60 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <p className="text-[13px] font-semibold text-foreground">{DOC_TYPE_LABELS[docType]}</p>
          {required && <Badge variant="outline" className="text-[9.5px] uppercase">Required</Badge>}
        </div>
        {badge && <Badge variant={badge.variant}>{badge.label}</Badge>}
      </div>

      {current && (
        <div className="mt-2 space-y-1 text-[12px] text-muted-foreground">
          <p>{current.file_name ?? "Uploaded file"}{current.size_bytes ? ` · ${Math.ceil(current.size_bytes / 1024)} KB` : ""}</p>
          {current.status === "REJECTED" && current.rejection_reason && (
            <p className="flex items-center gap-1.5 text-destructive">
              <AlertTriangle size={13} /> {current.rejection_reason}
            </p>
          )}
          {(current.issued_on || current.expires_on) && (
            <p>
              {current.issued_on ? `Issued ${current.issued_on}` : ""}
              {current.issued_on && current.expires_on ? " · " : ""}
              {current.expires_on ? `Expires ${current.expires_on}` : ""}
            </p>
          )}
        </div>
      )}

      <div className="mt-3 grid gap-2 sm:grid-cols-[1fr_auto_auto_auto] sm:items-end">
        <div>
          <Label htmlFor={`doc-file-${docType}`}>{current ? "Replace with a new file" : "File"}</Label>
          <input
            id={`doc-file-${docType}`} type="file" accept=".pdf,.jpg,.jpeg,.png"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            className="mt-1.5 block w-full text-[12.5px] text-foreground file:mr-3 file:rounded-control file:border file:border-input file:bg-card file:px-3 file:py-1.5 file:text-[12.5px] file:font-medium"
          />
        </div>
        <div>
          <Label htmlFor={`doc-issued-${docType}`}>Issued on</Label>
          <Input id={`doc-issued-${docType}`} type="date" value={issuedOn} onChange={(e) => setIssuedOn(e.target.value)} className="mt-1.5 w-36" />
        </div>
        <div>
          <Label htmlFor={`doc-expires-${docType}`}>Expires on</Label>
          <Input id={`doc-expires-${docType}`} type="date" value={expiresOn} onChange={(e) => setExpiresOn(e.target.value)} className="mt-1.5 w-36" />
        </div>
        <Button type="button" size="sm" disabled={!file || upload.isPending} onClick={() => upload.mutate()}>
          <Upload size={14} /> {upload.isPending ? "Uploading…" : "Upload"}
        </Button>
      </div>
    </div>
  );
}

export function DocumentsStep({ agencyId, detail, onUploaded, onContinue }: Props) {
  const latest = latestDocumentsByType(detail.documents);
  const missing = missingRequiredDocs(detail.documents, detail.required_doc_types.length ? detail.required_doc_types : REQUIRED_DOC_TYPES);

  return (
    <Card>
      <CardHeader>
        <CardTitle>Documents</CardTitle>
        <CardDescription>
          Registration certificate, agreement, insurance and police-verification policy are required before the
          agency can activate. The others are optional.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {missing.length > 0 ? (
          <div className="flex items-center gap-2 rounded-inner bg-warning/10 px-3 py-2 text-[12.5px] text-[#B54708]">
            <FileWarning size={15} className="shrink-0" />
            Still needed: {missing.map((t) => DOC_TYPE_LABELS[t as DocType] ?? t).join(", ")}
          </div>
        ) : (
          <div className="flex items-center gap-2 rounded-inner bg-success/10 px-3 py-2 text-[12.5px] text-[#067647]">
            <CheckCircle2 size={15} className="shrink-0" /> Every required document has been uploaded.
          </div>
        )}

        {DOC_TYPES.map((docType) => (
          <DocumentRow
            key={docType} agencyId={agencyId} docType={docType}
            required={(REQUIRED_DOC_TYPES as readonly string[]).includes(docType)}
            current={latest.get(docType)} onUploaded={onUploaded}
          />
        ))}
      </CardContent>
      <CardFooter className="justify-between">
        <p className="flex items-center gap-1.5 text-[12px] text-muted-foreground">
          <Clock size={13} /> Verification happens on a separate bank-admin screen, not in this wizard.
        </p>
        <Button onClick={onContinue}>Continue to master login</Button>
      </CardFooter>
    </Card>
  );
}
