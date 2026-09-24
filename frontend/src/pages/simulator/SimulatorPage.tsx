// ─── CHANGELOG (standalone plan) ───────────────────────────────────────────
// 2026-09-24 — New page (tasks P0-03 / P0-04). The agent app is a real
//   mobile web app, but on a laptop there was no way to SEE it as one next to
//   the web portal, or to watch an action on the phone arrive on the manager's
//   screen. This page puts the agent app in a phone frame beside the manager
//   view, drives the phone's GPS (click the map, or play the day's beat along
//   its road geometry), and shows every live event as the manager receives it.
//
//   Three things make it work and each is its own module:
//     - lib/sessionSlot.ts   two logins in one tab (frames share localStorage)
//     - lib/deviceLocation.ts the phone takes its position from this page
//     - lib/eventStream.ts   the manager is pushed events (SSE), not polled
//   The server still enforces everything — RBI contact hours, the 100 m
//   geofence against the submitted fix, OTP — the simulator bypasses nothing.
// ───────────────────────────────────────────────────────────────────────────
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { BatteryFull, MapPin, Pause, Play, RotateCw, SkipBack, Smartphone, Wifi } from "lucide-react";
import { DEFAULT_CENTRE } from "@/components/map/constants";
import { decodePolyline } from "@/components/map/polyline";
import { SIM_FRAME_READY, SIM_GEO_ERROR, SIM_GEO_FIX } from "@/lib/deviceLocation";
import type { StreamStatus } from "@/lib/eventStream";
import { EventTimeline } from "./EventTimeline";
import { GpsPanel, type Stop } from "./GpsPanel";
import { approach, buildTrack, FIELD_SPEED_MPS, positionAt, type LatLng, type Track } from "./routePlayback";
import { AGENT_SLOT, MANAGER_SLOT, frameEntry, readSlotAuth, slotGet, type SlotAuth } from "./simSession";
import { sim } from "./simTheme";

const DEVICES = {
  pixel8: { label: "Pixel 8", w: 412, h: 915, radius: 46 },
  iphone15: { label: "iPhone 15", w: 393, h: 852, radius: 56 },
} as const;
type DeviceId = keyof typeof DEVICES;

const SPEEDS = [1, 5, 20, 60] as const;
const HEARTBEAT_MS = 5_000;   // re-send the current fix so watchers stay fresh
const STOP_APPROACH_M = 25;   // "go to stop" parks this far out — inside the 100 m fence

interface BeatCase {
  id: string;
  case_number?: string;
  customer?: { full_name?: string; latitude?: number | null; longitude?: number | null };
}
interface BeatPayload {
  route_geometry?: string | null;
  start_latitude?: number | null;
  start_longitude?: number | null;
  ordered_case_ids?: string[];
  cases?: BeatCase[];
}

function useSlotAuth(slot: string): SlotAuth {
  const [auth, setAuth] = useState<SlotAuth>(() => readSlotAuth(slot));
  useEffect(() => {
    // A frame writing its session fires `storage` in THIS document (another
    // same-origin browsing context). The interval is the belt to that brace.
    const read = () => setAuth((prev) => {
      const next = readSlotAuth(slot);
      return next.accessToken === prev.accessToken && next.role === prev.role ? prev : next;
    });
    const onStorage = (e: StorageEvent) => { if (!e.key || e.key.endsWith(`:${slot}`)) read(); };
    window.addEventListener("storage", onStorage);
    const id = setInterval(read, 3_000);
    return () => { window.removeEventListener("storage", onStorage); clearInterval(id); };
  }, [slot]);
  return auth;
}

function useViewportHeight(): number {
  const [h, setH] = useState(() => window.innerHeight);
  useEffect(() => {
    const on = () => setH(window.innerHeight);
    window.addEventListener("resize", on);
    return () => window.removeEventListener("resize", on);
  }, []);
  return h;
}

function StatusDot({ status }: { status: StreamStatus }) {
  const map: Record<StreamStatus, [string, string]> = {
    live: [sim.tone.good, "Live — pushed"],
    polling: [sim.tone.warn, "Polling fallback"],
    connecting: [sim.tone.muted, "Connecting…"],
    closed: [sim.tone.muted, "Waiting for manager login"],
  };
  const [color, text] = map[status];
  return (
    <span className={sim.chip}>
      <span className="h-2 w-2 rounded-full" style={{ background: color }} />
      {text}
    </span>
  );
}

/**
 * The manager view at a real desktop width, scaled to fit the panel. Beside a
 * phone and a controls column the panel is ~750 px wide — below the manager
 * app's lg breakpoint — so an unscaled frame showed the manager app's MOBILE
 * layout (bottom tab bar), which is not what a manager at a desk sees.
 */
const DESKTOP_W = 1440;

function DesktopFrame({ src }: { src: string }) {
  const box = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 0, h: 0 });
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(([entry]) =>
      setSize({ w: entry.contentRect.width, h: entry.contentRect.height }));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const scale = size.w > 0 ? Math.min(1, size.w / DESKTOP_W) : 1;
  return (
    <div ref={box} className="relative min-h-0 flex-1 overflow-hidden">
      {size.w > 0 && (
        <iframe
          name="tiq-slot:manager"
          src={src}
          title="Manager view"
          style={{
            position: "absolute", top: 0, left: 0, border: 0,
            width: size.w / scale, height: size.h / scale,
            transform: `scale(${scale})`, transformOrigin: "top left",
          }}
        />
      )}
      {scale < 1 && (
        <span className="pointer-events-none absolute bottom-2 right-3 rounded-full bg-white/90 px-2 py-0.5 text-[10px] text-[#98A2B3]">
          {DESKTOP_W}px desktop · {Math.round(scale * 100)}%
        </span>
      )}
    </div>
  );
}

function Clock() {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 15_000);
    return () => clearInterval(id);
  }, []);
  return <>{now.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", hour12: false })}</>;
}

export default function SimulatorPage() {
  const [device, setDevice] = useState<DeviceId>("pixel8");
  const agentAuth = useSlotAuth(AGENT_SLOT);
  const managerAuth = useSlotAuth(MANAGER_SLOT);
  // Entry URLs are fixed at mount: changing an iframe's src reloads it, and a
  // login inside the frame must not bounce the frame back to its entry page.
  const [agentSrc] = useState(() => frameEntry(AGENT_SLOT, readSlotAuth(AGENT_SLOT)));
  const [managerSrc] = useState(() => frameEntry(MANAGER_SLOT, readSlotAuth(MANAGER_SLOT)));
  const [agentKey, setAgentKey] = useState(0);
  const [managerKey, setManagerKey] = useState(0);
  const [streamStatus, setStreamStatus] = useState<StreamStatus>("connecting");

  const [track, setTrack] = useState<Track | null>(null);
  const [stops, setStops] = useState<Stop[]>([]);
  const [fix, setFix] = useState<LatLng | null>(null);
  const [heading, setHeading] = useState<number | null>(null);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState<(typeof SPEEDS)[number]>(5);
  const [gpsLost, setGpsLost] = useState(false);
  const [beatNote, setBeatNote] = useState<string | null>(null);
  const metresRef = useRef(0);
  const agentFrame = useRef<HTMLIFrameElement>(null);

  // ── push the simulated fix into the phone ────────────────────────────────
  const latest = useRef({ fix, heading, playing, speed, gpsLost });
  useEffect(() => {
    latest.current = { fix, heading, playing, speed, gpsLost };
  });

  const pushFix = useCallback(() => {
    const { fix: f, heading: h, playing: p, speed: s, gpsLost: lost } = latest.current;
    const win = agentFrame.current?.contentWindow;
    if (!win || !f || lost) return;
    win.postMessage(
      { type: SIM_GEO_FIX, fix: { lat: f[0], lon: f[1], accuracy: 6, heading: h, speed: p ? FIELD_SPEED_MPS * s : 0 } },
      window.location.origin,
    );
  }, []);

  useEffect(() => { pushFix(); }, [fix, pushFix]);
  useEffect(() => {
    const id = setInterval(pushFix, HEARTBEAT_MS);
    return () => clearInterval(id);
  }, [pushFix]);
  useEffect(() => {
    const onMsg = (e: MessageEvent) => {
      if (e.origin !== window.location.origin) return;
      if (e.source !== agentFrame.current?.contentWindow) return;
      if ((e.data as { type?: string } | null)?.type === SIM_FRAME_READY) pushFix();
    };
    window.addEventListener("message", onMsg);
    return () => window.removeEventListener("message", onMsg);
  }, [pushFix]);

  const toggleGpsLost = () => {
    const next = !gpsLost;
    setGpsLost(next);
    if (next) {
      setPlaying(false);
      agentFrame.current?.contentWindow?.postMessage({ type: SIM_GEO_ERROR, code: 2 }, window.location.origin);
    }
  };
  useEffect(() => { if (!gpsLost) pushFix(); }, [gpsLost, pushFix]);

  // ── the agent's day: route + stops, loaded once the phone is signed in ──
  useEffect(() => {
    if (!agentAuth.accessToken || agentAuth.role !== "FIELD_AGENT") return;
    let cancelled = false;
    (async () => {
      try {
        const beat = await slotGet<BeatPayload>(AGENT_SLOT, "/agent/beat");
        if (cancelled) return;
        const byId = new Map((beat.cases ?? []).map((c) => [c.id, c]));
        const ordered = (beat.ordered_case_ids ?? []).map((id) => byId.get(id)).filter(Boolean) as BeatCase[];
        const list: Stop[] = (ordered.length ? ordered : beat.cases ?? [])
          .filter((c) => c.customer?.latitude != null && c.customer?.longitude != null)
          .map((c) => ({
            id: c.id,
            label: `${c.customer?.full_name ?? "Borrower"}${c.case_number ? ` · ${c.case_number}` : ""}`,
            lat: c.customer!.latitude as number,
            lon: c.customer!.longitude as number,
          }));
        const start: LatLng | null =
          beat.start_latitude != null && beat.start_longitude != null ? [beat.start_latitude, beat.start_longitude] : null;
        const road = beat.route_geometry ? decodePolyline(beat.route_geometry) : [];
        const pts: LatLng[] = road.length > 1 ? road : [...(start ? [start] : []), ...list.map((s) => [s.lat, s.lon] as LatLng)];
        setStops(list);
        setTrack(pts.length > 1 ? buildTrack(pts) : null);
        setBeatNote(road.length > 1 ? null : "No road geometry on today's beat — playback follows straight lines between stops.");
        setFix((f) => f ?? start ?? (list[0] ? [list[0].lat, list[0].lon] : null));
      } catch {
        if (cancelled) return;
        setBeatNote("No beat for today — click the map to place the phone.");
        try {
          const p = await slotGet<{ last_known_latitude?: number | null; last_known_longitude?: number | null }>(AGENT_SLOT, "/agent/profile");
          if (!cancelled && p.last_known_latitude != null && p.last_known_longitude != null)
            setFix((f) => f ?? [p.last_known_latitude as number, p.last_known_longitude as number]);
        } catch { /* stay on the default centre */ }
      }
    })();
    return () => { cancelled = true; };
  }, [agentAuth.accessToken, agentAuth.role]);

  // ── playback ─────────────────────────────────────────────────────────────
  useEffect(() => {
    if (!playing || !track) return;
    const id = setInterval(() => {
      metresRef.current = Math.min(track.total, metresRef.current + FIELD_SPEED_MPS * latest.current.speed);
      const p = positionAt(track, metresRef.current);
      setFix([p.lat, p.lon]);
      setHeading(p.heading);
      if (metresRef.current >= track.total) setPlaying(false);
    }, 1_000);
    return () => clearInterval(id);
  }, [playing, track]);

  const restartRoute = () => {
    if (!track) return;
    metresRef.current = 0;
    const p = positionAt(track, 0);
    setFix([p.lat, p.lon]);
    setHeading(p.heading);
  };

  const goToStop = (id: string) => {
    const s = stops.find((x) => x.id === id);
    if (!s) return;
    setPlaying(false);
    setFix((f) => approach(f ?? [s.lat, s.lon], [s.lat, s.lon], STOP_APPROACH_M));
    setHeading(null);
  };

  // ── layout: scale the phone to fit the viewport ─────────────────────────
  const vh = useViewportHeight();
  const d = DEVICES[device];
  const outerH = d.h + sim.phone.bezelPx * 2;
  const scale = Math.min(1, Math.max(0.45, (vh - 150) / outerH));
  const screenH = d.h - sim.phone.statusBarPx - sim.phone.homeBarPx;

  const centre = useMemo<LatLng>(() => fix ?? (DEFAULT_CENTRE as LatLng), [fix]);

  return (
    <div className={sim.page} style={{ fontFamily: sim.font }}>
      <header className="flex flex-wrap items-center justify-between gap-3 px-6 pb-3 pt-5">
        <div>
          <h1 className={sim.title}>Mobile App Simulator</h1>
          <p className={sim.subtitle}>
            The agent app on a phone, beside the manager's view — linked live.
            {agentAuth.name && <> Phone: <b className="font-semibold text-[#101828]">{agentAuth.name}</b>.</>}
            {managerAuth.name && <> Manager: <b className="font-semibold text-[#101828]">{managerAuth.name}</b>.</>}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <StatusDot status={streamStatus} />
          <div className={sim.segment} role="group" aria-label="Device">
            {(Object.keys(DEVICES) as DeviceId[]).map((id) => (
              <button key={id} type="button" className={sim.segmentItem(device === id)} onClick={() => setDevice(id)}>
                {DEVICES[id].label}
              </button>
            ))}
          </div>
          <button type="button" className={sim.button} onClick={() => setAgentKey((k) => k + 1)}>
            <RotateCw className="h-3.5 w-3.5" /> Phone
          </button>
          <button type="button" className={sim.button} onClick={() => setManagerKey((k) => k + 1)}>
            <RotateCw className="h-3.5 w-3.5" /> Manager
          </button>
        </div>
      </header>

      <main className="flex gap-4 px-6 pb-6" style={{ height: "calc(100svh - 96px)" }}>
        {/* ── the phone ── */}
        <section className="flex flex-shrink-0 items-start justify-center" style={{ width: (d.w + sim.phone.bezelPx * 2) * scale }}>
          <div style={{ transform: `scale(${scale})`, transformOrigin: "top center" }}>
            <div
              className="relative flex flex-col overflow-hidden shadow-[0_24px_60px_rgba(16,24,40,0.28)]"
              style={{ width: d.w + sim.phone.bezelPx * 2, height: outerH, borderRadius: d.radius, background: sim.phone.bezel, padding: sim.phone.bezelPx }}
            >
              <div className="flex flex-1 flex-col overflow-hidden bg-white" style={{ borderRadius: d.radius - sim.phone.bezelPx }}>
                <div className="flex items-center justify-between px-6 text-[13px] font-semibold text-[#101828]" style={{ height: sim.phone.statusBarPx }}>
                  <Clock />
                  {device === "iphone15"
                    ? <span className="h-[22px] w-[96px] rounded-full bg-black" aria-hidden />
                    : <span className="h-3 w-3 rounded-full bg-black" aria-hidden />}
                  <span className="flex items-center gap-1">
                    <Wifi className="h-3.5 w-3.5" /> <BatteryFull className="h-4 w-4" />
                  </span>
                </div>
                <iframe
                  key={agentKey}
                  ref={agentFrame}
                  name="tiq-slot:agent"
                  src={agentSrc}
                  title="Agent app"
                  allow="geolocation; camera; microphone; clipboard-write"
                  style={{ width: d.w, height: screenH, border: 0, display: "block" }}
                />
                <div className="flex items-center justify-center" style={{ height: sim.phone.homeBarPx }}>
                  <span className="h-1 w-32 rounded-full bg-[#101828]" />
                </div>
              </div>
            </div>
          </div>
        </section>

        {/* ── the manager's screen ── */}
        <section className={`${sim.card} flex min-w-0 flex-1 flex-col overflow-hidden`}>
          <div className="flex items-center justify-between border-b border-[#ECEDF1] px-4 py-2">
            <p className={sim.sectionLabel}>Manager view (web)</p>
            <a className={sim.link} href="/?slot=manager" target="_blank" rel="noreferrer">open in tab</a>
          </div>
          <DesktopFrame key={managerKey} src={managerSrc} />
        </section>

        {/* ── controls + timeline ── */}
        <aside className={`${sim.card} flex w-[360px] flex-shrink-0 flex-col overflow-hidden`}>
          <div className="space-y-3 p-4">
            <div className="flex items-center justify-between">
              <p className={sim.sectionLabel}>Phone GPS</p>
              <span className="font-mono text-[10px] text-[#98A2B3]">
                {fix ? `${fix[0].toFixed(5)}, ${fix[1].toFixed(5)}` : "no fix"}
              </span>
            </div>
            <GpsPanel fix={fix ?? centre} track={track} stops={stops} gpsLost={gpsLost} onPick={(p) => { setPlaying(false); setFix(p); setHeading(null); }} />
            <div className="flex flex-wrap items-center gap-2">
              <button type="button" className={sim.buttonPrimary} disabled={!track || gpsLost} onClick={() => setPlaying((v) => !v)}>
                {playing ? <><Pause className="h-3.5 w-3.5" /> Pause</> : <><Play className="h-3.5 w-3.5" /> Play route</>}
              </button>
              <button type="button" className={sim.button} disabled={!track} onClick={restartRoute}>
                <SkipBack className="h-3.5 w-3.5" /> Start
              </button>
              <div className={sim.segment} role="group" aria-label="Playback speed">
                {SPEEDS.map((s) => (
                  <button key={s} type="button" className={sim.segmentItem(speed === s)} onClick={() => setSpeed(s)}>×{s}</button>
                ))}
              </div>
            </div>
            <div className="flex items-center gap-2">
              <MapPin className="h-3.5 w-3.5 flex-shrink-0 text-[#667085]" />
              <select
                className={sim.select}
                value=""
                disabled={stops.length === 0}
                onChange={(e) => goToStop(e.target.value)}
              >
                <option value="">{stops.length ? `Go to a stop (${stops.length})…` : "Sign in on the phone to load the beat"}</option>
                {stops.map((s, i) => <option key={s.id} value={s.id}>{i + 1}. {s.label}</option>)}
              </select>
              <button type="button" className={gpsLost ? sim.buttonActive : sim.button} onClick={toggleGpsLost} title="Simulate losing the GPS signal">
                <Smartphone className="h-3.5 w-3.5" /> {gpsLost ? "GPS off" : "GPS on"}
              </button>
            </div>
            {beatNote && <p className="text-[11px] text-[#98A2B3]">{beatNote}</p>}
          </div>
          <div className="border-t border-[#ECEDF1] pt-3 flex min-h-0 flex-1 flex-col">
            <EventTimeline onStatus={setStreamStatus} />
          </div>
        </aside>
      </main>
    </div>
  );
}
