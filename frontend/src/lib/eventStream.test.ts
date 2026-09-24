import { describe, expect, it } from "vitest";
import { parseSseBlock, splitSseBuffer } from "./eventStream";

const EVENT = {
  id: "e1",
  type: "visit.recorded",
  at: "2026-09-24T06:00:00+00:00",
  agent_id: "a1",
  agent_name: "Asha",
  data: { case_id: "c1" },
};

describe("splitSseBuffer", () => {
  it("keeps an unfinished block for the next chunk", () => {
    const { blocks, rest } = splitSseBuffer("event: a\ndata: {}\n\nevent: b\ndata: {");
    expect(blocks).toEqual(["event: a\ndata: {}"]);
    expect(rest).toBe("event: b\ndata: {");
  });

  it("handles CRLF framing", () => {
    expect(splitSseBuffer("data: x\r\n\r\n").blocks).toEqual(["data: x"]);
  });
});

describe("parseSseBlock", () => {
  it("parses the server's event block", () => {
    const block = `event: visit.recorded\ndata: ${JSON.stringify(EVENT)}`;
    expect(parseSseBlock(block)).toEqual(EVENT);
  });

  it("ignores the connect comment, heartbeats and retry hints", () => {
    expect(parseSseBlock(": connected")).toBeNull();
    expect(parseSseBlock(": ping")).toBeNull();
    expect(parseSseBlock("retry: 3000")).toBeNull();
  });

  it("drops malformed data rather than throwing", () => {
    expect(parseSseBlock("data: {not json")).toBeNull();
    expect(parseSseBlock('data: {"type":"x"}')).toBeNull(); // no id → cannot de-duplicate
  });
});
