import { extractSubKey } from "./contextSubKeys";
import type { AssetDefinition, FieldDef } from "../data/types";
import type { ContextStore } from "../store/chatStore.types";

/** Resolves a field's context_key (which may be a "prefix_*" wildcard) against the session context
 * store.
 *
 * A field with a `sub_key` wants one entry of a map-shaped upstream output, not the document that
 * states it, so the entry is read back out of that document (see `lib/contextSubKeys.ts`). When the
 * document doesn't state it, the field is reported unresolved and the operator is asked — which is
 * the point: handing an 80KB CRO rewrite to a field whose answer is the single word "homeowner"
 * both bloats every prompt downstream of it and misinforms the prompt receiving it. */
export function resolveContext(
  field: FieldDef,
  context: ContextStore,
): { assetId: string; label: string; text: string } | undefined {
  if (field.context_keys?.length) return gatherContext(field.context_keys, context, field.context_excerpt_chars);
  if (!field.context_key || field.context_key === "unresolved_context_key") return undefined;

  let entry: { assetId: string; label: string; text: string } | undefined;

  if (field.context_key.endsWith("_*")) {
    const prefix = field.context_key.slice(0, -2);
    const match = Object.entries(context).find(
      ([key]) => key === prefix || key.startsWith(`${prefix}_`) || key.startsWith(prefix),
    );
    entry = match?.[1];
  } else {
    entry = context[field.context_key];
  }

  if (!entry || !field.sub_key) return entry;

  const value = extractSubKey(field.context_key, field.sub_key, entry.text);
  return value ? { ...entry, text: value } : undefined;
}

/** Gather several upstream outputs into one value, for a `context_keys` field.
 *
 * Each document is headed with the name of the asset that produced it, because the prompt receiving
 * them asks for "any of these already built" and cannot tell a funnel from a lead magnet in an
 * unlabelled concatenation. Undefined when none of the keys has been produced yet, which leaves the
 * field to be asked in the ordinary way.
 */
function gatherContext(
  keys: string[],
  context: ContextStore,
  excerptChars?: number,
): { assetId: string; label: string; text: string } | undefined {
  const seen = new Set<string>();
  const found = keys
    .map((key) => context[key])
    .filter((entry): entry is ContextEntryLike => Boolean(entry?.text?.trim()))
    // One stage publishes several keys that all point at the same document; list it once.
    .filter((entry) => (seen.has(entry.assetId) ? false : (seen.add(entry.assetId), true)));
  if (!found.length) return undefined;

  const body = (entry: ContextEntryLike) =>
    excerptChars ? excerptOf(entry.text, excerptChars) : entry.text.trim();
  const intro = excerptChars
    ? `Built and approved earlier in this run (treat as done, do not re-plan): ${found.map((e) => e.label).join(", ")}.\n\n`
    : "";

  return {
    assetId: found.map((entry) => entry.assetId).join("+"),
    label: found.map((entry) => entry.label).join(" + "),
    text: intro + found.map((entry) => `## ${entry.label}\n\n${body(entry)}`).join("\n\n---\n\n"),
  };
}

/** The opening of a document, as readable text: an HTML deliverable is reduced to its words first,
 * because its first thousand characters are `<head>` boilerplate that says nothing about the page. */
export function excerptOf(text: string, max: number): string {
  let plain = text.trim();
  if (/^\s*(?:<!doctype|<html|<head|<body|<div|<section)/i.test(plain) || /<\/(?:div|section|body)>/i.test(plain)) {
    plain = plain
      .replace(/<(script|style|svg|head)\b[\s\S]*?<\/\1>/gi, " ")
      .replace(/<[^>]+>/g, " ")
      .replace(/&nbsp;/g, " ")
      .replace(/\s+/g, " ")
      .trim();
  }
  if (plain.length <= max) return plain;
  const cut = plain.slice(0, max);
  const lastBreak = Math.max(cut.lastIndexOf("\n"), cut.lastIndexOf(". "));
  return `${(lastBreak > max * 0.5 ? cut.slice(0, lastBreak + 1) : cut).trimEnd()}\n\n[Excerpt. The full document was approved in this run.]`;
}

type ContextEntryLike = { assetId: string; label: string; text: string };

function conditionMatches(equals: string | number | boolean | string[], value: unknown): boolean {
  if (Array.isArray(equals)) return equals.includes(value as string);
  return equals === value;
}

/** True if this field should be silently skipped (auto-filled as N/A) given answers
 * collected so far for the *same* asset — used for conditional_on relationships like
 * "only ask Regulation Name if Regulated Field Flag was answered Yes". */
export function isConditionallySkipped(field: FieldDef, answers: Record<string, unknown>): boolean {
  if (!field.conditional_on) return false;
  const currentValue = answers[field.conditional_on.field];
  if (currentValue === undefined) return true;
  return !conditionMatches(field.conditional_on.equals, currentValue);
}

/** Run-level facts already established earlier in the run, keyed by a canonical fact name (see
 * `CLIENT_PROFILE_SOURCES` in `pipeline/pipelineData.ts`), plus the map from field_id to that
 * canonical name. Passed together so `planField` can answer "have we already been told this?"
 * without knowing anything about the pipeline's own bookkeeping. */
export interface KnownFacts {
  values: Record<string, string>;
  fieldToFact: Record<string, string>;
}

export type FieldPlan =
  | { action: "ask"; field: FieldDef }
  | { action: "auto-context"; field: FieldDef; label: string }
  | { action: "auto-known"; field: FieldDef; value: string }
  | { action: "confirm-context"; field: FieldDef; label: string; text: string }
  | { action: "auto-empty"; field: FieldDef }
  | { action: "auto-conditional-skip"; field: FieldDef }
  /** This field already has an answer, so it is passed over untouched. What makes the walk
   * idempotent — and therefore what lets an operator edit one earlier answer without the rest of
   * the stage's questions being asked again (see `editField` in `pipeline/pipelineStore.ts`). */
  | { action: "already-answered"; field: FieldDef };

/** Decides what happens to a single field, in isolation, given the current context store
 * and the answers already collected for this asset run. Does not mutate anything. */
export function planField(
  field: FieldDef,
  context: ContextStore,
  answers: Record<string, unknown>,
  knownFacts?: KnownFacts,
): FieldPlan {
  // Checked before everything else: an answer that exists is the answer, whatever its origin
  // (typed, skipped, auto-filled, or re-typed just now during an edit). A re-walk must never
  // re-ask it or quietly overwrite it with a fresh auto-fill.
  if (answers[field.field_id] !== undefined) {
    return { action: "already-answered", field };
  }

  if (isConditionallySkipped(field, answers)) {
    return { action: "auto-conditional-skip", field };
  }

  if (field.kind === "context_reference" || field.source === "auto_from_context" || field.source === "live_fetch") {
    const resolved = resolveContext(field, context);
    if (resolved) {
      // An overridable field stops to offer a choice: reuse the upstream output, or supply a
      // different source. Everything else fills in silently, as before.
      return field.overridable
        ? { action: "confirm-context", field, label: resolved.label, text: resolved.text }
        : { action: "auto-context", field, label: resolved.label };
    }

    if (field.fallback === "treat_as_empty_if_missing" || field.fallback === "skip_silently_if_missing") {
      return { action: "auto-empty", field };
    }
    // fallback is "ask_user_if_missing" (or unset) — fall through to asking.
    return { action: "ask", field };
  }

  // Plain input the operator has effectively already answered earlier in the run (the client's
  // name, their website) — reuse it instead of asking the same question once per asset.
  const factKey = knownFacts?.fieldToFact[field.field_id];
  const known = factKey ? knownFacts?.values[factKey] : undefined;
  if (known) return { action: "auto-known", field, value: known };

  return { action: "ask", field };
}

/** A field identified for a caller that wants to name it back to the operator — the id to act on,
 * and the label to show. Structurally the same as `EditableFieldRef` in `pipeline/pipelineStore.ts`,
 * kept declared here so this module stays free of pipeline imports. */
export interface WalkedField {
  fieldId: string;
  label: string;
}

function yesNo(text: string | undefined): boolean | undefined {
  const value = text?.trim();
  if (!value) return undefined;
  if (/^(y|yes|true)$/i.test(value)) return true;
  if (/^(n|no|false)$/i.test(value)) return false;
  return undefined;
}

export interface FieldWalkResult {
  index: number;
  field: FieldDef | null;
  /** The plan for `field`, when the walk stopped on one. `"ask"` needs a typed/clicked answer;
   * `"confirm-context"` needs an accept-or-override decision. */
  plan?: FieldPlan;
  /** Labels of the *producing assets* whose output was auto-filled along the way — what the
   * "Auto-filled from Context Store: …" line names. */
  autoContextLabels: string[];
  /** The fields those context auto-fills landed in, so each can be offered for editing. */
  autoContextFields: WalkedField[];
  /** Fields auto-filled from answers given earlier in this run. */
  autoKnownFields: WalkedField[];
}

/** Walks an asset's field list from `fromIndex`, auto-resolving everything that can be
 * auto-resolved, and returns the next field that actually needs a human decision (plus the
 * labels collected along the way so the UI can summarise what it filled in). */
export function findNextAskable(
  asset: AssetDefinition,
  context: ContextStore,
  answers: Record<string, unknown>,
  fromIndex: number,
  knownFacts?: KnownFacts,
): FieldWalkResult {
  const autoContextLabels: string[] = [];
  const autoContextFields: WalkedField[] = [];
  const autoKnownFields: WalkedField[] = [];

  for (let i = fromIndex; i < asset.fields.length; i++) {
    const field = asset.fields[i];
    const plan = planField(field, context, answers, knownFacts);

    if (plan.action === "ask" || plan.action === "confirm-context") {
      return { index: i, field, plan, autoContextLabels, autoContextFields, autoKnownFields };
    }

    if (plan.action === "already-answered") {
      continue; // keep the answer exactly as it is, and say nothing about it
    }

    if (plan.action === "auto-context") {
      // A yes/no field is stored as the yes/no itself, not as a marker for it. Whatever is
      // conditional on it (`regulation_name`, asked only when "regulated" is true) reads the answer
      // straight off `answers`, and a marker string is neither true nor false — so the follow-up
      // would never be asked, even for a client that is regulated.
      const flag = field.kind === "boolean_flag" ? yesNo(resolveContext(field, context)?.text) : undefined;
      answers[field.field_id] = flag ?? `[[context: ${plan.label}]]`;
      autoContextLabels.push(plan.label);
      autoContextFields.push({ fieldId: field.field_id, label: field.label });
    } else if (plan.action === "auto-known") {
      answers[field.field_id] = plan.value;
      autoKnownFields.push({ fieldId: field.field_id, label: field.label });
    } else {
      answers[field.field_id] = field.default ?? "N/A";
    }
  }

  return { index: -1, field: null, autoContextLabels, autoContextFields, autoKnownFields };
}
