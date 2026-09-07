// ─── CHANGELOG (prototype → product) ────────────────────────────────────────
// 2026-09-07 — Both hooks wrote a ref DURING RENDER (`targetRef.current =
//   target`) and called setState synchronously inside a mount effect. Render
//   has to be a pure function of props and state: React is allowed to call it
//   twice, throw the result away, or run it concurrently, and a render that
//   mutates something outside itself breaks each of those assumptions. Both
//   carried an `eslint-disable react-hooks/exhaustive-deps` on top.
//
//   useAnimatedValue turned out not to need any of that machinery. Its state
//   was only ever 0 — the "animate to target" step just handed `target` back —
//   so the whole thing is a delay flag plus a conditional return, which reads
//   the CURRENT target at render time and therefore cannot go stale.
//
//   useCountUp genuinely does need the latest target inside a
//   requestAnimationFrame callback that outlives the render which created it.
//   That ref is now written in an effect rather than during render, which is
//   the standard form of the same guard and satisfies the rule for the reason
//   the rule exists.
// ───────────────────────────────────────────────────────────────────────────
import { useEffect, useRef, useState } from "react";

/**
 * 0 on mount, then the real value once `delay` has passed — so a progress bar
 * animates in from empty even when the data was already cached in context.
 *
 * Returns `target` directly after the delay rather than copying it into state.
 * That is what removes the stale-closure problem the previous version needed a
 * render-time ref to work around: there is no closure, only a render.
 */
export function useAnimatedValue(target: number, delay = 80): number {
  const [started, setStarted] = useState(false);

  useEffect(() => {
    const t = setTimeout(() => setStarted(true), delay);
    return () => clearTimeout(t);
  }, [delay]);

  return started ? target : 0;
}

/**
 * Integer count-up from 0 to `target` over `duration` ms, for stat tiles.
 *
 * Unlike useAnimatedValue this one really does hold state: the intermediate
 * values exist only inside the animation.
 */
export function useCountUp(target: number, duration = 600, delay = 80): number {
  const [value, setValue] = useState(0);
  const didMount = useRef(false);
  const animating = useRef(true);

  // The latest target, for the rAF callback below — which is created once on
  // mount and would otherwise count up to whatever the target was then (0,
  // while the data is still loading). Written in an EFFECT, not during render.
  const targetRef = useRef(target);
  useEffect(() => {
    targetRef.current = target;
  }, [target]);

  useEffect(() => {
    // No setValue(0) here: useState already starts at 0, and a remount gets a
    // fresh state anyway. Setting it synchronously inside the effect was a
    // no-op that cost a render pass and tripped the rule.
    let start: number | null = null;
    let raf: number;

    const tick = (ts: number) => {
      if (!start) start = ts;
      const elapsed = ts - start - delay;
      if (elapsed < 0) { raf = requestAnimationFrame(tick); return; }
      const progress = Math.min(elapsed / duration, 1);
      // ease-out cubic
      const eased = 1 - Math.pow(1 - progress, 3);
      setValue(Math.round(eased * targetRef.current));
      if (progress < 1) raf = requestAnimationFrame(tick);
      else animating.current = false;
    };

    raf = requestAnimationFrame(tick);
    return () => { cancelAnimationFrame(raf); animating.current = false; };
  }, [duration, delay]);

  useEffect(() => {
    if (!didMount.current) { didMount.current = true; return; }
    // While the mount animation is still running it already tracks the ref —
    // snapping here would jump ahead and then visibly rewind on the next frame.
    if (animating.current) return;
    setValue(target);
  }, [target]);

  return value;
}
