// The current global filter, read from the URL, and a setter that writes it back.
import { useSearchParams } from "react-router";
import { filterFromParams, filterToParams, type KpiFilter } from "./kpiFilter";

export function useKpiFilter(): [KpiFilter, (next: KpiFilter) => void] {
  const [params, setParams] = useSearchParams();
  return [filterFromParams(params), (next) => setParams(filterToParams(next), { replace: true })];
}
