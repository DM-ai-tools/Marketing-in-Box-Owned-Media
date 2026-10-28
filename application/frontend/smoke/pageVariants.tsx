/* Pillar Page variants and the clip button. Not shipped; see smoke/README.md.
 *
 * Proven here, with `fetch` stubbed:
 *   - the picker offers each variant only when it has something to work from, and says why not;
 *   - drafting sends one generation per ticked variant, each naming its variant;
 *   - with the competitor search still to run, the first variant runs alone and the rest start with
 *     its listing filled in, so the search is paid for once;
 *   - the comparison card measures every draft, and approving one keeps the siblings in Deliverables;
 *   - the cards render their states; the clip button's helpers compose and refuse correctly.
 */
import { renderToStaticMarkup } from "react-dom/server";
import { comparisonRows, variantOptions } from "../src/lib/pageVariants";
import { attachedHeader, localRefusal, withAttachedText } from "../src/lib/attachedFile";
import { usePipelineStore } from "../src/pipeline/pipelineStore";
import type { PipelineMessage } from "../src/pipeline/pipelineStore";
import type { VariantPageRow } from "../src/pipeline/pipelineApi";
import { PageVariantsView } from "../src/pipeline/PageVariantsCard";
import { VariantCompareView } from "../src/pipeline/VariantCompareCard";
import { VariantNote } from "../src/pipeline/VariantNote";
import { AttachFileButton } from "../src/pipeline/AttachFileButton";

let pass = 0;
const fails: string[] = [];
const ok = (name: string, cond: boolean, extra?: string) => {
  if (cond) pass++;
  else fails.push(name + (extra ? `  ->  ${extra}` : ""));
};

console.error = () => {};
console.warn = () => {};
const flush = async () => {
  for (let i = 0; i < 20; i++) await new Promise((r) => setTimeout(r, 0));
};

// ------------------------------------------------------------------------------- the picker's rules

const base = { reference_design_source: "https://client.com.au/seo" };
let options = variantOptions(base, false);
ok("reference is offered with a hosted page", options.find((o) => o.id === "reference")!.available);
ok("competitor is off without a listing", !options.find((o) => o.id === "competitor")!.available);
ok("competitor says what to supply", /approve a competitor search/.test(options.find((o) => o.id === "competitor")!.reason ?? ""));
ok("skills off when not asked for", !options.find((o) => o.id === "skills")!.available);
options = variantOptions(
  {
    reference_design_source: "NO REFERENCE PAGE — use https://client.com.au as the landing page",
    competitor_analysis_pillar_page: "Competitor listing…",
    use_skill_document: "Yes",
    skill_document: "Attached file: skill.md\n\nRules",
    use_framework_document: "Yes",
    framework_document: "skip",
  },
  false,
);
ok("no-reference mode has no page to beat", !options.find((o) => o.id === "reference")!.available);
ok("a listing in the answers offers competitor", options.find((o) => o.id === "competitor")!.available);
ok("a skill document offers skills", options.find((o) => o.id === "skills")!.available && options.find((o) => o.id === "skills")!.defaultOn);
ok("a skipped framework is not a document", !options.find((o) => o.id === "framework")!.available);
ok("a parent pillar page counts in Phase 2", variantOptions({ parent_pillar_page_url: "https://c.com/social" }, false)[0].available);
ok("a listing on the run offers competitor", variantOptions({}, true).find((o) => o.id === "competitor")!.available);

// ------------------------------------------------------------------------------- comparison rows

const row = (variant: string, label: string, extra: Partial<VariantPageRow> = {}): VariantPageRow => ({
  label,
  url: null,
  words: 1200,
  h1: "H",
  sections: ["a", "b"],
  ctas: 2,
  internal_links: 3,
  images: 1,
  faq: true,
  schema_types: ["FAQPage"],
  proof_mentions: 2,
  keywords_covered: ["seo"],
  keywords_checked: 4,
  error: null,
  variant,
  has_page: true,
  topics_covered: [5, 10],
  ...extra,
});
const table = comparisonRows([row("reference", "Beat your current page"), row("competitor", "Outperform competitors", { topics_covered: [9, 10], keywords_covered: ["seo", "local seo"] })], {
  current: row("current", "Current page", { topics_covered: null, internal_links: 40 }),
  competitorMedianWords: 1800,
});
ok("columns are the drafts then the live page", table.columns.join("|") === "Beat your current page|Outperform competitors|Current page (live)");
const unreadTable = comparisonRows([row("reference", "Beat your current page")], { current: row("current", "Current page", { error: "the site served a bot check" }) });
ok("an unread live page keeps its column, with nothing measured", unreadTable.columns.length === 2 && unreadTable.rows.every((r) => r.cells[1] === "—"));
const topicsRow = table.rows.find((r) => r.label === "Competitor topics covered")!;
ok("topics covered marks the leading draft", topicsRow.best === 1 && topicsRow.cells[1] === "9/10" && topicsRow.cells[2] === "—");
ok("the current page is never marked best", table.rows.find((r) => r.label === "Internal links")!.best === null);
ok("words carries the competitor median", table.rows[0].label.includes("1,800"));
ok("a tie marks nothing", table.rows.find((r) => r.label === "Proof mentions")!.best === null);

// ------------------------------------------------------------------------------- the store

type Call = { url: string; body: Record<string, unknown> };
let calls: Call[] = [];
const sse = (events: object[]) =>
  new Response(events.map((e) => `data: ${JSON.stringify(e)}\n\n`).join(""), { status: 200, headers: { "Content-Type": "text/event-stream" } });
let releaseFirst: () => void = () => {};
const firstGate = new Promise<void>((r) => (releaseFirst = r));
globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
  const url = String(input);
  const body = init?.body && typeof init.body === "string" ? JSON.parse(init.body) : {};
  calls.push({ url, body });
  if (url.includes("/generate/pillar_page/stream")) {
    const variant = body.variant as string;
    const events: object[] = [];
    if (variant === "competitor" && !(body.answers as Record<string, string>).competitor_analysis_pillar_page) {
      events.push({ type: "prepass_start", asset_id: "competitor_analysis_seo_pillar_page" });
      events.push({ type: "prepass", asset_id: "competitor_analysis_seo_pillar_page", target_field_id: "competitor_analysis_pillar_page", skipped: false, content: "LISTING" });
    }
    events.push({
      type: "page_variant",
      variant,
      label: variant,
      reference: null,
      beat_sheet: variant === "competitor" ? { competitors: [], topics: [{ topic: "What is SEO", covered_by: ["a.com"] }], target_words: 2000, max_words: 1800, median_words: 1500, faq_share: "1/1", schema_seen: [] } : null,
      notes: [],
    });
    events.push({ type: "delta", text: `# ${variant} draft\n\n\`\`\`html\n<h1>${variant}</h1>\n\`\`\`\n` });
    events.push({ type: "done", stop_reason: "end_turn", truncated: false });
    if (variant === "competitor") await firstGate;
    return sse(events);
  }
  if (url.includes("/pillar/variants/compare")) {
    const drafts = body.drafts as { variant: string; label: string }[];
    return new Response(JSON.stringify({ pages: drafts.map((d) => row(d.variant, d.label)), current: row("current", "Current page") }), { status: 200 });
  }
  if (url.includes("/check/")) return new Response(JSON.stringify({ results: [], facts_missing: [], judge: "unavailable" }), { status: 200 });
  if (url.includes("/stages") || url.includes("/runs")) return new Response(JSON.stringify({ ok: true, run_id: "run-1", version: 1 }), { status: 200 });
  return new Response("{}", { status: 200 });
}) as typeof fetch;

const answers = { reference_design_source: "https://client.com.au/seo", use_skill_document: "No", use_framework_document: "No" };
const picker: PipelineMessage = {
  id: "picker",
  role: "assistant",
  kind: "page-variants",
  assetId: "pillar_page",
  phase: "phase1",
  createdAt: 1,
  pageVariants: { status: "choosing", answers, options: variantOptions(answers, false), chosen: [], drafts: {} },
} as PipelineMessage;
usePipelineStore.setState({ messages: [picker], runId: "run-1", sessionId: null, phase: "phase1", started: true } as never);

const started = usePipelineStore.getState().startPageVariants("picker", ["reference", "competitor"]);
await flush();
let generates = calls.filter((c) => c.url.includes("/generate/"));
ok("the competitor variant runs first, alone, while it searches", generates.length === 1 && generates[0].body.variant === "competitor", JSON.stringify(generates.map((g) => g.body.variant)));
releaseFirst();
await started;
await flush();
generates = calls.filter((c) => c.url.includes("/generate/"));
ok("one generation per ticked variant", generates.length === 2);
const second = generates.find((g) => g.body.variant === "reference");
ok("the second variant starts with the listing filled in", (second?.body.answers as Record<string, string>)?.competitor_analysis_pillar_page === "LISTING");
let messages = usePipelineStore.getState().messages;
const drafts = messages.filter((m) => m.kind === "generation");
ok("each draft is stamped with its variant", drafts.map((d) => d.variant).sort().join(",") === "competitor,reference");
ok("each draft keeps what it measured", Boolean(drafts.find((d) => d.variant === "competitor")?.pageVariant?.beat_sheet));
ok("the picker records its drafts", Object.keys(messages.find((m) => m.id === "picker")!.pageVariants!.drafts).length === 2);
ok("the picker is built", messages.find((m) => m.id === "picker")!.pageVariants!.status === "built");
const compare = messages.find((m) => m.kind === "variant-compare");
ok("a comparison card follows the drafts", compare?.variantCompare?.status === "done" && compare.variantCompare.pages?.length === 2);
const compareCall = calls.find((c) => c.url.includes("/pillar/variants/compare"));
ok("the comparison is sent the beat sheet's topics", JSON.stringify(compareCall?.body.topics) === '["What is SEO"]');
ok("the comparison asks for the live page", (compareCall?.body.answers as Record<string, string>)?.reference_design_source === "https://client.com.au/seo");
ok("the live page is a column", compare?.variantCompare?.current?.variant === "current");
ok("one comparison card, measured once both drafts were final", messages.filter((m) => m.kind === "variant-compare").length === 1 && calls.filter((c) => c.url.includes("/pillar/variants/compare")).length === 1);
const checksBefore = calls.filter((c) => c.url.includes("/check/")).length;
ok("both drafts were checked before the comparison", checksBefore === 2 && calls.findIndex((c) => c.url.includes("/pillar/variants/compare")) > calls.findLastIndex((c) => c.url.includes("/check/")));

// A draft still being checked holds the comparison back.
const compareCalls = () => calls.filter((c) => c.url.includes("/pillar/variants/compare")).length;
const beforeHold = compareCalls();
usePipelineStore.setState({
  messages: usePipelineStore.getState().messages.map((m) => (m.kind === "generation" && m.variant === "reference" ? { ...m, check: { status: "running" } } : m)),
} as never);
await usePipelineStore.getState().remeasureVariants(compare!.id);
ok("a draft still being checked is not measured", compareCalls() === beforeHold);
usePipelineStore.setState({
  messages: usePipelineStore.getState().messages.map((m) =>
    m.kind === "generation" && m.variant === "reference" ? { ...m, check: { status: "done" }, text: m.text + " Fixed." } : m,
  ),
} as never);
await usePipelineStore.getState().recheckAsset(drafts.find((d) => d.variant === "reference")!.id);
await flush();
ok("a changed draft re-measures the same card", compareCalls() === beforeHold + 1 && usePipelineStore.getState().messages.filter((m) => m.kind === "variant-compare").length === 1);

const chosen = drafts.find((d) => d.variant === "reference")!;
await usePipelineStore.getState().saveStage(chosen.id);
await flush();
messages = usePipelineStore.getState().messages;
const other = messages.find((m) => m.kind === "generation" && m.variant === "competitor")!;
ok("approving one variant saves it", messages.find((m) => m.id === chosen.id)?.savePhase === "saved");
ok("the sibling variant is kept in deliverables", Boolean(other.savePhase === "saved" && other.variantAlternate && !other.superseded));
ok("the approved variant is the run's primary", !messages.find((m) => m.id === chosen.id)?.variantAlternate);

// A variant built with Manus is a new draft that asks for Manus.
usePipelineStore.setState({
  messages: [{ ...drafts[0], id: "solo", savePhase: "idle", superseded: false, variantNotChosen: false } as PipelineMessage],
} as never);
calls = [];
await usePipelineStore.getState().buildVariantWithManus("solo");
await flush();
const manusCall = calls.find((c) => c.url.includes("/generate/"));
ok("Build with Manus asks for Manus on that variant", manusCall?.body.use_manus === true && manusCall?.body.variant === drafts[0].variant);
ok("the Claude draft is marked rebuilt", Boolean(usePipelineStore.getState().messages.find((m) => m.id === "solo")?.manusRebuilt));

// ------------------------------------------------------------------------------- the cards

const pickerHtml = renderToStaticMarkup(<PageVariantsView state={picker.pageVariants!} onStart={() => {}} />);
ok("the picker lists all four variants", (pickerHtml.match(/data-variant="/g) ?? []).length === 4);
ok("the picker says why one is off", pickerHtml.includes("approve a competitor search"));
ok("the picker offers to draft the ticked ones", pickerHtml.includes("Draft 1 variant"));
const building = renderToStaticMarkup(
  <PageVariantsView
    state={{ ...picker.pageVariants!, status: "building", chosen: ["reference"], drafts: { reference: "x" } }}
    draftStatus={() => "drafting"}
    onStart={() => {}}
  />,
);
ok("a building picker shows each draft's state", building.includes("· drafting") && building.includes("· not drafted") && !building.includes("type=\"checkbox\""));

const compareHtml = renderToStaticMarkup(
  <VariantCompareView
    state={{ status: "done", messageIds: ["a", "b"], pages: [row("reference", "Beat your current page"), row("competitor", "Outperform competitors", { topics_covered: [9, 10] })] }}
    draftIds={["a", "b"]}
    approvedId={null}
    closed={[false, false]}
    onApprove={() => {}}
    onRemeasure={() => {}}
  />,
);
ok("the comparison offers Approve per draft", (compareHtml.match(/Approve this one/g) ?? []).length === 2);
ok("the comparison marks the leader in words, not colour alone", compareHtml.includes("(leads)"));
const approvedHtml = renderToStaticMarkup(
  <VariantCompareView
    state={{ status: "done", messageIds: ["a", "b"], pages: [row("reference", "A"), row("competitor", "B")] }}
    draftIds={["a", "b"]}
    approvedId="a"
    closed={[false, true]}
    onApprove={() => {}}
    onRemeasure={() => {}}
  />,
);
ok("after approval the others read Kept in Deliverables", approvedHtml.includes("✓ For the run") && approvedHtml.includes("Kept in Deliverables") && !approvedHtml.includes("Approve this one"));
const errorHtml = renderToStaticMarkup(
  <VariantCompareView state={{ status: "error", messageIds: [], error: "offline" }} draftIds={[]} approvedId={null} closed={[]} onApprove={() => {}} onRemeasure={() => {}} />,
);
ok("a failed comparison offers Try again", errorHtml.includes("offline") && errorHtml.includes("Try again"));

const note = renderToStaticMarkup(
  <VariantNote
    message={{ ...drafts.find((d) => d.variant === "competitor")!, superseded: false, manus: undefined, savePhase: "idle", streaming: false } as PipelineMessage}
    onBuildWithManus={() => {}}
  />,
);
ok("the variant note names the variant", note.includes("Variant: Outperform competitors"));
ok("the variant note states the beat sheet", note.includes("1 topics to beat") || note.includes("topics to beat"));
ok("the variant note offers Build with Manus", note.includes("Build with Manus"));
ok("no note on an ordinary card", renderToStaticMarkup(<VariantNote message={{ ...drafts[0], variant: undefined } as PipelineMessage} />) === "");

// ------------------------------------------------------------------------------- the clip button

const extracted = { filename: "skill.pdf", kind: "pdf" as const, text: "Lead with the outcome.", chars: 70000, pages: 12, truncated: true };
ok("the header names the file and its size", attachedHeader(extracted) === "Attached file: skill.pdf (12 pages, 70,000 characters, first 22 kept)");
ok("attached text follows anything typed", withAttachedText("Use this:  ", extracted).startsWith("Use this:\n\nAttached file: skill.pdf"));
ok("attached text alone has no leading gap", withAttachedText("", { ...extracted, truncated: false, pages: null }).startsWith("Attached file: skill.pdf (70,000 characters)\n\nLead"));
ok("a large file is refused before upload", localRefusal({ name: "a.pdf", size: 11 * 1024 * 1024 })?.includes("10 MB") === true);
ok("an image is refused before upload", localRefusal({ name: "a.png", size: 10 })?.includes("PDF, Word") === true);
ok(".doc goes to the server, which says what to do", localRefusal({ name: "old.DOC", size: 10 }) === null);
const clip = renderToStaticMarkup(<AttachFileButton onText={() => {}} onError={() => {}} />);
ok("the clip accepts the five kinds", clip.includes('accept=".pdf,.docx,.doc,.md,.markdown,.txt"'));
ok("the clip is labelled", clip.includes("Attach a file (PDF, Word, Markdown or text)"));

console.log(`pageVariants: ${pass} passed, ${fails.length} failed`);
for (const f of fails) console.log("  FAIL " + f);
if (fails.length) process.exit(1);
