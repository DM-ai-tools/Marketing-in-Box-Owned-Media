/* The designed HTML report, and the recognisers that feed it. Not shipped; see smoke/README.md.
 *
 * The promise is the Visual view's, one level up: the exported file is a re-layout of the document,
 * never a summary of it. So, for every real output in `manual_execution/`:
 *
 *   - the whole report is built exactly as the download builds it, its tags are stripped, and every
 *     word of the source document (topic suggestions and competitor sources included) must be in it;
 *   - how much of the document still falls through to plain Markdown is printed, so a recogniser
 *     that stops matching shows up as a number going the wrong way.
 *
 * Then: each new recogniser fires on the real sample it was written for, and does not fire where it
 * must not.
 */
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { buildAssetReportExport, buildAssetReportHtml, isHtmlOnly } from "../src/lib/assetReportHtml";
import { parseAssetDocument } from "../src/lib/assetDocument";
import { buildAssetExport } from "../src/lib/exportAsset";
import { stripPromptEchoes } from "../src/lib/promptEcho";
import { isFolded, templateFor } from "../src/lib/assetTemplates";
import { parseVisualBlocks, toneOf, type VisualBlock } from "../src/lib/visualBlocks";

let pass = 0;
const fails: string[] = [];
const ok = (name: string, cond: boolean, extra?: string) => {
  if (cond) pass++;
  else fails.push(name + (extra ? `  ->  ${extra}` : ""));
};

const SAMPLES = join(process.cwd(), "..", "..", "manual_execution");
const sample = (name: string) => readFileSync(join(SAMPLES, name), "utf8");
const blocksOf = (text: string): VisualBlock[] => {
  const doc = parseAssetDocument(text);
  return (doc.sections.length ? doc.sections.map((s) => s.body) : [text]).flatMap((b) => parseVisualBlocks(b));
};

// ---------- the word check (same rules as visual.tsx and plan.tsx) ----------
const tokens = (s: string) =>
  s
    .toLowerCase()
    .replace(/[*#|>`_~]/g, " ")
    .split(/\s+/)
    .filter(Boolean)
    .filter((w) => !/^\d{1,2}[.)]$/.test(w) && !/^[a-h][.)]$/.test(w))
    .map((w) => w.replace(/^[^a-z0-9~$]+|[^a-z0-9%)]+$/g, ""))
    .filter((w) => /[a-z0-9]/.test(w));
const tally = (ws: string[]) => {
  const m = new Map<string, number>();
  for (const w of ws) m.set(w, (m.get(w) ?? 0) + 1);
  return m;
};
// A link's address is content too — in the source it is `[text](url)`, in the report an `href`. Both
// sides are read the same way, so a link that survives as a link counts, and one that lost its
// address would not.
const sourceText = (md: string) => md.replace(/\[([^\]]*)\]\(([^)\s]+)\)/g, " $1 $2 ");
const visibleText = (html: string) =>
  html
    .replace(/<style>[\s\S]*?<\/style>/g, " ")
    .replace(/<script>[\s\S]*?<\/script>/g, " ")
    .replace(/<a\s[^>]*href="([^"]*)"[^>]*>/g, " $1 ")
    .replace(/<[^>]+>/g, " ")
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&#x27;/g, "'")
    .replace(/&nbsp;/g, " ");

/* ---------- whole-report containment, over every real output ---------- */
if (!existsSync(SAMPLES)) {
  fails.push(`samples not found at ${SAMPLES}`);
} else {
  const files = readdirSync(SAMPLES).filter((f) => f.endsWith(".md"));
  const lossy: string[] = [];
  const coverage: string[] = [];
  for (const f of files) {
    const text = sample(f);
    const html = await buildAssetReportHtml({ text, label: f.replace(/\.md$/, ""), css: "", date: "30 September 2026" });
    const out = tally(tokens(visibleText(html)));
    const missing = [...tally(tokens(sourceText(text)))].filter(([w, n]) => (out.get(w) ?? 0) < n).map(([w]) => w);
    if (missing.length) lossy.push(`${f}: ${missing.slice(0, 8).join(" ")}`);

    let plain = 0;
    let all = 0;
    for (const b of blocksOf(text)) {
      const n = b.source.split("\n").filter((l) => l.trim()).length;
      all += n;
      if (b.kind === "markdown") plain += n;
    }
    coverage.push(`${String(Math.round((plain / Math.max(all, 1)) * 100)).padStart(3)}% plain  ${f}`);
  }
  ok(`every word of all ${files.length} real outputs is in its HTML report`, !lossy.length, lossy.slice(0, 4).join(" | "));
  console.log("plain-Markdown fall-through per real output:\n" + coverage.sort().reverse().join("\n"));
}

/* ---------- the report file itself ---------- */
{
  const text = sample("PART-1_CRO-Audit-Report_Social-Media-Marketing.md");
  const exported = await buildAssetReportExport({ text, label: "CRO Audit", stageNumber: 2, assetId: "cro", client: "TrafficRadius", css: "", date: "30 September 2026" });
  ok("report: .html file named for the stage", exported.filename === "02-cro-audit-report.html" && exported.mime === "text/html", exported.filename);
  ok("report: a full standalone document", /^<!doctype html>/.test(exported.content) && exported.content.includes('<meta name="viewport"'));
  ok("report: titled with the asset and the client", exported.content.includes("<title>CRO Audit — TrafficRadius</title>"));
  ok("report: hero names the kind of document", exported.content.includes(templateFor("cro").kicker));
  ok("report: a contents list linking each section", /<nav aria-label="Contents"/.test(exported.content) && /href="#/.test(exported.content));
  ok("report: the score glance is drawn", exported.content.includes("At a glance"));
  ok("report: folded sections open before printing", exported.content.includes("beforeprint"));

  const page = "```html\n<!doctype html><html><body>" + "<p>Page copy that runs on.</p>".repeat(10) + "</body></html>\n```";
  ok("html-only document is recognised (it keeps its plain page download)", isHtmlOnly(page) && !isHtmlOnly(text));
  const mixed = await buildAssetReportHtml({ text: `# PART 1 — Notes\n\nSome words.\n\n# PART 2 — Page\n\n${page}\n`, label: "Pillar", css: "" });
  ok("report: an embedded page is a sandboxed frame plus its source", /<iframe[^>]*sandbox=""/.test(mixed) && mixed.includes("Page source (HTML)"));
}

/* ---------- templates ---------- */
{
  ok("template: competitor sources start folded everywhere", isFolded(templateFor("icp"), "Competitors scanned") && isFolded(templateFor(undefined), "Topic suggestions"));
  ok("template: ordinary sections are not folded", !isFolded(templateFor("cro"), "PART 1 — Audit"));
  ok("template: blog and book read straight through", !!templateFor("blog").longForm && !!templateFor("book").longForm && !templateFor("webinar").longForm);
}

/* ---------- the new recognisers, on the real samples ---------- */
{
  const has = (blocks: VisualBlock[], kind: VisualBlock["kind"]) => blocks.some((b) => b.kind === kind);

  const cro = blocksOf(sample("PART-1_CRO-Audit-Report_Social-Media-Marketing.md"));
  ok("CRO: findings are recognised", has(cro, "findings"));
  ok("CRO: Working / Failing lists are recognised", has(cro, "checklist"));

  const funnel = blocksOf(sample("Funnel-Document_Social-Media-Marketing.md"));
  ok("Funnel: **Key:** value rows are a field grid", has(funnel, "fields"));
  ok("Funnel: the ASCII flow is a diagram, untouched", funnel.some((b) => b.kind === "diagram" && b.code.length > 0));

  const icp = blocksOf(sample("ICP-TrafficRadius-ProblemAware-Melbourne-ProfessionalServices.md"));
  ok("ICP: verbatim quote lists are recognised", has(icp, "quotes"));

  const webinar = blocksOf(sample("Webinar-Package_TrafficRadius_LLM-Only.md"));
  ok("Webinar: long prose folds after its first paragraph", webinar.some((b) => b.kind === "prose" && b.restWords >= 80 && b.lead.length > 0));

  // Negative cases.
  const data = parseVisualBlocks("| Name | Notes |\n| --- | --- |\n| A | one |\n| B | two |");
  ok("a plain data table stays a table", data.length === 1 && data[0].kind === "table");
  const seq = parseVisualBlocks("### Day 1 — Welcome\n**Timing:** Day 1\n**Goal:** Hello\n\n### Day 3 — Proof\n**Timing:** Day 3\n**Goal:** Trust");
  ok("**Key:** lines inside a timed sequence stay in the sequence", seq.length === 1 && seq[0].kind === "sequence");
  const plainList = parseVisualBlocks("1. one\n2. two\n3. three");
  ok("a plain numbered list stays Markdown", plainList.every((b) => b.kind === "markdown"));
  const shortProse = parseVisualBlocks("A short paragraph.\n\nAnother short one.");
  ok("short prose is not folded", shortProse.every((b) => b.kind === "markdown"));
  const sentence = parseVisualBlocks("- **The form has no labels on any of its seven inputs, which fails the accessibility check.** It also…\n- **Another long bolded sentence that is plainly not a label of any kind at all here.** More.");
  ok("a bolded opening sentence is not read as a field label", !sentence.some((b) => b.kind === "fields"));

  ok("tone: Working is good, Failing is bad, Fix is a warning", toneOf("Working:") === "good" && toneOf("Failing — five confirmed faults:") === "bad" && toneOf("Fix:") === "warn");
}

/* ---------- prompt echoes never reach a deliverable ---------- */
{
  const echo = "===== BEGIN INDUSTRY_VOICE ===== (applied — not reproduced here) ===== END INDUSTRY_VOICE =====";
  const overview = `# Overview\n\nMode: All Wishes.\n\n${echo}\n\nBusiness model: agency.`;
  const cleaned = stripPromptEchoes(overview);
  ok("echo: the one-line echo seen in a Value Ladder Overview is removed", !cleaned.includes("INDUSTRY_VOICE") && !cleaned.includes("not reproduced"), cleaned);
  ok("echo: the text around it is kept, with no doubled blank line", cleaned === "# Overview\n\nMode: All Wishes.\n\nBusiness model: agency.", JSON.stringify(cleaned));

  const block = "Step 0\n\n===== BEGIN INDUSTRY_VOICE =====\n(applied — not reproduced here)\n===== END INDUSTRY_VOICE =====\n\nNext.";
  ok("echo: a three-line echo is removed whole", stripPromptEchoes(block) === "Step 0\n\nNext.", JSON.stringify(stripPromptEchoes(block)));

  const table = `| Input | Resolved |\n| --- | --- |\n| Tier | 2 |\n| Industry voice | ${echo} |\n| Geo | Local |`;
  const t = stripPromptEchoes(table);
  ok("echo: a table row holding only the echo is dropped, the others kept", !t.includes("Industry voice") && t.includes("| Tier | 2 |") && t.includes("| Geo | Local |"), t);

  const clean = "# Plain\n\nNo prompt plumbing here.";
  ok("echo: a clean document is returned untouched", stripPromptEchoes(clean) === clean);

  const code = "```\n===== BEGIN INDUSTRY_VOICE =====\nx\n===== END INDUSTRY_VOICE =====\n```";
  ok("echo: fenced code is never touched", stripPromptEchoes(code) === code);

  const long = `===== BEGIN INDUSTRY_VOICE =====\n${"Real content the model wrote. ".repeat(12)}\n===== END INDUSTRY_VOICE =====`;
  const l = stripPromptEchoes(long);
  ok("echo: a long block keeps its content and loses only the markers", l.includes("Real content the model wrote.") && !l.includes("====="), l.slice(0, 80));

  const md = buildAssetExport({ text: overview, label: "Offers" });
  ok("echo: the Markdown export is clean", !md.content.includes("INDUSTRY_VOICE"));
  const rep = await buildAssetReportHtml({ text: overview, label: "Offers", css: "" });
  ok("echo: the HTML report is clean", !rep.includes("INDUSTRY_VOICE"));
}

console.log(`report: ${pass} passed, ${fails.length} failed`);
for (const f of fails) console.log(`  FAIL ${f}`);
if (fails.length) process.exit(1);
