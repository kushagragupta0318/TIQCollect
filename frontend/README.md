# TIQCollect frontend

One React 19 + Vite + TypeScript + Tailwind SPA with three route trees: `/agent` (the field
app), `/manager` (the agency view) and `/bank` (the bank portal, in progress). The API it talks
to is the FastAPI backend in `../backend`. The target structure is in
[docs/ARCHITECTURE.md](../docs/ARCHITECTURE.md) §4.

## Run

```bash
docker compose up -d          # from the repo root: API on :8400, this app on :5473
```

or on its own, against a running API:

```bash
npm ci
VITE_API_TARGET=http://localhost:8400 npm run dev
```

## Check a change

```bash
npm run build     # tsc -b + vite build: the real typecheck (never `npx tsc --noEmit`)
npm test          # vitest, pure modules
npm run lint      # eslint; CI fails on errors
```

## Where things go

| Path | Holds |
|---|---|
| `src/api/` | the one axios client (`axios.ts`) and one module per area |
| `src/lib/` | pure helpers (money, geo, event stream, location reporter), unit-tested |
| `src/components/ui/` | primitives: Button, Card, Badge, modals |
| `src/pages/<area>/` | route components, plus the pure logic they were split from (`*.ts` + `*.test.ts`) |
| `src/store/` | zustand: auth, SOS |

Environment variables are read at build time:
- `VITE_API_TARGET` (the dev proxy target)
- `VITE_TILE_URL` (a self-hosted tile server; wins over Mapbox)
- `VITE_MAPBOX_TOKEN`, `VITE_ALLOWED_MAP_HOSTS` (Mapbox tiles and the hosts
  that may use them; without a token the maps use OpenStreetMap:
  `src/lib/mapTiles.ts`)
- `VITE_ENABLE_SIMULATOR` (the `/simulator` page outside dev)

Maps are Leaflet. Point maps with more than 40 points cluster through
`leaflet.markercluster` (`src/components/map/points.ts`). The plugin ships in
its own lazy chunk, `points-*.js`: 35.7 kB (9.4 kB gzip) plus 1.2 kB of CSS,
measured on 2026-09-29. Agent pages never load it: the manager live map
does, and the bank portal (`/bank`) does on entry, because it imports the
agency directory eagerly. Re-measure after `npm run build` with
`ls -l dist/assets/points-*`.

## Install the app on a phone (PWA, 2026-09-24)

The app ships a web manifest and an app-shell service worker
(`src/lib/pwaConfig.ts`). Both exist **only in a build**: the dev server
(`npm run dev`, and the Docker `web` container) registers no service worker,
so hot reload and the `/simulator` frames behave as they always have.

To install on a phone on the same Wi-Fi:

1. Create the LAN certificate once — the `openssl` command in the comment at
   the top of `vite.config.ts` writes `certs/dev-{key,cert}.pem` for your LAN IP.
2. `npm run build`
3. `HTTPS=1 npm run preview` — on **port 4173, never the dev port 5473**.
   `vite preview` inherits the dev server's HTTPS certificate, `host: true`
   and `/api` proxy, so the phone talks to one origin as it does in
   development. The port matters: a service worker belongs to its origin, so a
   preview on 5473 would install one that the dev server (and the Docker `web`
   container, same port) never replaces, and dev would go on serving the
   preview's old code. The dev build removes any worker it finds on its own
   origin, but only once it loads.
4. On the phone, open `https://<your-lan-ip>:4173`, trust the certificate,
   then **Add to Home screen** (Android Chrome: ⋮ menu; iOS Safari: Share).

**The certificate has to be trusted on the phone**, not just clicked past: a
browser will not register a service worker for a page whose certificate it
has not accepted, and the app then runs as an ordinary web page, uninstalled.

The worker never touches `/api` (the SSE stream included), `/ws`, `/docs`,
`/redoc` or `/openapi.json`, and caches no API response — offline data is a
separate task (I02).

**Updates.** An open tab looks for a new build hourly and whenever it comes
back to the foreground. When one is waiting, a toast offers **Reload**, and
only the tab where Reload is tapped reloads. Any other open tab — the other
frame of the simulator, say — shows "updated in another tab" and keeps its
page until its user reloads, so a half-filled visit form is never lost.

**Rolling back.** Removing the plugin does not uninstall the worker from
phones that have it. Ship one build with `selfDestroying: true` in
`src/lib/pwaConfig.ts` (the worker unregisters itself and clears its caches
on the next visit), then remove the plugin.
