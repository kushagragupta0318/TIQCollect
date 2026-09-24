import { useState } from "react";
import { Calendar } from "lucide-react";
import type { AgentAvailabilityCalendar, AgentCalendarDay } from "@/api/manager";
import { CAL_MUTED } from "./calendarTheme";

// ── Agent Duty Calendar — proper month calendar with navigation ────────────────

export function DutyCalendarCard({ cal, loading, jumpToMonth }: { cal: AgentAvailabilityCalendar; loading: boolean; jumpToMonth?: string }) {
  const monthsAvailable = [...new Set(cal.calendar.map((d) => d.date.slice(0, 7)))].sort();
  const [visibleMonth, setVisibleMonth] = useState(
    monthsAvailable[monthsAvailable.length - 1] ?? ""
  );

  // Follow the page's jumpToMonth, re-applied when the jump or the calendar
  // changes. Adjusted during render rather than in an effect, so the card never
  // paints the old month first.
  const [synced, setSynced] = useState<{ jump?: string; cal?: AgentAvailabilityCalendar }>({});
  if (synced.jump !== jumpToMonth || synced.cal !== cal) {
    setSynced({ jump: jumpToMonth, cal });
    if (jumpToMonth && monthsAvailable.includes(jumpToMonth)) setVisibleMonth(jumpToMonth);
  }

  const monthIdx   = monthsAvailable.indexOf(visibleMonth);
  const canPrev    = monthIdx > 0;
  const canNext    = monthIdx < monthsAvailable.length - 1;
  const isOn       = cal.current_status === "ON_DUTY";

  const dayMap = new Map(
    cal.calendar.filter((d) => d.date.startsWith(visibleMonth)).map((d) => [d.date, d])
  );

  const today = new Date().toISOString().split("T")[0];
  const [year, month] = visibleMonth ? visibleMonth.split("-").map(Number) : [0, 0];
  const lastDay = year ? new Date(year, month, 0).getDate() : 0;
  const firstDow = year ? new Date(year, month - 1, 1).getDay() : 1; // 0=Sun
  const padCols = firstDow === 0 ? 0 : firstDow - 1;

  type Cell = null | { dayNum: number; dateStr: string; data: AgentCalendarDay | null; isFuture: boolean };

  const allCells: Cell[] = Array(padCols).fill(null);
  for (let d = 1; d <= lastDay; d++) {
    const dow = new Date(year, month - 1, d).getDay();
    if (dow === 0) continue;
    const dateStr = `${year}-${String(month).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
    allCells.push({ dayNum: d, dateStr, data: dayMap.get(dateStr) ?? null, isFuture: dateStr > today });
  }
  while (allCells.length % 6 !== 0) allCells.push(null);
  const calRows: Cell[][] = [];
  for (let i = 0; i < allCells.length; i += 6) calRows.push(allCells.slice(i, i + 6));

  // Attendance: count from actual rendered cells — on = has beat data, off = past working day with no data
  const pastCells = allCells.filter((c): c is NonNullable<Cell> => c !== null && !c.isFuture);
  const onDutyDays    = pastCells.filter((c) => c.data !== null).length;
  const offDutyDays   = pastCells.filter((c) => c.data === null).length;
  const attendancePct = pastCells.length > 0 ? Math.round(onDutyDays / pastCells.length * 100) : 0;

  const DOW_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  const visibleLabel = visibleMonth
    ? new Date(visibleMonth + "-01").toLocaleDateString("en-IN", { month: "long", year: "numeric" })
    : "";

  return (
    <div className="card p-4">
      <div className="flex items-center justify-between mb-1">
        <div className="flex items-center gap-2">
          <Calendar className="w-4 h-4 text-brand-600" />
          <h2 className="text-base font-bold" style={{ color: "#1C1C1F" }}>Duty Calendar</h2>
        </div>
        <span className="text-xs px-2 py-0.5 rounded-full font-semibold"
          style={{ background: isOn ? "rgba(22,163,74,0.10)" : "rgba(220,38,38,0.10)", color: isOn ? "#16a34a" : "#dc2626" }}>
          {isOn ? "● On Duty" : "○ Off Duty"}
        </span>
      </div>
      <p className="text-xs mb-4" style={{ color: "#6B6D76" }}>
        <span className="font-semibold" style={{ color: "#1677FF" }}>{cal.agent_name}</span>
        {" · "}{attendancePct}% attendance · {onDutyDays} on / {offDutyDays} off
      </p>

      {loading ? (
        <div className="h-48 rounded-xl animate-pulse" style={{ background: "#EFF0F4" }} />
      ) : (
        <>
          {/* Month navigator */}
          <div className="flex items-center justify-between mb-3">
            <button
              onClick={() => canPrev && setVisibleMonth(monthsAvailable[monthIdx - 1])}
              disabled={!canPrev}
              style={{
                width: 28, height: 28, borderRadius: 8, border: "1px solid #EAEBEF",
                background: canPrev ? "#F5F6F9" : "transparent",
                color: canPrev ? "#1C1C1F" : "#D1D5DB",
                cursor: canPrev ? "pointer" : "default",
                fontSize: 16, display: "flex", alignItems: "center", justifyContent: "center",
                fontWeight: 600,
              }}
            >‹</button>
            <p className="text-sm font-semibold" style={{ color: "#1C1C1F" }}>{visibleLabel}</p>
            <button
              onClick={() => canNext && setVisibleMonth(monthsAvailable[monthIdx + 1])}
              disabled={!canNext}
              style={{
                width: 28, height: 28, borderRadius: 8, border: "1px solid #EAEBEF",
                background: canNext ? "#F5F6F9" : "transparent",
                color: canNext ? "#1C1C1F" : "#D1D5DB",
                cursor: canNext ? "pointer" : "default",
                fontSize: 16, display: "flex", alignItems: "center", justifyContent: "center",
                fontWeight: 600,
              }}
            >›</button>
          </div>

          {/* Calendar grid */}
          <div style={{ display: "grid", gridTemplateColumns: "repeat(6, 1fr)", gap: "2px 0" }}>
            {DOW_LABELS.map((lbl) => (
              <div key={lbl} style={{ textAlign: "center", fontSize: "var(--cal-dow)", fontWeight: 600, color: CAL_MUTED, paddingBottom: 3 }}>
                {lbl}
              </div>
            ))}
            {calRows.flatMap((row, ri) =>
              row.map((cell, ci) => {
                if (!cell) return <div key={`${ri}-${ci}`} style={{ height: "var(--cal-cell-agent)" }} />;
                const hasData = !!cell.data;
                const numColor = cell.isFuture ? CAL_MUTED : hasData ? "#16a34a" : "#ef4444";

                return (
                  <div
                    key={`${ri}-${ci}`}
                    title={
                      cell.isFuture ? cell.dateStr
                      : hasData     ? `${cell.dateStr} · On Duty · ${cell.data!.cases} cases`
                      :               `${cell.dateStr} · Off Duty`
                    }
                    style={{ textAlign: "center", height: "var(--cal-cell-agent)", display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center" }}
                  >
                    <p style={{ fontSize: 11, fontWeight: 600, color: numColor, lineHeight: 1 }}>
                      {cell.dayNum}
                    </p>
                  </div>
                );
              })
            )}
          </div>

          {/* Legend */}
          <div className="flex items-center gap-3 mt-2 pt-2" style={{ borderTop: "1px solid #F3F4F6" }}>
            {[
              { label: "On Duty",  color: "#16a34a" },
              { label: "Off Duty", color: "#ef4444" },
              { label: "Upcoming", color: CAL_MUTED },
            ].map(({ label, color: c }) => (
              <div key={label} className="flex items-center gap-1">
                <span style={{ fontSize: 11, fontWeight: 600, color: c }}>●</span>
                <span className="text-xs" style={{ color: "#94a3b8" }}>{label}</span>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
