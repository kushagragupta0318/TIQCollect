import { describe, expect, it, vi } from "vitest";
import { rowActivation, type RowActivationProps } from "./rowActivation";

// The DataTable/HeatGrid call site pattern for a row that is NOT clickable —
// `onRowClick ? rowActivation(...) : {}` — never calls this module at all.
const nonClickableRowProps = (onRowClick?: () => void): Partial<RowActivationProps> =>
  onRowClick ? rowActivation(onRowClick) : {};

describe("rowActivation — a clickable row is named for assistive tech", () => {
  it("defaults to role=button and is keyboard-focusable", () => {
    const props = rowActivation(vi.fn());
    expect(props.role).toBe("button");
    expect(props.tabIndex).toBe(0);
  });

  it("a row that navigates instead can say so", () => {
    const props = rowActivation(vi.fn(), undefined, "link");
    expect(props.role).toBe("link");
    expect(props.tabIndex).toBe(0);
  });

  it("a non-clickable row gets neither a role nor a tabIndex", () => {
    const props = nonClickableRowProps(undefined);
    expect(props.role).toBeUndefined();
    expect(props.tabIndex).toBeUndefined();
  });

  it("a clickable row (the same ternary, with a handler) gets both", () => {
    const props = nonClickableRowProps(vi.fn());
    expect(props.role).toBe("button");
    expect(props.tabIndex).toBe(0);
  });
});
