import { useState, useEffect, useRef } from "react";

/**
 * Starts at 0 on every mount, then animates to `target` after a short delay.
 * Ensures progress bars and counters always animate on page navigation,
 * even when data is already cached in context.
 */
export function useAnimatedValue(target: number, delay = 80): number {
  const [value, setValue] = useState(0);
  const didMount = useRef(false);

  useEffect(() => {
    setValue(0);
    const t = setTimeout(() => setValue(target), delay);
    return () => clearTimeout(t);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Skip the first fire (mount) so the mount-effect animation isn't overridden.
  useEffect(() => {
    if (!didMount.current) { didMount.current = true; return; }
    setValue(target);
  }, [target]);

  return value;
}

/**
 * Integer count-up from 0 to `target` over `duration` ms.
 * For stat tiles that show numeric counts.
 */
export function useCountUp(target: number, duration = 600, delay = 80): number {
  const [value, setValue] = useState(0);
  const didMount = useRef(false);

  useEffect(() => {
    setValue(0);
    let start: number | null = null;
    let raf: number;

    const tick = (ts: number) => {
      if (!start) start = ts;
      const elapsed = ts - start - delay;
      if (elapsed < 0) { raf = requestAnimationFrame(tick); return; }
      const progress = Math.min(elapsed / duration, 1);
      // ease-out cubic
      const eased = 1 - Math.pow(1 - progress, 3);
      setValue(Math.round(eased * target));
      if (progress < 1) raf = requestAnimationFrame(tick);
    };

    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!didMount.current) { didMount.current = true; return; }
    setValue(target);
  }, [target]);

  return value;
}
