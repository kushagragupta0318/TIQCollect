/**
 * I01 — the service-worker lifecycle decisions (lib/swLifecycle.ts): which
 * tab reloads, when a tab looks for a new build, and the dev-origin cleanup.
 */
import { describe, expect, it, vi } from "vitest";
import {
  UPDATE_CHECK_MIN_GAP_MS, createSwLifecycle, unregisterAllWorkers,
} from "./swLifecycle";

function setup(start = 1_000_000) {
  let t = start;
  const deps = {
    promptUpdate: vi.fn<(apply: () => void) => void>(),
    warnUpdatedElsewhere: vi.fn<(reload: () => void) => void>(),
    reload: vi.fn(),
    now: () => t,
  };
  const life = createSwLifecycle(deps);
  const apply = vi.fn();
  life.setApplyUpdate(apply);
  return { deps, life, apply, advance: (ms: number) => { t += ms; } };
}

describe("which tab reloads", () => {
  it("the tab whose Reload was tapped applies the update and reloads", () => {
    const { deps, life, apply } = setup();
    life.onNeedRefresh();
    const tapReload = deps.promptUpdate.mock.calls[0][0];
    tapReload();
    expect(apply).toHaveBeenCalledOnce();
    life.onNeedReload();                       // the new worker took control
    expect(deps.reload).toHaveBeenCalledOnce();
    expect(deps.warnUpdatedElsewhere).not.toHaveBeenCalled();
  });

  it("a tab that only SAW the prompt is warned, not reloaded", () => {
    const { deps, life, apply } = setup();
    life.onNeedRefresh();                      // prompt shown, never tapped here
    life.onNeedReload();                       // another tab applied it
    expect(apply).not.toHaveBeenCalled();
    expect(deps.reload).not.toHaveBeenCalled();
    expect(deps.warnUpdatedElsewhere).toHaveBeenCalledOnce();
    // The warning's own button can still reload this tab, when its user chooses.
    deps.warnUpdatedElsewhere.mock.calls[0][0]();
    expect(deps.reload).toHaveBeenCalledOnce();
  });
});

describe("looking for a new build", () => {
  it("checks, then not again within the minimum gap, then again after it", () => {
    const { life, advance } = setup();
    const reg = { update: vi.fn().mockResolvedValue(undefined) };
    expect(life.maybeCheckForUpdate(reg)).toBe(true);
    advance(UPDATE_CHECK_MIN_GAP_MS - 1);
    expect(life.maybeCheckForUpdate(reg)).toBe(false);
    advance(1);
    expect(life.maybeCheckForUpdate(reg)).toBe(true);
    expect(reg.update).toHaveBeenCalledTimes(2);
  });

  it("an offline check does not throw", async () => {
    const { life } = setup();
    const reg = { update: vi.fn().mockRejectedValue(new TypeError("Failed to fetch")) };
    expect(life.maybeCheckForUpdate(reg)).toBe(true);
    await Promise.resolve();                   // the rejection is swallowed, not unhandled
  });
});

describe("the dev origin is kept clean", () => {
  it("unregisters every worker and deletes only Workbox caches", async () => {
    const regs = [{ unregister: vi.fn().mockResolvedValue(true) },
                  { unregister: vi.fn().mockResolvedValue(true) }];
    const container = { getRegistrations: vi.fn().mockResolvedValue(regs) };
    const deleted: string[] = [];
    const cacheStorage = {
      keys: vi.fn().mockResolvedValue(["workbox-precache-v2-http://localhost:5473/", "tiq-offline-drafts"]),
      delete: vi.fn(async (k: string) => { deleted.push(k); return true; }),
    };
    const n = await unregisterAllWorkers(container as never, cacheStorage);
    expect(n).toBe(2);
    for (const r of regs) expect(r.unregister).toHaveBeenCalledOnce();
    expect(deleted).toEqual(["workbox-precache-v2-http://localhost:5473/"]);
  });

  it("is a no-op where service workers do not exist", async () => {
    expect(await unregisterAllWorkers(undefined)).toBe(0);
  });
});
