// The reviewed day, shared between ManagerLayout's header (where the calendar
// control lives, beside the notification bell) and the Overview page (whose
// figures it governs).
//
// A context rather than props because the layout renders <Outlet />: there is
// no direct parent-child edge between the header and the page. Deliberately
// tiny — one string and a setter. It is NOT a general "manager page state"
// store and should not become one; a second unrelated value in here is a sign
// the thing needing it should own its own state instead.
//
// The context object and its hook live in useReviewDay.ts (same split as
// BeatContext/useBeat, for the same fast-refresh rule).
import { useMemo, useState, type ReactNode } from "react";
import { ReviewDayContext } from "./useReviewDay";

export function ReviewDayProvider({ children }: { children: ReactNode }) {
  const [reviewDay, setReviewDay] = useState("");
  const value = useMemo(() => ({ reviewDay, setReviewDay }), [reviewDay]);
  return <ReviewDayContext.Provider value={value}>{children}</ReviewDayContext.Provider>;
}
