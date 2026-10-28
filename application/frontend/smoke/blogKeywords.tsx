/* The Blog's keyword card: better keywords than the topic's own, measured against the client's page
 * and the competing pages. Not shipped; see smoke/README.md.
 *
 * Drives the real walk with `fetch` stubbed:
 *   - answering the topic reaches the primary keyword and opens the card, seeded with the chosen
 *     topic's own keyword, which is also the "current" keyword it compares with;
 *   - the request carries the run, the region and the client's site;
 *   - the card shows what was compared, the current keyword, why each suggestion is better, and both
 *     providers' numbers;
 *   - confirming answers the primary AND the supporting keywords, so the supporting question is never
 *     asked, and the answers carry the measured numbers.
 */
import { renderToStaticMarkup } from "react-dom/server";
import { ASSET_BY_ID } from "../src/data/assetCatalog";
import { BlogKeywordCard } from "../src/pipeline/BlogKeywordCard";
import { stagesFor } from "../src/pipeline/pipelineData";
import { usePipelineStore, type PipelineMessage } from "../src/pipeline/pipelineStore";

let pass = 0;
const fails: string[] = [];
const ok = (name: string, cond: boolean, extra?: string) => {
  if (cond) pass++;
  else fails.push(name + (extra ? `  ->  ${extra}` : ""));
};

const opp = (keyword: string, status: string, dv: number, dk: number, competitors: { domain: string; position: number }[] = []) => ({
  keyword, status, dataforseo_volume: dv, dataforseo_difficulty: dk,
  volume: dv, difficulty: dk, cpc: null, intent: "commercial", reference_position: null,
  competitors: competitors.map((c) => ({ ...c, url: `https://${c.domain}/` })), score: dv, sources: ["competitor"],
});
const RESULT = {
  seed: "ai seo services",
  location: "Australia",
  current: opp("ai seo services", "gap", 320, 0),
  primary: [
    opp("ai seo", "gap", 1000, 41, [{ domain: "apwebsolutions.com.au", position: 17 }]),
    opp("ai seo agency", "gap", 390, 0, [{ domain: "studiohawk.com.au", position: 2 }]),
  ],
  secondary: [opp("ai seo services melbourne", "new", 20, 0)],
  compared: [
    { role: "reference", url: "https://clicktrends.com.au/seo/", domain: "clicktrends.com.au", ranked: 2, on_topic: 0, error: "" },
    { role: "competitor", url: "https://studiohawk.com.au/seo-services/ai-seo/", domain: "studiohawk.com.au", ranked: 6, on_topic: 4, error: "" },
  ],
  competitor_source: "serp",
  dataforseo: { status: "ok", error: "" },
  notes: ["The client's reference page ranks for nothing on this topic yet."],
};

const requests: { url: string; body: Record<string, unknown> }[] = [];
globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
  const url = String(input);
  const body = init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : {};
  requests.push({ url, body });
  if (url.endsWith("/keywords/blog")) return new Response(JSON.stringify(RESULT), { status: 200 });
  if (url.includes("/context/")) return new Response("{}", { status: 404 });
  return new Response("{}", { status: 200 });
}) as typeof fetch;

const blog = ASSET_BY_ID["blog"];
const blogIndex = stagesFor("phase1").findIndex((s) => s.asset.asset_id === "blog");
const topicField = blog.fields.find((f) => f.field_id === "blog_topic_working_title")!;
const gate = {
  id: "gate",
  role: "assistant",
  kind: "headline-choice",
  assetId: "blog",
  phase: "phase1",
  createdAt: 1,
  field: topicField,
  headlines: { status: "chosen", candidates: [{ id: "c1", headline: "How AI SEO Services Beat Agencies", primary_keyword: "ai seo services" }], chosenIds: ["c1"] },
} as unknown as PipelineMessage;

usePipelineStore.setState({
  started: true,
  phase: "phase1",
  runId: "run-1",
  sessionId: "s-1",
  currentIndex: blogIndex,
  messages: [gate],
  clientProfile: { region: "Melbourne, Australia", website_url: "https://clicktrends.com.au/", target_service: "AI SEO" },
  intake: { asset: blog, answers: {}, awaitingFieldId: "blog_topic_working_title" },
  activeStatus: "running",
} as never);

usePipelineStore.getState().submitFreeform("1. How AI SEO Services Beat Agencies");
await new Promise((r) => setTimeout(r, 30));

let state = usePipelineStore.getState();
const card = state.messages.find((m) => m.serviceScan?.blog);
ok("the walk opens the keyword card at the primary keyword", !!card && card.field?.field_id === "primary_keyword", state.messages.map((m) => m.kind).join(","));
ok("seeded with the chosen topic's own keyword", card?.serviceScan?.blog?.seed === "ai seo services" && card?.serviceScan?.blog?.current === "ai seo services");
const req = requests.find((r) => r.url.endsWith("/keywords/blog"));
ok("the request carries the run, region and the client's site", req?.body.run_id === "run-1" && req?.body.region === "Melbourne, Australia" && req?.body.website_url === "https://clicktrends.com.au/", JSON.stringify(req?.body));
ok("the card is ready", card?.serviceScan?.status === "ready");

const html = renderToStaticMarkup(<BlogKeywordCard message={card!} />);
ok("it says what was compared", html.includes("Compared against") && html.includes("studiohawk.com.au") && html.includes("Google&#x27;s top 10"));
ok("it shows the current keyword with its numbers", html.includes("Current keyword:") && html.includes("320/mo"));
ok("each suggestion says why and by how much", html.includes("Competitors rank, you don&#x27;t") && html.includes("+680/mo vs current"));
ok("DataForSEO's numbers are shown", html.includes("1,000/mo · KD 41"));
ok("no other provider is named", !/ahrefs/i.test(html));
ok("who ranks is shown", html.includes("apwebsolutions.com.au #17"));
ok("the default primary is the best suggestion", html.includes("Use “ai seo”"));

// Confirm: primary "ai seo agency", supporting "ai seo services melbourne".
const all = [...RESULT.primary, ...RESULT.secondary];
usePipelineStore.getState().confirmBlogKeywords(card!.id, "ai seo agency", all.filter((k) => k.keyword === "ai seo services melbourne") as never, "ai seo pricing");
state = usePipelineStore.getState();
const answers = state.intake?.answers ?? {};
ok("the primary keyword is answered", answers.primary_keyword === "ai seo agency", String(answers.primary_keyword));
ok(
  "the supporting keywords are answered with their numbers",
  String(answers.secondary_supporting_keywords_optional).includes("ai seo services melbourne (DataForSEO 20/mo, KD 0)") &&
    String(answers.secondary_supporting_keywords_optional).includes("- ai seo pricing"),
  String(answers.secondary_supporting_keywords_optional),
);
ok("the supporting question is never asked", state.intake?.awaitingFieldId !== "secondary_supporting_keywords_optional", String(state.intake?.awaitingFieldId));
ok("the walk moves on to the next question", state.intake?.awaitingFieldId === "blog_type", String(state.intake?.awaitingFieldId));
ok("the transcript records the choice", state.messages.some((m) => m.role === "user" && m.text === "Primary keyword: ai seo agency. Supporting: ai seo services melbourne, ai seo pricing"));

console.log(`blogKeywords: ${pass} passed, ${fails.length} failed`);
for (const f of fails) console.log(`  FAIL ${f}`);
if (fails.length) process.exitCode = 1;
process.exit();
