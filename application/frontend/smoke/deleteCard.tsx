/* "Delete this card" on a generation card's ⋯ menu. Not shipped; see smoke/README.md.
 *
 * Driven through the real store action. The rules it pins:
 *   - a draft is deleted; if it was the stage's last open draft, the stage says so with a
 *     "draft deleted" card carrying "Start … again", and the run is back at Ready;
 *   - deleting a refined draft also removes the note that asked for it and reopens review on the
 *     draft it came from;
 *   - the stage's only approved card is refused, with the reason; a second approved card makes the
 *     older one deletable;
 *   - a card still generating is refused;
 *   - the confirmation is a root-mounted modal that names the stage.
 */
import { renderToStaticMarkup } from "react-dom/server";
import { GenerationDeleteDialog } from "../src/pipeline/GenerationDeleteDialog";
import { selectDeleteGeneration, usePipelineStore, type PipelineMessage } from "../src/pipeline/pipelineStore";
import { useUiStore } from "../src/store/uiStore";

// The store autosaves the chat after each change; there is no backend here, so that save never
// answers rather than failing noisily.
globalThis.fetch = (() => new Promise<Response>(() => {})) as typeof fetch;

let pass = 0;
const fails: string[] = [];
const ok = (name: string, cond: boolean, extra?: string) => {
  if (cond) pass++;
  else fails.push(name + (extra ? `  ->  ${extra}` : ""));
};

const gen = (id: string, over: Partial<PipelineMessage> = {}): PipelineMessage => ({
  id,
  role: "assistant",
  kind: "generation",
  assetId: "icp",
  text: `Draft ${id}`,
  createdAt: Date.now(),
  savePhase: "idle",
  ...over,
});

function seed(messages: PipelineMessage[]) {
  const state = {
    started: true,
    phase: "phase1" as const,
    messages,
    currentIndex: 0,
    intake: null,
    activeStatus: "hitl" as const,
    navStatus: "Awaiting Review" as const,
  };
  usePipelineStore.setState(state);
  Object.assign(usePipelineStore.getInitialState(), state);
}
const S = () => usePipelineStore.getState();
const ids = () => S().messages.map((m) => m.id);

/* ---------- the stage's only draft ---------- */
seed([gen("d1")]);
ok("a draft can be deleted", selectDeleteGeneration(S(), "d1").ok);
S().deleteGeneration("d1");
ok("the draft is gone", !ids().includes("d1"));
const stopped = S().messages.find((m) => m.kind === "stage-stopped");
ok("the stage says its draft was deleted", stopped?.stopped?.deleted === true && stopped.stopped.assetId === "icp");
ok("the run is back at Ready", S().navStatus === "Ready" && S().activeStatus === null);

/* ---------- a refined draft ---------- */
seed([
  gen("p1", { refineSubmitted: true }),
  { id: "n1", role: "user", kind: "text", text: "Make it shorter.", createdAt: Date.now() },
  gen("c1"),
]);
S().deleteGeneration("c1");
ok("the refinement and its note are gone", ids().join(",") === "p1", ids().join(","));
ok("the draft it came from is open for review again", S().messages[0].refineSubmitted === false);
ok("no stopped card: a draft is still on review", !S().messages.some((m) => m.kind === "stage-stopped"));

/* ---------- approved cards ---------- */
seed([gen("a1", { savePhase: "saved" })]);
const refused = selectDeleteGeneration(S(), "a1");
ok("the only approved card is refused", !refused.ok && /Re-run the stage/.test(refused.reason ?? ""), refused.reason);
S().deleteGeneration("a1");
ok("refused means nothing changed", ids().join(",") === "a1");

seed([gen("a1", { savePhase: "saved" }), gen("a2", { savePhase: "saved" })]);
ok("with a newer approved card, the older one can go", selectDeleteGeneration(S(), "a1").ok);
S().deleteGeneration("a1");
ok("and the stage is still approved", ids().join(",") === "a2" && !S().messages.some((m) => m.kind === "stage-stopped"));

seed([gen("a1", { savePhase: "saved" }), gen("x1", { assetId: "cro", savePhase: "saved" })]);
ok("another stage's approval does not count", !selectDeleteGeneration(S(), "a1").ok);

/* ---------- in flight ---------- */
seed([gen("s1", { streaming: true })]);
ok("a card still generating is refused", !selectDeleteGeneration(S(), "s1").ok);
seed([gen("v1", { savePhase: "saving" })]);
ok("a card being saved is refused", !selectDeleteGeneration(S(), "v1").ok);

/* ---------- the confirmation ---------- */
seed([gen("d2")]);
const target = { messageId: "d2", label: "ICP", approved: false };
Object.assign(useUiStore.getInitialState(), { pendingGenerationDelete: target });
useUiStore.setState({ pendingGenerationDelete: target });
const dialog = renderToStaticMarkup(<GenerationDeleteDialog />);
ok("the modal names the stage", dialog.includes("Delete this ICP card?"), dialog.slice(0, 300));
ok("the modal says what stays", dialog.includes("Context Store are not deleted"));
ok("cancel is offered", dialog.includes("Keep it"));

console.log(`deleteCard: ${pass} passed, ${fails.length} failed`);
for (const f of fails) console.log(`  FAIL ${f}`);
if (fails.length) process.exitCode = 1;
