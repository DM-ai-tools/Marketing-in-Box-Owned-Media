/* Phase 2's CRO stage — the stage that makes the phase self-sufficient. Not shipped; see
 * smoke/README.md.
 *
 * The Pillar Page prompt is a design replicator with no service input of any kind: it lays out
 * whatever copy it is handed, so that copy *is* the subject of the page. While Phase 2 had no CRO
 * stage, that copy could only be the parent Phase 1 run's rewrite of the client's headline service —
 * so a run pointed at "Meta Ads" came back a Social Media Marketing page — or a document the
 * operator wrote themselves and pasted in.
 *
 * What is worth proving here is not that the stage exists but that it does not merely move the work.
 * The value of this stage is almost entirely in what it does *not* ask: every question it asks is one
 * the operator already answered once during Phase 1. So the intake walk is run end to end against a
 * realistic inherited run, and the questions that remain are named exactly. If that list grows, this
 * fails and says what joined it.
 */
import { findNextAskable, planField } from "../src/lib/fieldResolution";
import { extractSubKey, isMapShapedContextKey } from "../src/lib/contextSubKeys";
import {
  CRO_CLIENT_SETTINGS,
  CRO_CLIENT_SETTINGS_KEY,
  PHASE2_ASSETS,
  SUB_SERVICE_FACT,
} from "../src/data/phase2Catalog";
import { SERVER_COMPOSED_CONTEXT_KEYS } from "../src/data/assetCatalog";
import {
  FIELD_TO_FACT_BY_PHASE,
  NEW_PAGE_OPTIONS,
  PHASE2_CONSTANT_ANSWERS,
  constantAnswersFor,
  stagesFor,
} from "../src/pipeline/pipelineData";
import type { ContextStore } from "../src/store/chatStore.types";

let pass = 0;
const fails: string[] = [];
const ok = (name: string, cond: boolean, extra?: string) => {
  if (cond) pass++;
  else fails.push(name + (extra ? `  ->  ${extra}` : ""));
};

const CRO = PHASE2_ASSETS.cro;
const PILLAR = PHASE2_ASSETS.pillar_page;

function entry(text: string, assetId = "cro") {
  return { assetId, label: "CRO Audit + Page Rewrite", text };
}

// --------------------------------------------------------------------------------------
// The stage is in the phase, and it is first
// --------------------------------------------------------------------------------------
{
  const order = stagesFor("phase2").map((s) => s.asset.asset_id);
  ok("phase2 runs cro", order.includes("cro"), order.join(","));
  // Stage 01 is the sub-service's own ICP; CRO follows it and still precedes everything it writes for.
  ok("icp is stage 01", order[0] === "icp", order.join(","));
  ok("cro is stage 02", order[1] === "cro", order.join(","));
  // It writes the copy the next stage designs, so anywhere else in the order is the same bug.
  ok("cro runs before pillar_page", order.indexOf("cro") < order.indexOf("pillar_page"));
}

// --------------------------------------------------------------------------------------
// What the run answers for itself
// --------------------------------------------------------------------------------------
{
  const constants = constantAnswersFor("phase2", "cro");
  const option = NEW_PAGE_OPTIONS.cro;
  ok("constants: url sentinel is the prompt's own", constants[option.urlFieldId] === option.urlAnswer, constants[option.urlFieldId]);
  ok("constants: content sentinel is the prompt's own", constants[option.contentFieldId] === option.contentAnswer);
  // The sentinel is what switches the prompt into build-from-scratch mode. Asserted on the seeded
  // value and not only on the table, because this is the value the stage actually submits.
  ok(
    "constants: both sentinels carry the marker",
    constants[option.urlFieldId].includes("NEW PAGE") && constants[option.contentFieldId].includes("NEW PAGE"),
  );
  ok("constants: page scope is the sub-service scope", constants.page_scope === "SUB-SERVICE");
  ok("constants: a fresh object each call", constantAnswersFor("phase2", "cro") !== constantAnswersFor("phase2", "cro"));
  ok("constants: phase 1 seeds nothing", Object.keys(constantAnswersFor("phase1", "cro")).length === 0);
  ok(
    "constants: every id is a real field of the stage",
    Object.keys(PHASE2_CONSTANT_ANSWERS.cro).every((id) => CRO.fields.some((f) => f.field_id === id)),
  );
}

// --------------------------------------------------------------------------------------
// What the parent run answers
// --------------------------------------------------------------------------------------
{
  ok("settings: the key is map-shaped", isMapShapedContextKey(CRO_CLIENT_SETTINGS_KEY));
  ok("settings: the key is server-composed", SERVER_COMPOSED_CONTEXT_KEYS.has(CRO_CLIENT_SETTINGS_KEY));

  for (const fieldId of CRO_CLIENT_SETTINGS) {
    const field = CRO.fields.find((f) => f.field_id === fieldId);
    ok("settings: " + fieldId + " exists on the stage", Boolean(field));
    if (!field) continue;
    ok(
      "settings: " + fieldId + " reads the settings document",
      field.source === "auto_from_context" && field.context_key === CRO_CLIENT_SETTINGS_KEY,
    );
    ok("settings: " + fieldId + " addresses itself", field.sub_key === fieldId);
    // Why the field is patched rather than rebuilt: seven of these are `enum_choice`, and
    // `QuestionWidget` renders its one-click pills on `kind`. Rebuilt as a context_reference, a
    // parent run with no settings document would drop the operator into a free-text box and ask
    // them to hand-type "2 PROFESSIONALLY REGULATED".
    ok("settings: " + fieldId + " keeps its widget", field.kind !== "context_reference", field.kind);
    // Absent settings must degrade to asking, never to submitting the stage with the field blank.
    ok("settings: " + fieldId + " falls back to asking", field.fallback === "ask_user_if_missing", String(field.fallback));
  }

  const claimTier = CRO.fields.find((f) => f.field_id === "claim_substantiation_tier");
  ok("settings: the claim tier keeps its choices", Boolean(claimTier?.choices?.length), JSON.stringify(claimTier?.choices));
}

// --------------------------------------------------------------------------------------
// The sub-service answers the one field that names a service
// --------------------------------------------------------------------------------------
{
  ok(
    "sub-service answers the target service field",
    FIELD_TO_FACT_BY_PHASE.phase2.target_service_or_sub_service === SUB_SERVICE_FACT,
    FIELD_TO_FACT_BY_PHASE.phase2.target_service_or_sub_service,
  );
}

// --------------------------------------------------------------------------------------
// Reading one setting back out of the document the server writes
// --------------------------------------------------------------------------------------
{
  // The shape `cro_settings.capture()` emits. If that renderer changes, this fails here rather than
  // quietly going back to asking every one of these questions in production.
  const doc = [
    "# CRO client settings",
    "",
    "- buyer_type = **B2B**  (Buyer Type)",
    "- claim_substantiation_tier = **1 CONSUMER-LAW**  (Claim Substantiation Tier)",
    "- word_for_the_reader = **marketing manager**  (Word for the reader)",
    "- primary_conversion_goal = **Book a free 30-minute strategy call, routed to the contact form**  (Primary Conversion Goal)",
    "",
  ].join("\n");

  const read = (subKey: string) => extractSubKey(CRO_CLIENT_SETTINGS_KEY, subKey, doc);

  ok("extract: buyer type", read("buyer_type") === "B2B", String(read("buyer_type")));
  ok("extract: the claim tier keeps its number", read("claim_substantiation_tier") === "1 CONSUMER-LAW", String(read("claim_substantiation_tier")));
  ok("extract: a two-word answer", read("word_for_the_reader") === "marketing manager", String(read("word_for_the_reader")));
  // Longer than the terminology ceiling of 120 would allow — which is the point of the per-key
  // ceiling: a conversion goal is a sentence, and throwing it away would put the question back.
  ok("extract: a sentence-long conversion goal survives", (read("primary_conversion_goal") ?? "").startsWith("Book a free 30-minute"), String(read("primary_conversion_goal")));
  // A setting the parent never answered is simply not in the document, and asking is then right.
  ok("extract: an absent setting is undefined", read("tone_of_voice") === undefined, String(read("tone_of_voice")));
}

// --------------------------------------------------------------------------------------
// The walk, end to end: what is still asked on a run that inherited everything it could
// --------------------------------------------------------------------------------------
{
  const settings = [
    "# CRO client settings",
    "",
    ...CRO_CLIENT_SETTINGS.map((id) => "- " + id + " = **inherited-" + id + "**  (x)"),
    "",
  ].join("\n");

  const context: ContextStore = {
    [CRO_CLIENT_SETTINGS_KEY]: entry(settings),
    icp: entry("# ICP\n\nthe audience", "icp"),
    competitor_analysis_cro: entry("# Competitors", "competitor_analysis_cro"),
  };

  const answers: Record<string, unknown> = constantAnswersFor("phase2", "cro");
  const knownFacts = {
    values: { client_name: "Acme", website_url: "https://acme.example/", [SUB_SERVICE_FACT]: "Meta Ads" },
    fieldToFact: FIELD_TO_FACT_BY_PHASE.phase2,
  };

  // Two kinds of stop, counted apart. `ask` is a question with an empty box under it;
  // `confirm-context` is an inherited document offered with an accept-or-replace card, which is one
  // click and carries its own answer. Conflating them would make this stage look like it asks
  // eleven questions when it asks nine.
  const asked: string[] = [];
  const confirmed: string[] = [];
  for (let guard = 0; guard < 100; guard++) {
    const result = findNextAskable(CRO, context, answers, 0, knownFacts);
    if (!result.field) break;
    (result.plan?.action === "confirm-context" ? confirmed : asked).push(result.field.field_id);
    answers[result.field.field_id] = "answered";
  }

  const inherited = CRO_CLIENT_SETTINGS.filter((id) => asked.includes(id));
  ok("walk: no inherited setting is asked", inherited.length === 0, inherited.join(","));
  ok("walk: the sub-service is not asked", !asked.includes("target_service_or_sub_service"), asked.join(","));
  ok("walk: the client name is not asked", !asked.includes("client_name"));
  ok("walk: no existing page is asked for", !asked.includes("existing_page_url") && !asked.includes("existing_page_content"));

  // The ICP (stage 01 of this run) and the run's own competitor listing are `overridable: true` on the Phase 1
  // asset — a deliberate "use ours or supply your own" stop, since an operator plausibly has better
  // research than the pipeline's. That is a choice, not a question, and Phase 2 keeps it.
  ok("walk: the ICP is offered, not asked", confirmed.includes("icp_document") && !asked.includes("icp_document"), confirmed.join(","));

  // Which ICP, though. Phase 2 builds its own at stage 01, for the sub-service, so the document on
  // this card is the one this run generated — and Phase 1's wording, "the ICP generated in this
  // run", is true here again. It must not be reworded back to claiming the parent's.
  const icp = CRO.fields.find((f) => f.field_id === "icp_document");
  ok("icp: Phase 2 writes an ICP of its own", Object.values(PHASE2_ASSETS).some((a) => a.writesContextKeys.includes("icp")));
  const icpPlan = planField(icp!, context, {}, knownFacts);
  ok(
    "icp: this run's own document is what fills it",
    icpPlan.action === "confirm-context" && icpPlan.text.includes("the audience"),
    icpPlan.action,
  );
  ok("icp: the card says this run generated it", /in this run/.test(icp?.helpText ?? ""), icp?.helpText);
  ok("icp: the card no longer points at the parent run", !/parent Phase 1 run/.test(icp?.helpText ?? ""), icp?.helpText);
  ok("icp: Phase 2's ICP asks for the sub-service", PHASE2_ASSETS.icp.fields.some((f) => f.field_id === "service_product_price_terms" && f.label === "Sub-Service + Price/Terms"));
  ok(
    "walk: the competitor listing is offered, not asked",
    confirmed.includes("competitor_analysis") && !asked.includes("competitor_analysis"),
    confirmed.join(","),
  );
  ok("walk: nothing else stops for confirmation", confirmed.length === 2, confirmed.join(","));

  // The honest remainder. Every one of these is page- or run-specific — nothing a parent run could
  // have answered for it — so the list is expected to be exactly this and no longer. A field joining
  // it is a question the operator did not have to answer before.
  const expected = [
    "parent_pillar_page_url",
    "sibling_pages_not_to_cannibalise",
    "existing_ranking_keywords_or_gsc_queries",
    "locked_offer_service_product_names",
    "locked_section_names_or_headings",
    "locked_content_blocks",
    "locked_legal_compliance_text",
    "cro_framework",
    "additional_notes_constraints",
  ];
  ok("walk: exactly the page-specific questions remain", JSON.stringify(asked) === JSON.stringify(expected), asked.join(","));
  console.log(
    "\n  Phase 2 CRO asks " + asked.length + " of " + CRO.fields.length + " fields (" + confirmed.length +
      " more are offered for confirmation): " + asked.join(", "),
  );
}

// --------------------------------------------------------------------------------------
// And the stage it exists for
// --------------------------------------------------------------------------------------
{
  const content = PILLAR.fields.find((f) => f.field_id === "improved_page_content");
  ok("pillar: the copy field survives", Boolean(content));
  ok("pillar: the copy is no longer asked for", content?.source === "auto_from_context", String(content?.source));
  ok("pillar: it reads the CRO rewrite", content?.context_key === "cro_rewritten_copy", String(content?.context_key));
  // Approved two stages ago in this same run, so stopping to ask whether to use it would be asking
  // the operator to replace something they had just approved.
  ok("pillar: it is not offered back for confirmation", !content?.overridable);

  // Walked to the end, because `improved_page_content` is the fifth field: stopping at the first
  // askable one would prove only that the four before it are still asked.
  const context: ContextStore = { cro_rewritten_copy: entry("# The Meta Ads page\n\nH1: ...") };
  const answers: Record<string, unknown> = {};
  const stops: string[] = [];
  for (let guard = 0; guard < 100; guard++) {
    const result = findNextAskable(PILLAR, context, answers, 0, {
      values: {},
      fieldToFact: FIELD_TO_FACT_BY_PHASE.phase2,
    });
    if (!result.field) break;
    stops.push(result.field.field_id);
    answers[result.field.field_id] = "answered";
  }
  ok("pillar: the walk never stops on the copy", !stops.includes("improved_page_content"), stops.join(","));
  ok(
    "pillar: the walk fills the copy from context",
    typeof answers.improved_page_content === "string" && String(answers.improved_page_content).startsWith("[[context:"),
    String(answers.improved_page_content),
  );
}

console.log("\n" + pass + " passed, " + fails.length + " failed");
if (fails.length) {
  console.log("\nFAILURES:");
  fails.forEach((f) => console.log("  x " + f));
  process.exitCode = 1;
}
