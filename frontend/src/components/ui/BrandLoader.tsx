// The one loading indicator for the whole platform: the brand mark, breathing.
// It covers the lazy-route fallback (App.tsx) and the post-login hold
// (ManagerLayout), so a wait looks the same wherever the user meets one. The
// pre-mount boot splash in index.html is a hand-written copy of this markup,
// because it has to paint before any bundle exists; the keyframe and the
// 1.6s/44px numbers live in index.css (.brand-loader) and are mirrored there.
//
// In-page data loading keeps its skeletons — they hold the layout, which a
// centred mark cannot — and only borrows this timing curve.
//
// The mark is referenced as /favicon.svg, not the bundled asset BrandLogo
// imports: index.html needs an unhashed URL, and using the same one here keeps
// the two in step (it is PWA-precached, so it also appears offline).

export function BrandLoader({ full = false, label = "Loading" }: { full?: boolean; label?: string }) {
  return (
    <div className={`brand-loader${full ? " brand-loader--full" : ""}`} role="status" aria-live="polite">
      <div className="brand-loader__stack">
        <img src="/favicon.svg" alt="" aria-hidden="true" width={44} height={44} className="brand-loader__mark" />
        {/* Visible, not sr-only: a breathing mark on its own reads as decoration.
            The caption says a wait is in progress. `label` stays the accessible
            name — it is what a screen reader announces and what the tests pin. */}
        <span className="brand-loader__label">Loading...</span>
        <span className="sr-only">{label}</span>
      </div>
    </div>
  );
}

export default BrandLoader;
