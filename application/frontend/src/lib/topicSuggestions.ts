import type { HeadlineCandidate } from "../pipeline/pipelineApi";
import type { HeadlineChoiceState, PipelineMessage } from "../pipeline/pipelineStore";

/** The "Topic suggestions" section at the top of an exported asset.
 *
 * A headline gate offers a batch of topics, each with the reason it was suggested, and the operator
 * picks from it. The document that follows is built on the picks, but the file on its own gave no
 * sign that there *was* a choice: a reader could not see what else was on the table, or why the
 * chosen ones were chosen over it. This section is that record — every topic offered, selected
 * first, each with its rationale and the evidence it was generated from.
 *
 * Shown in the reader (and so in Deliverables) and in the downloaded Markdown, placed by
 * `withTopicSuggestions` — before the lead magnet's PART 2 scorecard, at the top elsewhere. Never
 * written into `message.text`: the saved text is what the next stage reads out of the Context
 * Store, and a list of *rejected* topics there would be read as more topics to write about.
 *
 * Everything here comes from the gate's own message in the transcript, which keeps the full
 * candidate list and `chosenIds` after a pick — so it costs no request and works on a reloaded
 * chat exactly as on a live one.
 */

type ChosenGate = PipelineMessage & { headlines: HeadlineChoiceState };

/** The headline gates that fed this generation: chosen gates for the same asset, earlier in the same
 * leg of the transcript. One per field, the latest winning — a gate the operator re-ran after a
 * stage re-run is the one this document was actually built on. */
export function topicGatesFor(messages: PipelineMessage[], generation: PipelineMessage): ChosenGate[] {
  const end = messages.findIndex((m) => m.id === generation.id);
  if (end <= 0 || !generation.assetId) return [];

  const byField = new Map<string, ChosenGate>();
  for (const m of messages.slice(0, end)) {
    if (m.kind !== "headline-choice" || m.assetId !== generation.assetId) continue;
    if (m.phase !== generation.phase || m.trackId !== generation.trackId) continue;
    // Superseded gates are kept: a re-run supersedes the old generation along with its gate, and
    // that older document was still built on the older pick. "Latest before this message" is what
    // pairs each document with its own gate.
    if (m.headlines?.status !== "chosen" || !m.headlines.candidates?.length) continue;
    byField.set(m.field?.field_id ?? m.headlines.slot, m as ChosenGate);
  }
  return [...byField.values()];
}

const FUNNEL_NAMES: Record<string, string> = {
  TOFU: "TOFU (top of funnel: awareness)",
  MOFU: "MOFU (middle of funnel: consideration)",
  BOFU: "BOFU (bottom of funnel: decision)",
};

function capitalise(text: string): string {
  return text ? text[0].toUpperCase() + text.slice(1) : text;
}

/** What kind of piece the topic is for. The lead magnet names a format per candidate ("checklist",
 * "calculator"); every other slot is one content type throughout, named by the gate's subject. */
function contentType(c: HeadlineCandidate, subject: string | undefined): string | null {
  const format = typeof c.extras?.format === "string" ? c.extras.format.trim() : "";
  if (format && subject) return `${capitalise(format)} (${subject})`;
  if (format) return capitalise(format);
  return subject ? capitalise(subject) : null;
}

/** Why and on what basis this one was generated — the framework's reasoning, then the evidence. */
function candidateLines(c: HeadlineCandidate, subject: string | undefined): string[] {
  const lines: string[] = [];
  const bullet = (label: string, value: string | null | undefined) => {
    if (value && value.trim()) lines.push(`   - **${label}:** ${value.trim()}`);
  };

  bullet("Why this one", c.why_it_works);
  bullet("Funnel stage", c.funnel ? FUNNEL_NAMES[c.funnel.toUpperCase()] ?? c.funnel : null);
  bullet("Content type", contentType(c, subject));
  bullet("Search intent", c.intent ? capitalise(c.intent) : null);

  // The basis: which keyword it targets and whether this run's keyword data backs it. An ungrounded
  // candidate says so, rather than letting "no volume shown" read as a volume nobody measured.
  if (c.primary_keyword) {
    const demand = c.grounded
      ? c.search_volume !== null
        ? `${c.search_volume.toLocaleString("en-US")} searches/mo`
        : "in this run's keyword report"
      : "not in this run's keyword report, so no demand data";
    const cluster = c.source_cluster ? `, cluster "${c.source_cluster}"` : "";
    bullet("Keyword basis", `"${c.primary_keyword}" (${demand}${cluster})`);
  } else if (!c.grounded) {
    bullet("Keyword basis", "no keyword from this run's keyword report, so no demand data");
  }

  bullet("Headline formula", c.framework_formula);
  if (c.curiosity_elements?.length) bullet("Curiosity elements", c.curiosity_elements.join(", "));
  bullet("Specificity", c.specificity);
  if (c.traffic_temperature) bullet("Audience temperature", capitalise(c.traffic_temperature));
  bullet("Trend evidence", c.trend_evidence);
  if (typeof c.extras?.mechanic === "string") bullet("Mechanic", c.extras.mechanic);
  return lines;
}

function renderCandidate(c: HeadlineCandidate, n: number, subject: string | undefined): string {
  return [`${n}. **${c.headline.trim()}**`, ...candidateLines(c, subject)].join("\n");
}

function renderGate(gate: ChosenGate, heading: string): string {
  const h = gate.headlines;
  const candidates = h.candidates ?? [];
  const chosen = new Set(h.chosenIds ?? []);
  const picked = candidates.filter((c) => chosen.has(c.id));
  const rest = candidates.filter((c) => !chosen.has(c.id));
  const own = h.ownHeadline?.trim();

  // `###`/`####`, not `##`: inside the reader these are the section's own subheads, and a `##` would
  // be read as a new section by `assetDocument`'s plain-heading rule.
  const out: string[] = [];
  const selectedCount = own ? 1 : picked.length;
  out.push(`### ${heading}: ${selectedCount} selected of ${candidates.length} suggested`, "");

  const basis: string[] = [];
  if (h.serviceAnchor) basis.push(`Anchored on **${h.serviceAnchor}**.`);
  basis.push(
    h.groundedInKeywords
      ? "Ranked on this run's keyword report (search demand), then the headline framework's checklist."
      : "Built on the headline framework alone; no keyword report was available, so none carries demand data.",
  );
  if (h.webSearchUsed) basis.push("Live web search was used for trend evidence.");
  out.push(basis.join(" "), "");

  out.push("#### Selected", "");
  if (own) {
    out.push(`1. **${own}**`, "   - Written by the operator in place of the suggestions below.");
  } else if (picked.length) {
    out.push(picked.map((c, i) => renderCandidate(c, i + 1, h.subject)).join("\n\n"));
  } else {
    out.push("_None recorded._");
  }

  if (rest.length) {
    out.push("", "#### Also suggested, not selected", "");
    out.push(rest.map((c, i) => renderCandidate(c, i + 1, h.subject)).join("\n\n"));
  }
  return out.join("\n");
}

/** The section's own heading. `assetDocument` treats this exact line as a section boundary under
 * every splitting rule, so it becomes its own row in the reader's outline rather than being folded
 * into whichever part it was inserted after. */
export const TOPIC_SUGGESTIONS_HEADING = "# Topic suggestions";
export const TOPIC_SUGGESTIONS_LABEL = "Topic suggestions";

export function isTopicSuggestionsHeading(line: string): boolean {
  return line.trim() === TOPIC_SUGGESTIONS_HEADING;
}

/** Where the section goes in a stage's own document, when not at the top: before the part that
 * first acts on the pick. The lead magnet's output opens with the competitor landscape (PART 1),
 * which the pick does not depend on, and PART 2 is the scorecard of the *selected* concepts — so the
 * full list of what was offered belongs immediately ahead of it. */
const INSERT_BEFORE: Record<string, RegExp> = {
  lead_magnet: /^\s{0,3}(?:#{1,4}\s*)?(?:\*\*)?\s*PART\s+2\b.*SELECTED\s+CONCEPT/i,
};

/** The document with the topic section placed in it — before the stage's anchor heading where one
 * is declared and present, otherwise at the top. A heading inside a fenced block is content, never
 * an anchor. Returns `text` unchanged when there is no section to place. */
export function withTopicSuggestions(text: string, section: string, assetId?: string): string {
  if (!section) return text;
  const anchor = assetId ? INSERT_BEFORE[assetId] : undefined;
  if (anchor) {
    const lines = text.split("\n");
    let fence: string | null = null;
    for (let i = 0; i < lines.length; i++) {
      const open = /^\s{0,3}(`{3,}|~{3,})/.exec(lines[i]);
      if (open) {
        const char = open[1][0];
        fence = fence === null ? char : fence === char ? null : fence;
        continue;
      }
      if (fence === null && anchor.test(lines[i])) {
        return [...lines.slice(0, i), section, ...lines.slice(i)].join("\n");
      }
    }
  }
  return `${section}\n${text}`;
}

/** The whole section, or "" when this generation had no headline gate. Ends with a rule so the
 * asset's own first heading is clearly where the deliverable starts. */
export function renderTopicSuggestions(gates: ChosenGate[]): string {
  if (!gates.length) return "";
  const sections = gates.map((g) => renderGate(g, g.headlines.label ?? g.field?.label ?? "Topics"));
  return [
    TOPIC_SUGGESTIONS_HEADING,
    "",
    "Suggested with the headline framework before this asset was written. The selected topics are the ones the document below is built on; the rest are listed with the same reasoning so the choice can be reviewed.",
    "",
    sections.join("\n\n"),
    "",
    "---",
    "",
  ].join("\n");
}

/** Convenience for the export buttons: the section for one generation message, or "". */
export function topicPreambleFor(messages: PipelineMessage[], generation: PipelineMessage | undefined): string {
  return generation ? renderTopicSuggestions(topicGatesFor(messages, generation)) : "";
}
