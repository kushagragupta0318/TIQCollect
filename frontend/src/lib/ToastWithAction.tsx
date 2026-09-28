// ─── CHANGELOG (prototype → product) ───────────────────────────────────────
// 2026-09-28 — split out of registerServiceWorker.tsx (coordinator audit
//   follow-up on I01): react-refresh/only-export-components flags a component
//   defined beside a file's non-component exports (registerServiceWorker
//   itself), the same rule fixed the same way for useBeat out of
//   BeatContext.tsx. One export, its own file.
export function ToastWithAction({ title, note, action, onAction }: {
  title: string; note: string; action: string; onAction: () => void;
}) {
  return (
    <span className="flex items-center gap-3">
      <span>
        {title}
        <span className="block text-[12px] font-normal text-muted-foreground">{note}</span>
      </span>
      <button
        type="button"
        onClick={onAction}
        className="tap-target flex-shrink-0 rounded-xl px-3 py-1.5 text-xs font-semibold text-white"
        style={{ background: "#2563EB" }}
      >
        {action}
      </button>
    </span>
  );
}
