/* CRO's buyer-language cards: "Words the buyer uses for the outcome" and "Words to avoid".
 * Not shipped; see smoke/README.md.
 *
 * Proven here, driving the store's real actions with `fetch` stubbed:
 *   - reaching the outcome field opens a suggestions card, not a text question, and asks the server
 *     with this session's services, landing page, region and run;
 *   - the card shows outcome clusters with their measured searches; nothing starts ticked;
 *   - confirming writes a plain comma list (typed words first, then each picked cluster and its best
 *     searches), under the length that carries to Phase 2 whole, and moves to the avoid card;
 *   - the avoid card groups jargon and claims (never competitor names); its answer has no group labels; Skip
 *     answers NONE; the outcome card has no Skip;
 *   - a server error shows on the card, which still takes typed words.
 */
import { renderToStaticMarkup } from "react-dom/server";
import { ASSET_BY_ID } from "../src/data/assetCatalog";
import {
  BUYER_LANGUAGE_MAX_CHARS,
  BUYER_LANGUAGE_OPTION,
  composeOutcomeWordsAnswer,
  composeWordsToAvoidAnswer,
  stagesFor,
} from "../src/pipeline/pipelineData";
import type { BuyerLanguageResult } from "../src/pipeline/pipelineApi";
import { usePipelineStore } from "../src/pipeline/pipelineStore";
import type { PipelineMessage } from "../src/pipeline/pipelineStore";
import { BuyerLanguageCard } from "../src/pipeline/BuyerLanguageCard";

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

const phrase = (keyword: string, volume: number | null) => ({ keyword, volume, difficulty: 20, trend: null, score: 50 });
const OUTCOME: BuyerLanguageResult = {
  mode: "outcome",
  location: "Australia",
  clusters: [
    { name: "more customers", phrases: [phrase("how to get more customers", 1300), phrase("get more clients", 590), phrase("find new customers", 90)], combined_volume: 1980 },
    { name: "rank higher on google", phrases: [phrase("rank higher on google", 880)], combined_volume: 880, distinct: true },
  ],
  avoid: [],
  sources: ["suggestions for \"SEO\" (50)"],
  notes: [],
};
const AVOID: BuyerLanguageResult = {
  mode: "avoid",
  location: "Australia",
  clusters: [],
  avoid: [
    { term: "StudioHawk", kind: "competitor", reason: "", alternative: "", term_volume: null, alternative_volume: null },
    { term: "omnichannel synergy", kind: "jargon", reason: "", alternative: "marketing across channels", term_volume: null, alternative_volume: 390 },
    { term: "guarantee page one rankings", kind: "claim", reason: "Rankings cannot be guaranteed.", alternative: "", term_volume: null, alternative_volume: null },
  ],
  sources: [],
  notes: [],
};

const calls: Record<string, unknown>[] = [];
let failOutcome = false;
globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
  const url = String(input);
  if (url.endsWith("/api/pipeline/keywords/buyer-language")) {
    const body = JSON.parse(String(init?.body ?? "{}"));
    calls.push(body);
    if (body.mode === "outcome" && failOutcome) return new Response(JSON.stringify({ detail: "DataForSEO is not configured." }), { status: 422 });
    return new Response(JSON.stringify(body.mode === "outcome" ? OUTCOME : AVOID), { status: 200 });
  }
  return new Response(JSON.stringify({ detail: "not stubbed" }), { status: 404 });
}) as typeof fetch;

const CRO = ASSET_BY_ID.cro;

/** Every CRO field before the outcome field, answered, so the walk's next stop is the outcome words. */
function answeredBeforeOutcome(): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const f of CRO.fields) {
    if (f.field_id === BUYER_LANGUAGE_OPTION.outcomeFieldId) break;
    out[f.field_id] = f.choices?.[0] ?? "answered";
  }
  out.existing_page_url = "https://trafficradius.com.au/seo/";
  out.target_service_or_sub_service = "SEO";
  return out;
}

function seedAtOutcome() {
  const fieldBefore = CRO.fields[CRO.fields.findIndex((f) => f.field_id === BUYER_LANGUAGE_OPTION.outcomeFieldId) - 1];
  const answers = answeredBeforeOutcome();
  delete answers[fieldBefore.field_id];
  usePipelineStore.setState({
    started: true,
    isLoadingSession: false,
    phase: "phase1",
    runId: "run-1",
    sessionId: null,
    messages: [],
    context: {},
    currentIndex: stagesFor("phase1").findIndex((s) => s.asset.asset_id === "cro"),
    intake: { asset: CRO, answers, awaitingFieldId: fieldBefore.field_id },
    subStep: null,
    activeStatus: "running",
    progress: 50,
    navStatus: "Awaiting Input",
    clientProfile: {
      client_name: "Traffic Radius",
      website_url: "https://trafficradius.com.au/",
      region: "Melbourne, Australia",
      industry: "Search Engine Optimisation",
      industry_label: "Marketing agency",
    },
    editSeed: null,
    fault: null,
  } as never);
  return fieldBefore;
}

const last = () => [...usePipelineStore.getState().messages].reverse().find((m) => m.kind === "service-choice" && !m.superseded);
const answers = () => (usePipelineStore.getState().intake?.answers ?? {}) as Record<string, unknown>;
const render = (message: PipelineMessage) => renderToStaticMarkup(<BuyerLanguageCard message={message} />);

async function main() {
  // ---------- the answers' shapes ----------
  const composed = composeOutcomeWordsAnswer(OUTCOME.clusters, "a pipeline I can forecast; less wasted spend");
  ok(
    "typed words lead, then each cluster and its two best searches",
    composed ===
      "a pipeline I can forecast, less wasted spend, more customers, how to get more customers, get more clients, rank higher on google",
    composed,
  );
  const long = composeOutcomeWordsAnswer([{ name: "x", phrases: Array.from({ length: 60 }, (_, i) => ({ keyword: `phrase number ${i} here` })) }], "", 60);
  ok("an answer stops at a whole phrase under the Phase 2 limit", long.length <= BUYER_LANGUAGE_MAX_CHARS && !long.endsWith(","), String(long.length));
  ok(
    "words to avoid are a plain comma list with no group labels",
    composeWordsToAvoidAnswer(["StudioHawk", "omnichannel synergy"], 'cheap, "growth hacking"') === 'cheap, "growth hacking", StudioHawk, omnichannel synergy',
  );

  // ---------- the walk opens the outcome card ----------
  const before = seedAtOutcome();
  usePipelineStore.getState().submitAnswer(before.choices?.[0] ?? "answered");
  await flush();
  let card = last();
  ok("the outcome field opens a suggestions card", card?.serviceScan?.buyerLanguage?.mode === "outcome", JSON.stringify(card?.serviceScan));
  ok(
    "no text question is asked for it",
    !usePipelineStore.getState().messages.some((m) => m.kind === "question" && m.field?.field_id === BUYER_LANGUAGE_OPTION.outcomeFieldId),
  );
  const asked = calls[0] ?? {};
  ok(
    "the server is asked with this session's services, landing page, region and run",
    asked.mode === "outcome" &&
      JSON.stringify(asked.services) === JSON.stringify(["SEO", "Search Engine Optimisation"]) &&
      asked.landing_url === "https://trafficradius.com.au/seo/" &&
      asked.region === "Melbourne, Australia" &&
      asked.industry === "Marketing agency" &&
      asked.run_id === "run-1",
    JSON.stringify(asked),
  );
  ok("the card is ready with the clusters", card?.serviceScan?.status === "ready");
  let html = render(card!);
  ok("clusters show with their searches and volumes", html.includes("more customers") && html.includes("how to get more customers") && html.includes("1,300"), html.slice(0, 400));
  ok("nothing starts ticked", !html.includes("checked=\"\""));
  ok("a cluster about what sets the service apart is marked", (html.match(/data-distinct/g) ?? []).length === 1 && html.includes("specific to this service"));
  ok("the outcome card has no Skip (the field is required)", !html.includes("Skip"));
  ok("the outcome card offers Regenerate", html.includes("data-regenerate") && html.includes("Regenerate"));

  // ---------- Regenerate asks again on the same card ----------
  const asksBefore = calls.length;
  usePipelineStore.getState().regenerateBuyerLanguage(card!.id);
  ok("regenerating shows the card loading, its old clusters gone", last()?.serviceScan?.status === "loading" && !last()?.serviceScan?.buyerLanguage?.result);
  await flush();
  card = last();
  ok("regenerating asks the server once more, with the same inputs", calls.length === asksBefore + 1 && calls[calls.length - 1].mode === "outcome");
  ok("the same card is ready again, not a new one", card?.serviceScan?.status === "ready" && card?.id === last()?.id);

  // ---------- confirming moves to the avoid card ----------
  usePipelineStore.getState().confirmBuyerLanguage(card!.id, ["more customers"], "more qualified leads");
  await flush();
  ok(
    "the outcome answer is the typed words then the cluster",
    answers()[BUYER_LANGUAGE_OPTION.outcomeFieldId] === "more qualified leads, more customers, how to get more customers, get more clients",
    String(answers()[BUYER_LANGUAGE_OPTION.outcomeFieldId]),
  );
  card = last();
  ok("the avoid field opens its own card next", card?.serviceScan?.buyerLanguage?.mode === "avoid" && card.serviceScan.status === "ready");
  html = render(card!);
  ok("jargon and claims are grouped", html.includes("Jargon buyers") && html.includes("Claims to avoid"));
  ok("competitor names are never shown, even on a card stored with one", !html.includes("Competitor names") && !html.includes("StudioHawk"));
  ok("jargon shows the plain words buyers search", html.includes("marketing across channels") && html.includes("390"));
  ok("the avoid card can be skipped (the field is optional)", html.includes("Skip: none"));
  ok("only the outcome card offers Regenerate", !html.includes("data-regenerate"));

  usePipelineStore.getState().skipServices(card!.id);
  await flush();
  ok("Skip answers NONE, the field's own wording", answers()[BUYER_LANGUAGE_OPTION.avoidFieldId] === "NONE");
  ok(
    "the transcript says what was skipped",
    usePipelineStore.getState().messages.some((m) => m.role === "user" && m.text === "No words to avoid."),
  );

  // ---------- an error still takes typed words ----------
  failOutcome = true;
  const again = seedAtOutcome();
  usePipelineStore.getState().submitAnswer(again.choices?.[0] ?? "answered");
  await flush();
  card = last();
  ok("a server error shows on the card", card?.serviceScan?.status === "error" && render(card!).includes("DataForSEO is not configured."));
  usePipelineStore.getState().confirmBuyerLanguage(card!.id, [], "peace of mind");
  await flush();
  ok("typed words still answer it", answers()[BUYER_LANGUAGE_OPTION.outcomeFieldId] === "peace of mind");
  usePipelineStore.getState().skipServices(card!.id);
  ok("an answered outcome card cannot then be skipped", answers()[BUYER_LANGUAGE_OPTION.outcomeFieldId] === "peace of mind");

  console.log(`buyerLanguage: ${pass} passed, ${fails.length} failed`);
  for (const f of fails) console.log("  FAIL", f);
  if (fails.length) process.exit(1);
}

void main();
