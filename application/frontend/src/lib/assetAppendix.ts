import { checkAppliesTo, renderBusinessCheck } from "./businessCheck";
import { competitorAppendixFor } from "./competitorSources";
import { earlierCheckReport } from "../pipeline/pipelineStore";
import type { PipelineMessage } from "../pipeline/pipelineStore";

/** Everything that follows an asset in the reader and in its exported file, in one place.
 *
 * Two sections, in this order: the competitors it was benchmarked on (the evidence it was written
 * against), then the business check (how it held up against the client's own facts). Each is
 * Markdown placed after the asset and never written into the saved text, so the next stage still
 * reads only what the stage wrote.
 *
 * The reader and the export menu both call this, which is what keeps a deliverable opened from
 * Deliverables and the file downloaded from it the same document. */

/** The business check of one generation, or "" while it is running, failed, or never ran. */
export function businessCheckAppendixFor(messages: PipelineMessage[], generation: PipelineMessage | undefined): string {
  if (!generation || generation.kind !== "generation") return "";
  // A report stored on a card from before a stage stopped being checked must not reach the file.
  if (!checkAppliesTo(generation.phase, generation.assetId)) return "";
  const check = generation.check;
  if (check?.status !== "done" || !check.report) return "";
  return renderBusinessCheck(check.report, {
    earlier: earlierCheckReport(messages, generation),
    approved: generation.savePhase === "saved",
  });
}

export function assetAppendixFor(messages: PipelineMessage[], generation: PipelineMessage | undefined): string {
  return generation ? competitorAppendixFor(messages, generation) + businessCheckAppendixFor(messages, generation) : "";
}
