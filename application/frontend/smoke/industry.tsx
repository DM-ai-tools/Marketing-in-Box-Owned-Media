/* The client's industry and the stage advisories. Not shipped; see smoke/README.md.
 *
 * Driven through the store's real actions with `fetch` stubbed, because the decisions that matter
 * are all in the store:
 *   - the end of ICP intake stops for the industry before anything generates,
 *   - a confident guess becomes a confirm card and a missing one becomes a text box,
 *   - the answer is filed on the run, with the right source,
 *   - the forward walk stops in front of a stage the industry rarely needs and offers the skip,
 *   - a skip passes the stage over without claiming it was built,
 *   - and the default bucket changes nothing at all.
 * The render checks prove each card paints the words the operator acts on.
 */
import { renderToStaticMarkup } from "react-dom/server";
import { ASSET_BY_ID } from "../src/data/assetCatalog";
import { INDUSTRY_PROFILES } from "../src/data/industryProfiles";
import { GenerationStream } from "../src/pipeline/GenerationStream";
import { PipelineDiagram } from "../src/pipeline/PipelineDiagram";
import {
  INDUSTRY_BUCKET_FACT,
  INDUSTRY_CONFIRM_FIELD,
  INDUSTRY_LABEL_FACT,
  INDUSTRY_TEXT_FIELD,
  stagesFor,
} from "../src/pipeline/pipelineData";
import {
  approvedAssetIds,
  skippedAssetIds,
  usePipelineStore,
  type PipelineMessage,
} from "../src/pipeline/pipelineStore";
import { useUiStore } from "../src/store/uiStore";

let pass = 0;
const fails: string[] = [];
const ok = (name: string, cond: boolean, extra?: string) => {
  if (cond) pass++;
  else fails.push(name + (extra ? `  ->  ${extra}` : ""));
};

// Autosave and the stubbed-out generation both log on failure. Neither is under test here.
console.error = () => {};
console.warn = () => {};

// --------------------------------------------------------------------------------------
// fetch
// --------------------------------------------------------------------------------------

interface Call {
  url: string;
  method: string;
  body: Record<string, unknown> | null;
}
const calls: Call[] = [];
let inferReply: (body: Record<string, unknown>) => unknown = () => ({ profile: null });
let storedIndustry: unknown = null;

const json = (data: unknown, status = 200) =>
  new Response(JSON.stringify(data), { status, headers: { "Content-Type": "application/json" } });

globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
  const url = String(input);
  const method = init?.method ?? "GET";
  const body = typeof init?.body === "string" ? (JSON.parse(init.body) as Record<string, unknown>) : null;
  calls.push({ url, method, body });

  if (url.endsWith("/api/pipeline/industry/infer")) return json(inferReply(body ?? {}));
  if (/\/api\/pipeline\/runs\/[^/]+\/industry$/.test(url)) {
    if (method === "POST") {
      const profile = { ...body, bucket_label: INDUSTRY_PROFILES[body?.bucket as keyof typeof INDUSTRY_PROFILES]?.label };
      return json({ run_id: "run-1", profile, version: 1 });
    }
    return storedIndustry ? json({ run_id: "run-1", profile: storedIndustry }) : json({ detail: "none" }, 404);
  }
  if (/\/stages\/[^/]+\/save$/.test(url)) {
    return json({ run_id: "run-1", asset_id: "x", version: 1, status: "approved", saved_at: "" });
  }
  return json({ detail: "not stubbed" }, 500);
}) as typeof fetch;

const flush = async (rounds = 8) => {
  for (let i = 0; i < rounds; i++) await new Promise((r) => setTimeout(r, 0));
};

// --------------------------------------------------------------------------------------
// state
// --------------------------------------------------------------------------------------

/** See the note above `render()` in card.tsx for why the initial state is mutated. */
function seed(patch: Record<string, unknown>) {
  const base = {
    started: true,
    isLoadingSession: false,
    phase: "phase1" as const,
    runId: "run-1",
    sessionId: null,
    messages: [],
    context: {},
    currentIndex: 0,
    intake: null,
    subStep: null,
    activeStatus: null,
    progress: 0,
    navStatus: "Ready" as const,
    clientProfile: {},
    rerunReturnIndex: null,
    rerunReturnIntake: null,
    editSeed: null,
    fault: null,
  };
  const full = { ...base, ...patch };
  usePipelineStore.setState(full as never);
  Object.assign(usePipelineStore.getInitialState(), full);
  calls.length = 0;
}
useUiStore.setState({ workTab: "transcript" } as never);

/** Server rendering reads the store's initial state, so the live state is copied over first. */
const sync = () => Object.assign(usePipelineStore.getInitialState(), usePipelineStore.getState());
const html = () => (sync(), renderToStaticMarkup(<GenerationStream />));
const diagram = () => (sync(), renderToStaticMarkup(<PipelineDiagram />));
const msgs = () => usePipelineStore.getState().messages;
const lastQuestion = () => [...msgs()].reverse().find((m) => m.kind === "question");

const ICP = ASSET_BY_ID.icp;

/** An ICP intake with every field answered but the optional last one. */
function icpAnswers(): Record<string, unknown> {
  const answers: Record<string, unknown> = {};
  for (const f of ICP.fields) {
    if (f.field_id === "notes_constraints_optional") continue;
    answers[f.field_id] = f.choices?.[0] ?? `answer for ${f.field_id}`;
  }
  answers.company_name = "Arg Finance";
  answers.website_url = "argfinance.com.au";
  answers.industry = "Construction";
  return answers;
}

function atEndOfIcp() {
  seed({
    intake: { asset: ICP, answers: icpAnswers(), awaitingFieldId: "notes_constraints_optional" },
    activeStatus: "running",
    navStatus: "Awaiting Input",
  });
}

const guess = {
  bucket: "regulated_finance",
  bucket_label: "Regulated Finance",
  label: "Mortgage and business lending",
  source: "inferred_confirmed",
  confidence: 0.9,
  rationale: "The site offers home and business loans.",
};

async function main() {
  // --------------------------------------------------------------------------------------
  // 1. A confident guess: confirm card, then the run carries on
  // --------------------------------------------------------------------------------------
  atEndOfIcp();
  inferReply = () => ({ profile: guess });
  usePipelineStore.getState().submitAnswer("none");
  await flush();

  const infer = calls.find((c) => c.url.endsWith("/industry/infer"));
  ok("end of ICP intake asks the classifier", !!infer);
  const signals = (infer?.body?.answers ?? {}) as Record<string, string>;
  ok("classifier is sent the client's name and site", signals.company_name === "Arg Finance" && signals.website_url === "argfinance.com.au");
  ok("classifier is sent the ICP target industry as a hint", signals.industry === "Construction");
  ok("nothing generates before the industry is settled", !msgs().some((m) => m.kind === "generation"));

  const confirm = lastQuestion();
  ok("a guess becomes the confirm question", confirm?.field?.field_id === INDUSTRY_CONFIRM_FIELD.field_id);
  ok("the guess rides on the card", confirm?.industryGuess?.bucket === "regulated_finance");
  ok("intake waits on the confirm field", usePipelineStore.getState().intake?.awaitingFieldId === INDUSTRY_CONFIRM_FIELD.field_id);
  const confirmHtml = html();
  ok("card names the guess", confirmHtml.includes("Looks like Mortgage and business lending (Regulated Finance)"));
  ok("card offers the one-click confirm", confirmHtml.includes("Yes, that&#x27;s right"));
  ok("card shows why", confirmHtml.includes("The site offers home and business loans."));
  ok("card still offers every other bucket", confirmHtml.includes("Healthcare") && confirmHtml.includes("Other: specify"));

  usePipelineStore.getState().submitAnswer("Regulated Finance");
  await flush();
  const saved = calls.find((c) => c.method === "POST" && /\/runs\/run-1\/industry$/.test(c.url));
  ok("confirmed industry is filed on the run", !!saved);
  ok("accepted guess is recorded as inferred_confirmed", saved?.body?.source === "inferred_confirmed", String(saved?.body?.source));
  ok("accepted guess keeps the classifier's own label", saved?.body?.label === "Mortgage and business lending");
  const profile = usePipelineStore.getState().clientProfile;
  ok("bucket lands on the run profile", profile[INDUSTRY_BUCKET_FACT] === "regulated_finance");
  ok("label lands on the run profile", profile[INDUSTRY_LABEL_FACT] === "Mortgage and business lending");
  ok("the paused ICP stage then generates", msgs().some((m) => m.kind === "generation" && m.assetId === "icp"));
  const announce = msgs().find((m) => m.kind === "text" && m.text?.startsWith("Filed the client's industry as"));
  ok("the answer is announced with an edit chip", !!announce?.editableFields?.some((f) => f.fieldId === INDUSTRY_CONFIRM_FIELD.field_id));
  ok("the announcement names the stages it will advise against", !!announce?.text?.includes("Webinar"), announce?.text);

  // --------------------------------------------------------------------------------------
  // 2. Picking a different bucket than the guess
  // --------------------------------------------------------------------------------------
  atEndOfIcp();
  inferReply = () => ({ profile: guess });
  usePipelineStore.getState().submitAnswer("none");
  await flush();
  usePipelineStore.getState().submitAnswer("Legal & Professional Services");
  await flush();
  const picked = calls.find((c) => c.method === "POST" && /\/industry$/.test(c.url) && !c.url.endsWith("/infer"));
  ok("a different pill is recorded as operator_picked", picked?.body?.source === "operator_picked" && picked?.body?.bucket === "legal_professional");

  // --------------------------------------------------------------------------------------
  // 3. No confident guess: the text box, mapped server-side
  // --------------------------------------------------------------------------------------
  atEndOfIcp();
  inferReply = (body) =>
    body.typed ? { profile: { ...guess, label: String(body.typed), source: "operator_typed" } } : { profile: null };
  usePipelineStore.getState().submitAnswer("none");
  await flush();
  ok("no guess asks outright in a text field", lastQuestion()?.field?.field_id === INDUSTRY_TEXT_FIELD.field_id);
  ok("the text question renders", html().includes("What industry is the client in?"));
  usePipelineStore.getState().submitFreeform("Mortgage broking");
  await flush();
  const typedInfer = calls.filter((c) => c.url.endsWith("/infer")).pop();
  ok("typed answer is mapped by the classifier", typedInfer?.body?.typed === "Mortgage broking");
  const typedSave = calls.find((c) => c.method === "POST" && /\/runs\/run-1\/industry$/.test(c.url));
  ok("typed answer keeps the operator's words", typedSave?.body?.label === "Mortgage broking" && typedSave?.body?.source === "operator_typed");
  ok("typed answer takes the mapped bucket", typedSave?.body?.bucket === "regulated_finance");

  // "Other: specify" on the confirm card is the same path, with the choice's prefix stripped.
  atEndOfIcp();
  inferReply = (body) => (body.typed ? { profile: { ...guess, bucket: "general", label: String(body.typed) } } : { profile: guess });
  usePipelineStore.getState().submitAnswer("none");
  await flush();
  usePipelineStore.getState().submitAnswer("Other: Veterinary clinics");
  await flush();
  const otherInfer = calls.filter((c) => c.url.endsWith("/infer")).pop();
  ok('"Other: specify" sends only the typed words', otherInfer?.body?.typed === "Veterinary clinics", String(otherInfer?.body?.typed));
  const otherSave = calls.find((c) => c.method === "POST" && /\/runs\/run-1\/industry$/.test(c.url));
  ok('"Other" falls to general under the typed label', otherSave?.body?.bucket === "general" && otherSave?.body?.label === "Veterinary clinics");

  // --------------------------------------------------------------------------------------
  // 4. An industry already on the run is reused, not asked
  // --------------------------------------------------------------------------------------
  atEndOfIcp();
  storedIndustry = { ...guess, source: "operator_picked" };
  usePipelineStore.getState().submitAnswer("none");
  await flush();
  storedIndustry = null;
  ok("a stored industry is not asked again", !msgs().some((m) => m.kind === "question" && m.field?.field_id.startsWith("__industry")));
  ok("a stored industry skips the classifier", !calls.some((c) => c.url.endsWith("/infer")));
  ok("a stored industry still lands on the profile", usePipelineStore.getState().clientProfile[INDUSTRY_BUCKET_FACT] === "regulated_finance");

  // --------------------------------------------------------------------------------------
  // 5. The advisory on the forward walk
  // --------------------------------------------------------------------------------------
  const stages = stagesFor("phase1");
  const blogIndex = stages.findIndex((s) => s.asset.asset_id === "blog");
  const beforeBlog = stages.slice(0, blogIndex).map((s) => s.asset.asset_id);
  const savedCards = (ids: string[]): PipelineMessage[] =>
    ids.map((id, i) => ({
      id: `s${i}`,
      role: "assistant",
      kind: "generation",
      assetId: id,
      text: `${id} output`,
      savePhase: i === ids.length - 1 ? "idle" : "saved",
      phase: "phase1",
      createdAt: i,
    }));

  const approveLastBeforeBlog = async (bucket: string) => {
    seed({
      messages: savedCards(beforeBlog),
      currentIndex: blogIndex - 1,
      activeStatus: "hitl",
      clientProfile: { client_name: "Arg Finance", [INDUSTRY_BUCKET_FACT]: bucket, [INDUSTRY_LABEL_FACT]: INDUSTRY_PROFILES[bucket as keyof typeof INDUSTRY_PROFILES].label },
    });
    await usePipelineStore.getState().saveStage(`s${beforeBlog.length - 1}`);
    await flush();
  };

  await approveLastBeforeBlog("regulated_finance");
  const advisory = msgs().find((m) => m.kind === "stage-advisory");
  ok("finance: the walk stops in front of Blog", advisory?.advisory?.assetId === "blog" && advisory.advisory.status === "pending");
  ok("finance: Blog is not started behind the advisory", usePipelineStore.getState().intake === null);
  ok("finance: the cursor sits on Blog", usePipelineStore.getState().currentIndex === blogIndex);
  const advisoryHtml = html();
  ok("advisory card offers the skip first", advisoryHtml.indexOf(`Skip ${ASSET_BY_ID.blog.label}`) >= 0 && advisoryHtml.indexOf(`Skip ${ASSET_BY_ID.blog.label}`) < advisoryHtml.indexOf("Build it anyway"));
  ok("advisory card says nothing is removed", advisoryHtml.includes("Skipping removes nothing"));
  ok("diagram tags Blog as optional", diagram().includes("Optional for Regulated Finance"));

  usePipelineStore.getState().skipAdvisedStage(advisory!.id);
  await flush();
  const s = usePipelineStore.getState();
  ok("skip records Blog as skipped", skippedAssetIds(s.messages, "phase1").has("blog"));
  ok("skip does not claim Blog was built", !approvedAssetIds(s.messages, "phase1").has("blog"));
  ok("skip moves on to the next stage", s.currentIndex === blogIndex + 1, String(s.currentIndex));
  ok("diagram shows Blog as Skipped", diagram().includes("Skipped"));

  // Build it anyway.
  await approveLastBeforeBlog("regulated_finance");
  const again = msgs().find((m) => m.kind === "stage-advisory");
  usePipelineStore.getState().buildAdvisedStage(again!.id);
  await flush();
  const b = usePipelineStore.getState();
  ok("build anyway enters Blog", b.currentIndex === blogIndex && !skippedAssetIds(b.messages, "phase1").has("blog"));
  ok("build anyway marks the card", msgs().find((m) => m.id === again!.id)?.advisory?.status === "building");

  // The default bucket changes nothing.
  await approveLastBeforeBlog("marketing_agency");
  ok("agency: no advisory at all", !msgs().some((m) => m.kind === "stage-advisory"));
  ok("agency: Blog starts straight away", usePipelineStore.getState().currentIndex === blogIndex);
  ok("agency: diagram carries no optional tag", !diagram().includes("Optional for"));

  // --------------------------------------------------------------------------------------
  // 6. Changing the industry between stages
  // --------------------------------------------------------------------------------------
  seed({
    messages: savedCards(["icp", "cro"]).map((m) => ({ ...m, savePhase: "saved" as const })),
    currentIndex: 2,
    clientProfile: { [INDUSTRY_BUCKET_FACT]: "regulated_finance", [INDUSTRY_LABEL_FACT]: "Regulated Finance" },
  });
  usePipelineStore.getState().editField(INDUSTRY_CONFIRM_FIELD.field_id);
  const reask = lastQuestion();
  ok("change re-asks on the confirm card", reask?.field?.field_id === INDUSTRY_CONFIRM_FIELD.field_id && !!reask.industryStandalone);
  ok("change carries no stale guess", !reask?.industryGuess);
  usePipelineStore.getState().submitAnswer("Healthcare");
  await flush();
  const after = usePipelineStore.getState();
  ok("change files the new bucket", after.clientProfile[INDUSTRY_BUCKET_FACT] === "healthcare");
  ok("a standalone change resumes nothing", after.intake === null && !msgs().some((m) => m.kind === "generation" && m.assetId === "pillar_page"));
  ok("a change is announced as a change", msgs().some((m) => m.text?.startsWith("Client's industry changed to")));

  console.log(`industry: ${pass} passed, ${fails.length} failed`);
  for (const f of fails) console.log(`  FAIL ${f}`);
  if (fails.length) process.exitCode = 1;
  // Autosave timers would otherwise hold the process open.
  setTimeout(() => process.exit(process.exitCode ?? 0), 0);
}

void main();
