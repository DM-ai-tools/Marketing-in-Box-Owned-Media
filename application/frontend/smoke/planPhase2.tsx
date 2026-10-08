/* The Plan of Action runs after Phase 2 and covers every sub-service. Not shipped; see smoke/README.md.
 *
 * Proven here:
 *   - Phase 1 does not build it and Phase 2 builds it last,
 *   - Phase 2's plan reads the three engagement-wide documents, not this run's own CRO/funnel/ladder,
 *   - those documents are always re-read, never trusted from the session,
 *   - a sub-service finished after a plan was approved raises the "re-run the plan" notice, a
 *     sub-service finished before any plan was approved does not, and the plan's own approval never does,
 *   - a second sub-service's walk does not build a second plan.
 * The store is driven through its real `saveStage`, with `fetch` stubbed.
 */
import { PHASE2_ASSETS } from "../src/data/phase2Catalog";
import { PLAN_COMPOSED_KEYS } from "../src/data/assetCatalog";
import { findNextAskable, resolveContext } from "../src/lib/fieldResolution";
import { DEFERRED_TO_PHASE2, stagesFor } from "../src/pipeline/pipelineData";
import { usePipelineStore, planOfActionApproved } from "../src/pipeline/pipelineStore";
import type { PipelineMessage } from "../src/pipeline/pipelineStore";
import { useUiStore } from "../src/store/uiStore";

let pass = 0;
const fails: string[] = [];
const ok = (name: string, cond: boolean, extra?: string) => {
  if (cond) pass++;
  else fails.push(name + (extra ? `  ->  ${extra}` : ""));
};

console.error = () => {};
console.warn = () => {};
const requests: string[] = [];
globalThis.fetch = (async (url: string) => {
  requests.push(String(url));
  return new Response(
    JSON.stringify({ run_id: "r", asset_id: "a", version: 1, status: "approved", saved_at: new Date().toISOString() }),
    { status: 200, headers: { "Content-Type": "application/json" } },
  );
}) as typeof fetch;
const flush = async () => {
  for (let i = 0; i < 8; i++) await new Promise((r) => setTimeout(r, 0));
};

const PLAN = PHASE2_ASSETS.plan_of_action;
const PHASE2_IDS = stagesFor("phase2").map((s) => s.asset.asset_id);
const BEFORE_PLAN = PHASE2_IDS.filter((id) => id !== "plan_of_action");

function saved(assetId: string, trackId: string, id = `${trackId}-${assetId}`): PipelineMessage {
  return {
    id,
    role: "assistant",
    kind: "generation",
    assetId,
    text: "A finished document.",
    savePhase: "saved",
    phase: "phase2",
    trackId,
    createdAt: Date.now(),
  } as PipelineMessage;
}

/** Track B has approved everything before the plan except its last stage, which sits as an unsaved draft. */
function seedTrackB(extra: PipelineMessage[]) {
  const last = BEFORE_PLAN[BEFORE_PLAN.length - 1];
  const messages = [
    ...BEFORE_PLAN.slice(0, -1).map((id) => saved(id, "run-b")),
    { ...saved(last, "run-b", "draft"), savePhase: "idle" } as PipelineMessage,
    ...extra,
  ];
  const full = {
    started: true,
    isLoadingSession: false,
    phase: "phase2" as const,
    runId: "run-b",
    sessionId: null,
    messages,
    context: {},
    currentIndex: BEFORE_PLAN.length - 1,
    intake: null,
    subStep: null,
    activeStatus: null,
    progress: 90,
    navStatus: "Awaiting Review" as const,
    clientProfile: { client_name: "Acme", sub_service: "TikTok" },
    activePhase2TrackId: "run-b",
    phase2Tracks: {
      "run-a": { id: "run-a", label: "Meta Ads", runId: "run-a", sourceRunId: "run-p1", status: "complete" },
      "run-b": { id: "run-b", label: "TikTok", runId: "run-b", sourceRunId: "run-p1", status: "active" },
    },
    rerunReturnIndex: null,
    rerunReturnIntake: null,
    editSeed: null,
    fault: null,
  };
  usePipelineStore.setState(full as never);
  useUiStore.setState({ pendingPlanRerun: null });
}

async function main() {
  // ---------- where the plan sits ----------
  ok("Phase 1 does not build the plan", !stagesFor("phase1").some((s) => s.asset.asset_id === "plan_of_action"));
  ok("the plan is declared deferred", DEFERRED_TO_PHASE2.has("plan_of_action"));
  ok("Phase 2 builds the plan last", PHASE2_IDS[PHASE2_IDS.length - 1] === "plan_of_action", PHASE2_IDS.join(","));

  // ---------- what the Phase 2 plan reads ----------
  const ids = PLAN.fields.map((f) => f.field_id);
  for (const gone of ["cro_messaging_framework_optional", "existing_funnels_lead_magnets_optional", "existing_value_ladder_offers_optional"]) {
    ok(`Phase 2's plan no longer asks ${gone}`, !ids.includes(gone), ids.join(","));
  }
  const composedFields = PLAN.fields.filter((f) => f.context_key && PLAN_COMPOSED_KEYS[f.context_key]);
  ok("three plan inputs read the engagement-wide documents", composedFields.length === 3, JSON.stringify(composedFields.map((f) => f.context_key)));
  ok(
    "none of them keeps a gathered set or an icp_ wildcard",
    composedFields.every((f) => !f.context_keys && !f.context_key?.endsWith("_*") && !f.overridable),
  );
  const context = Object.fromEntries(
    Object.entries(PLAN_COMPOSED_KEYS).map(([key, label]) => [key, { assetId: key, label, text: `body of ${key}` }]),
  );
  const walked = findNextAskable(PLAN, context, {}, 0, undefined);
  ok(
    "with the documents composed, the plan's three inputs fill without a question",
    composedFields.every((f) => resolveContext(f, context)?.text === `body of ${f.context_key}`),
    JSON.stringify(walked.autoContextFields),
  );
  const missing = findNextAskable(PLAN, {}, { client_name: "x" }, 0, undefined);
  ok("with nothing built the plan asks rather than guessing", Boolean(missing.field), JSON.stringify(missing.field?.field_id));

  // ---------- the regulated-field question comes from CRO's claim tier ----------
  const settings = (tier: string) => ({
    ...context,
    cro_client_settings: {
      assetId: "cro",
      label: "CRO client settings",
      text: `- claim_substantiation_tier = **${tier}**`,
    },
  });
  // Walk the intake the way the store does, answering every question that comes up before the
  // two under test with a stand-in, and stop at the first one that is the regulation name.
  const walkIntake = (ctx: typeof context) => {
    const answers: Record<string, unknown> = {};
    let index = 0;
    for (let guard = 0; guard < 60; guard++) {
      const walk = findNextAskable(PLAN, ctx, answers, index, undefined);
      if (!walk.field) return { answers, asked: undefined };
      if (walk.field.field_id === "regulated_field_flag" || walk.field.field_id === "regulation_name") {
        return { answers, asked: walk.field.field_id };
      }
      answers[walk.field.field_id] = "x";
      index = walk.index + 1;
    }
    return { answers, asked: "runaway" };
  };
  for (const [tier, flag] of [
    ["0 GENERAL", false],
    ["1 CONSUMER-LAW", false],
    ["2 PROFESSIONALLY REGULATED", true],
    ["3 HEALTH-THERAPEUTIC", true],
  ] as const) {
    const { answers, asked } = walkIntake(settings(tier));
    ok(`tier "${tier}" answers the regulated flag as ${flag}`, answers.regulated_field_flag === flag, String(answers.regulated_field_flag));
    ok(
      `tier "${tier}" ${flag ? "goes on to ask" : "does not ask"} for the regulation name`,
      flag ? asked === "regulation_name" : asked !== "regulation_name" && asked !== "regulated_field_flag",
      String(asked),
    );
  }
  ok("with no CRO settings the regulated flag is asked, not defaulted", walkIntake(context).asked === "regulated_field_flag");

  // ---------- a sub-service finished after the plan ----------
  seedTrackB([saved("plan_of_action", "run-a")]);
  ok("a plan on another track counts", planOfActionApproved(usePipelineStore.getState().messages));
  await usePipelineStore.getState().saveStage("draft");
  await flush();
  const notice = useUiStore.getState().pendingPlanRerun;
  ok("finishing a later sub-service raises the re-run notice", notice !== null, JSON.stringify(notice));
  ok("and it names the sub-service", notice?.subService === "TikTok", JSON.stringify(notice));
  const after = usePipelineStore.getState();
  ok("the second sub-service does not build a second plan", !after.messages.some((m) => m.kind === "generation" && m.assetId === "plan_of_action" && m.trackId === "run-b"));
  ok("its walk ends instead", after.navStatus === "Ready" && after.activeStatus === null, `${after.navStatus}/${after.activeStatus}`);

  // ---------- no plan yet: nothing to be out of date ----------
  seedTrackB([]);
  await usePipelineStore.getState().saveStage("draft");
  await flush();
  ok("no notice when no plan has been approved", useUiStore.getState().pendingPlanRerun === null);
  ok("the walk goes on to the plan stage instead", usePipelineStore.getState().currentIndex === PHASE2_IDS.indexOf("plan_of_action"), String(usePipelineStore.getState().currentIndex));

  // ---------- the plan's own approval never raises it ----------
  seedTrackB([saved("plan_of_action", "run-a")]);
  usePipelineStore.setState({
    messages: [
      ...BEFORE_PLAN.map((id) => saved(id, "run-b")),
      { ...saved("plan_of_action", "run-b", "plan-draft"), savePhase: "idle" } as PipelineMessage,
      saved("plan_of_action", "run-a"),
    ],
    currentIndex: PHASE2_IDS.indexOf("plan_of_action"),
  } as never);
  await usePipelineStore.getState().saveStage("plan-draft");
  await flush();
  ok("approving the plan itself raises nothing", useUiStore.getState().pendingPlanRerun === null);

  console.log(`planPhase2: ${pass} passed, ${fails.length} failed`);
  for (const f of fails) console.log(`  FAIL ${f}`);
  if (fails.length) process.exitCode = 1;
  setTimeout(() => process.exit(process.exitCode ?? 0), 0);
}

void main();
