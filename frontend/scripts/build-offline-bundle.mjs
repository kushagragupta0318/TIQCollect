// Rebuilds the static, backend-free bundle that Command Center's Field Recovery
// page frames.
//
//   node field-ops-stub/frontend/scripts/build-offline-bundle.mjs
//
// It builds THIS frontend in `offline` mode (see ../.env.offline) and writes the
// result to ../../backend/static/tiqcollect-offline/, which stub_main.py mounts
// at /tiqcollect — so the Field Ops service serves the Field Recovery UI and
// Command Center just points an iframe at it. In that mode the app replays
// ../public/offline/snapshots.json through its offline axios adapter, so neither
// this frontend nor the TIQCollect backend needs to be running.
//
// To refresh the DATA (not the code), re-capture the snapshots against a live
// backend and then re-run this build:
//   1. start the TIQCollect backend on :8400 (see ../../backend)
//   2. python field-ops-stub/frontend/scripts/capture-offline-snapshots.py \
//        http://127.0.0.1:8400 field-ops-stub/frontend/public/offline/snapshots.json --refresh
//
// Note: vite build is used directly rather than `npm run build`, because that
// script also runs `tsc -b`, which fails on type errors that pre-date this
// integration (mock/sampleData.ts, agent pages).
import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const frontendDir = path.resolve(here, "..");
const outDir = path.resolve(here, "../../backend/static/tiqcollect-offline");

// Vite's JS entry is invoked with this Node binary rather than via npx, which
// avoids the shell (spawning npx.cmd without one fails with EINVAL on Windows).
const viteBin = path.join(frontendDir, "node_modules", "vite", "bin", "vite.js");
if (!existsSync(viteBin)) {
  console.error(`vite not installed — run: npm --prefix field-ops-stub/frontend install`);
  process.exit(1);
}

const result = spawnSync(
  process.execPath,
  [viteBin, "build", "--mode", "offline", "--base=./", "--outDir", outDir, "--emptyOutDir"],
  { cwd: frontendDir, stdio: "inherit" }
);

if (result.error) {
  console.error(result.error.message);
  process.exit(1);
}
process.exit(result.status ?? 1);
