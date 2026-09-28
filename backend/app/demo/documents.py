# ─── CHANGELOG (standalone plan) ────────────────────────────────────────────
# 2026-09-28 (B16, d4) — NEW. Appendix C.3's agency documents as specimen
#   PDFs. Each is a one-page PDF built by hand (no PDF dependency added),
#   carrying the agency's invented particulars and, on every page, the footer
#   "Specimen — fictional demo document". The bytes are a pure function of the
#   roster, with no timestamp inside, so the sha256 recorded on
#   tenancy.agency_documents is stable across runs. A pg_dump carries no MinIO
#   objects, so a restored box re-uploads them from here, idempotently
#   (scripts/ensure_demo_documents.py, run by the entrypoint after a restore).
# ────────────────────────────────────────────────────────────────────────────
"""Specimen agency documents: deterministic bytes, a stable key, an idempotent upload."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date

from app.demo import roster as R


@dataclass(frozen=True)
class Specimen:
    agency_key: str
    doc_type: str
    title: str
    issued_on: date
    expires_on: date | None
    storage_key: str
    body: bytes

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.body).hexdigest()


def storage_key(agency: R.AgencyRoster, doc_type: str) -> str:
    return f"agency-documents/{agency.id}/{doc_type.lower()}.pdf"


def _esc(s: str) -> bytes:
    """A PDF literal string in WinAnsi (cp1252 has the em dash; the rupee
    sign does not, so amounts are written 'Rs.')."""
    b = s.encode("cp1252", errors="replace")
    return b.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def pdf_bytes(title: str, lines: list[str], footer: str = R.SPECIMEN_FOOTER) -> bytes:
    """A valid one-page A4 PDF with Helvetica text. Deterministic."""
    y = 780
    ops = [b"BT /F1 18 Tf 56 %d Td (%s) Tj ET" % (y, _esc(title))]
    y -= 18
    ops.append(b"0.6 G 56 %d m 539 %d l S 0 G" % (y, y))
    y -= 30
    for line in lines:
        ops.append(b"BT /F1 11 Tf 56 %d Td (%s) Tj ET" % (y, _esc(line)))
        y -= 18
    ops.append(b"0.8 0 0 rg BT /F1 10 Tf 56 40 Td (%s) Tj ET 0 g" % _esc(footer))
    ops.append(b"0.85 g BT /F1 60 Tf 0.707 0.707 -0.707 0.707 140 300 Tm (SPECIMEN) Tj ET 0 g")
    content = b"\n".join(ops)
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> "
        b"/Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        b"<< /Length %d >>\nstream\n%s\nendstream" % (len(content), content),
    ]
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (i, body)
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return bytes(out)


def _issued(agency: R.AgencyRoster, doc_type: str) -> date:
    """Issue dates that tell the onboarding story: corporate documents years
    before, the agreement and policies around onboarding."""
    start = agency.contract["start_date"]
    if doc_type == "INCORPORATION_CERT":
        year = int(agency.row["cin"][8:12]) if agency.row["entity_type"] == "PVT_LTD" else start.year - 3
        return date(year, 4 + (sum(map(ord, agency.key)) % 6), 11)
    if doc_type in ("GST", "PAN"):
        inc = _issued(agency, "INCORPORATION_CERT")
        return date(inc.year, min(inc.month + 2, 12), 3)
    # AGREEMENT / INSURANCE / POLICE_VERIFICATION_POLICY / DRA_REGISTER
    return date(start.year, start.month, 1) if start.day > 5 else start


def _expires(agency: R.AgencyRoster, doc_type: str, years: int | None, issued: date) -> date | None:
    if doc_type in agency.expiring_docs:
        return date.fromordinal(R.ANCHOR_DATE.toordinal() + agency.expiring_docs[doc_type])
    if years is None:
        return None
    return date.fromordinal(issued.replace(year=issued.year + years).toordinal() - 1)


def specimens(agency: R.AgencyRoster) -> list[Specimen]:
    """Every document the agency has uploaded (Appendix C.3 minus missing_docs)."""
    row, c = agency.row, agency.contract
    addr = row["registered_address"]
    out = []
    for doc_type, years, title in R.AGENCY_DOCUMENTS:
        if doc_type in agency.missing_docs:
            continue
        issued = _issued(agency, doc_type)
        expires = _expires(agency, doc_type, years, issued)
        lines = [
            f"Name: {row['legal_name']}",
            f"{'LLPIN' if row['entity_type'] == 'LLP' else 'CIN'}: {row['cin']}",
            f"PAN: {row['pan']}    GSTIN: {row['gstin']}",
            f"Registered office: {addr['line1']}, {addr['city']}, {addr['state']} {addr['pincode']}",
            f"Date of issue: {issued.isoformat()}",
            f"Valid until: {expires.isoformat() if expires else 'not applicable'}",
            "",
        ]
        if doc_type == "AGREEMENT":
            bank = R.BANKS[agency.bank_key]
            lines += [f"Between {bank['legal_name']} and {row['legal_name']}",
                      f"Contract no. {c['contract_no']}, {c['start_date']} to {c['end_date']}",
                      f"Placed-case capacity {c['max_placed_cases']}, agent seats {c['max_agents']}",
                      f"First-visit SLA {c['sla_first_visit_days']} days; security deposit Rs. "
                      f"{int(c['security_deposit']):,}"]
        elif doc_type == "INSURANCE":
            lines += ["Professional indemnity cover: Rs. 50,00,000 aggregate",
                      "Insurer: a fictional general insurer (specimen)"]
        elif doc_type == "DRA_REGISTER":
            lines += [f"Field staff certified under the DRA programme: {agency.n_agents}",
                      "Certificate numbers and expiry dates: see the agent register in TIQCollect"]
        elif doc_type == "POLICE_VERIFICATION_POLICY":
            lines += ["Every field agent is police-verified before deployment and every 2 years"]
        for p in agency.people:
            lines.append(f"{p['role']}: {p['name']}")
        out.append(Specimen(agency.key, doc_type, title, issued, expires, storage_key(agency, doc_type),
                            pdf_bytes(title, lines)))
    return out


def upload_missing(specs: list[Specimen]) -> tuple[int, int]:
    """Put each specimen in MinIO unless its key already exists. Returns
    (uploaded, already_there). Raises if MinIO is unreachable: the caller
    decides whether that is fatal."""
    import io

    from app.core import storage
    storage.ensure_bucket()
    client = storage._client()          # the one configured client (core/storage.py)
    up = have = 0
    for s in specs:
        if storage.key_exists(s.storage_key):
            have += 1
            continue
        client.put_object(storage.BUCKET, s.storage_key, io.BytesIO(s.body), len(s.body),
                          content_type="application/pdf")
        up += 1
    return up, have
