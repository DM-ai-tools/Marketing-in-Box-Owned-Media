import { useEffect, useState } from "react";
import { AnimatePresence, animate, motion, useReducedMotion } from "framer-motion";
import { useUiStore } from "../store/uiStore";

/** How long the "done" state stays before it slides away. */
const DONE_MS = 4500;

/** Ahrefs at work, shown at the root while a keyword call is in flight and for a moment after.
 *
 * Raised by the pipeline store around the two places Ahrefs is called: the run's keyword prepass
 * (`runKeywordPrepass`) and the Plan of Action's sub-service volumes (`loadPlanDiscovery`). While it
 * works, an equaliser of bars rises and falls. When the data lands, a check draws itself and the
 * number of keywords measured counts up, then the chip leaves on its own. A failure clears it
 * instead, and the reason goes to `ProviderNoticeToast`, which stays.
 *
 * Bottom-left, so it never sits on top of the notice in the bottom-right corner. Under reduced
 * motion every animation becomes a plain state change.
 */
export function AhrefsActivity() {
  const activity = useUiStore((s) => s.ahrefsActivity);
  const clear = useUiStore((s) => s.clearAhrefsActivity);
  const reduceMotion = useReducedMotion();

  const doneId = activity?.phase === "done" ? activity.id : null;
  useEffect(() => {
    if (doneId === null) return;
    const timer = window.setTimeout(() => clear(doneId), DONE_MS);
    return () => window.clearTimeout(timer);
  }, [doneId, clear]);

  return (
    <AnimatePresence>
      {activity && (
        <motion.div
          key="ahrefs-activity"
          role="status"
          aria-live="polite"
          layout={!reduceMotion}
          initial={reduceMotion ? { opacity: 0 } : { opacity: 0, y: 16, scale: 0.96 }}
          animate={{ opacity: 1, y: 0, scale: 1 }}
          exit={reduceMotion ? { opacity: 0 } : { opacity: 0, y: 12, scale: 0.96 }}
          transition={reduceMotion ? { duration: 0 } : { type: "spring", stiffness: 340, damping: 28 }}
          className="fixed bottom-4 left-4 z-40 flex max-w-[calc(100vw-2rem)] items-center gap-3 overflow-hidden rounded-xl border border-[var(--border-strong)] bg-[var(--bg-raised)] py-2.5 pl-3 pr-4"
          style={{ boxShadow: "0 12px 32px -12px rgb(var(--shadow-color) / 0.3)" }}
        >
          {activity.phase === "working" && !reduceMotion && <Sheen />}
          <span className="relative flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-[var(--bg-sunken)]">
            {activity.phase === "working" ? <Equaliser still={!!reduceMotion} /> : <DrawnCheck still={!!reduceMotion} />}
          </span>
          <span className="relative min-w-0">
            <span className="block text-[0.68rem] font-semibold uppercase tracking-wide text-[var(--color-electric-blue)]">
              Ahrefs
            </span>
            {activity.phase === "working" ? (
              <span className="block truncate text-[0.8rem] text-[var(--fg-muted)]">{activity.label}</span>
            ) : (
              <span className="block truncate text-[0.8rem]">
                <CountUp to={activity.count} still={!!reduceMotion} /> {activity.unit}
                {activity.extra && <span className="text-[var(--fg-muted)]"> · {activity.extra}</span>}
                <span className="block truncate text-[0.7rem] text-[var(--fg-faint)]">{activity.label}</span>
              </span>
            )}
          </span>
        </motion.div>
      )}
    </AnimatePresence>
  );
}

/** Five bars rising and falling out of step: data being pulled. */
function Equaliser({ still }: { still: boolean }) {
  const heights = [0.45, 0.8, 0.6, 0.95, 0.5];
  return (
    <span className="flex h-4 items-end gap-[2px]" aria-hidden>
      {heights.map((h, i) => (
        <motion.span
          key={i}
          className="w-[3px] rounded-full bg-[var(--color-electric-blue)]"
          style={{ height: "100%", originY: 1, scaleY: still ? h : undefined }}
          animate={still ? undefined : { scaleY: [0.25, h, 0.35, 1, 0.25] }}
          transition={still ? undefined : { duration: 1.1, repeat: Infinity, ease: "easeInOut", delay: i * 0.12 }}
        />
      ))}
    </span>
  );
}

/** A check that draws itself once, in the success colour. */
function DrawnCheck({ still }: { still: boolean }) {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden>
      <motion.path
        d="M5 12.5l4.5 4.5L19 7.5"
        stroke="var(--color-signal-green)"
        strokeWidth="2.6"
        strokeLinecap="round"
        strokeLinejoin="round"
        initial={still ? false : { pathLength: 0 }}
        animate={{ pathLength: 1 }}
        transition={still ? { duration: 0 } : { duration: 0.45, ease: "easeOut" }}
      />
    </svg>
  );
}

/** A light band sweeping across the chip while Ahrefs is working. */
function Sheen() {
  return (
    <motion.span
      aria-hidden
      className="pointer-events-none absolute inset-y-0 w-1/3"
      style={{ background: "linear-gradient(90deg, transparent, var(--hover), transparent)" }}
      initial={{ left: "-35%" }}
      animate={{ left: "110%" }}
      transition={{ duration: 1.6, repeat: Infinity, ease: "easeInOut" }}
    />
  );
}

/** A number that counts up from 0 to `to`, once. Also used by the sub-service card's volumes. */
export function CountUp({ to, still, duration = 0.9 }: { to: number; still?: boolean; duration?: number }) {
  const [value, setValue] = useState(still ? to : 0);
  useEffect(() => {
    if (still) {
      setValue(to);
      return;
    }
    const controls = animate(0, to, { duration, ease: "easeOut", onUpdate: (v) => setValue(Math.round(v)) });
    return () => controls.stop();
  }, [to, still, duration]);
  return <span className="tabular-nums">{value.toLocaleString("en-US")}</span>;
}
