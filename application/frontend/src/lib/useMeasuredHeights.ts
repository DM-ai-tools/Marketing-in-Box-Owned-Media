import { useCallback, useEffect, useRef, useState } from "react";

/**
 * Live heights of a set of keyed elements, for a layout that positions them absolutely.
 *
 * One ResizeObserver for all of them, so a card that grows (wrapping text, a web font arriving, a
 * node unfolding its detail) reports its new height and the caller re-flows. `offsetHeight` rather
 * than the observer's box, because it ignores ancestor transforms: the map is drawn inside a
 * `scale()`, and a scaled measurement would be fed back into an unscaled layout.
 *
 * `refFor(id)` returns the same callback for the same id across renders, so React does not detach
 * and re-attach every card on every render.
 */
export function useMeasuredHeights(): {
  heights: ReadonlyMap<string, number>;
  refFor: (id: string) => (el: HTMLElement | null) => void;
} {
  const [heights, setHeights] = useState<ReadonlyMap<string, number>>(() => new Map());
  const observer = useRef<ResizeObserver | null>(null);
  const elements = useRef(new Map<string, HTMLElement>());
  const callbacks = useRef(new Map<string, (el: HTMLElement | null) => void>());

  const record = useCallback((entries: [string, number][]) => {
    if (!entries.length) return;
    setHeights((prev) => {
      let changed = false;
      const next = new Map(prev);
      for (const [id, h] of entries) {
        if (Math.abs((prev.get(id) ?? -1) - h) > 0.5) {
          next.set(id, h);
          changed = true;
        }
      }
      return changed ? next : prev;
    });
  }, []);

  useEffect(() => {
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver((entries) => {
      const out: [string, number][] = [];
      for (const entry of entries) {
        const el = entry.target as HTMLElement;
        const id = el.dataset.measureId;
        if (id) out.push([id, el.offsetHeight]);
      }
      record(out);
    });
    observer.current = ro;
    elements.current.forEach((el) => ro.observe(el));
    return () => {
      ro.disconnect();
      observer.current = null;
    };
  }, [record]);

  const refFor = useCallback((id: string) => {
    let cb = callbacks.current.get(id);
    if (!cb) {
      cb = (el: HTMLElement | null) => {
        const previous = elements.current.get(id);
        if (previous && previous !== el) {
          observer.current?.unobserve(previous);
          elements.current.delete(id);
        }
        if (el) {
          el.dataset.measureId = id;
          elements.current.set(id, el);
          observer.current?.observe(el);
        }
      };
      callbacks.current.set(id, cb);
    }
    return cb;
  }, []);

  return { heights, refFor };
}
