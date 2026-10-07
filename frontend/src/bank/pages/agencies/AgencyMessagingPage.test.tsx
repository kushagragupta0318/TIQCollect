// @vitest-environment jsdom
/**
 * The bank's half of bank<->agency messaging. Three behaviours worth a test:
 *
 *  · the inbox renders a thread's title, counterparty and pending badge, and
 *    selecting a row loads and shows that thread's messages;
 *  · sending a reply posts only the body — sender_side is never sent, it is
 *    derived server-side (services/messaging_service.py._side);
 *  · an ISSUE thread offers the resolve/close control; a REVERSAL thread
 *    (no mutable status of its own) does not.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import api from "@/api/axios";
import type { InboxThread, ThreadDetail } from "@/api/messaging";
import AgencyMessagingPage from "./AgencyMessagingPage";

vi.mock("react-router", () => ({ useNavigate: () => vi.fn() }));
vi.mock("@/api/axios", () => ({ default: { get: vi.fn(), post: vi.fn() } }));

const INBOX: InboxThread[] = [
  {
    thread_id: "t-issue-1", subject_type: "ISSUE", subject_id: "issue-1",
    title: "Settlement discount not applied", status: "OPEN", counterparty: "AGENCY",
    // Deliberately not identical to the thread's own message body below — a
    // real preview is a truncation, and both text nodes coexist once the
    // thread is open, so the test needs them distinguishable.
    last_message: { sender_side: "AGENCY", preview: "Can you check the last three accounts please", at: "2026-10-07T09:00:00Z" },
    pending: true, unread: true, message_count: 2,
  },
  {
    thread_id: "t-reversal-1", subject_type: "REVERSAL", subject_id: "rev-1",
    title: "Reversal rev1234", status: "OPEN", counterparty: "AGENCY",
    last_message: { sender_side: "BANK", preview: "Reversal approved, processing now.", at: "2026-10-06T12:00:00Z" },
    pending: false, unread: false, message_count: 3,
  },
];

const ISSUE_THREAD: ThreadDetail = {
  thread: { id: "t-issue-1", subject_type: "ISSUE", subject_id: "issue-1", status: "OPEN", bank_id: "b1", agency_id: "a1" },
  subject_type: "ISSUE", subject_id: "issue-1",
  messages: [
    { id: "m1", sender_user_id: "u1", sender_side: "AGENCY", body: "Can you check the last three accounts?", created_at: "2026-10-07T09:00:00Z" },
  ],
};

const REVERSAL_THREAD: ThreadDetail = {
  thread: { id: "t-reversal-1", subject_type: "REVERSAL", subject_id: "rev-1", status: "OPEN", bank_id: "b1", agency_id: "a1" },
  subject_type: "REVERSAL", subject_id: "rev-1",
  messages: [
    // Deliberately NOT identical to the inbox row's last_message.preview
    // below — a real preview is a truncation, not always a byte match, and
    // the test needs the two text nodes distinguishable once both render.
    { id: "m2", sender_user_id: "u2", sender_side: "BANK", body: "Reversal approved. We're processing it now and will confirm once posted.", created_at: "2026-10-06T12:00:00Z" },
  ],
};

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={qc}><AgencyMessagingPage /></QueryClientProvider>);
}

afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});

describe("AgencyMessagingPage", () => {
  it("lists the inbox and opens a thread's messages on selection", async () => {
    vi.mocked(api.get).mockImplementation((url: string) => {
      if (url === "/messaging/threads") return Promise.resolve({ data: { threads: INBOX } });
      if (url === "/messaging/threads/ISSUE/issue-1") return Promise.resolve({ data: ISSUE_THREAD });
      return Promise.reject(new Error(`unexpected GET ${url}`));
    });
    renderPage();

    expect(await screen.findByText("Settlement discount not applied")).toBeTruthy();
    expect(screen.getByText("Reversal rev1234")).toBeTruthy();
    expect(screen.getByText("Awaiting your reply")).toBeTruthy();

    fireEvent.click(screen.getByText("Settlement discount not applied"));
    expect(await screen.findByText("Can you check the last three accounts?")).toBeTruthy();
    // ISSUE threads get the status control; this is one.
    expect(screen.getByDisplayValue("Open")).toBeTruthy();
  });

  it("sends a reply with only the body, never a sender_side", async () => {
    vi.mocked(api.get).mockImplementation((url: string) => {
      if (url === "/messaging/threads") return Promise.resolve({ data: { threads: INBOX } });
      if (url === "/messaging/threads/ISSUE/issue-1") return Promise.resolve({ data: ISSUE_THREAD });
      return Promise.reject(new Error(`unexpected GET ${url}`));
    });
    vi.mocked(api.post).mockResolvedValue({ data: { ...ISSUE_THREAD, messages: [...ISSUE_THREAD.messages] } });
    renderPage();

    fireEvent.click(await screen.findByText("Settlement discount not applied"));
    await screen.findByText("Can you check the last three accounts?");

    fireEvent.change(screen.getByPlaceholderText(/^Reply…/), { target: { value: "Checked — refund issued." } });
    fireEvent.click(screen.getByRole("button", { name: /Send/ }));

    await waitFor(() => expect(vi.mocked(api.post)).toHaveBeenCalledWith(
      "/messaging/threads/ISSUE/issue-1/messages", { body: "Checked — refund issued." }));
  });

  it("shows the reply instantly and clears the input, before the server responds", async () => {
    vi.mocked(api.get).mockImplementation((url: string) => {
      if (url === "/messaging/threads") return Promise.resolve({ data: { threads: INBOX } });
      if (url === "/messaging/threads/ISSUE/issue-1") return Promise.resolve({ data: ISSUE_THREAD });
      return Promise.reject(new Error(`unexpected GET ${url}`));
    });
    // The mocked round trip never resolves during this test — if the bubble
    // and the cleared textarea only appeared after it, this would time out
    // waiting for them instead of finding them straight away.
    vi.mocked(api.post).mockReturnValue(new Promise(() => {}));
    renderPage();

    fireEvent.click(await screen.findByText("Settlement discount not applied"));
    await screen.findByText("Can you check the last three accounts?");

    const box = screen.getByPlaceholderText(/^Reply…/) as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: "On it, checking now." } });
    fireEvent.click(screen.getByRole("button", { name: /Send/ }));

    expect(await screen.findByText("On it, checking now.")).toBeTruthy();
    // Both the bubble's own timestamp line and the Send button itself say
    // "Sending…" while this is in flight — either is fine proof of it.
    expect(screen.getAllByText("Sending…").length).toBeGreaterThan(0);
    expect(box.value).toBe("");
  });

  it("offers no status control on a REVERSAL thread (status lives on the issue, not the thread)", async () => {
    vi.mocked(api.get).mockImplementation((url: string) => {
      if (url === "/messaging/threads") return Promise.resolve({ data: { threads: INBOX } });
      if (url === "/messaging/threads/REVERSAL/rev-1") return Promise.resolve({ data: REVERSAL_THREAD });
      return Promise.reject(new Error(`unexpected GET ${url}`));
    });
    renderPage();

    fireEvent.click(await screen.findByText("Reversal rev1234"));
    expect(await screen.findByText("Reversal approved. We're processing it now and will confirm once posted.")).toBeTruthy();
    expect(screen.queryByDisplayValue("Open")).toBeNull();
  });
});
