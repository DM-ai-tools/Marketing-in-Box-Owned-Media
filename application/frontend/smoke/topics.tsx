/* The "Topic suggestions" section at the top of an exported asset.
 *
 * Pure — no rendering. Asserts which headline gate a generation is paired with, what the section
 * says about each topic, and that it lands in `.md` exports only. */
import { buildAssetExport } from "../src/lib/exportAsset";
import { renderTopicSuggestions, topicGatesFor, topicPreambleFor, withTopicSuggestions } from "../src/lib/topicSuggestions";
import { parseAssetDocument } from "../src/lib/assetDocument";
import type { HeadlineCandidate } from "../src/pipeline/pipelineApi";
import type { PipelineMessage } from "../src/pipeline/pipelineStore";

let pass = 0;
const fails: string[] = [];
const ok = (name: string, cond: boolean, extra?: string) => {
  if (cond) pass++;
  else fails.push(name + (extra ? `  ->  ${extra}` : ""));
};

const candidate = (id: string, headline: string, over: Partial<HeadlineCandidate> = {}): HeadlineCandidate => ({
  id,
  headline,
  primary_keyword: `${id} keyword`,
  source_cluster: "Social media for e-commerce",
  intent: "informational",
  funnel: "TOFU",
  traffic_temperature: "cold",
  framework_formula: "Number + Outcome + Timeframe",
  curiosity_elements: ["specific number"],
  specificity: "Names the platform and the result",
  why_it_works: `Reason for ${id}.`,
  trend_evidence: null,
  char_count: headline.length,
  channel_limit_ok: true,
  checklist_pass: true,
  checklist_notes: "",
  search_volume: 1900,
  difficulty: 30,
  grounded: true,
  extras: {},
  ...over,
});

const gate = (id: string, chosenIds: string[], candidates: HeadlineCandidate[], over: Partial<PipelineMessage> = {}): PipelineMessage =>
  ({
    id,
    role: "assistant",
    kind: "headline-choice",
    assetId: "blog",
    phase: "phase1",
    field: { field_id: "blog_topic_working_title", label: "Blog Topic" },
    headlines: {
      slot: "blog_topic",
      status: "chosen",
      label: "Blog topics",
      subject: "blog post",
      serviceAnchor: "Social Media Marketing",
      groundedInKeywords: true,
      candidates,
      chosenIds,
    },
    ...over,
  }) as PipelineMessage;

const generation = (id: string, over: Partial<PipelineMessage> = {}): PipelineMessage =>
  ({ id, role: "assistant", kind: "generation", assetId: "blog", phase: "phase1", text: "# Blog post\n\nBody.", ...over }) as PipelineMessage;

const blogGate = gate("g1", ["c2", "c3"], [
  candidate("c1", "Seven Instagram Mistakes Costing Stores Sales"),
  candidate("c2", "How To Double Shopify Sales From TikTok In 90 Days", { funnel: "MOFU", intent: "commercial" }),
  candidate("c3", "The E-commerce Social Calendar That Actually Converts", { grounded: false, search_volume: null }),
]);

// --- pairing ---------------------------------------------------------------------------------------

const messages: PipelineMessage[] = [
  gate("old", ["c1"], [candidate("c1", "An Older Pick")]),
  { id: "q", role: "assistant", kind: "question", assetId: "blog" } as PipelineMessage,
  blogGate,
  gate("lm", ["c1"], [candidate("c1", "Lead Magnet Pick")], { assetId: "lead_magnet", field: { field_id: "x", label: "LM" } as PipelineMessage["field"] }),
  gate("p2", ["c1"], [candidate("c1", "Phase Two Pick")], { phase: "phase2" }),
  gate("pending", [], [candidate("c1", "Never Chosen")], { field: { field_id: "other", label: "Other" } as PipelineMessage["field"], headlines: { slot: "blog_topic", status: "pending", candidates: [candidate("c1", "Never Chosen")] } }),
  generation("gen"),
  gate("after", ["c1"], [candidate("c1", "Picked After The Document")], { field: { field_id: "later", label: "Later" } as PipelineMessage["field"] }),
];

const paired = topicGatesFor(messages, messages.find((m) => m.id === "gen")!);
ok("one gate per field, the latest before the document", paired.length === 1 && paired[0].id === "g1", paired.map((g) => g.id).join(","));
ok("another asset's gate is not paired", !paired.some((g) => g.id === "lm"));
ok("the other phase's gate is not paired", !paired.some((g) => g.id === "p2"));
ok("an unchosen gate is not paired", !paired.some((g) => g.id === "pending"));
ok("a gate after the document is not paired", !paired.some((g) => g.id === "after"));
ok("a generation with no gate gets no section", topicPreambleFor([generation("solo")], generation("solo")) === "");

// --- content ---------------------------------------------------------------------------------------

const md = renderTopicSuggestions(paired);
ok("section is headed", md.startsWith("# Topic suggestions"));
ok("counts selected of suggested", md.includes("## Blog topics: 2 selected of 3 suggested"), md);
ok("states the anchor", md.includes("Anchored on **Social Media Marketing**"));
ok("states the keyword-report basis", md.includes("Ranked on this run's keyword report"));
const selected = md.slice(md.indexOf("### Selected"), md.indexOf("### Also suggested"));
const rest = md.slice(md.indexOf("### Also suggested"));
ok("selected topics are listed as selected", selected.includes("How To Double Shopify Sales") && selected.includes("Social Calendar"));
ok("unselected topics are listed separately", rest.includes("Seven Instagram Mistakes") && !rest.includes("Shopify"));
ok("each topic says why", md.includes("**Why this one:** Reason for c2.") && md.includes("**Why this one:** Reason for c1."));
ok("each topic names its funnel stage", md.includes("**Funnel stage:** MOFU (middle of funnel: consideration)"));
ok("each topic names its content type", md.includes("**Content type:** Blog post"));
ok("keyword basis carries the measured demand", md.includes('"c2 keyword" (1,900 searches/mo, cluster "Social media for e-commerce")'));
ok("an ungrounded topic says it has no demand data", md.includes("not in this run's keyword report, so no demand data"));
ok("the headline formula is the stated basis", md.includes("**Headline formula:** Number + Outcome + Timeframe"));
ok("the section ends with a rule before the asset", md.trimEnd().endsWith("---"));

const lmMd = renderTopicSuggestions([
  gate("lm2", ["c1"], [candidate("c1", "The 5-Minute Ad Audit", { extras: { format: "checklist", mechanic: "Tick through ten checks." } })], {
    headlines: { slot: "lead_magnet_concept", status: "chosen", subject: "lead magnet", label: "Lead magnet concepts", chosenIds: ["c1"], candidates: [candidate("c1", "The 5-Minute Ad Audit", { extras: { format: "checklist", mechanic: "Tick through ten checks." } })] },
  }) as never,
]);
ok("a lead magnet's content type is its format", lmMd.includes("**Content type:** Checklist (lead magnet)"), lmMd);
ok("a lead magnet's mechanic is carried", lmMd.includes("**Mechanic:** Tick through ten checks."));

const ownMd = renderTopicSuggestions([
  gate("own", [], [candidate("c1", "Suggested But Passed Over")], {
    headlines: { slot: "blog_topic", status: "chosen", label: "Blog topics", ownHeadline: "My Own Topic", chosenIds: [], candidates: [candidate("c1", "Suggested But Passed Over")] },
  }) as never,
]);
ok("an operator's own topic is the selection", ownMd.includes("1. **My Own Topic**") && ownMd.includes("Written by the operator"));
ok("with an own topic, every suggestion is listed as not selected", ownMd.includes("### Also suggested, not selected") && ownMd.includes("Suggested But Passed Over"));

// --- export ----------------------------------------------------------------------------------------

const exported = buildAssetExport({ text: "# Blog post\n\nBody.", label: "Blog", stageNumber: 5, preamble: md });
ok("the .md export opens with the section", exported.filename === "05-blog.md" && exported.content.startsWith("# Topic suggestions"));
ok("the asset follows the section intact", exported.content.endsWith("# Blog post\n\nBody."));
ok("no preamble leaves the export unchanged", buildAssetExport({ text: "# Blog post", label: "Blog" }).content === "# Blog post");
// Long enough to count as a page (`isPreviewableHtml` ignores anything under 200 characters).
const html = "```html\n<!doctype html><html><body>" + "<p>Page copy that runs on.</p>".repeat(10) + "</body></html>\n```";
const htmlExport = buildAssetExport({ text: html, label: "Pillar", preamble: md });
ok("an .html export never gets Markdown in front of it", htmlExport.filename.endsWith(".html") && !htmlExport.content.includes("Topic suggestions"));

// --- placement in the document, and the reader's outline -----------------------------------------

const para = (n: number) => Array.from({ length: n }, (_, i) => `Line ${i} of real prose with several words in it.`).join("\n");
const LEAD_MAGNET = [
  "**PART 1 — COMPETITOR LEAD MAGNET LANDSCAPE**",
  para(20),
  "```text",
  "PART 2 — SELECTED CONCEPT SCORECARD (quoted inside a fence, not the real heading)",
  "```",
  "",
  "**PART 2 — SELECTED CONCEPT SCORECARD**",
  "| Concept | Score |",
  "|---|---|",
  "| The 5-Minute Ad Audit | 42 |",
  "",
  "**PART 3 — LEAD MAGNET BRIEFS**",
  para(20),
].join("\n");

const lmGate = gate("lmg", ["c1"], [
  candidate("c1", "The 5-Minute Ad Audit", { extras: { format: "checklist" } }),
  candidate("c2", "The Meta Ads Benchmark Report", { extras: { format: "benchmark report" } }),
], { assetId: "lead_magnet", headlines: undefined as never });
(lmGate as PipelineMessage).headlines = {
  slot: "lead_magnet_concept", status: "chosen", label: "Lead magnet concepts", subject: "lead magnet",
  chosenIds: ["c1"],
  candidates: [
    candidate("c1", "The 5-Minute Ad Audit", { extras: { format: "checklist" } }),
    candidate("c2", "The Meta Ads Benchmark Report", { extras: { format: "benchmark report" } }),
  ],
};
const lmSection = renderTopicSuggestions([lmGate as never]);
const placed = withTopicSuggestions(LEAD_MAGNET, lmSection, "lead_magnet");
const at = (needle: string) => placed.indexOf(needle);
ok("lead magnet: section sits after PART 1", at("# Topic suggestions") > at("PART 1 — COMPETITOR"));
ok(
  "lead magnet: section sits right before the real PART 2 heading, not the fenced quote",
  at("# Topic suggestions") > at("(quoted inside a fence") && at("# Topic suggestions") < at("**PART 2 — SELECTED CONCEPT SCORECARD**"),
);
ok("lead magnet: the stage's own text is all still there", LEAD_MAGNET.split("\n").every((l) => placed.includes(l)));

const lmDoc = parseAssetDocument(placed);
const labels = lmDoc.sections.map((s) => s.label);
ok(
  "reader: topic suggestions is its own section, between PART 1 and PART 2",
  labels.join(" | ") === "PART 1 — COMPETITOR LEAD MAGNET LANDSCAPE | Topic suggestions | PART 2 — SELECTED CONCEPT SCORECARD | PART 3 — LEAD MAGNET BRIEFS",
  labels.join(" | "),
);
const topicSection = lmDoc.sections.find((s) => s.label === "Topic suggestions");
ok("reader: every suggested concept is in that section", !!topicSection && topicSection.body.includes("The 5-Minute Ad Audit") && topicSection.body.includes("The Meta Ads Benchmark Report"));
ok(
  "reader: its gate and selected/not-selected headings are subheads, not sections",
  !!topicSection && topicSection.subheads.some((h) => h.label.startsWith("Lead magnet concepts")) && topicSection.subheads.some((h) => h.label === "Selected"),
  JSON.stringify(topicSection?.subheads),
);
ok("reader: the split rule is still the document's own", lmDoc.pattern === "part");

const noAnchor = withTopicSuggestions("**PART 1 — A**\nx", lmSection, "lead_magnet");
ok("lead magnet without a PART 2 heading: section goes to the top", noAnchor.startsWith("# Topic suggestions"));
ok("other stages: section goes to the top", withTopicSuggestions("# Blog\n\nBody", md, "blog").startsWith("# Topic suggestions"));
ok("no section: text unchanged", withTopicSuggestions(LEAD_MAGNET, "", "lead_magnet") === LEAD_MAGNET);
ok("parser is unchanged for a document with no topic section", parseAssetDocument(LEAD_MAGNET).sections.length === 3);

const lmExport = buildAssetExport({ text: LEAD_MAGNET, label: "Lead Magnet", preamble: lmSection, assetId: "lead_magnet" });
ok("lead magnet export places the section where the reader does", lmExport.content === placed);

console.log(`${pass} passed, ${fails.length} failed`);
if (fails.length) {
  for (const f of fails) console.log("  FAIL " + f.slice(0, 600));
  process.exit(1);
}
