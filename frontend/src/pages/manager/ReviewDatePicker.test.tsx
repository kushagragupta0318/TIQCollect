// @vitest-environment jsdom
/**
 * The Overview's date control. What matters is that it stays out of the way
 * on today, and that a reviewed day is both visible and escapable — the page
 * is showing something other than the default and must say so.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { ReviewDatePicker } from "./ReviewDatePicker";
import { todayIso, yesterdayIso } from "@/lib/today";

afterEach(cleanup);

describe("ReviewDatePicker", () => {
  it("is a calendar icon and nothing else while viewing today", () => {
    render(<ReviewDatePicker value="" onChange={() => {}} />);
    expect(screen.getByLabelText("View a past day")).toBeTruthy();
    // No quick buttons on the page until the popover is opened.
    expect(screen.queryByRole("button", { name: "Today" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Yesterday" })).toBeNull();
  });

  it("offers Yesterday and a month grid, but NOT Today, while on today", () => {
    // A "Today" button while already on today does nothing; a dead control
    // teaches the reader to ignore the row it is in.
    render(<ReviewDatePicker value="" onChange={() => {}} />);
    fireEvent.click(screen.getByLabelText("View a past day"));
    expect(screen.getByRole("dialog")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Today" })).toBeNull();
    expect(screen.getByRole("button", { name: "Yesterday" })).toBeTruthy();
    // An OWN grid, not the native <input type=date> popup, which opened as a
    // second floating panel outside this one and looked different per OS.
    expect(screen.getByRole("button", { name: todayIso() })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Previous month" })).toBeTruthy();
  });

  it("offers Today once the manager has left today", () => {
    const onChange = vi.fn();
    render(<ReviewDatePicker value="2026-09-21" onChange={onChange} />);
    fireEvent.click(screen.getByLabelText(/Reviewing 2026-09-21/));
    fireEvent.click(screen.getByRole("button", { name: "Today" }));
    expect(onChange).toHaveBeenCalledWith("");
  });

  it("shows the reviewed day with a way back, only when reviewing", () => {
    const onChange = vi.fn();
    const { rerender } = render(<ReviewDatePicker value="" onChange={onChange} />);
    expect(screen.queryByTitle("Back to today")).toBeNull();

    rerender(<ReviewDatePicker value="2026-09-21" onChange={onChange} />);
    const back = screen.getByTitle("Back to today");
    expect(back.textContent).toMatch(/21 Sep/);

    fireEvent.click(back);
    expect(onChange).toHaveBeenCalledWith("");   // "" is today
  });

  it("reports the chosen day and closes", () => {
    const onChange = vi.fn();
    render(<ReviewDatePicker value="" onChange={onChange} />);
    fireEvent.click(screen.getByLabelText("View a past day"));
    fireEvent.click(screen.getByRole("button", { name: "Yesterday" }));
    expect(onChange).toHaveBeenCalledWith(yesterdayIso());
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("cannot be used to ask for a future day", () => {
    render(<ReviewDatePicker value="" onChange={() => {}} />);
    fireEvent.click(screen.getByLabelText("View a past day"));
    // Today is pickable; tomorrow is rendered but disabled, and the month
    // cannot be advanced past the current one.
    expect((screen.getByRole("button", { name: todayIso() }) as HTMLButtonElement).disabled).toBe(false);
    const tomorrow = new Date(`${todayIso()}T00:00:00Z`);
    tomorrow.setUTCDate(tomorrow.getUTCDate() + 1);
    const tStr = tomorrow.toISOString().slice(0, 10);
    const tCell = screen.queryByRole("button", { name: tStr });
    if (tCell) expect((tCell as HTMLButtonElement).disabled).toBe(true);   // same month
    expect((screen.getByRole("button", { name: "Next month" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("picking today clears the review rather than pinning the date", () => {
    // "" is what the rest of the app reads as "today", so selecting today in
    // the grid must return to the live view, not freeze it on today's date.
    const onChange = vi.fn();
    render(<ReviewDatePicker value="2026-09-21" onChange={onChange} />);
    fireEvent.click(screen.getByLabelText(/Reviewing/));
    fireEvent.click(screen.getByRole("button", { name: "Next month" }));
    fireEvent.click(screen.getByRole("button", { name: todayIso() }));
    expect(onChange).toHaveBeenCalledWith("");
  });

  it("closes on Escape, so the popover is never a trap", () => {
    render(<ReviewDatePicker value="" onChange={() => {}} />);
    fireEvent.click(screen.getByLabelText("View a past day"));
    expect(screen.getByRole("dialog")).toBeTruthy();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("carries the shared header hover class, not a dead Tailwind hover:", () => {
    // The background is an INLINE style (it depends on `reviewing`), and an
    // inline background outranks a utility class — `hover:bg-muted` on this
    // button never fired. index.css's .header-icon-btn rule is what wins.
    render(<ReviewDatePicker value="" onChange={() => {}} />);
    const btn = screen.getByLabelText("View a past day");
    expect(btn.className).toContain("header-icon-btn");
    expect(btn.className).not.toContain("hover:bg-muted");
  });

  it("marks itself active so the hover keeps the brand colour", () => {
    // Without data-active a reviewing (blue) button would go grey on hover.
    const { rerender } = render(<ReviewDatePicker value="" onChange={() => {}} />);
    expect(screen.getByLabelText("View a past day").getAttribute("data-active")).toBeNull();
    rerender(<ReviewDatePicker value="2026-09-21" onChange={() => {}} />);
    expect(screen.getByLabelText(/Reviewing/).getAttribute("data-active")).toBe("true");
  });
});
