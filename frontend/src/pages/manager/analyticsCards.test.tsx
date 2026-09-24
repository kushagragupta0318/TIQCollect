// @vitest-environment jsdom
/**
 * How the duty calendar and the monthly report follow the page's selection.
 *
 * Written before their prop-to-state sync moved out of effects (lint
 * react-hooks/set-state-in-effect), so the refactor is held to the behaviour
 * the page already had.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { AgentAvailabilityCalendar, AgentPerfEntry } from "@/api/manager";
import { getMonthlyReport } from "@/api/manager";
import { DutyCalendarCard } from "./DutyCalendarCard";
import { MonthlyReportSection } from "./MonthlyReportSection";

vi.mock("@/api/manager", () => ({ getMonthlyReport: vi.fn() }));

afterEach(() => {
  cleanup();
  vi.mocked(getMonthlyReport).mockReset();
});

function calendar(agentId: string, months: string[]): AgentAvailabilityCalendar {
  return {
    agent_id: agentId,
    agent_name: `Agent ${agentId}`,
    current_status: "ON_DUTY",
    calendar: months.map((m) => ({
      date: `${m}-02`, day_of_week: "Wed", status: "ON_DUTY" as const, beat_status: "COMPLETED", cases: 3,
    })),
    summary: { total_working_days: 0, on_duty_days: 0, off_duty_days: 0, attendance_rate_pct: 0 },
    monthly_summary: [],
  };
}

const heading = (label: string) => screen.queryByText(label, { exact: false });

describe("DutyCalendarCard", () => {
  it("opens on the latest month that has data", () => {
    render(<DutyCalendarCard cal={calendar("A", ["2026-08", "2026-09"])} loading={false} />);
    expect(heading("September 2026")).not.toBeNull();
  });

  it("jumps to the month the page asks for", () => {
    const cal = calendar("A", ["2026-08", "2026-09"]);
    const { rerender } = render(<DutyCalendarCard cal={cal} loading={false} />);
    rerender(<DutyCalendarCard cal={cal} loading={false} jumpToMonth="2026-08" />);
    expect(heading("August 2026")).not.toBeNull();
    expect(heading("September 2026")).toBeNull();
  });

  it("applies a jump given on the first render", () => {
    render(<DutyCalendarCard cal={calendar("A", ["2026-08", "2026-09"])} loading={false} jumpToMonth="2026-08" />);
    expect(heading("August 2026")).not.toBeNull();
  });

  it("ignores a jump to a month with no data", () => {
    render(<DutyCalendarCard cal={calendar("A", ["2026-08", "2026-09"])} loading={false} jumpToMonth="2026-07" />);
    expect(heading("September 2026")).not.toBeNull();
  });

  it("re-applies the jump when a new calendar arrives that has the month", () => {
    const { rerender } = render(
      <DutyCalendarCard cal={calendar("A", ["2026-09"])} loading={false} jumpToMonth="2026-08" />,
    );
    expect(heading("September 2026")).not.toBeNull();
    rerender(<DutyCalendarCard cal={calendar("B", ["2026-08", "2026-09"])} loading={false} jumpToMonth="2026-08" />);
    expect(heading("August 2026")).not.toBeNull();
  });
});

const agent = (id: string) => ({ agent_id: id, agent_name: `Agent ${id}` }) as AgentPerfEntry;
const MONTHS = ["2026-07", "2026-08", "2026-09"];
const selectedMonth = () => (screen.getByLabelText("Report month") as HTMLSelectElement).value;

async function generateReport(text: string) {
  vi.mocked(getMonthlyReport).mockResolvedValueOnce({
    month: "2026-08", scope: "agent", report_text: text, ai_generated: true, ai_status: "ok",
  });
  fireEvent.click(screen.getByText("Generate Report"));
  await screen.findByText(text);
}

describe("MonthlyReportSection", () => {
  it("defaults to the second-latest month", () => {
    render(<MonthlyReportSection months={MONTHS} selectedAgent={null} />);
    expect(selectedMonth()).toBe("2026-08");
  });

  it("selects a pre-selected month given on the first render", () => {
    render(<MonthlyReportSection months={MONTHS} selectedAgent={null} preSelectedMonth="2026-07" />);
    expect(selectedMonth()).toBe("2026-07");
  });

  it("ignores a pre-selected month that is not in the list", () => {
    render(<MonthlyReportSection months={MONTHS} selectedAgent={null} preSelectedMonth="2025-01" />);
    expect(selectedMonth()).toBe("2026-08");
  });

  it("clears a generated report when the agent changes", async () => {
    const { rerender } = render(<MonthlyReportSection months={MONTHS} selectedAgent={agent("A")} />);
    await generateReport("Report for agent A");
    rerender(<MonthlyReportSection months={MONTHS} selectedAgent={agent("B")} />);
    expect(screen.queryByText("Report for agent A")).toBeNull();
  });

  it("keeps the report when the page re-renders with the same agent", async () => {
    const { rerender } = render(<MonthlyReportSection months={MONTHS} selectedAgent={agent("A")} />);
    await generateReport("Report for agent A");
    rerender(<MonthlyReportSection months={MONTHS} selectedAgent={agent("A")} />);
    expect(screen.queryByText("Report for agent A")).not.toBeNull();
  });

  it("a new pre-selected month selects it and clears the report", async () => {
    const { rerender } = render(<MonthlyReportSection months={MONTHS} selectedAgent={agent("A")} />);
    await generateReport("Report for agent A");
    rerender(<MonthlyReportSection months={MONTHS} selectedAgent={agent("A")} preSelectedMonth="2026-07" />);
    expect(selectedMonth()).toBe("2026-07");
    expect(screen.queryByText("Report for agent A")).toBeNull();
  });
});
