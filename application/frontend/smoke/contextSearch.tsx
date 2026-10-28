/* Context search in the intake: suggested answers from documents the run already approved.
 * Not shipped; see smoke/README.md.
 *
 * Proven here, driving the store's real actions with `fetch` stubbed:
 *   - entering a stage asks the server once for suggestions, with only the questions the walk would
 *     actually ask: never one answered from the client profile, never one already resolved from
 *     context, never a topic or file question;
 *   - a context field whose document is present but whose sub-key the regex could not find is sent
 *     restricted to that document (and its producer), so only it can answer;
 *   - the first question card carries its suggestion, the card renders it with its source and the
 *     verbatim quote, and "Use this" answers the question with it;
 *   - a failed prefill leaves the stage asking exactly as before;
 *   - the generation card says when an input was sent as its relevant sections.
 */
import { renderToStaticMarkup } from "react-dom/server";
import { ASSET_BY_ID } from "../src/data/assetCatalog";
import { prefillCandidates, suggestionValue } from "../src/lib/contextSearch";
import { ContextRetrievalNote, SuggestionOptions } from "../src/pipeline/ContextSearch";
import type { FieldSuggestion } from "../src/pipeline/pipelineApi";
import { FIELD_TO_FACT_BY_PHASE, stagesFor } from "../src/pipeline/pipelineData";
import { usePipelineStore } from "../src/pipeline/pipelineStore";

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

const OFFERS = ASSET_BY_ID.offers;
const CONTEXT = {
  competitor_analysis_offers: { assetId: "competitor_analysis_offers", label: "Competitor Analysis", text: "Rivals." },
  icp: { assetId: "icp", label: "ICP", text: "The buyer is a clinic owner." },
  cro_rewritten_copy: { assetId: "cro", label: "CRO", text: "# PART 2\n\nGrow leads." },
  // Present, but states none of the terminology labels the regex looks for.
  cro_terminology_map: { assetId: "cro", label: "CRO", text: "# PART 2\n\nGrow leads." },
};
const PROFILE = { client_name: "Traffic Radius", website_url: "https://trafficradius.com.au/", region: "Melbourne, Australia" };

type Sent = { fields: { field_id: string; kind: string; choices: string[]; keys: string[] }[] };
let prefillCalls: Sent[] = [];
let prefillFails = false;

const answerFor = (f: Sent["fields"][number]) =>
  f.kind === "boolean_flag" ? "Yes" : f.choices.length ? f.choices[0] : f.kind === "number" ? "42" : `From ICP: ${f.field_id}`;

globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
  const url = String(input);
  if (/\/api\/pipeline\/runs\/[^/]+\/prefill\/offers$/.test(url)) {
    const body = JSON.parse(String(init?.body ?? "{}")) as Sent;
    prefillCalls.push(body);
    if (prefillFails) return new Response("{}", { status: 500 });
    const suggestions: FieldSuggestion[] = body.fields.map((f) => ({
      field_id: f.field_id,
      answer: answerFor(f),
      quote: "The buyer is a clinic owner.",
      context_key: "icp",
      source_label: "Icp",
      section_path: "Buyer",
      score: 0.6,
    }));
    return new Response(JSON.stringify({ status: "ok", suggestions, notes: [], dropped: 0 }), { status: 200 });
  }
  return new Response(JSON.stringify({ detail: "not stubbed" }), { status: 404 });
}) as typeof fetch;

function seedAtOffers() {
  usePipelineStore.setState({
    started: true,
    isLoadingSession: false,
    phase: "phase1",
    runId: "run-1",
    sessionId: null,
    messages: [],
    context: CONTEXT,
    currentIndex: stagesFor("phase1").findIndex((s) => s.asset.asset_id === "offers"),
    intake: null,
    subStep: null,
    activeStatus: "idle",
    progress: 0,
    navStatus: "Ready",
    clientProfile: { ...PROFILE, industry_bucket: "marketing_agency", industry_label: "Marketing agency" },
    editSeed: null,
    fault: null,
  } as never);
}

const lastQuestion = () =>
  [...usePipelineStore.getState().messages].reverse().find((m) => m.kind === "question" && !m.superseded);

async function main() {
  // ---------- which questions are sent ----------
  const candidates = prefillCandidates(
    OFFERS,
    CONTEXT as never,
    { values: PROFILE, fieldToFact: FIELD_TO_FACT_BY_PHASE.phase1 },
    (key) => (key.startsWith("cro_") ? "cro" : undefined),
  );
  const ids = candidates.map((c) => c.field_id);
  const resolvedCtx = OFFERS.fields.filter((f) => f.context_key === "icp_*" || f.context_key === "cro_rewritten_copy");
  ok("context fields that resolve are not sent", resolvedCtx.every((f) => !ids.includes(f.field_id)), ids.join(","));
  const profileFields = OFFERS.fields.filter(
    (f) => (PROFILE as Record<string, string>)[FIELD_TO_FACT_BY_PHASE.phase1[f.field_id] ?? ""],
  );
  ok(
    "fields answered from the client profile are not sent",
    profileFields.length > 0 && profileFields.every((f) => !ids.includes(f.field_id)),
    profileFields.map((f) => f.field_id).join(","),
  );
  ok("no topic or file question is sent", candidates.every((c) => c.kind !== "headline_choice" && c.kind !== "file_attach"));
  const subKey = candidates.find((c) => c.field_id === "word_for_the_buyer");
  ok(
    "an unextractable sub-key is sent restricted to its document and producer",
    JSON.stringify(subKey?.keys) === JSON.stringify(["cro_terminology_map", "cro"]),
    JSON.stringify(subKey),
  );
  ok("plain questions search every document", candidates.filter((c) => c.field_id !== "word_for_the_buyer" && !c.keys.length).length > 0);

  // ---------- the walk carries the suggestion ----------
  seedAtOffers();
  usePipelineStore.getState().resumeStage();
  await flush();
  ok("one prefill request per stage", prefillCalls.length === 1, String(prefillCalls.length));
  const q = lastQuestion();
  ok("the first question is asked", !!q?.field, JSON.stringify(usePipelineStore.getState().messages.map((m) => m.kind)));
  ok("it carries its suggestion", !!q?.suggestion && q.suggestion.field_id === q.field?.field_id, JSON.stringify(q?.suggestion));

  if (q?.field && q.suggestion) {
    const html = renderToStaticMarkup(<SuggestionOptions message={q} />);
    ok("the card names the source and section", html.includes("ICP") && html.includes("Buyer"), html);
    ok("the card shows the quote", html.includes("The buyer is a clinic owner."));
    ok("the card offers Use this", html.includes("Use this"));

    const fieldId = q.field.field_id;
    usePipelineStore.getState().submitAnswer(suggestionValue(q.field, q.suggestion.answer));
    await flush();
    const answered = usePipelineStore.getState().intake?.answers?.[fieldId];
    ok("Use this answers the question with the suggestion", answered !== undefined && String(answered) !== "", String(answered));
    const next = lastQuestion();
    ok(
      "the next question carries its own suggestion",
      !next?.field || next.suggestion?.field_id === next.field.field_id,
      JSON.stringify(next?.suggestion),
    );
  }

  // ---------- a failure changes nothing ----------
  prefillCalls = [];
  prefillFails = true;
  seedAtOffers();
  usePipelineStore.getState().resumeStage();
  await flush();
  const plain = lastQuestion();
  ok("a failed prefill still asks", !!plain?.field && prefillCalls.length === 1);
  ok("with no suggestion", !plain?.suggestion);

  // ---------- the generation card ----------
  const note = renderToStaticMarkup(
    <ContextRetrievalNote
      assetId="sms_sequence"
      report={{
        fields: [
          { field_id: "cro_rewritten_copy", context_key: "cro", sections: 6, total_sections: 41, chars_before: 82_000, chars_after: 13_500 },
        ],
        notes: [],
      }}
    />,
  );
  ok("the card says an input was sent as its relevant sections", note.includes("6 of 41 sections") && note.includes("14k of 82k"), note);
  ok("and names the input by its label", note.includes("Brand Voice (from CRO)"), note);
  ok("nothing to say, nothing rendered", renderToStaticMarkup(<ContextRetrievalNote report={{ fields: [], notes: [] }} />) === "");

  console.log(`contextSearch: ${pass} passed, ${fails.length} failed`);
  for (const f of fails) console.log("  FAIL", f);
  if (fails.length) process.exit(1);
}

void main();
