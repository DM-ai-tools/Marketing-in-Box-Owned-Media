import { useEffect, useState, type ReactNode } from "react";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";

/** What "Fix with Refine" is doing, in words. The request reports no progress, so the step shown is
 * paced by elapsed time (as the business check's scan is) and never claims a count. */
const STEPS = ["Reading the findings", "Writing the fixes", "Placing them in this draft"];
const STEP_MS = 2600;
/** How long "Fixes applied" stays on the card after the draft updates. */
export const APPLIED_MS = 4000;

function useElapsed(active: boolean): number {
  const [started] = useState(() => Date.now());
  const [now, setNow] = useState(started);
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(() => setNow(Date.now()), 500);
    return () => clearInterval(timer);
  }, [active]);
  return now - started;
}

/** The banner while a fix is applied to this card: a spinning ring, a shimmer across the banner,
 * the current step cross-fading, elapsed seconds and an indeterminate bar. */
export function FixingBanner() {
  const still = Boolean(useReducedMotion());
  const elapsed = useElapsed(true);
  const step = Math.min(STEPS.length - 1, Math.floor(elapsed / STEP_MS));
  return (
    <motion.div
      data-fixing
      role="status"
      aria-live="polite"
      initial={still ? false : { opacity: 0, y: -6, scale: 0.98 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      transition={{ duration: 0.25, ease: "easeOut" }}
      className="fx-banner relative mb-2.5 overflow-hidden rounded-lg border px-2.5 py-2 text-[0.76rem]"
      style={{ borderColor: "color-mix(in srgb, var(--color-electric-blue) 45%, var(--border))" }}
    >
      <div className="relative flex items-center gap-2">
        <svg width="16" height="16" viewBox="0 0 24 24" aria-hidden className="bc-spin shrink-0">
          <circle cx="12" cy="12" r="9" fill="none" stroke="var(--border-strong)" strokeWidth="3" />
          <path d="M21 12a9 9 0 0 0-9-9" fill="none" stroke="var(--color-electric-blue)" strokeWidth="3" strokeLinecap="round" />
        </svg>
        <div className="min-w-0 flex-1">
          <div className="font-semibold text-[var(--fg)]">Applying the fixes to this draft</div>
          <div className="relative h-[1.2em] overflow-hidden text-[var(--fg-muted)]">
            <AnimatePresence mode="wait" initial={false}>
              <motion.span
                key={step}
                className="absolute inset-x-0 block truncate"
                initial={still ? false : { opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                exit={still ? undefined : { opacity: 0, y: -8 }}
                transition={{ duration: 0.22 }}
              >
                {STEPS[step]}
                <span className="fx-dots" aria-hidden />
              </motion.span>
            </AnimatePresence>
          </div>
        </div>
        <span className="shrink-0 tabular-nums text-[0.7rem] text-[var(--fg-faint)]">{Math.floor(elapsed / 1000)}s</span>
      </div>
      <p className="relative m-0 mt-1 text-[0.7rem] text-[var(--fg-faint)]">It updates here when they are done.</p>
      <div className="relative mt-1.5 h-[3px] overflow-hidden rounded-full bg-[var(--bg-sunken)]">
        <div className="bc-bar absolute inset-y-0 w-1/3 rounded-full" style={{ backgroundColor: "var(--color-electric-blue)" }} />
      </div>
    </motion.div>
  );
}

/** The draft while it is being fixed: still readable, slightly dimmed, with a beam passing over it. */
export function FixingDraft({ fixing, children }: { fixing: boolean; children: ReactNode }) {
  return (
    <div className={fixing ? "fx-draft relative" : undefined} aria-busy={fixing || undefined}>
      {children}
      {fixing && <div className="fx-scan pointer-events-none absolute inset-x-0 top-0 h-16" aria-hidden />}
    </div>
  );
}

/** "Fixes applied", for a few seconds after the fixed draft replaces the old one. */
export function FixesApplied({ at }: { at: number | undefined }) {
  const still = Boolean(useReducedMotion());
  const [show, setShow] = useState(() => Boolean(at && Date.now() - at < APPLIED_MS));
  useEffect(() => {
    if (!at) return;
    const left = APPLIED_MS - (Date.now() - at);
    if (left <= 0) return;
    setShow(true);
    const timer = setTimeout(() => setShow(false), left);
    return () => clearTimeout(timer);
  }, [at]);
  return (
    <AnimatePresence>
      {show && (
        <motion.div
          data-fixes-applied
          role="status"
          initial={still ? false : { opacity: 0, y: -6, scale: 0.97 }}
          animate={{ opacity: 1, y: 0, scale: 1 }}
          exit={still ? { opacity: 0 } : { opacity: 0, y: -6 }}
          transition={{ duration: 0.25 }}
          className="mb-2.5 flex items-center gap-2 rounded-lg border px-2.5 py-1.5 text-[0.76rem] font-medium"
          style={{
            borderColor: "color-mix(in srgb, var(--color-signal-green) 50%, var(--border))",
            color: "var(--color-signal-green)",
          }}
        >
          <motion.svg
            width="16"
            height="16"
            viewBox="0 0 24 24"
            aria-hidden
            initial={still ? false : { scale: 0.4, rotate: -20 }}
            animate={{ scale: 1, rotate: 0 }}
            transition={{ type: "spring", stiffness: 420, damping: 16 }}
          >
            <circle cx="12" cy="12" r="10" fill="currentColor" opacity="0.15" />
            <motion.path
              d="M7 12.5l3.2 3.2L17 9"
              fill="none"
              stroke="currentColor"
              strokeWidth="2.4"
              strokeLinecap="round"
              strokeLinejoin="round"
              initial={still ? false : { pathLength: 0 }}
              animate={{ pathLength: 1 }}
              transition={{ duration: 0.35, delay: 0.1 }}
            />
          </motion.svg>
          Fixes applied. Checking the draft again…
        </motion.div>
      )}
    </AnimatePresence>
  );
}
