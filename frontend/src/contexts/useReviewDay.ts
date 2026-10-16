/**
 * The reviewed-day context object and the hook that reads it.
 *
 * SPLIT OUT OF ReviewDayContext.tsx for the same reason useBeat.ts was split
 * out of BeatContext.tsx: a file that exports both a component (the provider)
 * and non-components (the hook, the types) trips
 * react-refresh/only-export-components. Named for the hook, not the context,
 * so `./useReviewDay` cannot resolve to `ReviewDayContext` on a
 * case-insensitive filesystem.
 */
import { createContext, useContext } from "react";

export interface ReviewDayValue {
  /** "" means today. A YYYY-MM-DD string means that past day is being reviewed. */
  reviewDay: string;
  setReviewDay: (day: string) => void;
}

export const ReviewDayContext = createContext<ReviewDayValue>({
  reviewDay: "",
  setReviewDay: () => {},
});

export function useReviewDay(): ReviewDayValue {
  return useContext(ReviewDayContext);
}
