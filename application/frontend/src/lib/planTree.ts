/** The Plan of Action, as a tree.
 *
 * Why parse rather than ask the model for a diagram
 * -------------------------------------------------
 * `Plan-of-Action-Architect-Prompt.md` already offers an "OPTIONAL PART 2 — TREE-VIEW HTML
 * VISUALISATION" — a self-contained interactive HTML file it will build *if requested*. Using that
 * would mean the diagram is regenerated from scratch on every run: a different layout each time, a
 * few thousand output tokens each time, and no guarantee the markup is even valid, on the one asset
 * whose whole point is to be navigable. So the plan's *text* stays the deliverable and the diagram
 * is derived from it here — same document, same tree, every time, at no token cost.
 *
 * What the plan's shape actually is
 * ---------------------------------
 * Its declared output format is ten numbered top-level sections (Executive Summary, Current State,
 * Phase 1, Phase 2, Phase 3, Total Asset Summary, Funnel Overview, Lead Magnet Master List,
 * Sub-Service Priority Ranking, Monthly Execution Timeline). Underneath, three shapes recur:
 *
 *   * **Phase 1** lists eight numbered assets.
 *   * **Phase 2** repeats a fixed per-sub-service schema, and its heading is `## [N]. [Name]` —
 *     the same `N.` form the top-level sections use. That collision is the reason this module
 *     cannot simply split on numbers: it has to read *relative* depth.
 *   * **Phase 3** uses lettered categories, `A.` through `H.`, plus a funnels group.
 *
 * How depth is decided
 * --------------------
 * Every heading-like line is given a raw level, and the raw levels are then *normalised* — the
 * distinct levels a document actually used are mapped onto 1, 2, 3… in order. A run that writes
 * `#` then `###` with no `##` in between nests identically to one that writes `#` then `##`, which
 * matters because these documents are generated and their heading discipline varies.
 *
 * Nothing is dropped. Every line of the plan lands in some node's `body`, and the caller can always
 * fall back to the plain document — see `PlanMindMap`, where that is a visible switch rather than
 * an error path.
 */

export type PlanNodeKind =
  | "root"
  | "phase"
  | "summary"
  | "timeline"
  | "table"
  | "sub-service"
  | "category"
  | "page"
  | "blog"
  | "lead-magnet"
  | "funnel"
  | "email"
  | "sms"
  | "case-study"
  | "video"
  | "industry"
  | "location"
  | "keyword"
  | "icp"
  | "offer"
  | "webinar"
  | "book"
  | "podcast"
  | "strategy"
  | "detail";

export interface PlanNode {
  id: string;
  title: string;
  kind: PlanNodeKind;
  /** 0 for the root. */
  depth: number;
  /** The verbatim markdown belonging to this node, excluding its children's. */
  body: string;
  children: PlanNode[];
  /** Star rating out of five, when the plan states one for this item. */
  stars?: number;
  /** Monthly search volume, when stated (`~1,200/mo`). */
  volume?: number;
  /** A URL slug, when stated (`Proposed URL: /x`). */
  slug?: string;
  /** Total descendants, for the node's own badge and for layout decisions. */
  count: number;
}

export interface PlanTree {
  root: PlanNode;
  /** False when the text yielded nothing worth drawing — the caller shows the document instead. */
  available: boolean;
  reason?: string;
  /** Headline figures for the diagram's header strip. */
  totals: { nodes: number; phases: number; assets: number; months?: number };
}

/* ---------------------------------------------------------------------------------------------
 * Kinds
 *
 * Matched on the heading text, most specific first. These are the asset vocabulary the prompt
 * itself uses for its colour-coded tags ("blog, lead magnet, funnel, email, SMS, page, case study,
 * video, industry, location"), so a tag here means the same thing it means in the document.
 * ------------------------------------------------------------------------------------------- */
const KIND_RULES: [PlanNodeKind, RegExp][] = [
  // "Phase 2: Sub-Service Expansion" is a phase. "Phase 1 Funnel Flow" and "Phase 3 Funnels" are
  // funnels that happen to name their phase, so the lookahead lets them fall through — otherwise
  // they counted towards the phase total and the header strip claimed five phases out of three.
  ["phase", /^\s*(?:\d+\.\s*)?phase\s*\d\b(?![^\n]*\bfunnels?\b)/i],
  ["timeline", /\b(?:execution\s+timeline|month-by-month|monthly\s+execution|timeline)\b/i],
  ["summary", /\b(?:executive\s+summary|current\s+state|total\s+asset\s+summary|overview)\b/i],
  // The pipeline's own stage names, which the plan lists by name in Phase 1 and Phase 2
  // (`Plan-of-Action-Architect-Prompt.md` Steps 2-3). Book before webinar: "Book (from Webinar)".
  ["icp", /\bICP\b|\bideal\s+customer\s+profile\b/i],
  ["offer", /\bvalue\s+ladder\b/i],
  ["book", /^\s*(?:\d+\.\s*)?book\b/i],
  ["webinar", /\bwebinars?\b/i],
  ["podcast", /\bpodcasts?\b/i],
  ["strategy", /\bcontent\s+marketing\s+strategy\b|\bsocial\s+content\s+strategy\s+audit\b/i],
  ["lead-magnet", /\blead\s*magnets?\b/i],
  // Not `top-of-funnel` / `bottom-of-funnel` / `funnel stage`, which describe where an asset sits
  // rather than naming a funnel — "D. Educational / Top-of-Funnel Blogs" is a blog category.
  ["funnel", /(?<!\b(?:top|middle|mid|bottom)[- ]of[- ])\bfunnels?\b(?!\s+stage)/i],
  ["email", /\b(?:email\s*sequence|nurture\s*sequence|welcome\s*sequence|newsletter)\b/i],
  ["sms", /\b(?:sms|text\s*sequence)\b/i],
  ["case-study", /\bcase\s*stud(?:y|ies)\b/i],
  ["video", /\bvideo\b/i],
  ["industry", /\bindustry(?:-specific)?\b/i],
  ["location", /\blocation(?:-based)?\b/i],
  ["blog", /\bblogs?\b|\bblog\s*post\b/i],
  ["keyword", /\b(?:keyword|search\s*volume|seo)\b/i],
  ["page", /\b(?:pillar\s*page|landing\s*page|page)\b/i],
  ["table", /\b(?:ranking|master\s*list|roll-?up)\b/i],
];

function kindOf(title: string, depth: number): PlanNodeKind {
  for (const [kind, re] of KIND_RULES) if (re.test(title)) return kind;
  if (depth === 1) return "summary";
  if (depth === 2) return "sub-service";
  return "detail";
}

/* ---------------------------------------------------------------------------------------------
 * Heading detection
 * ------------------------------------------------------------------------------------------- */

interface RawHeading {
  line: number;
  title: string;
  /** Pre-normalisation. Smaller is shallower. */
  level: number;
  /** The leading `N.` of the heading, when it has one. Used to resolve the numbering collision —
   * see `demoteNumberRestarts`. */
  num: number | null;
  /** Text that sat on the heading's own line after a `LABEL:` — the label's *value*.
   *
   * Kept because dropping it loses data. `TARGET KEYWORD CLUSTER: single tooth implant (~880/mo),
   * one tooth implant cost (~590/mo)` puts the first keywords on the label line and the rest on
   * the next, so taking the label as the title and only the *following* lines as the body threw
   * away the first half of every keyword cluster, content brief and funnel flow in the plan. */
  inline?: string;
  /** Filled in by normalisation, then corrected to the node's real position in the tree. */
  depth: number;
}

function leadingNumber(line: string): number | null {
  const match = /^\s{0,3}(?:#{1,6}\s*)?(?:\*\*)?(\d{1,2})[.)]\s/.exec(line);
  if (!match) return null;
  const value = Number(match[1]);
  return Number.isFinite(value) ? value : null;
}

/**
 * Resolve the plan's numbering collision.
 *
 * The declared output format numbers its ten top-level sections `1.` to `10.`. Phase 2's
 * per-sub-service schema *also* writes `## [N]. [Sub-Service Name]`, restarting at 1 — so the
 * document contains `… 3. Phase 1, 4. Phase 2, 1. Single-Tooth, 2. Full-Arch, 5. Phase 3 …` at one
 * heading level, and splitting on heading level alone puts the sub-services beside Phase 2 rather
 * than inside it.
 *
 * The tell is that the outer sequence only ever ascends. A number that does not continue it is the
 * start of a nested run, and the run ends when the outer sequence resumes at exactly where it left
 * off. Requiring `last + 1` to resume, rather than merely a larger number, is what stops a long
 * nested run (sub-services 1..9 under an outer 4) from promoting itself back out half way through.
 */
function demoteNumberRestarts(headings: RawHeading[]): void {
  const numbered = headings.filter((h) => h.num !== null);
  if (!numbered.length) return;

  const target = Math.min(...numbered.map((h) => h.depth));
  let last = 0;
  let inRestart = false;

  for (const heading of headings) {
    if (heading.depth !== target || heading.num === null) continue;
    const continues = inRestart ? heading.num === last + 1 : heading.num > last;
    if (continues) {
      last = heading.num;
      inRestart = false;
    } else {
      heading.depth = target + 1;
      inRestart = true;
    }
  }
}

const STAR_FILLED = /[★✦]/g;
const STARS_TEXT = /(\d(?:\.\d)?)\s*(?:\/\s*5)?\s*stars?\b/i;
const VOLUME = /~?\s*([\d,]{2,})\s*(?:\/\s*mo|per\s+month|monthly|searches)/i;
const SLUG = /(?:proposed\s+url|url\s+slug|slug)\s*[:—-]\s*(\/[\w\-/]*)/i;

/** Strip markdown decoration and any trailing count/volume noise from a heading. */
function cleanTitle(raw: string): string {
  return raw
    .replace(/^[\s>]*#+\s*/, "")
    .replace(/\*\*/g, "")
    .replace(/^\s*[-*•]\s*/, "")
    .replace(/[:：]\s*$/, "")
    .replace(/\s+/g, " ")
    .trim();
}

/**
 * Every heading-like line, with a raw level.
 *
 * The levels are deliberately spread out (10, 20, 30…) so a later rule can be slotted between two
 * existing ones without renumbering, and because only their *order* survives normalisation.
 */
function scanHeadings(lines: string[]): RawHeading[] {
  const out: RawHeading[] = [];
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
    // A table row is content, and its first cell often looks like a label.
    if (/^\s{0,3}\|/.test(line)) continue;

    const md = /^\s{0,3}(#{1,6})\s+(\S.*?)\s*$/.exec(line);
    if (md) {
      out.push({ line: i, num: leadingNumber(line), depth: 0, title: cleanTitle(md[2]), level: md[1].length * 10 });
      continue;
    }

    // `A. Industry-Specific Landing Pages` — Phase 3's lettered categories. Anchored and capped at
    // one letter so a sentence beginning "A." cannot match.
    const letter = /^\s{0,3}(?:\*\*)?([A-H])[.)]\s+(\S[^\n]{2,90}?)(?:\*\*)?\s*$/.exec(line);
    if (letter) {
      out.push({ line: i, num: leadingNumber(line), depth: 0, title: `${letter[1]}. ${cleanTitle(letter[2])}`, level: 75 });
      continue;
    }

    // `3. Lead Magnet (one, matched to …)` — Phase 1's numbered asset list, and Phase 2's
    // sub-service headings when the model writes them without a `#`. Requires the line to be a
    // whole short title rather than a sentence: no terminal full stop, and under 110 characters.
    const numbered = /^\s{0,3}(?:\*\*)?(\d{1,2})[.)]\s+(\S[^\n]{2,110}?)(?:\*\*)?\s*$/.exec(line);
    if (numbered && !/[.!?]$/.test(numbered[2].trim())) {
      out.push({ line: i, num: leadingNumber(line), depth: 0, title: cleanTitle(numbered[2]), level: 80 });
      continue;
    }

    // `TARGET KEYWORD CLUSTER: …` — the fixed labels inside a sub-service brief. Upper-case, so a
    // sentence cannot match, and the value is kept with the label rather than becoming a child.
    const label = /^\s{0,3}(?:\*\*)?([A-Z][A-Z0-9 &/'-]{3,40})\s*[:：]\s*(.*)$/.exec(line);
    if (label) {
      out.push({ line: i, num: leadingNumber(line), depth: 0, title: cleanTitle(label[1]), level: 90, inline: label[2]?.trim() || undefined });
      continue;
    }
  }
  return out;
}

/** Map the raw levels a document actually used onto 1, 2, 3… in order. */
function normaliseLevels(headings: RawHeading[]): Map<number, number> {
  const used = [...new Set(headings.map((h) => h.level))].sort((a, b) => a - b);
  return new Map(used.map((level, i) => [level, i + 1]));
}

/* ---------------------------------------------------------------------------------------------
 * Metrics
 * ------------------------------------------------------------------------------------------- */

function starsIn(text: string): number | undefined {
  const filled = text.match(STAR_FILLED);
  if (filled && filled.length >= 1 && filled.length <= 5) return filled.length;
  const stated = STARS_TEXT.exec(text);
  if (stated) {
    const value = Math.round(Number(stated[1]));
    if (value >= 1 && value <= 5) return value;
  }
  return undefined;
}

function volumeIn(text: string): number | undefined {
  const match = VOLUME.exec(text);
  if (!match) return undefined;
  const value = Number(match[1].replace(/,/g, ""));
  return Number.isFinite(value) && value > 0 ? value : undefined;
}

/** Months in the plan's timeline.
 *
 * Counted from the timeline's own `Month N` headings first, and only then from a stated
 * "12-month" figure. The other order read the Executive Summary's plan span and reported it as
 * the timeline length, which are different numbers whenever the timeline is written out in
 * fewer months than the plan nominally covers.
 */
function monthsIn(text: string): number | undefined {
  const monthHeadings = text.match(/^\s{0,3}#{0,4}\s*Month\s+\d{1,2}\b/gim);
  if (monthHeadings && monthHeadings.length >= 2) return monthHeadings.length;
  const explicit = /\b(\d{1,2})[-\s]?month\b/i.exec(text);
  if (explicit) {
    const value = Number(explicit[1]);
    if (value >= 1 && value <= 36) return value;
  }
  return monthHeadings?.length || undefined;
}

/* ---------------------------------------------------------------------------------------------
 * Build
 * ------------------------------------------------------------------------------------------- */

const ASSET_KINDS = new Set<PlanNodeKind>([
  "page",
  "blog",
  "lead-magnet",
  "funnel",
  "email",
  "sms",
  "case-study",
  "video",
  "industry",
  "location",
]);

/** Below this many nodes there is no tree worth drawing and the document reads better. */
const NL = String.fromCharCode(10);

const MIN_NODES = 4;

export function buildPlanTree(text: string | undefined | null, planTitle = "Plan of Action"): PlanTree {
  const source = text ?? "";
  const empty: PlanTree = {
    root: { id: "root", title: planTitle, kind: "root", depth: 0, body: "", children: [], count: 0 },
    available: false,
    totals: { nodes: 0, phases: 0, assets: 0 },
  };
  if (!source.trim()) return { ...empty, reason: "the plan is empty." };

  const lines = source.split("\n");
  const headings = scanHeadings(lines);
  if (headings.length < MIN_NODES) {
    return {
      ...empty,
      reason: `only ${headings.length} heading(s) were found, which is not a plan structure this can draw.`,
    };
  }

  const levelOf = normaliseLevels(headings);
  for (const heading of headings) heading.depth = levelOf.get(heading.level) ?? 1;
  demoteNumberRestarts(headings);
  const root: PlanNode = {
    id: "root",
    title: planTitle,
    kind: "root",
    depth: 0,
    body: lines.slice(0, headings[0].line).join("\n"),
    children: [],
    count: 0,
  };

  // Standard outline build: a stack holding the open ancestor at each depth.
  const stack: PlanNode[] = [root];

  headings.forEach((heading, index) => {
    const depth = heading.depth;
    const next = headings[index + 1];
    const following = lines.slice(heading.line + 1, next ? next.line : lines.length).join(NL);
    // A label's own value leads its body, so `LABEL: value` keeps `value`.
    const body = heading.inline ? [heading.inline, following].join(NL) : following;

    const node: PlanNode = {
      id: `n${index}`,
      title: heading.title || `Section ${index + 1}`,
      kind: kindOf(heading.title, depth),
      depth,
      body,
      children: [],
      count: 0,
      stars: starsIn(`${heading.title}\n${body.slice(0, 600)}`),
      volume: volumeIn(`${heading.title}\n${body.slice(0, 600)}`),
      slug: SLUG.exec(body.slice(0, 600))?.[1],
    };

    // Pop until the stack top is this node's parent. `depth` is 1-based, and `stack[0]` is the
    // root, so the parent lives at index `depth - 1`.
    while (stack.length > depth) stack.pop();
    while (stack.length < depth) stack.push(stack[stack.length - 1]);
    stack[stack.length - 1].children.push(node);
    stack.push(node);
  });

  /* A plan that opens with its own `# Owned Media Plan of Action — Client` title puts every
   * section one level deeper than a plan that does not, which would draw the whole diagram as a
   * single branch off a redundant node. When the root has exactly one child and that child has
   * children of its own, the child *is* the document title: adopt its children and keep the
   * caller's shorter title for the root. */
  while (root.children.length === 1 && root.children[0].children.length > 0) {
    const only = root.children[0];
    /* The wrapper's own title is kept in the root's body rather than discarded. It is the only
     * place the plan states its own name ("Owned Media Plan of Action — Client"), and the root
     * node shows the caller's shorter label instead, so without this the document title was the
     * one line of the plan that reached no node at all. */
    root.body = [only.title, root.body, only.body].filter((b) => b.trim()).join(NL);
    root.children = only.children;
  }

  /* Depth is re-derived from the tree rather than trusted from the normalised heading level.
   * The two disagree whenever a document skips a level — Phase 1's numbered assets sit at
   * normalised level 5 while their parent phase is at 2, because the level table is global and
   * they are the only headings of their kind. The diagram lays nodes out in columns by depth, so
   * Kind is re-derived with it, because `kindOf` falls back to the depth when the title matches
   * no rule — a sub-service named "Single-Tooth Implants" is identified by *being at depth 2*,
   * and computing that before the depth was corrected labelled it a generic detail.
   * a node's depth has to mean its distance from the root and nothing else. */
  const setDepth = (node: PlanNode, depth: number) => {
    node.depth = depth;
    if (node.kind !== "root") node.kind = kindOf(node.title, depth);
    node.children.forEach((child) => setDepth(child, depth + 1));
  };
  setDepth(root, 0);

  // Descendant counts, bottom-up.
  const countUp = (node: PlanNode): number => {
    node.count = node.children.reduce((sum, child) => sum + 1 + countUp(child), 0);
    return node.count;
  };
  countUp(root);

  const all: PlanNode[] = [];
  const walk = (node: PlanNode) => {
    all.push(node);
    node.children.forEach(walk);
  };
  walk(root);

  return {
    root,
    available: true,
    totals: {
      nodes: all.length - 1,
      phases: all.filter((n) => n.kind === "phase").length,
      assets: all.filter((n) => ASSET_KINDS.has(n.kind)).length,
      months: monthsIn(source),
    },
  };
}

/** Depth-first list of every node, for search and for the standalone export. */
export function flattenPlan(root: PlanNode): PlanNode[] {
  const out: PlanNode[] = [];
  const walk = (node: PlanNode) => {
    out.push(node);
    node.children.forEach(walk);
  };
  walk(root);
  return out;
}
