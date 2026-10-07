// @vitest-environment jsdom
/**
 * The Audit page's own decisions: that an actor-less row reads as the system
 * rather than as a blank, that a sensitive action is marked, and — the one
 * that matters — that the "pending attribution" count is SHOWN when the
 * server reports one.
 *
 * That last test is the page's honesty guarantee. Known issue 6 means some
 * events carry no tenant and cannot be shown to any bank; a page that dropped
 * them silently would read as a quiet week to the one person whose job is to
 * notice it was not.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";

import { AuditPage } from "./AuditPage";
import { actionLabel, when, type AuditPayload } from "./auditModel";

vi.mock("@/api/axios", () => ({ default: { get: vi.fn() } }));
const api = (await import("@/api/axios")).default as unknown as { get: ReturnType<typeof vi.fn> };

afterEach(() => {
  cleanup();
  api.get.mockReset();
});

function payload(over: Partial<AuditPayload> = {}): AuditPayload {
  return {
    since: "2026-10-01T00:00:00+00:00",
    total: 2,
    limit: 50,
    offset: 0,
    entries: [
      { id: "1", created_at: "2026-10-06T09:30:00+00:00", action: "MODEL_PROMOTED",
        actor_name: "Ananya Iyer", actor_id: "u1", agency_id: null, entity_type: "Model",
        entity_id: null, success: true, failure_reason: null, ip_address: "10.0.0.2" },
      { id: "2", created_at: "2026-10-06T08:00:00+00:00", action: "PTP_UPDATED",
        actor_name: null, actor_id: null, agency_id: "a1", entity_type: "PTP",
        entity_id: null, success: true, failure_reason: null, ip_address: null },
    ],
    counts_by_action: { MODEL_PROMOTED: 1, PTP_UPDATED: 1 },
    coverage: {
      declared_action_types: 52,
      sensitive_actions: ["MODEL_PROMOTED"],
      pending_attribution: 0,
      window_days: 7,
      note: "Rows written with no actor and no entity tenant carry no bank.",
    },
    ...over,
  };
}

/** MemoryRouter, not optional: the page reads its deep-link defaults with
 *  useSearchParams, which throws outside a Router. Every test here rendered
 *  without one and so every test here was failing -- the file went red when
 *  the `action` deep link landed and nothing said so. `at` is the URL the
 *  reader arrived on. */
function show(body: AuditPayload, at = "/bank/audit") {
  api.get.mockResolvedValue({ data: body });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <MemoryRouter initialEntries={[at]}>
      <QueryClientProvider client={qc}>
        <AuditPage />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

/** The params the page sent on its last request. */
function lastParams(): Record<string, unknown> {
  return (api.get.mock.calls.at(-1)?.[1]?.params ?? {}) as Record<string, unknown>;
}

describe("AuditPage", () => {
  it("lists the bank's recorded actions", async () => {
    show(payload());
    await waitFor(() => expect(screen.getByText("Model promoted")).toBeTruthy());
    expect(screen.getByText("Ananya Iyer")).toBeTruthy();
    expect(screen.getByText(/2 recorded actions/)).toBeTruthy();
  });

  it("names the system as the actor rather than leaving a blank", async () => {
    show(payload());
    await waitFor(() => expect(screen.getByText("(system)")).toBeTruthy());
  });

  it("marks a sensitive action, and only a sensitive one", async () => {
    show(payload());
    await waitFor(() => expect(screen.getByText("Model promoted")).toBeTruthy());
    expect(screen.getAllByText("sensitive")).toHaveLength(1);
  });

  it("SAYS how many events are pending attribution, and that the count is platform-wide", async () => {
    show(payload({ coverage: { ...payload().coverage, pending_attribution: 4 } }));
    await waitFor(() => expect(screen.getByText(/4 system events pending attribution, platform-wide/)).toBeTruthy());
    // Not "4 of YOUR events": the count cannot be narrowed to one bank, which
    // is the whole reason those rows are unattributed.
    expect(screen.getByText(/cannot be narrowed to yours/)).toBeTruthy();
  });

  it("states the window rather than implying it shows everything", async () => {
    show(payload());
    await waitFor(() => expect(screen.getByText(/Last 7 days, newest first/)).toBeTruthy());
  });

  it("says nothing about attribution when there is nothing to admit", async () => {
    show(payload());
    await waitFor(() => expect(screen.getByText("Model promoted")).toBeTruthy());
    expect(screen.queryByText(/pending attribution/)).toBeNull();
  });

  it("shows an empty window as empty rather than as an error", async () => {
    show(payload({ total: 0, entries: [], counts_by_action: {} }));
    await waitFor(() => expect(screen.getByText(/No recorded actions in this window/)).toBeTruthy());
  });
});

describe("helpers", () => {
  it("reads an action as a label, not as shouting", () => {
    expect(actionLabel("MODEL_PROMOTED")).toBe("Model promoted");
    expect(actionLabel("LOGIN")).toBe("Login");
  });

  it("renders a missing or unparseable timestamp as a dash", () => {
    expect(when(null)).toBe("—");
    expect(when("not a date")).toBe("—");
  });

  it("asks for one entity's rows when it is deep-linked to one", async () => {
    // What dd's thread panel links to: this thread's own entries, not every
    // MESSAGE_SENT in the bank filtered client-side.
    show(payload(), "/bank/audit?entity_type=MessageThread&entity_id=aaaa-bbbb");
    await waitFor(() => expect(screen.getByText("Model promoted")).toBeTruthy());
    expect(lastParams().entity_type).toBe("MessageThread");
    expect(lastParams().entity_id).toBe("aaaa-bbbb");
  });

  it("SAYS it is pinned to one entity, and offers the whole trail", async () => {
    // A pinned view that does not say so reads as a quiet week, which is the
    // same fault as hiding the pending-attribution count.
    show(payload(), "/bank/audit?entity_type=MessageThread&entity_id=aaaa-bbbb");
    await waitFor(() => expect(screen.getByRole("note")).toBeTruthy());
    expect(screen.getByRole("note").textContent).toMatch(/not the whole trail/);
    expect(screen.getByText("Show everything")).toBeTruthy();
  });

  it("sends no entity params when it was not deep-linked", async () => {
    show(payload());
    await waitFor(() => expect(screen.getByText("Model promoted")).toBeTruthy());
    expect("entity_type" in lastParams()).toBe(false);
    expect("entity_id" in lastParams()).toBe(false);
    expect(screen.queryByRole("note")).toBeNull();
  });
});
