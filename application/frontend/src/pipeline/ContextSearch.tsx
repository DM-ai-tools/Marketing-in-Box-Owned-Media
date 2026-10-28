import { motion } from "framer-motion";
import { ASSET_BY_ID, producerAssetIdFor } from "../data/assetCatalog";
import { suggestionValue } from "../lib/contextSearch";
import type { ContextRetrievalReport } from "./pipelineApi";
import { usePipelineStore, type PipelineMessage } from "./pipelineStore";

/** The document a context key names, as the operator knows it: the stage that produced it. */
function sourceName(contextKey: string, fallback: string): string {
  const asset = ASSET_BY_ID[contextKey] ?? ASSET_BY_ID[producerAssetIdFor(contextKey) ?? ""];
  return asset?.label ?? fallback;
}

/** An answer found in a document this run already approved, offered on the question itself.
 *
 * Above the answer line rather than instead of it, like the industry guess: the suggestion is the
 * one-click answer, typing (or another pill) is the correction. The quote is shown so the operator
 * can see the answer was read, not made up; the server drops any suggestion whose quote is not in
 * its source. Nothing is ever filled in without this click. */
export function SuggestionOptions({ message }: { message: PipelineMessage }) {
  const submitAnswer = usePipelineStore((s) => s.submitAnswer);
  const suggestion = message.suggestion;
  const field = message.field;
  if (!suggestion || !field) return null;

  const where = sourceName(suggestion.context_key, suggestion.source_label);
  const pills = field.kind === "enum_choice" || field.kind === "multi_select" || field.kind === "boolean_flag";

  return (
    <div
      data-suggestion
      className="mt-2.5 rounded-xl border border-dashed border-[var(--border)] bg-[var(--bg-sunken)] px-3 py-2.5"
    >
      <div className="text-[0.72rem] font-semibold uppercase tracking-wide text-[var(--fg-faint)]">
        Found in {where}
        {suggestion.section_path ? ` › ${suggestion.section_path}` : ""}
      </div>
      <p className="mt-1 whitespace-pre-wrap text-[0.86rem] font-medium">{suggestion.answer}</p>
      <p className="mt-1 text-[0.72rem] italic leading-relaxed text-[var(--fg-muted)]">“{suggestion.quote}”</p>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <motion.button
          type="button"
          onClick={() => submitAnswer(suggestionValue(field, suggestion.answer))}
          whileTap={{ scale: 0.97 }}
          className="min-h-10 cursor-pointer rounded-full px-3.5 py-1.5 text-[0.78rem] font-semibold text-white sm:min-h-0"
          style={{ backgroundColor: "var(--color-electric-blue)" }}
        >
          Use this
        </motion.button>
        <span className="text-[0.72rem] text-[var(--fg-faint)]">
          {pills ? "or choose a different option below" : "or type a different answer below"}
        </span>
      </div>
    </div>
  );
}

/** Which upstream documents this stage read as their relevant sections rather than whole. Said on
 * the card because it changes what the stage saw: a draft that misses a proof point the CRO
 * rewrite had is explained here, not hidden. */
export function ContextRetrievalNote({
  report,
  assetId,
}: {
  report?: ContextRetrievalReport;
  assetId?: string;
}) {
  if (!report?.fields.length) return null;
  const fields = assetId ? ASSET_BY_ID[assetId]?.fields ?? [] : [];
  const k = (chars: number) => `${Math.max(1, Math.round(chars / 1000))}k`;
  return (
    <div data-context-retrieval className="mt-3 rounded-xl border border-[var(--border)] px-3 py-2 text-[0.76rem]">
      <p className="font-semibold">Relevant sections only</p>
      <ul className="mt-1 list-disc pl-4 text-[var(--fg-muted)]">
        {report.fields.map((f) => {
          const label = fields.find((field) => field.field_id === f.field_id)?.label ?? f.field_id;
          return (
            <li key={f.field_id}>
              {label}: {f.sections} of {f.total_sections} sections of {sourceName(f.context_key, f.context_key)} (
              {k(f.chars_after)} of {k(f.chars_before)} characters), chosen for this input by meaning.
            </li>
          );
        })}
      </ul>
    </div>
  );
}
