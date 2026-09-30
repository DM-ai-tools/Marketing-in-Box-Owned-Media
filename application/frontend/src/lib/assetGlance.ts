/** "At a glance": the one picture that summarises an asset, drawn from its own visual blocks.
 *
 * Derived and additive. Everything a glance shows is also in the section it came from, and every
 * glance item names that section so the reader can jump to it. A glance never replaces text; it is
 * the answer to "what is in here?" before the operator starts scrolling.
 *
 * Which picture an asset gets is its own preference, falling back through the rest: a CRO audit is
 * its score, an offer ladder its rungs, a lead magnet its winning concept.
 */

import type { AssetDocument } from "./assetDocument";
import { plainCell, parseVisualBlocks, scoreOf, type ShowSlot, type VisualBlock } from "./visualBlocks";

export type Glance =
  | { kind: "score"; sectionId: string; value: number; max: number; label: string; bars: { label: string; value: number; max: number }[] }
  | { kind: "ladder"; rungs: { label: string; sectionId: string; count: number; prices: string[] }[] }
  | { kind: "flow"; sectionId: string; steps: string[] }
  | { kind: "winner"; sectionId: string; name: string; value: number; max: number; of: number }
  | { kind: "sequence"; sectionId: string; steps: { timing: string; label: string }[] }
  | { kind: "show"; sectionId: string; slots: ShowSlot[] }
  | { kind: "plan" };

type Kind = Glance["kind"];

const PREFERENCE: Record<string, Kind[]> = {
  cro: ["score"],
  offers: ["ladder"],
  funnel: ["flow", "sequence"],
  funnel_hub_media: ["flow"],
  lead_magnet: ["winner"],
  sms_sequence: ["sequence"],
  webinar: ["show", "sequence"],
  podcast: ["show", "sequence"],
  plan_of_action: ["plan"],
};
const FALLBACK: Kind[] = ["score", "winner", "ladder", "flow", "sequence", "show"];

interface Located {
  sectionId: string;
  label: string;
  block: VisualBlock;
}

function located(doc: AssetDocument): Located[] {
  return doc.sections.flatMap((s) => parseVisualBlocks(s.body).map((block) => ({ sectionId: s.id, label: s.label, block })));
}

function scoreGlance(all: Located[]): Glance | null {
  const gauge = all.find((l) => l.block.kind === "gauge");
  const table = all.find((l) => l.block.kind === "table" && l.block.visual === "score" && l.block.totalColumn === null);
  if (!gauge && !table) return null;
  const bars: { label: string; value: number; max: number }[] = [];
  if (table && table.block.kind === "table") {
    const col = table.block.scoreColumns[0];
    for (const r of table.block.table.rows) {
      const s = scoreOf(r[col] ?? "", table.block.columnMax[col]);
      if (s && (s.type === "ratio" || s.type === "stars")) bars.push({ label: plainCell(r[0] ?? ""), value: s.value, max: s.max });
    }
  }
  if (gauge && gauge.block.kind === "gauge") {
    return { kind: "score", sectionId: gauge.sectionId, value: gauge.block.value, max: gauge.block.max, label: gauge.block.label, bars };
  }
  // No overall score written: only a layer table, which still makes a chart — but not a gauge
  // claiming a total the document never stated.
  return bars.length >= 2 && table ? { kind: "score", sectionId: table.sectionId, value: NaN, max: NaN, label: "", bars } : null;
}

/** The headline figure of a price line: "$297", "$2,800/month", "Free" — the rest of the sentence is
 * on the offer's card, not lost. */
const HEADLINE_PRICE = /\bfree\b|(?:[A-Z]{1,3})?\$\s?\d(?:[\d,]*\d)?(?:\.\d+)?\s?k?(?:\s?\/\s?(?:mo|month|seat|year|yr|hr|hour|week))?/i;

function ladderGlance(all: Located[]): Glance | null {
  // One rung per section: a wish can hold more than one run of offers, split by a note between them.
  const bySection = new Map<string, { label: string; sectionId: string; count: number; prices: string[] }>();
  for (const l of all) {
    if (l.block.kind !== "offers") continue;
    const rung = bySection.get(l.sectionId) ?? { label: l.label, sectionId: l.sectionId, count: 0, prices: [] };
    rung.count += l.block.offers.length;
    for (const o of l.block.offers) {
      const price = o.fields.find((f) => /^price$/i.test(f.key))?.value ?? "";
      const headline = HEADLINE_PRICE.exec(price)?.[0];
      if (headline) rung.prices.push(/^free$/i.test(headline) ? "Free" : headline.replace(/\s+/g, ""));
    }
    bySection.set(l.sectionId, rung);
  }
  const rungs = [...bySection.values()];
  return rungs.length ? { kind: "ladder", rungs } : null;
}

function flowGlance(all: Located[]): Glance | null {
  const flow = all.find((l) => l.block.kind === "table" && l.block.visual === "flow");
  if (!flow || flow.block.kind !== "table") return null;
  return { kind: "flow", sectionId: flow.sectionId, steps: flow.block.table.rows.map((r) => plainCell(r[0] ?? "")) };
}

function winnerGlance(all: Located[]): Glance | null {
  const t = all.find((l) => l.block.kind === "table" && l.block.visual === "score" && l.block.totalColumn !== null);
  if (!t || t.block.kind !== "table" || t.block.totalColumn === null) return null;
  const col = t.block.totalColumn;
  let best: { name: string; value: number; max: number } | null = null;
  for (const r of t.block.table.rows) {
    const s = scoreOf(r[col] ?? "", t.block.columnMax[col]);
    if (s && s.type === "ratio" && (!best || s.value > best.value)) best = { name: plainCell(r[0] ?? ""), value: s.value, max: s.max };
  }
  return best ? { kind: "winner", sectionId: t.sectionId, ...best, of: t.block.table.rows.length } : null;
}

function sequenceGlance(all: Located[]): Glance | null {
  const seq = all.find((l) => l.block.kind === "sequence");
  if (seq && seq.block.kind === "sequence") {
    return { kind: "sequence", sectionId: seq.sectionId, steps: seq.block.steps.map((s) => ({ timing: s.timing, label: plainCell(s.heading) })) };
  }
  const t = all.find((l) => l.block.kind === "table" && l.block.visual === "timeline");
  if (!t || t.block.kind !== "table") return null;
  const key = t.block.keyColumn;
  const title = key === 0 ? 1 : 0;
  return {
    kind: "sequence",
    sectionId: t.sectionId,
    steps: t.block.table.rows.map((r) => ({ timing: plainCell(r[key] ?? ""), label: plainCell(r[title] ?? "") })),
  };
}

function showGlance(all: Located[]): Glance | null {
  const show = all.find((l) => l.block.kind === "runOfShow");
  return show && show.block.kind === "runOfShow" ? { kind: "show", sectionId: show.sectionId, slots: show.block.slots } : null;
}

const BUILDERS: Record<Exclude<Kind, "plan">, (all: Located[]) => Glance | null> = {
  score: scoreGlance,
  ladder: ladderGlance,
  flow: flowGlance,
  winner: winnerGlance,
  sequence: sequenceGlance,
  show: showGlance,
};

/** The glance for this asset, or null when its document has nothing to draw one from. */
export function assetGlance(assetId: string | undefined, doc: AssetDocument): Glance | null {
  const preferred = (assetId && PREFERENCE[assetId]) || [];
  if (preferred[0] === "plan") return { kind: "plan" };
  const all = located(doc);
  for (const kind of [...preferred, ...FALLBACK.filter((k) => !preferred.includes(k))]) {
    if (kind === "plan") continue;
    const glance = BUILDERS[kind](all);
    if (glance) return glance;
  }
  return null;
}
