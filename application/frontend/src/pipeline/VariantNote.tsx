import { VARIANT_LABEL } from "../lib/pageVariants";
import type { PipelineMessage } from "./pipelineStore";

const num = (n: number) => n.toLocaleString("en-US");

/** One line on a Pillar Page variant's draft: which take it is, what it measured before writing,
 * and "Build with Manus" once it is drafted. Renders nothing on any other card. */
export function VariantNote({ message, onBuildWithManus }: { message: PipelineMessage; onBuildWithManus?: () => void }) {
  if (!message.variant) return null;
  const measure = message.pageVariant;
  const facts: string[] = [];
  if (measure?.reference && !measure.reference.error) {
    facts.push(`against the current page (${num(measure.reference.words)} words, ${measure.reference.sections.length} sections)`);
  }
  if (measure?.beat_sheet) {
    const read = measure.beat_sheet.competitors.filter((c) => !c.error).length;
    facts.push(`${read} competitor page${read === 1 ? "" : "s"} read, ${measure.beat_sheet.topics.length} topics to beat`);
  }
  const offerManus =
    onBuildWithManus && !message.streaming && !message.superseded && message.savePhase !== "saved" && message.text?.trim() && !message.manus;
  return (
    <div data-variant-note={message.variant} className="mb-2.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-[0.74rem]">
      <span
        className="rounded-full border px-2 py-0.5 font-semibold"
        style={{ borderColor: "var(--color-electric-blue)", color: "var(--color-electric-blue)" }}
      >
        Variant: {VARIANT_LABEL[message.variant]}
      </span>
      {facts.length > 0 && <span className="text-[var(--fg-muted)]">Measured {facts.join("; ")}</span>}
      {measure?.notes.map((note) => (
        <span key={note} className="text-[var(--color-signal-amber)]">
          {note}
        </span>
      ))}
      {offerManus && (
        <button
          type="button"
          onClick={onBuildWithManus}
          className="cursor-pointer rounded-full border border-[var(--border-strong)] px-2.5 py-0.5 font-medium"
          title="Build this variant with the Manus agent, which opens the reference page in a browser. Costs Manus credits."
        >
          Build with Manus
        </button>
      )}
      {message.variantAlternate && (
        <span className="text-[var(--fg-faint)]">Kept in Deliverables — another variant was approved for the rest of the run.</span>
      )}
      {!message.variantAlternate && message.variantNotChosen && (
        <span className="text-[var(--fg-faint)]">Not chosen: another variant was approved.</span>
      )}
      {message.manusRebuilt && <span className="text-[var(--fg-faint)]">Rebuilt with Manus below.</span>}
    </div>
  );
}
