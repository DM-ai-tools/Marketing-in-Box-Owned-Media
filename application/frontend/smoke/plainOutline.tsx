/* A plain-text deliverable read as the outline its layout states. Not shipped; see smoke/README.md.
 *
 * `lib/plainOutline.ts` exists because a real Phase 2 ICP (ARG Finance, Property Development
 * Finance) came back as plain text — the prompt's template copied literally, no `#`, no `**` — and
 * the reader showed it as one wall. This runs that exact document through the pass and checks:
 *
 *   - it now splits into the sixteen numbered sections, with the quotes, goals and objections drawn;
 *   - not one word was added, lost or reordered;
 *   - every Markdown output in `manual_execution/` comes back byte-identical (the pass only ever
 *     touches a document with no Markdown at all), and every plain one keeps every word;
 *   - a numbered list on consecutive lines is not mistaken for section headings.
 */
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { parseAssetDocument } from "../src/lib/assetDocument";
import { upgradePlainOutline } from "../src/lib/plainOutline";
import { parseVisualBlocks } from "../src/lib/visualBlocks";

let pass = 0;
const fails: string[] = [];
const ok = (name: string, cond: boolean, extra?: string) => {
  if (cond) pass++;
  else fails.push(name + (extra ? `  ->  ${extra}` : ""));
};

const SAMPLES = join(process.cwd(), "..", "..", "manual_execution");
const PLAIN_ICP = "ICP-ARG-Finance_Property-Development-Finance_Phase2_PlainText.md";
const PHASE1_ICP = "ICP-TrafficRadius-ProblemAware-Melbourne-ProfessionalServices.md";

/** The words, in order, with markup characters removed — so `**Label:**` and `Label:` compare equal. */
const words = (s: string) =>
  s
    .replace(/[*#]/g, " ")
    .split(/\s+/)
    .filter((w) => w && w !== "-");

const sameWords = (a: string, b: string) => words(a).join(" ") === words(b).join(" ");

if (!existsSync(SAMPLES)) {
  fails.push(`samples not found at ${SAMPLES}`);
} else {
  const raw = readFileSync(join(SAMPLES, PLAIN_ICP), "utf8");
  ok("the raw plain ICP has no structure (the bug)", !parseAssetDocument(raw).structured);

  const up = upgradePlainOutline(raw);
  const doc = parseAssetDocument(up);
  const labels = doc.sections.map((s) => s.label);
  ok("upgraded: structured", doc.structured, doc.pattern);
  ok("upgraded: sixteen numbered sections after the overview", labels.length === 17 && labels[0] === "Overview", labels.join(" | "));
  ok("upgraded: section 12 keeps its full title", labels.includes("12. Objections (Top 8) + What It Really Means"));
  ok("upgraded: the title is a heading", up.startsWith("# ICP (Solution-Aware) — ARG Finance"));
  ok("upgraded: the avatar is a labelled row", up.includes('**Avatar Name:** Marco Delic — "The Second-Site Developer"'));
  ok("upgraded: every word kept, in order", sameWords(raw, up));

  const blocksIn = (label: string) => parseVisualBlocks(doc.sections.find((s) => s.label.startsWith(label))?.body ?? "");
  ok("upgraded: what they say out loud is quote cards", blocksIn("3.").some((b) => b.kind === "quotes"));
  const goals = blocksIn("5.").find((b) => b.kind === "fields");
  ok("upgraded: the three goals are field rows", !!goals && goals.kind === "fields" && goals.rows.length === 3);
  ok("upgraded: the objections are a list of eight", (doc.sections.find((s) => s.label.startsWith("12."))?.body.match(/^- "/gm) ?? []).length === 8);
  ok("upgraded: the checklist is untouched", doc.sections.find((s) => s.label.startsWith("16."))!.body.trim() === raw.slice(raw.indexOf("- Does the applicant hold")).trim());

  ok("idempotent", upgradePlainOutline(up) === up);
  ok("the Markdown Phase 1 ICP is untouched", upgradePlainOutline(readFileSync(join(SAMPLES, PHASE1_ICP), "utf8")) === readFileSync(join(SAMPLES, PHASE1_ICP), "utf8"));

  const changed: string[] = [];
  const lossy: string[] = [];
  for (const f of readdirSync(SAMPLES).filter((n) => n.endsWith(".md"))) {
    const text = readFileSync(join(SAMPLES, f), "utf8");
    const out = upgradePlainOutline(text);
    if (out !== text) changed.push(f);
    if (!sameWords(text, out)) lossy.push(f);
  }
  ok("corpus: only the plain-text sample is rewritten", changed.length === 1 && changed[0] === PLAIN_ICP, changed.join(", "));
  ok("corpus: no document loses a word", !lossy.length, lossy.join(", "));
}

const list = "Steps\n\n1. Hook the reader\n2. Show the proof\n3. Ask for the call\n4. Follow up";
ok("a consecutive numbered list is not an outline", upgradePlainOutline(list) === list);
const sentences = "Intro\n\n1. We start here.\n\nBody\n\n2. Then this happens.\n\nMore\n\n3. And it ends.";
ok("numbered sentences are not section titles", upgradePlainOutline(sentences) === sentences);

console.log(`plainOutline: ${pass} passed, ${fails.length} failed`);
for (const f of fails) console.log(`  FAIL ${f}`);
if (fails.length) process.exitCode = 1;
