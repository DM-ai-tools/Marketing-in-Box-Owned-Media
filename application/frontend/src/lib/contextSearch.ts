import { planField, type KnownFacts } from "./fieldResolution";
import type { AssetDefinition, FieldDef } from "../data/types";
import type { ContextStore } from "../store/chatStore.types";

/** Which of a stage's questions to send to `POST /pipeline/runs/{id}/prefill/{asset}`, the search
 * of this run's approved documents for answers the operator would otherwise type
 * (`app/services/vector_context.py`).
 *
 * Only questions the walk would actually ask:
 * - A plain question goes with no key restriction: any approved document may answer it.
 * - A context field whose document is present but whose `sub_key` the regex extractor could not
 *   find (`lib/contextSubKeys.ts`) goes restricted to that document. This is the one case where the
 *   regex is wrong rather than the document missing; without it the field silently becomes a
 *   question.
 * - A context field whose document is simply absent is left out. A search cannot find a document
 *   that was never approved.
 *
 * Also left out: questions answered by a card of their own (topic suggestions, file attachments),
 * and conditional follow-ups, which depend on an answer not yet given. The server drops the legal
 * guardrail fields itself (`NEVER_SUGGEST`), so that rule lives in one place. */
export interface PrefillFieldIn {
  field_id: string;
  label: string;
  help: string;
  kind: string;
  choices: string[];
  keys: string[];
}

const NOT_SUGGESTED_KINDS = new Set(["headline_choice", "file_attach"]);

export function prefillCandidates(
  asset: AssetDefinition,
  context: ContextStore,
  knownFacts: KnownFacts,
  producerOf: (contextKey: string) => string | undefined,
): PrefillFieldIn[] {
  const out: PrefillFieldIn[] = [];
  for (const field of asset.fields) {
    if (NOT_SUGGESTED_KINDS.has(field.kind) || field.conditional_on) continue;
    if (planField(field, context, {}, knownFacts).action !== "ask") continue;

    let keys: string[] = [];
    if (isContextField(field)) {
      const key = field.context_key;
      if (!field.sub_key || !key || key.endsWith("_*") || !context[key]?.text?.trim()) continue;
      const producer = producerOf(key);
      keys = producer && producer !== key ? [key, producer] : [key];
    }

    out.push({
      field_id: field.field_id,
      label: field.label,
      help: field.helpText ?? "",
      kind: field.kind === "context_reference" ? "text" : field.kind,
      choices: field.choices ?? [],
      keys,
    });
  }
  return out;
}

function isContextField(field: FieldDef): boolean {
  return field.kind === "context_reference" || field.source === "auto_from_context" || field.source === "live_fetch";
}

/** The value a suggestion submits, in the shape `submitAnswer` expects for this field. */
export function suggestionValue(field: FieldDef, answer: string): string | number | boolean {
  if (field.kind === "boolean_flag") return /^y(es)?$/i.test(answer.trim());
  if (field.kind === "number") {
    const n = Number(answer.replace(/,/g, ""));
    return Number.isFinite(n) ? n : answer;
  }
  return answer;
}
