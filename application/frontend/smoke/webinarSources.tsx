/* The Webinar's competitor sources: read from the pages the competitor research found, shown with
 * why each was chosen. Not shipped; see smoke/README.md.
 *
 * Drives the real walk with `fetch` stubbed:
 *   - answering the competitor research reaches the sources field and opens the card instead of the
 *     question, sending the listing it was answered with;
 *   - the card shows each page, whether it was read, why it was chosen, and what came back;
 *   - confirming answers the field with the pages read plus anything typed, and the walk moves on.
 */
import { renderToStaticMarkup } from "react-dom/server";
import { ASSET_BY_ID } from "../src/data/assetCatalog";
import { WebinarSourcesCard } from "../src/pipeline/WebinarSourcesCard";
import { stagesFor } from "../src/pipeline/pipelineData";
import { usePipelineStore } from "../src/pipeline/pipelineStore";

let pass = 0;
const fails: string[] = [];
const ok = (name: string, cond: boolean, extra?: string) => {
  if (cond) pass++;
  else fails.push(name + (extra ? `  ->  ${extra}` : ""));
};

const LISTING = [
  "Competitor analysis — benchmarked against https://client.com.au",
  "",
  "1. Alpha (alpha.com.au) — Verified",
  "   Page: https://alpha.com.au/webinars/",
  "   Offering: Monthly live webinar on paid social, gated by email, closing on a free audit.",
].join("\n");

const RESULT = {
  sources: [
    {
      name: "Alpha", domain: "alpha.com.au", url: "https://alpha.com.au/webinars/", confidence: "Verified",
      reason: "Found by the webinar competitor research (#1, verified): Monthly live webinar on paid social, gated by email, closing on a free audit.",
      status: "read", title: "Free paid social webinar", chars: 4200, excerpt: "Join our monthly masterclass…", error: "",
    },
    {
      name: "Beta", domain: "beta.com.au", url: "https://beta.com.au/events/", confidence: "Partially verified",
      reason: "Found by the webinar competitor research (#2, partially verified).",
      status: "failed", title: null, chars: 0, excerpt: "", error: "The site blocked an automated read (403).",
    },
  ],
  block: "COMPETITOR WEBINAR SOURCES — read automatically…\n\n### Alpha — https://alpha.com.au/webinars/\nWhy this source: …",
  notes: ["Only 1 competitor webinar page could be read; the prompt asks for 2-5. Add transcripts or slide outlines of your own if you have them."],
};

const requests: { url: string; body: Record<string, unknown> }[] = [];
globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
  const url = String(input);
  const body = init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : {};
  requests.push({ url, body });
  if (url.endsWith("/webinar/sources")) return new Response(JSON.stringify(RESULT), { status: 200 });
  if (url.includes("/context/")) return new Response("{}", { status: 404 });
  return new Response("{}", { status: 200 });
}) as typeof fetch;

const webinar = ASSET_BY_ID["webinar"];
const fieldIds = webinar.fields.map((f) => f.field_id);
ok(
  "the research comes before the sources",
  fieldIds.indexOf("competitor_analysis_webinars") < fieldIds.indexOf("competitor_webinar_sources"),
  fieldIds.join(","),
);

usePipelineStore.setState({
  started: true,
  phase: "phase1",
  runId: "run-1",
  sessionId: "s-1",
  currentIndex: stagesFor("phase1").findIndex((s) => s.asset.asset_id === "webinar"),
  messages: [],
  clientProfile: { region: "Melbourne, Australia", website_url: "https://client.com.au/" },
  intake: { asset: webinar, answers: { webinar_topic_working_title: "1. Paid social pricing" }, awaitingFieldId: "competitor_analysis_webinars" },
  activeStatus: "running",
} as never);

usePipelineStore.getState().submitFreeform(LISTING);
await new Promise((r) => setTimeout(r, 30));

let state = usePipelineStore.getState();
const card = state.messages.find((m) => m.serviceScan?.webinar);
ok("the walk opens the sources card, not the question", !!card && card.field?.field_id === "competitor_webinar_sources", state.messages.map((m) => m.kind).join(","));
ok("no plain question is asked for the sources", !state.messages.some((m) => m.kind === "question" && m.field?.field_id === "competitor_webinar_sources"));
const req = requests.find((r) => r.url.endsWith("/webinar/sources"));
ok("the request carries the research listing", String(req?.body.listing).includes("alpha.com.au/webinars/"), JSON.stringify(req?.body));
ok("the card is ready", card?.serviceScan?.status === "ready");

const html = renderToStaticMarkup(<WebinarSourcesCard message={card!} />);
ok("each page is shown with its link", html.includes("https://alpha.com.au/webinars/") && html.includes("https://beta.com.au/events/"));
ok("why each page was chosen is shown", html.includes("Why: ") && html.includes("Monthly live webinar on paid social"));
ok("what was read is shown", html.includes("Read") && html.includes("4,200 characters") && html.includes("Join our monthly masterclass"));
ok("a page that could not be read says why", html.includes("Couldn&#x27;t read") && html.includes("403"));
ok("a thin result is flagged", html.includes("Only 1 competitor webinar page"));
ok("the button counts what will be used", html.includes("Use 1 source"));

usePipelineStore.getState().confirmWebinarSources(card!.id, "https://gamma.com.au/webinar-replay");
state = usePipelineStore.getState();
const answer = String(state.intake?.answers.competitor_webinar_sources ?? "");
ok("the answer holds the pages read", answer.startsWith("COMPETITOR WEBINAR SOURCES"), answer.slice(0, 60));
ok("and what the operator added", answer.includes("SOURCES ADDED BY THE OPERATOR:") && answer.includes("gamma.com.au"));
ok("the walk moves on", state.intake?.awaitingFieldId === "webinar_format", String(state.intake?.awaitingFieldId));
ok("the transcript records it", state.messages.some((m) => m.role === "user" && m.text === "Competitor webinar sources: 1 page read (Alpha), plus my own"));
const done = state.messages.find((m) => m.id === card!.id);
ok("the card becomes history and still lists the pages", done?.serviceScan?.status === "done" && renderToStaticMarkup(<WebinarSourcesCard message={done!} />).includes("alpha.com.au"));

console.log(`webinarSources: ${pass} passed, ${fails.length} failed`);
for (const f of fails) console.log(`  FAIL ${f}`);
if (fails.length) process.exitCode = 1;
process.exit();
