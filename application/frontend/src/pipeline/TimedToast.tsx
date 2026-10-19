import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { useUiStore } from "../store/uiStore";

/** A short notice that dismisses itself (`showToast`, 3 seconds by default) — for news, not for a
 * problem the operator has to act on (that is `ProviderNoticeToast`, which stays until dismissed).
 * Root-mounted, for the reason `ChatDeleteDialog` is: a card animating on `transform` would contain
 * a `position: fixed` child. */
export function TimedToast() {
  const toast = useUiStore((s) => s.toast);
  const reduceMotion = useReducedMotion();

  return (
    <div className="pointer-events-none fixed inset-x-0 bottom-4 z-50 flex justify-center px-4">
      <AnimatePresence>
        {toast && (
          <motion.div
            key={toast.id}
            role="status"
            aria-live="polite"
            initial={reduceMotion ? { opacity: 0 } : { opacity: 0, y: 16, scale: 0.97 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={reduceMotion ? { opacity: 0 } : { opacity: 0, y: 12, scale: 0.97 }}
            transition={reduceMotion ? { duration: 0 } : { type: "spring", stiffness: 320, damping: 26 }}
            className="pointer-events-auto w-full max-w-[24rem] rounded-xl border border-[var(--border-strong)] bg-[var(--bg-raised)] px-4 py-3"
            style={{ boxShadow: "0 12px 32px -10px rgb(var(--shadow-color) / 0.32)" }}
          >
            <div className="flex items-center gap-3">
              <span className="h-2 w-2 shrink-0 rounded-full" style={{ backgroundColor: "var(--color-signal-green)" }} />
              <p className="text-[0.85rem] font-medium">{toast.text}</p>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
