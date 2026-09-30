import { useEffect, useMemo, useRef } from "react";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { Hint } from "../components/Hint";
import { PhaseToggle } from "./PhaseToggle";
import { PHASE_META, PHASE_ORDER, stagesFor, totalStagesFor } from "./pipelineData";
import { INDUSTRY_BUCKET_FACT, INDUSTRY_LABEL_FACT } from "./pipelineData";
import { approvedAssetIds, skippedAssetIds, usePipelineStore } from "./pipelineStore";
import { notRecommendedReason } from "../data/industryProfiles";

type NodeStatus = "idle" | "pending" | "running" | "hitl" | "done";

/** `done` comes from the transcript, not from the cursor's position.
 *
 * `index < currentIndex` was the old test and it only held while the run was strictly sequential.
 * A stage started out of order or skipped moves the cursor past stages that were never built, and
 * every one of them would have reported "✓ Saved" — see `approvedAssetIds`. A stage the cursor has
 * passed but that holds no approved output now reads "Queued", which is both true and what makes it
 * offer a way back in. */
function statusFor(
  index: number,
  currentIndex: number,
  activeStatus: "running" | "hitl" | null,
  done: boolean,
): NodeStatus {
  if (done) return "done";
  if (index === currentIndex) return activeStatus ?? "pending";
  if (index === currentIndex + 1 && activeStatus !== null) return "pending";
  return "idle";
}

const BADGE_LABEL: Record<Exclude<NodeStatus, "running">, string> = {
  idle: "Queued",
  pending: "Next",
  hitl: "⏸ Review",
  done: "✓ Saved",
};

const NODE_STYLE: Record<NodeStatus, string> = {
  idle: "border-[var(--border)] opacity-40",
  pending: "border-[var(--border-strong)] opacity-80",
  running: "node-running border-[var(--color-electric-blue)]",
  hitl: "node-hitl border-[var(--color-signal-orange)]",
  done: "border-[var(--color-signal-green)]",
};

const BADGE_STYLE: Record<NodeStatus, string> = {
  idle: "border border-[var(--border-strong)] text-[var(--fg-faint)]",
  pending: "border border-[var(--border-strong)] text-[var(--fg-muted)]",
  running: "badge-pulsing text-[var(--electric-blue-fg)]",
  hitl: "badge-pulsing text-[var(--signal-orange-fg)]",
  done: "text-[var(--signal-green-fg)]",
};

const BADGE_BG: Partial<Record<NodeStatus, string>> = {
  running: "var(--color-electric-blue)",
  hitl: "var(--color-signal-orange)",
  done: "var(--color-signal-green)",
};

/**
 * The phase swap.
 *
 * Two sequences of cards are not two states of one list — Phase 2 is a different, shorter run with
 * its own numbering, and a card that morphed from "Stage 08 · Blog Post" into "Stage 04 · Blog Post"
 * would suggest the two are the same step. So the old list leaves and the new one arrives.
 *
 * The asymmetry is deliberate. The outgoing list goes as one block, fast: it is the answer to a
 * question the operator has already stopped asking, and staggering fifteen rows out meant the top of
 * the pane — right where they just clicked — sat motionless for the better part of a second while
 * the header had already flipped to the other phase. The *incoming* list is what they are waiting
 * to read, so that one cascades from the top down, which shows the new order being laid out in
 * order rather than appearing all at once.
 *
 * Travel follows the phases' order, so going forward and coming back are visibly opposite motions
 * rather than the same animation twice.
 */
const LIST_VARIANTS = {
  enter: { transition: { staggerChildren: 0.045, delayChildren: 0.04 } },
  // On the container, not the rows: with no `exit` variant of their own the rows do not animate
  // individually, so `mode="wait"` only has this one short animation to wait for.
  exit: (direction: number) => ({
    opacity: 0,
    x: direction * -20,
    transition: { duration: 0.17, ease: [0.4, 0, 1, 1] as const },
  }),
};

const ROW_VARIANTS = {
  initial: (direction: number) => ({ opacity: 0, x: direction * 32, scale: 0.97 }),
  enter: {
    opacity: 1,
    x: 0,
    scale: 1,
    transition: { duration: 0.3, ease: [0.16, 1, 0.3, 1] as const },
  },
};

function ProgressBar({ status, progress }: { status: NodeStatus; progress: number }) {
  if (status === "idle" || status === "pending") return null;

  const pct = status === "running" ? progress : 100;
  const color =
    status === "hitl" ? "var(--color-signal-orange)" : status === "done" ? "var(--color-signal-green)" : "var(--color-electric-blue)";

  return (
    <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-[var(--bg-sunken)]">
      <div
        className={`relative h-full rounded-full transition-[width] duration-300 ease-out ${status === "running" ? "progress-shimmer" : ""}`}
        style={{ width: `${pct}%`, backgroundColor: color }}
      />
    </div>
  );
}

function Connector({ active, done }: { active: boolean; done: boolean }) {
  return (
    <div className="relative ml-3.5 h-5 w-4 overflow-visible">
      <div
        className="mx-auto h-full w-0.5"
        style={{
          backgroundColor: done
            ? "var(--color-signal-green)"
            : active
              ? "var(--color-electric-blue)"
              : "var(--border-strong)",
        }}
      />
      {active && <div className="connector-active absolute inset-0" />}
    </div>
  );
}

export function PipelineDiagram() {
  const phase = usePipelineStore((s) => s.phase);
  const currentIndex = usePipelineStore((s) => s.currentIndex);
  const activeStatus = usePipelineStore((s) => s.activeStatus);
  const progress = usePipelineStore((s) => s.progress);
  const navStatus = usePipelineStore((s) => s.navStatus);
  const startAtStage = usePipelineStore((s) => s.startAtStage);
  // The messages array, then the set — a selector returning a fresh `Set` would hand
  // `useSyncExternalStore` a new snapshot on every read.
  const messages = usePipelineStore((s) => s.messages);
  const activePhase2TrackId = usePipelineStore((s) => s.activePhase2TrackId);
  const approved = useMemo(() => approvedAssetIds(messages, phase, activePhase2TrackId), [messages, phase, activePhase2TrackId]);
  const skipped = useMemo(() => skippedAssetIds(messages, phase, activePhase2TrackId), [messages, phase, activePhase2TrackId]);
  // Advisory only: a stage the client's industry rarely needs is tagged, never hidden.
  const industryBucket = usePipelineStore((s) => s.clientProfile[INDUSTRY_BUCKET_FACT]);
  const industryLabel = usePipelineStore((s) => s.clientProfile[INDUSTRY_LABEL_FACT] ?? "");
  const runningLabel = navStatus === "Awaiting Input" ? "Awaiting Input" : "Generating…";
  const nodeRefs = useRef<(HTMLDivElement | null)[]>([]);
  const reduceMotion = useReducedMotion();

  useEffect(() => {
    if (activeStatus !== "hitl") return;
    nodeRefs.current[currentIndex]?.scrollIntoView({ behavior: "smooth", block: "center" });
  }, [currentIndex, activeStatus]);

  const stages = stagesFor(phase);
  const total = totalStagesFor(phase);
  const doneCount = Math.min(approved.size, total);
  const pct = Math.round((doneCount / total) * 100);
  // +1 when moving to a later phase, -1 coming back, so the rows travel the way the switch did.
  const direction = PHASE_ORDER.indexOf(phase) === 0 ? -1 : 1;

  return (
    // A query container, not a viewport-breakpoint layout: this pane is ~38% of a desktop window but
    // the full width of a phone, so what the stage rows need to know is how wide *they* are — the
    // window size doesn't tell them. The `@[...]` variants below all measure this box.
    <div className="@container flex h-full min-w-0 flex-col">
      <div className="pane-scroll min-h-0 flex-1 overflow-y-auto px-4 py-5 sm:px-6 sm:py-6">
        <div className="mb-3">
          <div className="mb-2 text-[0.72rem] font-semibold uppercase tracking-wide text-[var(--fg-faint)]">
            Asset Pipeline
          </div>
          <PhaseToggle />
          <Hint>
            Queued stages can be started out of order — "Start here" checks what that stage needs
            against this run first, and offers to use what's already here, take a document you
            paste, or run the one stage that produces it.
          </Hint>
        </div>

        {/* `mode="wait"` so the outgoing sequence is gone before the incoming one arrives: run
            together, two lists of different lengths overlap into an unreadable pile. Keyed on the
            phase, which is what makes the swap an exit/enter at all rather than a re-render. */}
        <AnimatePresence mode="wait" custom={direction} initial={false}>
          <motion.div
            key={phase}
            custom={direction}
            variants={reduceMotion ? undefined : LIST_VARIANTS}
            initial="initial"
            animate="enter"
            exit="exit"
          >
            {stages.map((stage, i) => {
          const status = statusFor(i, currentIndex, activeStatus, approved.has(stage.asset.asset_id));
          const isLast = i === stages.length - 1;
          // Queued only. A stage that is running, next, or already saved has its own affordances
          // (the review gate, the resume banner, Re-run) and a second way in would compete with
          // them. Withheld entirely mid-generation: jumping then abandons a stream in flight.
          const startable = status === "idle" && activeStatus !== "running";
          const wasSkipped = status === "idle" && skipped.has(stage.asset.asset_id);
          const advisedAgainst = status !== "done" ? notRecommendedReason(industryBucket, stage.asset.asset_id) : undefined;
          return (
            <motion.div
              key={stage.asset.asset_id}
              custom={direction}
              variants={reduceMotion ? undefined : ROW_VARIANTS}
            >
              <div
                ref={(el) => {
                  nodeRefs.current[i] = el;
                }}
                className={`rounded-2xl border-2 bg-[var(--bg-raised)] px-3 py-2.5 transition-[opacity,box-shadow] duration-300 ease-out @[20rem]:px-3.5 @[20rem]:py-3 ${NODE_STYLE[status]}`}
                // Inline, not a second opacity utility: `NODE_STYLE.idle` already carries
                // `opacity-40` and which of two same-specificity utilities wins depends on
                // stylesheet order, not on the order they are written here.
                style={startable ? { opacity: 0.75 } : undefined}
              >
                <div className="flex flex-wrap items-center gap-x-2 gap-y-1.5">
                  <span
                    className={`text-[1.1rem] leading-none ${status === "running" ? "icon-rotating" : ""}`}
                    aria-hidden
                  >
                    {stage.emoji}
                  </span>
                  <div className="min-w-[9rem] flex-1">
                    <div className="flex items-center gap-1.5 text-[0.68rem] font-medium text-[var(--fg-faint)]">
                      <span>Stage {String(stage.stageNumber).padStart(2, "0")}</span>
                      <span>·</span>
                      <span>{stage.hitl ? "HITL" : "Auto-Chain"}</span>
                    </div>
                    {/* Wraps to two lines in a narrow pane rather than truncating: the asset name is
                        what identifies the row, and "Value Ladder & Offer…" identifies nothing. */}
                    <div className="text-[0.88rem] font-semibold leading-snug @[24rem]:truncate">
                      {stage.asset.label}
                    </div>
                    {advisedAgainst && (
                      <div className="mt-0.5 text-[0.66rem] text-[var(--fg-faint)]" title={advisedAgainst}>
                        Optional for {industryLabel || "this industry"}
                      </div>
                    )}
                  </div>
                  <span
                    className={`ml-auto shrink-0 rounded-full px-2 py-0.5 text-[0.62rem] font-semibold ${BADGE_STYLE[status]}`}
                    style={{ backgroundColor: BADGE_BG[status] }}
                  >
                    {status === "running" ? runningLabel : wasSkipped ? "Skipped" : BADGE_LABEL[status]}
                  </span>
                </div>
                <ProgressBar status={status} progress={progress} />
                {startable && (
                  <button
                    type="button"
                    onClick={() => startAtStage(stage.asset.asset_id)}
                    className="mt-1 min-h-9 cursor-pointer py-1 text-[0.7rem] font-semibold underline underline-offset-2 text-[var(--fg-muted)] transition-colors hover:text-[var(--fg)] sm:min-h-0"
                  >
                    Start here →
                  </button>
                )}
              </div>
              {!isLast && <Connector active={status === "running"} done={status === "done"} />}
            </motion.div>
          );
        })}
          </motion.div>
        </AnimatePresence>
      </div>

      <div className="shrink-0 border-t border-[var(--border)] bg-[var(--bg)] px-4 pb-[max(0.75rem,env(safe-area-inset-bottom))] pt-3 sm:px-6">
        <div className="mb-1.5 flex items-center justify-between text-[0.76rem] font-medium text-[var(--fg-muted)]">
          <span>
            {PHASE_META[phase].label} · {doneCount} / {total} Complete
          </span>
          <span>{pct}%</span>
        </div>
        <div className="h-2 w-full overflow-hidden rounded-full bg-[var(--bg-sunken)]">
          <div
            className="h-full rounded-full transition-[width] duration-500 ease-out"
            style={{ width: `${pct}%`, backgroundColor: "var(--color-electric-blue)" }}
          />
        </div>
      </div>
    </div>
  );
}
