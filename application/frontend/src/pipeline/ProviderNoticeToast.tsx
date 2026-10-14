import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { useUiStore } from "../store/uiStore";

/** The notice that keyword data could not be fetched from Ahrefs.
 *
 * Raised from the pipeline store (`notifyProvider`) when the keyword prepass or the Plan of Action's
 * sub-service volumes fail: no `AHREFS_API_KEY`, a rejected key, an account out of API units, a
 * region with no country, or Ahrefs unreachable. The run carries on without volumes, so this is a
 * corner notice, not the blocking `FaultDialog`. It stays until dismissed, because the reason is
 * something the operator has to act on (a key, a top-up) and a self-dismissing error is easy to miss.
 */
export function ProviderNoticeToast() {
  const notice = useUiStore((s) => s.providerNotice);
  const dismiss = useUiStore((s) => s.dismissProviderNotice);
  const reduceMotion = useReducedMotion();

  return (
    <AnimatePresence>
      {notice && (
        <motion.div
          role="alert"
          initial={reduceMotion ? { opacity: 0 } : { opacity: 0, y: 16, scale: 0.97 }}
          animate={{ opacity: 1, y: 0, scale: 1 }}
          exit={reduceMotion ? { opacity: 0 } : { opacity: 0, y: 12, scale: 0.97 }}
          transition={reduceMotion ? { duration: 0 } : { type: "spring", stiffness: 320, damping: 26 }}
          className="fixed bottom-4 right-4 z-50 w-[min(24rem,calc(100vw-2rem))] rounded-xl border border-[var(--border-strong)] bg-[var(--bg-raised)] px-4 py-3"
          style={{ boxShadow: "0 12px 32px -10px rgb(var(--shadow-color) / 0.32)" }}
        >
          <div className="flex items-start gap-3">
            <span className="mt-1.5 h-2 w-2 shrink-0 rounded-full" style={{ backgroundColor: "var(--color-signal-amber)" }} />
            <div className="min-w-0 flex-1">
              <p className="text-[0.85rem] font-semibold">{notice.title}</p>
              <p className="mt-0.5 break-words text-[0.78rem] leading-relaxed text-[var(--fg-muted)]">{notice.detail}</p>
            </div>
            <button
              type="button"
              onClick={dismiss}
              aria-label="Dismiss"
              className="-mr-1 shrink-0 cursor-pointer rounded-md px-1.5 text-[1rem] leading-none text-[var(--fg-faint)] hover:text-[var(--fg)]"
            >
              ×
            </button>
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
