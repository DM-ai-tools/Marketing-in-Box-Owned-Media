import type { PlanNode, PlanNodeKind } from "./planTree";

/** Short tag per kind. Upper-case and monospace, so it reads as a label rather than a word. */
export const KIND_TAG: Record<PlanNodeKind, string> = {
  root: "PLAN",
  phase: "PHASE",
  summary: "SUMMARY",
  timeline: "TIMELINE",
  table: "TABLE",
  "sub-service": "SERVICE",
  category: "GROUP",
  page: "PAGE",
  blog: "BLOG",
  "lead-magnet": "MAGNET",
  funnel: "FUNNEL",
  email: "EMAIL",
  sms: "SMS",
  "case-study": "CASE",
  video: "VIDEO",
  industry: "INDUSTRY",
  location: "LOCATION",
  keyword: "KEYWORD",
  icp: "ICP",
  offer: "OFFER",
  webinar: "WEBINAR",
  book: "BOOK",
  podcast: "PODCAST",
  strategy: "STRATEGY",
  detail: "",
};

/**
 * What job a node's asset does, which is what the map's colour encodes.
 *
 * Five families rather than one hue per kind: the tag already names the exact kind, so colour only
 * has to answer the coarser question an operator scanning a phase actually asks ("where is the
 * conversion work?"), and five hues stay learnable. Tokens live in `index.css` (`--plan-*`), with
 * their dark-mode values beside them.
 */
export type PlanFamily = "structure" | "content" | "convert" | "nurture" | "target" | "strategy";

const KIND_FAMILY: Record<PlanNodeKind, PlanFamily> = {
  root: "structure",
  phase: "structure",
  summary: "strategy",
  timeline: "strategy",
  table: "strategy",
  detail: "strategy",
  category: "strategy",
  "sub-service": "target",
  industry: "target",
  location: "target",
  keyword: "target",
  page: "content",
  blog: "content",
  video: "content",
  "case-study": "content",
  "lead-magnet": "convert",
  funnel: "convert",
  email: "nurture",
  sms: "nurture",
  icp: "target",
  offer: "convert",
  webinar: "convert",
  book: "content",
  podcast: "content",
  strategy: "strategy",
};

export const FAMILY_LABEL: Record<PlanFamily, string> = {
  structure: "Plan & phases",
  content: "Content",
  convert: "Conversion",
  nurture: "Nurture",
  target: "Targeting",
  strategy: "Strategy & detail",
};

/** Legend order: the funnel, top to bottom. */
export const FAMILY_ORDER: PlanFamily[] = ["structure", "target", "content", "convert", "nurture", "strategy"];

export function familyOf(kind: PlanNodeKind): PlanFamily {
  return KIND_FAMILY[kind];
}

export function familyColor(family: PlanFamily): string {
  return `var(--plan-${family})`;
}

/**
 * A plain-text excerpt of a node's own markdown, for the card's inline detail.
 *
 * The side panel renders the body verbatim; this is a preview, so tables and fences are skipped
 * (a table row squashed into a sentence reads as noise) and the cut lands on a word boundary.
 */
export function plainExcerpt(node: PlanNode, max = 220): string {
  const text = node.body
    .replace(/^\s{0,3}(`{3,}|~{3,})[\s\S]*?^\s{0,3}\1\s*$/gm, " ")
    .split("\n")
    .filter((line) => !/^\s*\|/.test(line) && !/^\s*[-=:|\s]+$/.test(line))
    .map((line) =>
      line
        .replace(/^\s{0,3}#{1,6}\s*/, "")
        .replace(/^\s*(?:[-*•+]|\d+[.)])\s+/, "")
        .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
        .replace(/[*_`]+/g, ""),
    )
    .join(" ")
    .replace(/\s+/g, " ")
    .trim();
  if (text.length <= max) return text;
  const cut = text.slice(0, max);
  const space = cut.lastIndexOf(" ");
  return `${(space > max * 0.6 ? cut.slice(0, space) : cut).replace(/[,;:.\s]+$/, "")}…`;
}
