import { motion } from "framer-motion";
import { INDUSTRY_PROFILES, isIndustryBucket } from "../data/industryProfiles";
import { usePipelineStore } from "./pipelineStore";
import type { PipelineMessage } from "./pipelineStore";

/** The classifier's guess, offered on the industry question as the one-click answer.
 *
 * Above the pills rather than instead of them. The guess is usually right, and confirming it should
 * be one click. When it is wrong, the other buckets and "Other: specify" are right underneath, so
 * correcting it costs one click too. The rationale is shown because "why does it think we're a
 * lender?" is the question an operator has when a machine names their client's industry. */
export function IndustryGuessOptions({ message }: { message: PipelineMessage }) {
  const submitAnswer = usePipelineStore((s) => s.submitAnswer);
  const guess = message.industryGuess;
  if (!guess || !isIndustryBucket(guess.bucket)) return null;
  const bucketLabel = INDUSTRY_PROFILES[guess.bucket].label;
  const named = guess.label && guess.label !== bucketLabel ? `${guess.label} (${bucketLabel})` : bucketLabel;

  return (
    <div className="mt-2.5 rounded-xl border border-dashed border-[var(--border)] bg-[var(--bg-sunken)] px-3 py-2.5">
      <div className="text-[0.76rem] font-semibold">Looks like {named}</div>
      {guess.rationale ? (
        <p className="mt-0.5 text-[0.72rem] leading-relaxed text-[var(--fg-muted)]">{guess.rationale}</p>
      ) : null}
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <motion.button
          type="button"
          onClick={() => submitAnswer(bucketLabel)}
          whileTap={{ scale: 0.97 }}
          className="min-h-10 cursor-pointer rounded-full px-3.5 py-1.5 text-[0.78rem] font-semibold text-white sm:min-h-0"
          style={{ backgroundColor: "var(--color-electric-blue)" }}
        >
          Yes, that's right
        </motion.button>
      </div>
      <p className="mt-1.5 text-[0.68rem] leading-relaxed text-[var(--fg-faint)]">
        Not right? Pick the closest option below, or choose "Other" and type it in.
      </p>
    </div>
  );
}

/** The forward walk reached a stage the client's industry rarely needs.
 *
 * The skip is the primary action because it is the recommendation. Building it anyway sits right
 * beside it, and the stage stays in the pipeline either way. That is the whole difference between
 * this and removing the stage: a static industry rule will be wrong for some client, and the
 * operator is the one who knows which. */
export function StageAdvisoryCard({ message }: { message: PipelineMessage }) {
  const skipAdvisedStage = usePipelineStore((s) => s.skipAdvisedStage);
  const buildAdvisedStage = usePipelineStore((s) => s.buildAdvisedStage);
  const advisory = message.advisory;
  if (!advisory) return null;
  const pending = advisory.status === "pending" && !message.superseded;

  return (
    <div
      className="w-full min-w-0 max-w-[35rem] rounded-2xl border border-[var(--border)] bg-[var(--bg-raised)] px-3 py-3 msg-rise @[30rem]:px-4 @[30rem]:py-3.5"
      style={pending ? undefined : { opacity: 0.7 }}
    >
      <div className="text-[0.92rem] font-medium">
        Next is {advisory.label}. It's usually skipped for {advisory.industryLabel || "this industry"}.
      </div>
      <p className="mt-1 text-[0.8rem] leading-relaxed text-[var(--fg-muted)]">{advisory.reason}</p>
      {pending ? (
        <>
          <div className="mt-2.5 flex flex-wrap items-center gap-2">
            <motion.button
              type="button"
              onClick={() => skipAdvisedStage(message.id)}
              whileTap={{ scale: 0.97 }}
              className="min-h-10 cursor-pointer rounded-full px-3.5 py-1.5 text-[0.8rem] font-semibold text-white sm:min-h-0"
              style={{ backgroundColor: "var(--color-electric-blue)" }}
            >
              Skip {advisory.label}
            </motion.button>
            <motion.button
              type="button"
              onClick={() => buildAdvisedStage(message.id)}
              whileHover={{ backgroundColor: "var(--hover)" }}
              whileTap={{ scale: 0.97 }}
              className="min-h-10 cursor-pointer rounded-full border border-[var(--border-strong)] px-3.5 py-1.5 text-[0.8rem] font-medium sm:min-h-0"
            >
              Build it anyway
            </motion.button>
          </div>
          <p className="mt-1.5 text-[0.68rem] leading-relaxed text-[var(--fg-faint)]">
            Skipping removes nothing. {advisory.label} stays in the Asset Pipeline, and "Start here" builds it whenever you want.
          </p>
        </>
      ) : (
        <p className="mt-2 text-[0.76rem] italic text-[var(--fg-faint)]">
          {advisory.status === "skipped" ? "skipped" : advisory.status === "building" ? "building it anyway" : "no longer current"}
        </p>
      )}
    </div>
  );
}
