// ─── CHANGELOG (prototype → product) ───────────────────────────────────────
// 2026-09-17 — NEW. Scroll-reveal for the manager pages.
//
//   The Overview and Analytics pages already animate their cards IN on mount
//   (`enter 420ms`, staggered), but that plays once, at load, whether or not
//   the card is on screen — so everything below the fold had already finished
//   animating by the time a manager scrolled to it, and arrived flat. This
//   wrapper fades a section in (opacity 0 → 1, 14px rise) the first time it
//   enters the viewport, and then leaves it alone.
//
//   Two rules keep it from fighting the mount animation:
//     * A section that is ALREADY in the viewport at mount is left untouched —
//       the existing `enter` animation on its cards is the entrance, and a
//       second fade on top would double it.
//     * `prefers-reduced-motion` leaves everything untouched.
//
//   Deliberately no React state: the hide/show is written straight to the
//   element's style from the effect and the IntersectionObserver callback.
//   A one-shot visual needs no re-render, and a synchronous setState in an
//   effect is the lint rule (`react-hooks/set-state-in-effect`) this repo is
//   still paying down. The element is only ever hidden when it is off-screen,
//   so writing the style after first paint cannot flash.
// ─────────────────────────────────────────────────────────────────────────────

import { useEffect, useRef, type CSSProperties, type ReactNode } from "react";

const EASE = "cubic-bezier(0.16,1,0.3,1)";

export function Reveal({
  children,
  className,
  style,
  /** How far inside the bottom edge a section must be before it reveals. */
  rootMargin = "0px 0px -8% 0px",
  duration = 520,
  rise = 14,
}: {
  children: ReactNode;
  className?: string;
  style?: CSSProperties;
  rootMargin?: string;
  duration?: number;
  rise?: number;
}) {
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
    const inView = el.getBoundingClientRect().top < window.innerHeight;
    if (reduce || inView || typeof IntersectionObserver === "undefined") return;

    el.style.opacity = "0";
    el.style.transform = `translateY(${rise}px)`;
    el.style.willChange = "opacity, transform";

    const io = new IntersectionObserver(
      (entries) => {
        if (!entries.some((e) => e.isIntersecting)) return;
        io.disconnect();
        el.style.transition = `opacity ${duration}ms ${EASE}, transform ${duration}ms ${EASE}`;
        // Next frame, so the transition has a start state to run from.
        requestAnimationFrame(() => {
          el.style.opacity = "1";
          el.style.transform = "none";
        });
        const done = () => {
          el.style.willChange = "auto";
          el.style.transition = "";
          el.removeEventListener("transitionend", done);
        };
        el.addEventListener("transitionend", done);
      },
      { rootMargin, threshold: 0.08 },
    );
    io.observe(el);
    return () => {
      io.disconnect();
      // Unmounting mid-reveal must not leave a hidden node behind for a
      // remount (React strict-mode double-invokes effects in dev).
      el.style.opacity = "";
      el.style.transform = "";
      el.style.transition = "";
      el.style.willChange = "";
    };
    // Mount-only by design: the decision is about where the section sits at
    // load. rootMargin/duration/rise are treated as constants for the life of
    // the element.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div ref={ref} className={className} style={style}>
      {children}
    </div>
  );
}
