/* The Pillar Page's secondary / cluster terms, suggested by DataForSEO.
 * Not shipped; see smoke/README.md.
 *
 * Proven here, driving the store's real actions with `fetch` stubbed:
 *   - answering the head term opens the terms card (not a text question), asked about that head
 *     term and the client's region, with nothing ticked (the field is optional),
 *   - the card shows DataForSEO's terms with their totals, and says why when DataForSEO failed,
 *   - confirming writes one line per term with its volume and provider plus typed terms; skipping
 *     answers N/A,
 *   - with no usable head term the plain question is asked instead.
 */
import { renderToStaticMarkup } from "react-dom/server";
import { ASSET_BY_ID } from "../src/data/assetCatalog";
import { SECONDARY_KEYWORD_OPTION, composeSecondaryTermsAnswer, stagesFor } from "../src/pipeline/pipelineData";
import type { SecondarySuggestions } from "../src/pipeline/pipelineApi";
import { usePipelineStore } from "../src/pipeline/pipelineStore";
import type { PipelineMessage } from "../src/pipeline/pipelineStore";
import { SecondaryKeywordCard } from "../src/pipeline/SecondaryKeywordCard";

let pass = 0;
const fails: string[] = [];
const ok = (name: string, cond: boolean, extra?: string) => {
  if (cond) pass++;
  else fails.push(name + (extra ? `  ->  ${extra}` : ""));
};

console.error = () => {};
console.warn = () => {};
const flush = async () => {
  for (let i = 0; i < 10; i++) await new Promise((r) => setTimeout(r, 0));
};

const k = (keyword: string, volume: number | null, difficulty: number | null = 20, intent: string | null = null) => ({
  keyword,
  volume,
  difficulty,
  cpc: null,
  intent,
});

let response: SecondarySuggestions = {
  head_term: "social media marketing agency",
  location: "Australia",
  dataforseo: {
    provider: "dataforseo",
    status: "ok",
    error: "",
    keywords: [
      k("social media management", 1700, 0, "commercial"),
      k("social media agency melbourne", 320, 16, "commercial"),
      k("social media marketing agency melbourne", 210, 21, "commercial"),
    ],
  },
};
const calls: Record<string, unknown>[] = [];
globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
  const url = String(input);
  if (url.endsWith("/api/pipeline/keywords/secondary")) {
    calls.push(JSON.parse(String(init?.body ?? "{}")));
    return new Response(JSON.stringify(response), { status: 200 });
  }
  return new Response(JSON.stringify({ detail: "not stubbed" }), { status: 404 });
}) as typeof fetch;

const PILLAR = ASSET_BY_ID.pillar_page;

/** Every Pillar Page field before the head term, answered, so the walk's next stop is the secondary terms. */
function answeredBeforeHeadTerm(): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const f of PILLAR.fields) {
    if (f.field_id === SECONDARY_KEYWORD_OPTION.headTermFieldId) break;
    out[f.field_id] = f.choices?.[0] ?? "answered";
  }
  return out;
}

function seedAtHeadTerm() {
  usePipelineStore.setState({
    started: true,
    isLoadingSession: false,
    phase: "phase1",
    runId: "run-1",
    sessionId: null,
    messages: [],
    context: {},
    currentIndex: stagesFor("phase1").findIndex((s) => s.asset.asset_id === "pillar_page"),
    intake: { asset: PILLAR, answers: answeredBeforeHeadTerm(), awaitingFieldId: SECONDARY_KEYWORD_OPTION.headTermFieldId },
    subStep: null,
    activeStatus: "running",
    progress: 50,
    navStatus: "Awaiting Input",
    clientProfile: { client_name: "Traffic Radius", website_url: "https://trafficradius.com.au/", region: "Melbourne, Australia" },
    editSeed: null,
    fault: null,
  } as never);
}

const last = (kind: PipelineMessage["kind"]) =>
  [...usePipelineStore.getState().messages].reverse().find((m) => m.kind === kind && !m.superseded);
const answers = () => (usePipelineStore.getState().intake?.answers ?? {}) as Record<string, unknown>;
const awaiting = () => usePipelineStore.getState().intake?.awaitingFieldId;
const render = (message: PipelineMessage) => renderToStaticMarkup(<SecondaryKeywordCard message={message} />);

async function main() {
  // ---------- the answer's shape ----------
  const composed = composeSecondaryTermsAnswer(
    [
      { provider: "dataforseo", keyword: "social media management", volume: 1700 },
      { provider: "dataforseo", keyword: "social media marketing agency melbourne", volume: 210 },
      { provider: "dataforseo", keyword: "social media agency melbourne", volume: null },
    ],
    "LinkedIn ads, social media management",
    "Australia",
  );
  ok(
    "one line per term, with its measured number, then typed terms",
    composed ===
      "Monthly Google searches in Australia, as measured by the provider named.\n" +
        "- social media management — 1,700/mo (DataForSEO)\n" +
        "- social media marketing agency melbourne — 210/mo (DataForSEO)\n" +
        "- social media agency melbourne — not measured (DataForSEO)\n" +
        "- LinkedIn ads — typed by the operator; not measured",
    composed,
  );
  ok("nothing picked is no answer", composeSecondaryTermsAnswer([], "  ", "Australia") === "");

  // ---------- the walk reaches the card ----------
  seedAtHeadTerm();
  usePipelineStore.getState().submitAnswer("social media marketing agency");
  await flush();
  const card = last("service-choice");
  ok("the head term opens the terms card", !!card?.serviceScan?.secondary && awaiting() === SECONDARY_KEYWORD_OPTION.fieldId, String(awaiting()));
  ok(
    "no text question is asked for it",
    !usePipelineStore.getState().messages.some((m) => m.kind === "question" && m.field?.field_id === SECONDARY_KEYWORD_OPTION.fieldId),
  );
  ok(
    "DataForSEO is asked about the head term and the client's region",
    calls[0]?.head_term === "social media marketing agency" && calls[0]?.region === "Melbourne, Australia",
    JSON.stringify(calls[0]),
  );
  ok("the suggestions are on the card", card?.serviceScan?.status === "ready" && card.serviceScan.secondary?.result?.location === "Australia");

  const html = render(card!);
  ok("one DataForSEO list, no other provider", html.includes('data-provider="dataforseo"') && !/ahrefs/i.test(html));
  ok("the list states its totals", html.includes("3 terms") && html.includes("median KD"), html.slice(0, 400));
  ok("DataForSEO's intent is shown", html.includes("commercial"));
  ok("nothing starts ticked: the field is optional", !html.includes("checked=\"\""));
  ok("Skip is offered", html.includes("Skip: no secondary terms"));

  // ---------- confirming ----------
  usePipelineStore.getState().confirmSecondaryKeywords(
    card!.id,
    [
      { provider: "dataforseo", keyword: "social media management", volume: 1700 },
      { provider: "dataforseo", keyword: "social media agency melbourne", volume: 320 },
    ],
    "LinkedIn ads",
  );
  await flush();
  const answer = String(answers()[SECONDARY_KEYWORD_OPTION.fieldId] ?? "");
  ok("the answer carries each term's provider and volume", answer.includes("- social media management — 1,700/mo (DataForSEO)") && answer.includes("320/mo (DataForSEO)"), answer);
  ok("typed terms are kept", answer.includes("- LinkedIn ads — typed by the operator"));
  ok("the walk moves on", awaiting() !== SECONDARY_KEYWORD_OPTION.fieldId, String(awaiting()));
  const done = usePipelineStore.getState().messages.find((m) => m.id === card!.id)!;
  ok("the card shows what was used", done.serviceScan?.status === "done" && render(done).includes("social media agency melbourne"));

  // ---------- a chosen headline seeds with its keyword, never the headline ----------
  seedAtHeadTerm();
  const headline = "How AI SEO Services Beat Traditional Agencies In Melbourne";
  usePipelineStore.setState({
    messages: [
      {
        id: "gate-1",
        role: "assistant",
        kind: "headline-choice",
        assetId: "pillar_page",
        field: PILLAR.fields.find((f) => f.field_id === SECONDARY_KEYWORD_OPTION.headTermFieldId),
        headlines: {
          slot: "pillar_head_term",
          status: "chosen",
          chosenIds: ["c1"],
          candidates: [{ id: "c1", headline, primary_keyword: "ai seo services" }],
        },
      },
    ],
  } as never);
  const seedCalls = calls.length;
  usePipelineStore.getState().submitAnswer(headline);
  await flush();
  ok("the suggestion's keyword is searched, not its headline", calls[seedCalls]?.head_term === "ai seo services", JSON.stringify(calls[seedCalls]));
  const seeded = last("service-choice")!;
  ok("the card names the term it searched", seeded.serviceScan?.secondary?.headTerm === "ai seo services");

  // ---------- searching a different term ----------
  usePipelineStore.getState().researchSecondaryKeywords(seeded.id, "seo agency melbourne");
  await flush();
  ok("a new term asks DataForSEO again", calls[calls.length - 1]?.head_term === "seo agency melbourne");
  const researched = usePipelineStore.getState().messages.find((m) => m.id === seeded.id)!;
  ok("the card shows the new term's results", researched.serviceScan?.status === "ready" && researched.serviceScan.secondary?.headTerm === "seo agency melbourne");
  ok("the search box is offered", render(researched).includes("Search a different term"));

  // ---------- nothing found says why ----------
  const saved = response;
  response = { ...response, dataforseo: { ...response.dataforseo, keywords: [] } };
  usePipelineStore.getState().researchSecondaryKeywords(seeded.id, headline);
  await flush();
  const empty = render(usePipelineStore.getState().messages.find((m) => m.id === seeded.id)!);
  ok("an empty result explains a headline is not a search term", empty.includes("data-no-terms") && empty.includes("reads like a headline"), empty.slice(0, 300));
  response = saved;

  // ---------- skipping ----------
  seedAtHeadTerm();
  usePipelineStore.getState().submitAnswer("social media marketing agency");
  await flush();
  usePipelineStore.getState().skipServices(last("service-choice")!.id);
  ok("skipping answers N/A", answers()[SECONDARY_KEYWORD_OPTION.fieldId] === "N/A");

  // ---------- DataForSEO failing ----------
  response = { ...response, dataforseo: { provider: "dataforseo", status: "error", error: "DataForSEO says the account is out of balance.", keywords: [] } };
  seedAtHeadTerm();
  usePipelineStore.getState().submitAnswer("social media marketing agency");
  await flush();
  const partial = render(last("service-choice")!);
  ok("the card says why", partial.includes('data-provider-status="error"') && partial.includes("out of balance"));
  ok("the text box still works", partial.includes("Any other terms?"));

  // ---------- no head term ----------
  seedAtHeadTerm();
  const before = calls.length;
  usePipelineStore.getState().submitAnswer(
    "A very long head term answer that is clearly a sentence rather than a keyword someone would search for at all",
  );
  await flush();
  ok("an unusable head term asks the plain question", calls.length === before && !last("service-choice")?.serviceScan?.secondary);

  console.log(`secondaryKeywords: ${pass} passed, ${fails.length} failed`);
  for (const f of fails) console.log("  FAIL", f);
  if (fails.length) process.exit(1);
}

void main();
