import { usePipelineStore } from "../pipeline/pipelineStore";
import { MENU_ROW, PILL } from "./PlanMapDownload";

/** "Build deployable funnel" on the Funnel and Funnel Hub & Media cards.
 *
 * The build is offered on its own once both stages are approved; this is the way back to it — for a
 * run approved before the offer existed, or after "Not now". It opens the build card, which loads a
 * stored build when the run has one rather than building again. Offered only on a run, since the
 * build reads the approved documents off it. */
export function FunnelBuildMenuItem({ variant, onDone }: { variant: "menu" | "pill"; onDone?: () => void }) {
  const offer = usePipelineStore((s) => s.offerFunnelBuild);
  const runId = usePipelineStore((s) => s.runId);
  if (!runId) return null;

  return (
    <button
      type="button"
      role={variant === "menu" ? "menuitem" : undefined}
      onClick={() => {
        void offer();
        onDone?.();
      }}
      className={variant === "menu" ? MENU_ROW : PILL}
    >
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden>
        <path d="M4 5h16l-6 7v6l-4 2v-8L4 5Z" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" />
      </svg>
      {variant === "menu" ? "Build deployable funnel" : "Deployable funnel"}
    </button>
  );
}
