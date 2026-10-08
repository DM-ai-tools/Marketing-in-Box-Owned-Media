/* The start-at-a-stage gate: the readiness card and the "Start here" affordance on the pipeline.
 * Not shipped — see smoke/README.md.
 *
 * What is worth proving in a render check here is not the layout but the *offers*. Every wrong
 * answer this card can give sends an operator to do work they do not need to do, or hides work they
 * do:
 *
 *   - offering to supply a competitor list, which the stage's own scan produces — there is no stage
 *     to run for it and nothing to paste,
 *   - reporting a document as missing when the run inherited it from its Phase 1 parent,
 *   - calling a stage blocked over `unresolved_context_key`, which is a question and not a missing
 *     document, and
 *   - naming the whole four-asset chain to run when the run is one paste away.
 *
 * The readiness payloads below are the shapes `GET /runs/{id}/readiness/{asset}` really returns;
 * `tests/test_standalone_runs.py` is what pins that they are.
 *
 * The second half covers the other way into this: a run whose client has no page for the ICP's
 * service at all, where the CRO stage offers build-from-scratch or a skip to the page build.
 */
import { renderToStaticMarkup } from "react-dom/server";
import { ASSET_BY_ID } from "../src/data/assetCatalog";
import { GenerationStream } from "../src/pipeline/GenerationStream";
import { NEW_PAGE_OPTIONS, stagesFor } from "../src/pipeline/pipelineData";
import { PipelineDiagram } from "../src/pipeline/PipelineDiagram";
import { StageGateCard } from "../src/pipeline/StageGateCard";
import { usePipelineStore, type PipelineMessage, type StageGateState } from "../src/pipeline/pipelineStore";
import type { AssetReadiness, DependencyStatus } from "../src/pipeline/pipelineApi";
import { useUiStore } from "../src/store/uiStore";

let pass = 0;
const fails: string[] = [];
const ok = (name: string, cond: boolean, extra?: string) => {
  if (cond) pass++;
  else fails.push(name + (extra ? `  ->  ${extra}` : ""));
};

/** See the note above `render()` in card.tsx for why the initial state is mutated: zustand v5 reads
 * `getInitialState` for the server snapshot, so a plain `setState` is invisible to renderToString. */
function seedPipeline(patch: Record<string, unknown>) {
  usePipelineStore.setState(patch as never);
  Object.assign(usePipelineStore.getInitialState(), patch);
}
function seedUi(patch: Record<string, unknown>) {
  useUiStore.setState(patch as never);
  Object.assign(useUiStore.getInitialState(), patch);
}

function dep(over: Partial<DependencyStatus> = {}): DependencyStatus {
  return {
    field_id: "icp_document",
    label: "ICP Document",
    context_key: "icp",
    sub_key: null,
    required: true,
    fallback: "ask_user_if_missing",
    producer: "icp",
    ready: false,
    stored_under: null,
    seeded: false,
    source: null,
    from_run_id: null,
    version: null,
    chars: null,
    manual: false,
    ...over,
  };
}

function readiness(over: Partial<AssetReadiness> = {}): AssetReadiness {
  return {
    run_id: "r1",
    asset_id: "offers",
    phase: "phase1",
    blocked: true,
    dependencies: [dep()],
    prepass: "competitor_analysis_offers",
    prepass_fields: [
      {
        field_id: "competitor_analysis",
        label: "Competitor Analysis",
        context_key: "competitor_analysis_offers",
        required: false,
        ready: false,
        version: null,
      },
    ],
    run_first: ["icp"],
    seedable: ["icp"],
    writes: ["offer_ladder"],
    ...over,
  };
}

function card(gate: Partial<StageGateState>): PipelineMessage {
  const message: PipelineMessage = {
    id: "g1",
    role: "assistant",
    kind: "stage-gate",
    assetId: gate.assetId ?? "offers",
    createdAt: Date.now(),
    gate: { assetId: "offers", index: 5, status: "pending", ...gate },
  };
  // The row reads the gate back off the store by message id, so the store has to hold the card.
  seedPipeline({ phase: "phase1", messages: [message] });
  return message;
}

/* ---------- a blocked stage ---------- */
{
  const html = renderToStaticMarkup(<StageGateCard message={card({ readiness: readiness() })} />);
  ok("blocked: names the stage", html.includes("Value Ladder") || html.includes("Offer"), html.slice(0, 400));
  ok("blocked: says one document is missing", /1 document is missing/.test(html), html.slice(0, 900));
  ok("blocked: names the producing stage", html.includes("Produced by"), html.slice(0, 1200));
  ok("blocked: offers to run it first", html.includes("Run "), html.slice(0, 1500));
  ok("blocked: still allows starting anyway", html.includes("Start anyway"), html.slice(0, 1500));
  ok("blocked: offers a paste", html.includes("Paste it"), html.slice(0, 1500));
}

/* ---------- the competitor prepass is a step, never an offer ---------- */
{
  const html = renderToStaticMarkup(<StageGateCard message={card({ readiness: readiness() })} />);
  ok("prepass: shown as automatic", html.includes("Competitor scan"), html.slice(0, 1500));
  ok("prepass: says nothing to supply", /nothing to supply or run first/.test(html), html.slice(0, 2000));
  // The row list is built from `dependencies` only. A prepass field leaking into it would carry a
  // "Paste it" button for a document no operator can produce.
  ok(
    "prepass: not offered as a dependency row",
    !html.includes("Competitor Analysis</span>"),
    html.slice(0, 2000),
  );
}

{
  const already = readiness({
    prepass_fields: [
      {
        field_id: "competitor_analysis",
        label: "Competitor Analysis",
        context_key: "competitor_analysis_offers",
        required: false,
        ready: true,
        version: 1,
      },
    ],
  });
  const html = renderToStaticMarkup(<StageGateCard message={card({ readiness: already })} />);
  ok("prepass: reuse is stated when the run has one", /reuses it rather than searching again/.test(html), html.slice(0, 2000));
}

/* ---------- a ready stage ---------- */
{
  const ready = readiness({
    blocked: false,
    run_first: [],
    dependencies: [dep({ ready: true, stored_under: "icp", source: "this_run", version: 1, chars: 4200 })],
  });
  const html = renderToStaticMarkup(<StageGateCard message={card({ readiness: ready })} />);
  ok("ready: says everything is here", /Everything this stage needs is in the run/.test(html), html.slice(0, 900));
  ok("ready: primary button starts the stage", html.includes("<button") && !html.includes("Start anyway"), html.slice(0, 1600));
  ok("ready: no run-first offer", !html.includes("first</button>"), html.slice(0, 1600));
  ok("ready: says where it came from", /Approved in this run/.test(html), html.slice(0, 1400));
  ok("ready: still offers a replacement", html.includes("Use my own instead"), html.slice(0, 1600));
}

/* ---------- where a ready document came from is stated, not implied ---------- */
{
  const inherited = readiness({
    blocked: false,
    run_first: [],
    dependencies: [
      dep({ ready: true, stored_under: "icp", source: "inherited", from_run_id: "r0", version: 2, chars: 100 }),
    ],
  });
  const html = renderToStaticMarkup(<StageGateCard message={card({ readiness: inherited })} />);
  ok("inherited: labelled as the parent run's", /Inherited from the Phase 1 run/.test(html), html.slice(0, 1400));
}

{
  const seeded = readiness({
    blocked: false,
    run_first: [],
    dependencies: [
      dep({ ready: true, seeded: true, stored_under: "icp", source: "this_run", version: 1, chars: 90 }),
    ],
  });
  const html = renderToStaticMarkup(<StageGateCard message={card({ readiness: seeded })} />);
  ok("seeded: labelled as supplied by the operator", /a document you supplied/.test(html), html.slice(0, 1400));
}

/* ---------- a document no asset writes ---------- */
{
  const orphan = readiness({
    asset_id: "sms_sequence",
    blocked: false,
    run_first: [],
    dependencies: [
      dep({
        field_id: "email_sequence_copy",
        label: "Email Sequence Copy",
        context_key: "email_sequence_copy",
        required: false,
        producer: null,
      }),
    ],
    seedable: ["email_sequence_copy"],
  });
  const html = renderToStaticMarkup(
    <StageGateCard message={card({ assetId: "sms_sequence", index: 13, readiness: orphan })} />,
  );
  ok("no producer: says nothing produces it", /No stage produces this/.test(html), html.slice(0, 1500));
  ok("no producer: never offers to run one", !/Run .*first/.test(html), html.slice(0, 1600));
  ok("no producer: still offers a paste", html.includes("Paste it"), html.slice(0, 1600));
}

/* ---------- an always-asked field is not a missing document ---------- */
{
  const manual = readiness({
    asset_id: "funnel_hub_media",
    blocked: false,
    run_first: [],
    dependencies: [
      dep({
        field_id: "reference_folder_knowledge_base",
        label: "Reference Folder",
        context_key: "unresolved_context_key",
        required: true,
        producer: null,
        manual: true,
      }),
    ],
    prepass: null,
    prepass_fields: [],
    seedable: [],
  });
  const html = renderToStaticMarkup(
    <StageGateCard message={card({ assetId: "funnel_hub_media", index: 4, readiness: manual })} />,
  );
  ok("manual: not listed as a document", !html.includes("Reference Folder"), html.slice(0, 1500));
  ok("manual: stage is startable", html.includes("<button") && !html.includes("Start anyway"), html.slice(0, 1500));
  ok("manual: no upstream requirement claimed", /builds from its own questions/.test(html), html.slice(0, 900));
}

/* ---------- the full chain is shown but only its head is offered ---------- */
{
  const chain = readiness({
    asset_id: "sms_sequence",
    run_first: ["icp", "cro", "pillar_page", "funnel"],
    dependencies: [dep(), dep({ field_id: "funnel_stages", label: "Funnel Stages", context_key: "funnel_stages", producer: "funnel" })],
  });
  const html = renderToStaticMarkup(
    <StageGateCard message={card({ assetId: "sms_sequence", index: 13, readiness: chain })} />,
  );
  ok("chain: full order shown", /Full chain if you run rather than paste/.test(html), html.slice(0, 2500));
  // Offering all four invites picking the last, which is itself still blocked.
  const offers = html.match(/Run [^<]*first/g) ?? [];
  ok("chain: only the head is offered", offers.length === 1, JSON.stringify(offers));
}

/* ---------- loading and error states ---------- */
{
  const loading = renderToStaticMarkup(<StageGateCard message={card({ status: "loading" })} />);
  ok("loading: says what it is doing", /Checking what this stage still needs/.test(loading), loading.slice(0, 600));
  // "Start at Stage 06" is in the heading, so absence of the *word* proves nothing — what must
  // not be there yet is anything to press.
  ok("loading: offers nothing yet", !loading.includes("<button"), loading.slice(0, 600));

  const failed = renderToStaticMarkup(
    <StageGateCard message={card({ status: "error", error: "Network is down." })} />,
  );
  ok("error: shows the reason", failed.includes("Network is down."), failed.slice(0, 600));
  ok("error: offers a retry", failed.includes("Check again"), failed.slice(0, 700));
}

/* ---------- a decided gate stops offering ---------- */
{
  const entered = renderToStaticMarkup(
    <StageGateCard message={card({ status: "entered", readiness: readiness() })} />,
  );
  // The summary prose still says "paste it below" — it is the record of what was decided. It is
  // the buttons that must be gone.
  ok("entered: no buttons left", !entered.includes("<button"), entered.slice(0, 1600));
  ok("entered: reads as history", entered.includes("Decided."), entered.slice(0, 1800));
}

/* ---------- the pipeline pane's own affordance ---------- */
{
  seedUi({ hintsOn: true });
  seedPipeline({ phase: "phase1", currentIndex: 2, activeStatus: null, progress: 0, navStatus: "Ready", messages: [] });
  const html = renderToStaticMarkup(<PipelineDiagram />);
  // The arrow is what distinguishes the button from the hint that explains it.
  const starts = html.match(/Start here →/g) ?? [];
  // 14 stages (the Plan of Action is written after Phase 2), minus the one under the cursor. The
  // two the cursor has passed are *not* excluded: nothing here has been approved, so they were
  // skipped rather than done, and being able to go back into them is the point.
  ok("diagram: every stage but the current one offers a start", starts.length === 13, String(starts.length));
  ok("diagram: nothing claims to be saved", !html.includes("✓ Saved"), html.slice(0, 1500));
  ok("diagram: progress counts nothing done", html.includes("0 / 14 Complete"), html.slice(-900));
  ok("diagram: the hint explains it", html.includes("out of order"), html.slice(0, 1200));
}

{
  // The bug this guards: `done` used to be `index < currentIndex`, so a run that jumped to stage 09
  // showed eight stages it never built as "✓ Saved", with the progress bar agreeing.
  const saved = (assetId: string, id: string): PipelineMessage => ({
    id,
    role: "assistant",
    kind: "generation",
    assetId,
    text: "A finished document.",
    savePhase: "saved",
    phase: "phase1",
    createdAt: Date.now(),
  });
  seedPipeline({
    phase: "phase1",
    currentIndex: 8,
    activeStatus: null,
    progress: 0,
    navStatus: "Ready",
    messages: [saved("icp", "s1")],
  });
  const html = renderToStaticMarkup(<PipelineDiagram />);
  ok("diagram: only the approved stage is done", (html.match(/✓ Saved/g) ?? []).length === 1, String((html.match(/✓ Saved/g) ?? []).length));
  ok("diagram: progress counts approvals, not the cursor", html.includes("1 / 14 Complete"), html.slice(-900));
  // 14 stages, minus the approved one (done, no offer) and the one under the cursor.
  ok("diagram: the skipped-over stages are still enterable", (html.match(/Start here →/g) ?? []).length === 12, String((html.match(/Start here →/g) ?? []).length));
}

{
  seedPipeline({ phase: "phase1", currentIndex: 2, activeStatus: "running", progress: 40, navStatus: "Generating…", messages: [] });
  const html = renderToStaticMarkup(<PipelineDiagram />);
  ok(
    "diagram: nothing to jump to mid-generation",
    !html.includes("Start here →"),
    String((html.match(/Start here →/g) ?? []).length),
  );
}

/* ---------- "there is no page for this service yet" ----------
 *
 * Mounted through the real `GenerationStream` rather than by rendering the option block directly,
 * because what needs proving is that the offer reaches the *active* question and nowhere else: on a
 * superseded copy, on an answered one, or while the field is being edited it must not be there. */
function renderStream(messages: PipelineMessage[], intake: unknown): string {
  const seed = { started: true, isLoadingSession: false, phase: "phase1" as const, messages, currentIndex: 1, intake };
  seedPipeline(seed as Record<string, unknown>);
  return renderToStaticMarkup(<GenerationStream />);
}

const CRO = ASSET_BY_ID.cro;
const URL_FIELD = CRO.fields.find((f) => f.field_id === "existing_page_url");

function pageQuestion(over: Partial<PipelineMessage> = {}): PipelineMessage {
  return {
    id: "q1",
    role: "assistant",
    kind: "question",
    assetId: "cro",
    field: URL_FIELD,
    pageSource: { assetId: "cro", subject: "Social Media Marketing for e-commerce" },
    createdAt: Date.now(),
    ...over,
  };
}

const awaitingUrl = { asset: CRO, answers: {}, awaitingFieldId: "existing_page_url" };

{
  ok("fixture: the CRO page field exists", !!URL_FIELD, String(URL_FIELD?.field_id));
  const html = renderStream([pageQuestion()], awaitingUrl);
  ok("no-page: the offer is on the active question", html.includes("build it from scratch"), html.slice(-1400));
  ok("no-page: names the service it would be for", html.includes("Social Media Marketing for e-commerce"), html.slice(-1400));
  ok("no-page: offers the skip", html.includes("Skip to"), html.slice(-1400));
  // The specific mistake this exists to prevent, said out loud rather than left to be inferred.
  ok("no-page: warns against nominating the home page", html.includes("home page"), html.slice(-1600));
  ok(
    "no-page: says build-from-scratch keeps the documents the next stage needs",
    html.includes("terminology map"),
    html.slice(-1600),
  );
  // Typing a URL must stay the one-action path: the normal answer line is still there.
  ok("no-page: the ordinary answer path is untouched", html.includes("Type your answer below"), html.slice(-1800));
}

{
  const answered = renderStream([pageQuestion({ answered: true })], { ...awaitingUrl, awaitingFieldId: null });
  ok("no-page: gone once answered", !answered.includes("build it from scratch"), answered.slice(-900));

  const superseded = renderStream([pageQuestion({ superseded: true })], awaitingUrl);
  ok("no-page: gone on a superseded copy", !superseded.includes("build it from scratch"), superseded.slice(-900));

  // An edit is a correction to an answer that exists. Re-offering "there is no page" there would
  // put a mode switch behind a button labelled as a typo fix.
  const editing = renderStream([pageQuestion({ editing: true })], awaitingUrl);
  ok("no-page: not offered mid-edit", !editing.includes("build it from scratch"), editing.slice(-900));
}

{
  // Every other question is unaffected — the offer is keyed off the field, not bolted onto the card.
  const other = CRO.fields.find((f) => f.field_id === "primary_conversion_goal");
  const html = renderStream(
    [{ id: "q2", role: "assistant", kind: "question", assetId: "cro", field: other, createdAt: Date.now() }],
    { ...awaitingUrl, awaitingFieldId: "primary_conversion_goal" },
  );
  ok("no-page: not offered on other fields", !html.includes("build it from scratch"), html.slice(-900));
}

{
  // The sentinels the buttons write have to be the ones the prompt switches on. Asserted here as
  // well as in `backend/tests/test_new_page_mode.py` because this is the copy the click uses.
  const option = NEW_PAGE_OPTIONS.cro;
  ok("no-page: url sentinel carries the marker", option.urlAnswer.includes("NEW PAGE"), option.urlAnswer);
  ok("no-page: content sentinel carries the marker", option.contentAnswer.includes("NEW PAGE"), option.contentAnswer);
  ok("no-page: both fields are real CRO fields", CRO.fields.some((f) => f.field_id === option.urlFieldId) && CRO.fields.some((f) => f.field_id === option.contentFieldId));
  ok("no-page: the content field comes after the url field", CRO.fields.findIndex((f) => f.field_id === option.contentFieldId) > CRO.fields.findIndex((f) => f.field_id === option.urlFieldId));
  ok("no-page: the stage offered instead is in this phase", stagesFor("phase1").some((s) => s.asset.asset_id === option.insteadAssetId));
}

console.log(`\n${pass} passed, ${fails.length} failed`);
if (fails.length) {
  console.log("\nFAILURES:");
  fails.forEach((f) => console.log("  x " + f));
  process.exitCode = 1;
}
