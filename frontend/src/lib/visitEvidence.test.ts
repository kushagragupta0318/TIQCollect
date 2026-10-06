import { describe, expect, it } from "vitest";
import {
  DOCUMENT_CATEGORY_IDS, DOCUMENT_CATEGORY_LABELS, DOCUMENT_MAX_BYTES, documentProblem, escalationFields,
} from "./visitEvidence";

describe("documentProblem", () => {
  it("accepts the types the server issues an upload for", () => {
    for (const type of ["image/jpeg", "image/png", "image/webp", "application/pdf"]) {
      expect(documentProblem({ type, size: 1000 })).toBeNull();
    }
  });

  it("refuses a type the server would not issue an upload for, in words an agent can act on", () => {
    expect(documentProblem({ type: "image/heic", size: 1000 })).toMatch(/photo .* or a PDF/);
    expect(documentProblem({ type: "", size: 1000 })).not.toBeNull();
    expect(documentProblem({ type: "application/zip", size: 1000 })).not.toBeNull();
  });

  it("refuses a file over the size cap, and accepts one exactly at it", () => {
    expect(documentProblem({ type: "application/pdf", size: DOCUMENT_MAX_BYTES })).toBeNull();
    expect(documentProblem({ type: "application/pdf", size: DOCUMENT_MAX_BYTES + 1 })).toMatch(/10 MB/);
  });
});

describe("escalationFields", () => {
  const form = { escalationNotes: "  Refused at the door  ", witnessPresent: true, witnessName: " Neighbour " };

  it("sends nothing when the outcome did not ask for them", () => {
    expect(escalationFields(false, form)).toEqual({});
  });

  it("sends the trimmed notes, the witness, and the trimmed witness name", () => {
    expect(escalationFields(true, form)).toEqual({
      escalation_notes: "Refused at the door", witness_present: true, witness_name: "Neighbour",
    });
  });

  it("never sends a witness name without a witness", () => {
    expect(escalationFields(true, { ...form, witnessPresent: false }).witness_name).toBeUndefined();
    expect(escalationFields(true, { ...form, witnessPresent: false }).witness_present).toBe(false);
  });

  it("sends no notes for a blank box", () => {
    expect(escalationFields(true, { ...form, escalationNotes: "   " }).escalation_notes).toBeUndefined();
  });
});

describe("the category list", () => {
  it("has a label for every id, and no duplicates", () => {
    expect(new Set(DOCUMENT_CATEGORY_IDS).size).toBe(DOCUMENT_CATEGORY_IDS.length);
    for (const id of DOCUMENT_CATEGORY_IDS) expect(DOCUMENT_CATEGORY_LABELS[id]).toBeTruthy();
  });
});
