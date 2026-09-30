/* Clearing a phase, and the warning in front of every deletion. Not shipped — see smoke/README.md.
 *
 * The thing worth pinning here is the **scope**. A chat carries both legs, and the ordinary shape of
 * the work is a finished Phase 1 run with a Phase 2 leg on top of it — so "clear" has to mean the
 * leg on screen and nothing else. A version of this that cleared the chat would look right in every
 * screenshot and would quietly throw away fifteen approved assets the moment somebody restarted
 * their sub-service. Every assertion under "what clearing resets" is about that boundary.
 *
 * Three halves, for the three ways it can go wrong:
 *
 *   - `selectCanClearPhase` / `selectOtherPhaseHasWork` are pure functions, so they are called
 *     directly. They decide whether the control appears at all and what it is allowed to call
 *     itself — a button reading "Clear chat" that clears one phase is the mislabel this file exists
 *     to prevent.
 *   - `clearPhase` is called against a seeded store and its result asserted, leg by leg.
 *   - the render checks prove the control is there, carries the right label, and only asks on the
 *     first click.
 *
 * The dialog's own copy is local component state and is not reachable from a first paint, so the
 * `ConfirmDialog` assertions below go through `ChatDeleteDialog`, which is store-driven.
 *
 * Network is stubbed off: clearing a leg restarts it, and restarting reaches for the run and its
 * context. This file is about what the store holds the moment the clear lands, which `clearPhase`
 * sets synchronously before any of that.
 */
import { renderToStaticMarkup } from "react-dom/server";
import { ChatDeleteDialog } from "../src/pipeline/ChatDeleteDialog";
import { TopNav } from "../src/pipeline/TopNav";
import { ASSET_BY_ID } from "../src/data/assetCatalog";
import {
  selectCanClearPhase,
  selectOtherPhaseHasWork,
  usePipelineStore,
  type PipelineMessage,
} from "../src/pipeline/pipelineStore";
import { useChatSessionsStore } from "../src/store/chatSessionsStore";
import { useUiStore } from "../src/store/uiStore";

globalThis.fetch = (() => Promise.reject(new Error("no network under a render check"))) as never;
// The store's own failure paths are not what this file is about, and a floating rejection from the
// restart would otherwise take the process down before the assertions are printed. Its logging goes
// with it: "Failed to create the Phase 2 run" is the stub doing its job, and printing it four times
// buries the line that matters.
process.on("unhandledRejection", () => {});
console.error = () => {};

let pass = 0;
const fails: string[] = [];
const ok = (name: string, cond: boolean, extra?: string) => {
  if (cond) pass++;
  else fails.push(name + (extra ? `  ->  ${extra}` : ""));
};

/** See the note above `render()` in card.tsx for why the initial state is mutated. */
function seed(patch: Record<string, unknown>) {
  const base = {
    started: true,
    isLoadingSession: false,
    phase: "phase1" as const,
    phaseSlots: {},
    messages: [],
    context: {},
    currentIndex: 0,
    intake: null,
    subStep: null,
    activeStatus: null,
    progress: 0,
    navStatus: "Ready" as const,
    sessionId: null,
    sessionTitle: "New chat",
    clientProfile: {},
    runId: null,
    sourceRunId: null,
  };
  const full = { ...base, ...patch };
  usePipelineStore.setState(full as never);
  Object.assign(usePipelineStore.getInitialState(), full);
}
function seedUi(patch: Record<string, unknown>) {
  useUiStore.setState(patch as never);
  Object.assign(useUiStore.getInitialState(), patch);
}

const state = () => usePipelineStore.getState();

function saved(id: string, assetId: string, phase: "phase1" | "phase2"): PipelineMessage {
  return {
    id,
    role: "assistant",
    kind: "generation",
    assetId,
    text: "An approved document.",
    savePhase: "saved",
    phase,
    createdAt: Date.now(),
  };
}

/** A finished Phase 1 leg, parked, with a Phase 2 leg two stages into it on top — the shape this
 * whole feature is about. */
const PHASE1_SLOT = {
  started: true,
  runId: "run-p1",
  sourceRunId: null,
  currentIndex: 15,
  subStep: null,
  intake: null,
  context: { icp: { text: "the ICP" }, cro: { text: "the rewrite" } },
  progress: 100,
};

/* ---------- when the leg on screen holds something ---------- */
{
  seed({ phase: "phase2", messages: [saved("a", "cro", "phase2")] });
  ok("clearable: cards in this leg", selectCanClearPhase(state()) === true);

  seed({
    phase: "phase2",
    messages: [saved("a", "icp", "phase1")],
    intake: { asset: ASSET_BY_ID.cro, answers: {}, awaitingFieldId: "client_name" },
  });
  ok("clearable: an intake in progress", selectCanClearPhase(state()) === true);

  seed({ phase: "phase2", messages: [], currentIndex: 2 });
  ok("clearable: a cursor past stage 01", selectCanClearPhase(state()) === true);
}

/* ---------- and when it does not ---------- */
{
  // The case the chat-wide version got wrong: Phase 1 is finished, the operator has just switched to
  // a Phase 2 leg that has not started, and there is nothing here to clear. Offering it would put a
  // warning in front of destroying nothing — and the thing it would have destroyed was Phase 1.
  seed({
    phase: "phase2",
    messages: [saved("a", "icp", "phase1"), saved("b", "cro", "phase1")],
    phaseSlots: { phase1: PHASE1_SLOT },
    currentIndex: 0,
    runId: null,
  });
  ok("not clearable: an unstarted leg beside a finished one", selectCanClearPhase(state()) === false);

  seed({ started: false, messages: [], currentIndex: 0 });
  ok("not clearable: a blank chat", selectCanClearPhase(state()) === false);

  seed({ messages: [saved("a", "cro", "phase1")], isLoadingSession: true });
  ok("not clearable: while a chat is opening", selectCanClearPhase(state()) === false);
}

/* ---------- which leg the label is allowed to name ---------- */
{
  seed({ phase: "phase2", messages: [saved("a", "icp", "phase1"), saved("b", "cro", "phase2")] });
  ok("other leg: cards from the other phase count", selectOtherPhaseHasWork(state()) === true);

  seed({ phase: "phase1", messages: [], phaseSlots: { phase2: { ...PHASE1_SLOT, currentIndex: 2 } } });
  ok("other leg: a parked slot counts", selectOtherPhaseHasWork(state()) === true);

  // Strict on the stamp, unlike `messagesInPhase`: an unstamped card predates per-phase stamping and
  // belongs to the leg on screen. Counted for the other phase, it would claim work for a phase that
  // never ran, and the button would call itself "Clear Phase 1" on a chat with only one leg.
  seed({ phase: "phase1", messages: [{ ...saved("a", "cro", "phase1"), phase: undefined }] });
  ok("other leg: an unstamped card belongs to this one", selectOtherPhaseHasWork(state()) === false);
}

/* ---------- clearing Phase 2 leaves Phase 1 alone ---------- */
{
  seed({
    phase: "phase2",
    phaseSlots: { phase1: PHASE1_SLOT },
    messages: [
      saved("p1-a", "icp", "phase1"),
      saved("p1-b", "cro", "phase1"),
      saved("p2-a", "cro", "phase2"),
      saved("p2-b", "pillar_page", "phase2"),
    ],
    context: { cro: { text: "the sub-service rewrite" } },
    currentIndex: 2,
    runId: "run-p2",
    sourceRunId: "run-p1",
    clientProfile: { client_name: "Acme", sub_service: "Meta Ads" },
    intake: { asset: ASSET_BY_ID.cro, answers: { client_name: "Acme" }, awaitingFieldId: "client_name" },
    activeStatus: "hitl",
    navStatus: "Awaiting Review",
    progress: 100,
  });

  state().clearPhase();
  const after = state();
  const ids = after.messages.map((m) => m.id).filter((id) => id.startsWith("p1-"));

  ok("phase2 clear: this leg's cards are gone", !after.messages.some((m) => m.id.startsWith("p2-")), JSON.stringify(after.messages.map((m) => m.id)));
  ok("phase2 clear: Phase 1's cards are kept", ids.length === 2, JSON.stringify(after.messages.map((m) => m.id)));
  ok("phase2 clear: Phase 1's parked leg is untouched", after.phaseSlots.phase1?.currentIndex === 15 && after.phaseSlots.phase1?.runId === "run-p1", JSON.stringify(after.phaseSlots));
  ok("phase2 clear: back at stage 01", after.currentIndex === 0, String(after.currentIndex));
  ok("phase2 clear: this leg's run link dropped", after.runId === null);
  // The parent link is not a property of the cleared leg — it is which Phase 1 run this chat's
  // Phase 2 builds on, and that run is still there.
  ok("phase2 clear: still built on the same Phase 1 run", after.sourceRunId === "run-p1", String(after.sourceRunId));
  ok("phase2 clear: context restarts from Phase 1's", after.context.cro !== undefined, JSON.stringify(Object.keys(after.context)));
  // ...minus the audience. Phase 2 builds its own ICP, so Phase 1's must not come back with the clear.
  ok("phase2 clear: Phase 1's ICP is not carried over", after.context.icp === undefined, JSON.stringify(Object.keys(after.context)));
  ok("phase2 clear: the client is kept", after.clientProfile.client_name === "Acme", JSON.stringify(after.clientProfile));
  ok("phase2 clear: the sub-service goes with the leg", after.clientProfile.sub_service === undefined, JSON.stringify(after.clientProfile));
  ok("phase2 clear: the intake is dropped", after.intake === null);
  ok("phase2 clear: the chat carries on", after.started === true);
  ok("phase2 clear: says what it did and what it kept", after.messages.some((m) => m.text?.includes("Phase 2 cleared") && m.text?.includes("Phase 1 is untouched")), JSON.stringify(after.messages.map((m) => m.text).slice(-1)));
}

/* ---------- and the other way round ---------- */
{
  seed({
    phase: "phase1",
    phaseSlots: { phase2: { ...PHASE1_SLOT, runId: "run-p2", currentIndex: 3 } },
    messages: [saved("p1-a", "icp", "phase1"), saved("p2-a", "cro", "phase2")],
    currentIndex: 4,
    runId: "run-p1",
    clientProfile: { client_name: "Acme", sub_service: "Meta Ads" },
  });

  state().clearPhase();
  const after = state();

  ok("phase1 clear: Phase 2's parked leg survives", after.phaseSlots.phase2?.runId === "run-p2", JSON.stringify(after.phaseSlots));
  ok("phase1 clear: Phase 2's cards survive", after.messages.some((m) => m.id === "p2-a"));
  ok("phase1 clear: this leg's cards are gone", !after.messages.some((m) => m.id === "p1-a"));
  ok("phase1 clear: starts from nothing, not from a parent", after.sourceRunId === null && Object.keys(after.context).length === 0);
  // Same client, and Phase 2 is still running on it.
  ok("phase1 clear: the client and its sub-service are kept", after.clientProfile.client_name === "Acme" && after.clientProfile.sub_service === "Meta Ads", JSON.stringify(after.clientProfile));
}

/* ---------- a stale slot for the leg being cleared ---------- */
{
  // `setPhase` parks the outgoing leg without removing the incoming one, so the phase on screen can
  // still have a slot from an earlier visit. Left behind, it would restore the pre-clear cursor the
  // moment the operator switched away and back — a clear that undoes itself on the toggle.
  seed({
    phase: "phase2",
    phaseSlots: { phase1: PHASE1_SLOT, phase2: { ...PHASE1_SLOT, runId: "run-old", currentIndex: 6 } },
    messages: [saved("p1-a", "icp", "phase1"), saved("p2-a", "cro", "phase2")],
    currentIndex: 2,
  });
  state().clearPhase();
  ok("clear: this leg's stale slot is dropped", state().phaseSlots.phase2 === undefined, JSON.stringify(state().phaseSlots));
  ok("clear: the other leg's slot is not", state().phaseSlots.phase1?.runId === "run-p1");
}

/* ---------- clearing the only leg empties the chat, and keeps its row ---------- */
{
  const removed: string[] = [];
  useChatSessionsStore.setState({ remove: async (id: string) => void removed.push(id) } as never);

  seed({
    phase: "phase1",
    messages: [saved("p1-a", "icp", "phase1")],
    currentIndex: 3,
    runId: "run-p1",
    sessionId: "s-9",
    sessionTitle: "Acme Dental",
    clientProfile: { client_name: "Acme" },
  });
  state().clearPhase();
  const after = state();

  ok("only leg: the transcript is empty", after.messages.length === 0);
  ok("only leg: back to the welcome screen", after.started === false);
  // "The client name was wrong three stages back" is one of the reasons to clear, so an emptied chat
  // must not auto-answer the next run with it.
  ok("only leg: the client profile is dropped", Object.keys(after.clientProfile).length === 0, JSON.stringify(after.clientProfile));
  // Deleting a chat is the sidebar's job, behind its own warning. Clearing keeps the row: the other
  // leg may still be using it, and on this path the operator asked to start over, not to lose the
  // chat out of their history.
  ok("only leg: the history row is kept", removed.length === 0, JSON.stringify(removed));
  ok("only leg: the chat is still the one open", after.sessionId === "s-9");
}

/* ---------- the control ---------- */
{
  seedUi({ demoMode: false, workTab: "transcript", hintsOn: true, pendingChatDelete: null });

  seed({ phase: "phase1", messages: [saved("a", "cro", "phase1")], currentIndex: 3 });
  const alone = renderToStaticMarkup(<TopNav />);
  ok("nav: reads Clear chat when this is the whole chat", alone.includes("Clear chat"), alone.slice(0, 1100));

  // Two legs, so the button names the one it would clear. A "Clear chat" here would be a lie about
  // fifteen approved assets.
  seed({
    phase: "phase2",
    phaseSlots: { phase1: PHASE1_SLOT },
    messages: [saved("p1-a", "icp", "phase1"), saved("p2-a", "cro", "phase2")],
    currentIndex: 2,
  });
  const both = renderToStaticMarkup(<TopNav />);
  ok("nav: names the leg when the other holds work", both.includes("Clear Phase 2"), both.slice(0, 1200));
  ok("nav: and does not claim the chat", !both.includes(">Clear chat<"), both.slice(0, 1200));
  // One click away from restarting a leg would be too few.
  ok("nav: does not clear on the first click", !both.includes("Keep it"), both.slice(0, 1400));

  seed({ phase: "phase2", messages: [saved("p1-a", "icp", "phase1")], phaseSlots: { phase1: PHASE1_SLOT }, currentIndex: 0 });
  const unstarted = renderToStaticMarkup(<TopNav />);
  ok("nav: absent on a leg with nothing in it", !unstarted.includes("Clear Phase"), unstarted.slice(0, 1100));

  // The sample transcript is fixtures; the run this would clear is the operator's real one.
  seedUi({ demoMode: true });
  seed({ phase: "phase1", messages: [saved("a", "cro", "phase1")], currentIndex: 3 });
  const sample = renderToStaticMarkup(<TopNav />);
  ok("nav: withdrawn while the sample transcript is up", !sample.includes("Clear chat"), sample.slice(0, 1100));
  seedUi({ demoMode: false });
}

/* ---------- the delete warning ---------- */
{
  seedUi({ pendingChatDelete: null });
  const quiet = renderToStaticMarkup(<ChatDeleteDialog />);
  ok("delete: silent until asked", !quiet.includes("Delete this chat?"), quiet.slice(0, 300));

  useChatSessionsStore.setState({
    sessions: [{ id: "s-3", title: "Acme Dental", created_at: "", updated_at: "" }],
  } as never);
  seedUi({ pendingChatDelete: { id: "s-3", title: "Acme Dental" } });
  const asking = renderToStaticMarkup(<ChatDeleteDialog />);
  ok("delete: warns before deleting", asking.includes("Delete this chat?"), asking.slice(0, 400));
  // Which chat, by name. "Delete this chat" beside a list of six is not enough to check the aim.
  ok("delete: names the chat", asking.includes("Acme Dental"), asking.slice(0, 900));
  ok("delete: says it cannot be undone", asking.includes("cannot be undone"), asking.slice(-900));
  // The one fact that makes the decision informed rather than frightening.
  ok("delete: says approved assets survive", asking.includes("Context Store"), asking.slice(0, 1600));
  ok("delete: offers a way out", asking.includes("Keep it"), asking.slice(-900));
}

console.log(`\n${pass} passed, ${fails.length} failed`);
if (fails.length) {
  console.log("\nFAILURES:");
  fails.forEach((f) => console.log("  x " + f));
  process.exitCode = 1;
}
