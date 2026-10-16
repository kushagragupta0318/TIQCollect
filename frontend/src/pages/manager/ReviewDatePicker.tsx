// The Overview's date control: a calendar icon, and nothing else, until the
// manager actually goes looking at a past day.
//
// It replaced a permanent Today / Yesterday / date-input row. That row cost
// three controls' worth of space on every page load for a feature used
// occasionally, and read as page furniture rather than as an action. Here the
// quick options live inside the popover, so they still exist but cost nothing
// until asked for.
//
// When a past day IS selected the control grows a chip naming that day with a
// way back to today — the one state where a permanent affordance earns its
// place, because the page is now showing something other than what a manager
// expects by default and must say so.
import { useEffect, useRef, useState } from "react";
import { CalendarDays, ChevronLeft, ChevronRight, X } from "lucide-react";
import { todayIso, yesterdayIso } from "@/lib/today";

/** "Sun, 21 Sep" — enough to recognise the day without eating the row. */
function chipLabel(iso: string): string {
  const d = new Date(`${iso}T00:00:00`);
  return Number.isNaN(d.getTime())
    ? iso
    : d.toLocaleDateString("en-IN", { weekday: "short", day: "numeric", month: "short" });
}


/** "October 2026" for the grid's header. */
function monthLabel(ym: string): string {
  return new Date(`${ym}-01T00:00:00`).toLocaleDateString("en-IN", { month: "long", year: "numeric" });
}

/** Step a YYYY-MM cursor by whole months, without tripping over month lengths. */
function addMonths(ym: string, delta: number): string {
  const [y, m] = ym.split("-").map(Number);
  const d = new Date(Date.UTC(y, m - 1 + delta, 1));
  return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, "0")}`;
}

function isCurrentMonth(ym: string): boolean {
  return ym === todayIso().slice(0, 7);
}

/**
 * One month as 7-column cells, Monday first: leading nulls pad to the first
 * weekday, then every day as YYYY-MM-DD.
 *
 * All seven days, unlike DutyCalendarCard's grid which drops Sundays — that
 * one shows a working week, this one is picking any date that happened.
 */
function monthCells(ym: string): (string | null)[] {
  const [y, m] = ym.split("-").map(Number);
  const lastDay = new Date(y, m, 0).getDate();
  const firstDow = new Date(y, m - 1, 1).getDay();   // 0=Sun
  const pad = (firstDow + 6) % 7;                    // Monday-first
  const cells: (string | null)[] = Array(pad).fill(null);
  for (let d = 1; d <= lastDay; d++) {
    cells.push(`${y}-${String(m).padStart(2, "0")}-${String(d).padStart(2, "0")}`);
  }
  return cells;
}

export function ReviewDatePicker({ value, onChange }: {
  /** "" means today. */
  value: string;
  onChange: (day: string) => void;
}) {
  const [open, setOpen] = useState(false);
  // The month on screen. Opens on the reviewed day's month, or this one.
  const [cursor, setCursor] = useState(() => (value || todayIso()).slice(0, 7));
  const wrapRef = useRef<HTMLDivElement>(null);

  // Close on an outside click or Escape — a popover that can only be dismissed
  // by re-clicking its own trigger is a trap on a touch screen.
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const reviewing = value !== "";

  return (
    <div className="flex items-center gap-2 flex-shrink-0" ref={wrapRef}>
      {reviewing && (
        <button
          type="button"
          onClick={() => onChange("")}
          title="Back to today"
          className="tap-target-h inline-flex items-center gap-1.5 rounded-xl px-2.5 py-1.5 text-xs font-semibold transition hover:brightness-95"
          style={{ background: "#EFF6FF", border: "1px solid #BFDBFE", color: "#1D4ED8" }}
        >
          {chipLabel(value)}
          <X className="w-3 h-3" aria-hidden="true" />
          <span className="sr-only">Back to today</span>
        </button>
      )}

      <div className="relative">
        <button
          type="button"
          onClick={() => {
            // Reset here rather than in an effect on `open`: syncing state to
            // a prop inside useEffect is the cascading-render pattern lint
            // rejects (react-hooks/set-state-in-effect), and opening is the
            // actual moment the month should be decided.
            if (!open) setCursor((value || todayIso()).slice(0, 7));
            setOpen((v) => !v);
          }}
          aria-haspopup="dialog"
          aria-expanded={open}
          aria-label={reviewing ? `Reviewing ${value}. Change the day` : "View a past day"}
          title={reviewing ? `Reviewing ${value}` : "View a past day"}
          // Matched to NotificationBell beside it, value for value: 40x40,
          // radius 10, #E1E3E9, #667085, and the same `tap-target` class.
          // It had been 38px / radius 12 / #EAEBEF, which read as a smaller,
          // differently-rounded button in a row of three.
          // `header-icon-btn` carries the hover, not a Tailwind `hover:`
          // class: the background here is an INLINE style (it depends on
          // `reviewing`), and an inline background outranks a utility class,
          // so `hover:bg-muted` on this element never fired at all. The rule
          // in index.css uses the same specificity and so can win.
          className="tap-target header-icon-btn relative flex flex-shrink-0 items-center justify-center"
          data-active={reviewing ? "true" : undefined}
          style={{
            width: 40, height: 40, borderRadius: 10,
            background: reviewing ? "#1677FF" : "#fff",
            border: `1px solid ${reviewing ? "#1677FF" : "#E1E3E9"}`,
            color: reviewing ? "#fff" : "#667085",
          }}
        >
          <CalendarDays className="w-4 h-4" aria-hidden="true" />
        </button>

        {open && (
          <div
            role="dialog"
            aria-label="Pick a day to review"
            className="absolute right-0 z-30 mt-2 rounded-card overflow-hidden"
            style={{ width: 268, background: "#fff", border: "1px solid #E1E3E9",
                     boxShadow: "0 12px 32px rgba(16,24,40,0.14)" }}
          >
            {/* Yesterday (and Today, once you have left it) sit on a tinted
                strip so the shortcuts read as separate from the grid. */}
            <div className="flex gap-1.5 px-3 py-2.5" style={{ background: "#F7F8FA", borderBottom: "1px solid #EAEBEF" }}>
              {[
                ...(reviewing ? [{ k: "", label: "Today" }] : []),
                { k: yesterdayIso(), label: "Yesterday" },
              ].map((o) => (
                <button
                  key={o.label}
                  type="button"
                  onClick={() => { onChange(o.k); setOpen(false); }}
                  aria-pressed={value === o.k}
                  className="tap-target-h flex-1 rounded-control px-2 py-1.5 text-xs font-semibold transition hover:brightness-95"
                  style={{ background: value === o.k ? "#1677FF" : "#fff",
                           border: `1px solid ${value === o.k ? "#1677FF" : "#E1E3E9"}`,
                           color: value === o.k ? "#fff" : "#475467" }}
                >
                  {o.label}
                </button>
              ))}
            </div>

            {/* Month header */}
            <div className="flex items-center justify-between px-3 pt-3 pb-1.5">
              <button type="button" onClick={() => setCursor(addMonths(cursor, -1))}
                      aria-label="Previous month"
                      className="tap-target-h header-icon-btn flex items-center justify-center"
                      style={{ width: 28, height: 28, borderRadius: 8, background: "#fff",
                               border: "1px solid #E1E3E9", color: "#667085" }}>
                <ChevronLeft className="w-3.5 h-3.5" />
              </button>
              <span className="text-[13px] font-bold" style={{ color: "#1C1C1F" }}>
                {monthLabel(cursor)}
              </span>
              <button type="button" onClick={() => setCursor(addMonths(cursor, 1))}
                      aria-label="Next month"
                      disabled={isCurrentMonth(cursor)}
                      className="tap-target-h header-icon-btn flex items-center justify-center disabled:opacity-35 disabled:cursor-not-allowed"
                      style={{ width: 28, height: 28, borderRadius: 8, background: "#fff",
                               border: "1px solid #E1E3E9", color: "#667085" }}>
                <ChevronRight className="w-3.5 h-3.5" />
              </button>
            </div>

            {/* Mon-first, because the working week here starts on Monday. */}
            <div className="grid grid-cols-7 gap-0.5 px-3">
              {["M", "T", "W", "T", "F", "S", "S"].map((d, i) => (
                <span key={i} className="text-[10px] font-semibold text-center py-1"
                      style={{ color: "#98A2B3" }}>{d}</span>
              ))}
            </div>
            <div className="grid grid-cols-7 gap-0.5 px-3 pb-2">
              {monthCells(cursor).map((cell, i) => {
                if (cell === null) return <span key={`p${i}`} />;
                const selected = cell === value;
                const isToday = cell === todayIso();
                const future = cell > todayIso();
                return (
                  <button
                    key={cell}
                    type="button"
                    disabled={future}
                    aria-label={cell}
                    aria-current={isToday ? "date" : undefined}
                    onClick={() => { onChange(cell === todayIso() ? "" : cell); setOpen(false); }}
                    className="day-cell text-[12px] font-semibold"
                    style={{
                      height: 30, borderRadius: 8,
                      background: selected ? "#1677FF" : "transparent",
                      color: future ? "#D0D5DD" : selected ? "#fff" : isToday ? "#1677FF" : "#344054",
                      border: isToday && !selected ? "1px solid #BFDBFE" : "1px solid transparent",
                      cursor: future ? "not-allowed" : "pointer",
                    }}
                  >
                    {Number(cell.slice(8, 10))}
                  </button>
                );
              })}
            </div>

            <p className="px-3 pb-3 text-[10.5px] leading-snug" style={{ color: "#8A8C94" }}>
              Tiles follow this date. The trend keeps its own range.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}

export default ReviewDatePicker;
