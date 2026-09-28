/** Reading the structure a stage's output already has.
 *
 * Why this exists
 * ---------------
 * Every large prompt in `assets/Prompts/` ends by declaring a labelled output format. The CRO
 * rewrite closes with "Deliver in four labelled parts: PART 0 — MODE RESOLUTION TABLE / PART 1 —
 * CRO AUDIT REPORT / PART 2 — REWRITTEN PAGE / PART 3 — IMPLEMENTATION PACK". Pillar Page declares
 * PART 1-4. The blog prompt declares `PART 1 — The Set Plan` and then one `## Blog N — [Title]`
 * block per post. Webinar runs STEP 1-8 per package.
 *
 * The transcript threw all of it away: `GenerationCard` handed the whole document to one
 * `<Markdown>`, always expanded, in a flat list of fifteen stages. With `blog`, `lead_magnet` and
 * `webinar` configured at 128k output tokens, one card could be ten thousand words of unbroken
 * scroll.
 *
 * So this reads the headings back out and the card becomes an outline. Nothing is summarised and
 * nothing is dropped — this is a *reading* change, not a content change.
 *
 * The precedent is `htmlBlocks.ts`, which already does exactly this for one case: it lifts a fenced
 * ```html block out of the Markdown and hands it to a real renderer, leaving the surrounding prose
 * untouched. Same idea, generalised to the document's own sections.
 *
 * Losslessness
 * ------------
 * Two guarantees, and both are load-bearing because this sits between the operator and a
 * deliverable they are about to send to a client:
 *
 *   1. **Every character of the body reaches a section.** Text before the first heading becomes a
 *      lead section rather than being skipped, and each section's body is the verbatim slice
 *      between its heading and the next. The only line not re-rendered is the heading itself,
 *      which becomes the section's label — so it is still on screen, once instead of twice.
 *   2. **A document this cannot read renders exactly as it did before.** `structured: false` is a
 *      supported, expected outcome, and the caller falls back to a single `<Markdown>`.
 *
 * Exports are unaffected either way: `AssetExportButtons` is handed `message.text`, which this
 * never mutates.
 */

import { splitHtmlBlocks } from "./htmlBlocks";
import { isTopicSuggestionsHeading, TOPIC_SUGGESTIONS_LABEL } from "./topicSuggestions";

export type SectionKind = "prose" | "table" | "html";

export interface DocSubhead {
  id: string;
  label: string;
}

export interface DocSection {
  /** Unique within the document, and stable for the same text — used as the anchor id. */
  id: string;
  label: string;
  /** Verbatim Markdown between this heading and the next. */
  body: string;
  /** Words outside fenced code blocks, so a 900-line HTML part is not reported as 40,000 words. */
  words: number;
  kind: SectionKind;
  /** `###`-level headings inside, for the reader's outline rail. */
  subheads: DocSubhead[];
  /** Previewable HTML blocks inside (see `htmlBlocks.isPreviewableHtml`). */
  htmlBlocks: number;
}

export interface AssetDocument {
  sections: DocSection[];
  /** Words in the whole document, fenced code excluded. */
  words: number;
  /** False when no usable structure was found — render the text as one block, as before. */
  structured: boolean;
  /** Which rule matched, for the card's title attribute and for debugging a bad split. */
  pattern: string;
  /** Every `[CLIENT TO CONFIRM: …]` the document carries. The operator's real to-do list, and
   * until now buried in the implementation pack's prose. */
  placeholders: string[];
}

/** Below this many sections an outline is not a summary of anything. */
const MIN_SECTIONS = 2;

/** Below this many words the document fits on a screen or two, and an outline is pure overhead —
 * the operator would open every row immediately. */
const MIN_WORDS_FOR_OUTLINE = 250;

const MAX_SUBHEADS = 14;

/** Strip the Markdown decoration off a heading so it can be used as a label. */
function cleanLabel(raw: string): string {
  return raw
    .replace(/^\s*#+\s*/, "")
    .replace(/\*\*/g, "")
    .replace(/^\s*[-–—:]\s*/, "")
    .replace(/\s*[-–—:]\s*$/, "")
    .replace(/\s+/g, " ")
    .trim();
}

function slug(text: string): string {
  return (
    text
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 48) || "section"
  );
}

/**
 * The boundary rules, most specific first. The first rule that finds at least `MIN_SECTIONS`
 * headings wins the whole document — rules are not mixed, because a document split by two
 * different conventions at once produces sections at two different scales, which reads as a bug.
 *
 * Every rule anchors the entire line. That is what keeps prose out: the Pillar Page prompt's own
 * body contains the sentence "This table is PART 2 of the output", and an unanchored match would
 * have split the document there.
 */
const RULES: { name: string; match: (line: string) => string | null }[] = [
  {
    // `**PART 2 — REWRITTEN PAGE**`, `## PART 1 — CRO AUDIT REPORT`, `PART 0 — MODE RESOLUTION`
    name: "part",
    match: (line) => {
      const m = /^\s{0,3}(?:#{1,4}\s*)?(?:\*\*)?\s*(PART\s+\d+\b.*?)(?:\*\*)?\s*$/i.exec(line);
      return m ? cleanLabel(m[1]) : null;
    },
  },
  {
    // The plural deliverables: `## Blog 3 — Recovery Week by Week`, `## Webinar 2 — …`.
    // Requires a real heading marker, because "Blog 3" can legitimately appear in a sentence.
    //
    // It also accepts a `PART n` heading, which looks like mixing rules and is not. The blog
    // prompt's declared format is "PART 1 — The Set Plan" followed by one `## Blog N` block per
    // post: the set plan is a peer of the blocks it introduces, at the same level. Matching only
    // the blocks demoted it to the untitled lead section, so a real part of the deliverable lost
    // its name. The `part` rule still wins outright on a document that is only parts, because it
    // is tried first.
    name: "block",
    match: (line) => {
      const block =
        /^\s{0,3}#{1,4}\s*(?:\*\*)?\s*((?:Blog|Webinar|Lead\s*Magnet|Episode|Chapter|Book|Post|Offer|Concept|Sequence|Email)\s+\d+\b.*?)(?:\*\*)?\s*$/i.exec(
          line,
        );
      if (block) return cleanLabel(block[1]);
      const part = /^\s{0,3}(?:#{1,4}\s*)?(?:\*\*)?\s*(PART\s+\d+\b.*?)(?:\*\*)?\s*$/i.exec(line);
      return part ? cleanLabel(part[1]) : null;
    },
  },
  {
    // `### STEP 2B — COMPETITOR WEBINAR SYNTHESIS`
    name: "step",
    match: (line) => {
      const m = /^\s{0,3}(?:#{1,4}\s*)?(?:\*\*)?\s*(STEP\s+\d+[A-Za-z]?\b.*?)(?:\*\*)?\s*$/i.exec(line);
      return m ? cleanLabel(m[1]) : null;
    },
  },
  {
    // `## 4. Phase 2: Sub-Service Expansion` — the Plan of Action's declared numbering.
    name: "numbered",
    match: (line) => {
      const m = /^\s{0,3}#{1,3}\s*(\d{1,2}[.)]\s+\S.*?)\s*$/.exec(line);
      return m ? cleanLabel(m[1]) : null;
    },
  },
  {
    // Plain `#`/`##` headings. The catch-all for the stages whose prompts declare no parts.
    name: "heading",
    match: (line) => {
      const m = /^\s{0,3}(#{1,2})\s+(\S.*?)\s*$/.exec(line);
      return m ? cleanLabel(m[2]) : null;
    },
  },
];

interface Boundary {
  line: number;
  label: string;
}

/** Line indices where a rule matched, skipping anything inside a fenced code block.
 *
 * The fence skip is not optional: the page-design stages emit a complete HTML document inside a
 * ```html fence, and an `<h2>` or a `#` in there is content, not a section of the deliverable.
 */
function scan(lines: string[], rule: (line: string) => string | null): Boundary[] {
  const out: Boundary[] = [];
  let fenceChar: string | null = null;

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const fence = /^\s{0,3}(`{3,}|~{3,})/.exec(line);
    if (fence) {
      const char = fence[1][0];
      if (fenceChar === null) fenceChar = char;
      else if (fenceChar === char) fenceChar = null;
      continue;
    }
    if (fenceChar !== null) continue;

    const label = rule(line);
    if (label) out.push({ line: i, label });
  }
  return out;
}

/** Words outside fenced code. A fenced 900-line page is one block of markup, not prose. */
export function countWords(text: string): number {
  let total = 0;
  let fenceChar: string | null = null;

  for (const line of text.split("\n")) {
    const fence = /^\s{0,3}(`{3,}|~{3,})/.exec(line);
    if (fence) {
      const char = fence[1][0];
      if (fenceChar === null) fenceChar = char;
      else if (fenceChar === char) fenceChar = null;
      continue;
    }
    if (fenceChar !== null) continue;
    const trimmed = line.trim();
    if (trimmed) total += trimmed.split(/\s+/).length;
  }
  return total;
}

function findSubheads(body: string, sectionId: string): DocSubhead[] {
  const out: DocSubhead[] = [];
  let fenceChar: string | null = null;

  for (const line of body.split("\n")) {
    const fence = /^\s{0,3}(`{3,}|~{3,})/.exec(line);
    if (fence) {
      const char = fence[1][0];
      if (fenceChar === null) fenceChar = char;
      else if (fenceChar === char) fenceChar = null;
      continue;
    }
    if (fenceChar !== null) continue;

    const heading = /^\s{0,3}#{3,4}\s+(\S.*?)\s*$/.exec(line);
    // A bold-only line is how the CRO and blog prompts label their sub-parts ("**Finding.**" is
    // not one — the trailing period and inline continuation rule it out, hence the `$` anchor).
    const bold = heading ? null : /^\s{0,3}\*\*([^*]{3,80})\*\*\s*:?\s*$/.exec(line);
    const label = heading ? cleanLabel(heading[1]) : bold ? cleanLabel(bold[1]) : null;
    if (!label) continue;

    out.push({ id: `${sectionId}-${slug(label)}-${out.length}`, label });
    if (out.length >= MAX_SUBHEADS) break;
  }
  return out;
}

function kindOf(body: string, htmlBlocks: number): SectionKind {
  if (htmlBlocks > 0) return "html";
  const tableLines = body.split("\n").filter((l) => /^\s{0,3}\|/.test(l)).length;
  return tableLines >= 2 ? "table" : "prose";
}

const PLACEHOLDER = /\[CLIENT TO CONFIRM:([^\]]*)\]/gi;

export function findPlaceholders(text: string): string[] {
  const out: string[] = [];
  for (const match of text.matchAll(PLACEHOLDER)) {
    const value = match[1].trim();
    if (value && !out.includes(value)) out.push(value);
  }
  return out;
}

/** Parse a stage's Markdown output into its declared sections.
 *
 * Never throws and never returns partial content. On any document it cannot split, the result is
 * `structured: false` with an empty section list, which the caller renders exactly as before.
 */
export function parseAssetDocument(text: string | undefined | null): AssetDocument {
  const source = text ?? "";
  const words = countWords(source);
  const placeholders = findPlaceholders(source);
  const empty: AssetDocument = { sections: [], words, structured: false, pattern: "none", placeholders };

  if (!source.trim()) return empty;

  const lines = source.split("\n");
  let chosen: { name: string; boundaries: Boundary[] } | null = null;
  for (const rule of RULES) {
    const boundaries = scan(lines, rule.match);
    if (boundaries.length >= MIN_SECTIONS) {
      chosen = { name: rule.name, boundaries };
      break;
    }
  }
  if (!chosen) return empty;

  // The topic-suggestions section the reader inserts (see `lib/topicSuggestions.ts`) is a boundary
  // under whichever rule won. Added only after the rule was chosen on the document's own headings,
  // so inserting it can never change how the stage's output is split.
  const rule = RULES.find((r) => r.name === chosen.name)!;
  chosen.boundaries = scan(lines, (line) => (isTopicSuggestionsHeading(line) ? TOPIC_SUGGESTIONS_LABEL : rule.match(line)));

  const sections: DocSection[] = [];

  const push = (label: string, from: number, to: number) => {
    const body = lines.slice(from, to).join("\n");
    if (!body.trim() && !label) return;
    const id = `s${sections.length}-${slug(label)}`;
    const htmlBlocks = splitHtmlBlocks(body).filter((s) => s.kind === "html").length;
    sections.push({
      id,
      label,
      body,
      words: countWords(body),
      kind: kindOf(body, htmlBlocks),
      subheads: findSubheads(body, id),
      htmlBlocks,
    });
  };

  // Anything above the first heading. Real runs put the mode-resolution preamble here, and
  // dropping it would lose the one part of the document that says what the rest assumed.
  const first = chosen.boundaries[0];
  if (lines.slice(0, first.line).join("\n").trim()) push("Overview", 0, first.line);

  chosen.boundaries.forEach((boundary, i) => {
    const next = chosen.boundaries[i + 1];
    push(boundary.label, boundary.line + 1, next ? next.line : lines.length);
  });

  const structured = sections.length >= MIN_SECTIONS && words >= MIN_WORDS_FOR_OUTLINE;
  return { sections, words, structured, pattern: chosen.name, placeholders };
}

/**
 * The section labels visible in a partial document, for the live progress tracker.
 *
 * Separate from `parseAssetDocument` because it runs against text that is still arriving: it needs
 * to be cheap, it must tolerate a heading that is still half-written, and it has no use for bodies.
 * The caller throttles how often it is called (see `StreamProgress`).
 */
export function scanStreamHeadings(text: string): { pattern: string; labels: string[] } {
  if (!text) return { pattern: "none", labels: [] };
  const lines = text.split("\n");
  // The last line is very likely mid-write, so it is not offered as a completed heading.
  const settled = lines.slice(0, Math.max(0, lines.length - 1));

  for (const rule of RULES) {
    const boundaries = scan(settled, rule.match);
    if (boundaries.length >= 1 && rule.name !== "heading") {
      return { pattern: rule.name, labels: boundaries.map((b) => b.label) };
    }
    if (boundaries.length >= MIN_SECTIONS) {
      return { pattern: rule.name, labels: boundaries.map((b) => b.label) };
    }
  }
  return { pattern: "none", labels: [] };
}
