/* The card path, rendered with the real store. Not shipped — deleted after the check runs.
 *
 * `smoke.tsx` covers the new presentation components in isolation. This is the higher-risk half:
 * `GenerationCard` and `ActionRow` were edited in place inside a 700-line file that drives a
 * 3,300-line store, and the requirement was that the core working of the app not regress. So the
 * real `GenerationStream` is mounted against a seeded store and every state a generation can be in
 * is asserted — streaming, finished, saved, superseded, interrupted, failed, refining.
 *
 * Server rendering means effects never run, so this proves the *first paint* of each state. What it
 * cannot prove is a click handler firing; those are asserted structurally instead (the button
 * exists, is enabled or disabled correctly, and carries the right label). */
import { renderToStaticMarkup } from "react-dom/server";
import { GenerationStream } from "../src/pipeline/GenerationStream";
import { usePipelineStore, type PipelineMessage } from "../src/pipeline/pipelineStore";
import { AssetReader } from "../src/pipeline/AssetReader";
import { useUiStore } from "../src/store/uiStore";
import css from "../src/index.css?raw";

let pass = 0;
const fails: string[] = [];
const ok = (name: string, cond: boolean, extra?: string) => {
  if (cond) pass++;
  else fails.push(name + (extra ? `  ->  ${extra}` : ""));
};

const pad = (n: number) =>
  Array.from({ length: n }, (_, i) => `Body line ${i} with several real words in it.`).join("\n");

const CRO_TEXT = `**PART 0 — MODE RESOLUTION TABLE**

| Input | Resolved |
|---|---|
| Scope | SERVICE PAGE |

**PART 1 — CRO AUDIT REPORT**
${pad(30)}

**PART 2 — REWRITTEN PAGE**
${pad(60)}
Confirm [CLIENT TO CONFIRM: the guarantee wording] before publishing.

**PART 3 — IMPLEMENTATION PACK**
${pad(20)}
`;

/** A document whose widest element is a nine-column matrix — the shape that exposed the
 *  reader's width problem. */
const WIDE_TABLE_DOC = [
  "**PART 1 — COMPETITOR MATRIX**",
  "",
  "| Competitor | Pillar | Blogs | Magnets | Funnels | Emails | SMS | Video | Verdict |",
  "|---|---|---|---|---|---|---|---|---|",
  "| Rival One | yes | 24 | 3 | 2 | 5 | no | yes | ahead on depth |",
  "| Rival Two | no | 8 | 1 | 1 | 2 | no | no | behind |",
  "",
  "**PART 2 — NOTES**",
  "",
  pad(40),
  "",
  "**PART 3 — ACTIONS**",
  "",
  pad(20),
].join(String.fromCharCode(10));

const SHORT_TEXT = "A two-sentence answer. Nothing worth an outline here.";

function message(over: Partial<PipelineMessage>): PipelineMessage {
  return {
    id: "m1",
    role: "assistant",
    kind: "generation",
    assetId: "cro",
    text: CRO_TEXT,
    createdAt: Date.now(),
    ...over,
  };
}

/** Seed only what the transcript reads, and render it.
 *
 * Seeding goes into the *initial state object*, which needs explaining. zustand v5 passes
 * `getInitialState` as `useSyncExternalStore`'s **server** snapshot (`zustand/react.js`), so a
 * server render deliberately reads the state as created and ignores every later `setState` — that
 * is what keeps a real SSR page hydration-stable, and it is correct behaviour rather than a bug.
 *
 * Reassigning `usePipelineStore.getInitialState` does not help either: `create` closes over the
 * original api object and `Object.assign`s a *copy* onto the hook, so the copy is not the function
 * the hook calls. What does work is mutating the object that function returns, which is the same
 * reference every time.
 *
 * `setState` is still called so `getState()` and the server snapshot agree. Nothing in `src/` is
 * patched by any of this.
 */
function render(messages: PipelineMessage[]): string {
  const seed = {
    started: true,
    isLoadingSession: false,
    phase: "phase1" as const,
    messages,
    currentIndex: 1,
    intake: null,
  };
  usePipelineStore.setState(seed);
  Object.assign(usePipelineStore.getInitialState(), seed);
  return renderToStaticMarkup(<GenerationStream />);
}

/* ---------- a finished, unapproved draft ---------- */
{
  const html = render([message({})]);
  ok("draft: card renders", html.includes("CRO — Page Rewrite"), html.slice(0, 200));
  ok("draft: stage number shown", html.includes("Stage 02"));
  ok("draft: outline replaces the wall", html.includes("PART 2 — REWRITTEN PAGE"));
  ok("draft: body text is not on screen while collapsed", !html.includes("Body line 0"));
  ok("draft: meta line counts the parts", html.includes("4 parts"), html.slice(0, 400));
  ok("draft: placeholder flag raised", html.includes("1 to confirm"));
  ok("draft: the document can be opened", html.includes("Open document"));
  ok("draft: Save is the visible primary action", html.includes("Save It"));
  ok("draft: Refine is not a top-level button any more", !html.includes("Refine / Request Changes"));
  ok("draft: the overflow menu is present", html.includes("More actions for"));
  ok("draft: Download is not a top-level button any more", !html.includes(">Download<"));
}

/* ---------- streaming ---------- */
{
  const html = render([message({ streaming: true, text: "## STEP 1 — READ CONTEXT\nreading the ICP" })]);
  ok("streaming: progress replaces the flood", html.includes("Starting") || html.includes("Step 1"), html.slice(0, 300));
  ok("streaming: the tail is shown", html.includes("reading the ICP"));
  ok("streaming: full stream is offered", html.includes("Show full stream"));
  ok("streaming: no action row while running", !html.includes("Save It"));
}

/* ---------- streaming with nothing yet ---------- */
{
  const html = render([message({ streaming: true, text: "" })]);
  ok("streaming empty: honest waiting state", html.includes("waiting for the first tokens"), html.slice(0, 300));
  ok("streaming empty: does not claim a step", !html.includes("Step 1"));
}

/* ---------- already approved: collapses to one row ---------- */
{
  const html = render([message({ savePhase: "saved" })]);
  ok("saved: collapses to a row", !html.includes("PART 2 — REWRITTEN PAGE"), html.slice(0, 400));
  ok("saved: still names the asset", html.includes("CRO — Page Rewrite"));
  ok("saved: word count shown", /\d,?\d* words/.test(html), html.slice(0, 400));
  ok("saved: can be opened", html.includes("Open ↗"));
  ok("saved: can be expanded", html.includes("Expand"));
}

/* ---------- saving / save error ---------- */
{
  const saving = render([message({ savePhase: "saving" })]);
  ok("saving: button shows progress", saving.includes("Saving…"), saving.slice(0, 400));
  ok("saving: button disabled", saving.includes("disabled=\"\""));

  const errored = render([message({ savePhase: "error", saveError: "network down" })]);
  ok("save error: reason shown", errored.includes("network down"), errored.slice(0, 400));
  ok("save error: offers a retry", errored.includes("Retry Save"));
}

/* ---------- refine in progress ---------- */
{
  const refining = render([message({ refining: true })]);
  // Matched without the apostrophe: React escapes it to `&#x27;` in the serialised output.
  ok("refining: the note form takes the row", refining.includes("like changed"), refining.slice(0, 300));
  ok("refining: the note textarea is present", refining.includes("<textarea"));
  ok("refining: offers Send & Regenerate", refining.includes("Regenerate"));
  ok("refining: no overflow menu competing with it", !refining.includes("More actions for"));

  const submitted = render([message({ refineSubmitted: true })]);
  ok("refine sent: says so", submitted.includes("Regenerating from your note"), submitted.slice(0, 400));
  ok("refine sent: exports still reachable", submitted.includes("More actions for"));
}

/* ---------- superseded ---------- */
{
  const html = render([message({ superseded: true })]);
  ok("superseded: explains itself", html.includes("Replaced"), html.slice(0, 400));
  ok("superseded: no action row", !html.includes("Save It"));
  ok("superseded: can still be opened for comparison", html.includes("Open document"));
}

/* ---------- interrupted ---------- */
{
  const html = render([message({ interrupted: true })]);
  ok("interrupted: warns", html.includes("This draft is incomplete"), html.slice(0, 400));
  ok("interrupted: offers a regenerate", html.includes("Generate it again"));
}

/* ---------- truncated by the output ceiling ---------- */
{
  const html = render([message({ truncated: true, answers: { topic: "Meta Ads" } })]);
  ok("truncated: warns it is unfinished", html.includes("This draft is unfinished"), html.slice(0, 500));
  ok("truncated: names the ceiling, not a dropped connection", html.includes("maximum reply length"), html.slice(0, 500));
  ok("truncated: Continue is the primary action", html.includes("Continue writing it"));
  ok("truncated: regenerate is still offered as the secondary", html.includes("Start it again"));
  ok("truncated: Save is still offered", html.includes("Save It"));
  ok("truncated: Save is warned, not blocked", html.includes("stores a document that stops part-way"), html.slice(0, 800));
}

/* ---------- truncated with no stored intake ---------- */
{
  const html = render([message({ truncated: true, answers: undefined })]);
  ok("truncated no intake: Continue is withheld", !html.includes("Continue writing it"), html.slice(0, 500));
  ok("truncated no intake: says why", html.includes("no stored intake"), html.slice(0, 500));
}

/* ---------- a continuation is appending ---------- */
{
  const html = render([message({ streaming: true, continuing: true, text: "and then the next paragraph of the draft" })]);
  ok("continuing: does not claim the stage restarted", !html.includes("Starting"), html.slice(0, 400));
  ok("continuing: names the finish", html.includes("Finishing") || html.includes("picking up"), html.slice(0, 400));
}

/* ---------- a stream that produced nothing is reconnecting ---------- */
{
  const html = render([message({ streaming: true, text: "", reconnecting: { attempt: 2, of: 3 } })]);
  ok("reconnecting: says so", html.includes("Reconnecting"), html.slice(0, 400));
  ok("reconnecting: names the attempt", html.includes("Attempt 2 of 3"), html.slice(0, 400));
}

/* ---------- generation failed ---------- */
{
  const html = render([message({ generationError: "upstream 500", text: undefined })]);
  ok("failed: reason shown", html.includes("upstream 500"), html.slice(0, 400));
  ok("failed: offers a retry", html.includes("Retry Generation"));
}

/* ---------- a short document keeps the old rendering ---------- */
{
  const html = render([message({ text: SHORT_TEXT })]);
  ok("short: text is rendered in full", html.includes("A two-sentence answer"), html.slice(0, 400));
  ok("short: no outline forced onto it", !html.includes("Open document"));
  ok("short: actions still present", html.includes("Save It"));
}

/* ---------- a prepass chip still renders above the body ---------- */
{
  const html = render([
    message({ prepass: { status: "running", label: "Competitor Analysis — CRO", assetId: "x" } as never }),
  ]);
  ok("prepass: running chip renders", html.includes("Researching competitors"), html.slice(0, 500));
}

/* ---------- many saved assets: the whole point of the change ---------- */
{
  const many = Array.from({ length: 8 }, (_, i) =>
    message({ id: `m${i}`, savePhase: "saved", assetId: i % 2 ? "cro" : "icp" }),
  );
  const html = render(many);
  ok("many saved: all render", (html.match(/Open ↗/g) ?? []).length === 8, String((html.match(/Open ↗/g) ?? []).length));
  ok("many saved: none dumps its document", !html.includes("PART 2 — REWRITTEN PAGE"));
}

/* ---------- the reader pane ---------- */
{
  const seed = { started: true, isLoadingSession: false, phase: "phase1" as const, messages: [message({})] };
  usePipelineStore.setState(seed);
  Object.assign(usePipelineStore.getInitialState(), seed);

  const closed = renderToStaticMarkup(<AssetReader />);
  ok("reader: nothing rendered when no document is open", closed === "", closed.slice(0, 120));

  Object.assign(useUiStore.getInitialState(), { readerMessageId: "m1" });
  useUiStore.setState({ readerMessageId: "m1" });
  const open = renderToStaticMarkup(<AssetReader />);

  ok("reader: opens as a dialog", open.includes('role="dialog"'), open.slice(0, 200));
  ok("reader: names the asset", open.includes("CRO — Page Rewrite"), open.slice(0, 300));
  ok("reader: outline rail lists every part", ["PART 0", "PART 1", "PART 2", "PART 3"].every((x) => open.includes(x)));
  ok("reader: offers find-in-document", open.includes("Find in document"));
  ok("reader: renders the bodies in full, unlike the card", open.includes("Body line 0"));
  ok("reader: carries the export actions", open.includes("Download"));
  ok("reader: can be closed", open.includes(">Close<"));

  // A reader pointed at a message that is no longer in the transcript must not render an empty
  // sheet — the effect closes it, and the first paint must at least not throw.
  Object.assign(useUiStore.getInitialState(), { readerMessageId: "gone" });
  useUiStore.setState({ readerMessageId: "gone" });
  const orphan = renderToStaticMarkup(<AssetReader />);
  ok("reader: a missing message renders nothing rather than an empty shell", orphan === "", orphan.slice(0, 120));
}

/* ---------- the reader's width, and the table that exposed it ---------- */
{
  const seed = { started: true, isLoadingSession: false, phase: "phase1" as const, messages: [message({ text: WIDE_TABLE_DOC })] };
  usePipelineStore.setState(seed);
  Object.assign(usePipelineStore.getInitialState(), seed);
  Object.assign(useUiStore.getInitialState(), { readerMessageId: "m1" });
  useUiStore.setState({ readerMessageId: "m1" });

  const open = renderToStaticMarkup(<AssetReader />);

  /* The sheet grows with the viewport rather than sitting at a fixed 52rem, which on a wide
   * monitor left a nine-column table squeezed into 37rem while 1,000px of window went unused. */
  ok("reader: sheet width is adaptive", open.includes("sm:w-[min(94vw,76rem)]"), "expected a min() width");
  ok("reader: no fixed 52rem sheet", !open.includes("sm:w-[52rem]"));

  /* Prose keeps a readable measure; tables and previews take the pane. A single container cap
   * could not do both. */
  ok("reader: content column carries the reading measure", open.includes("doc-measure"), "expected .doc-measure");
  ok("reader: content column is no longer capped at 44rem", !open.includes("max-w-[44rem]"));

  /* The actual reported symptom: `.md-scroll` paints a 1.75rem cover gradient at each edge to
   * hint at horizontal overflow, and it was hardcoded to the *card* colour. On the reader, which
   * is `--bg`, that band was the wrong colour and read as the first and last column being cut. */
  ok("reader: tells the table scroller its surface colour", open.includes("--md-cover"), "expected --md-cover");
  ok("reader: the table is in a scroller", open.includes("md-scroll"), "expected .md-scroll");

  // Every column still reaches the DOM — the complaint was about display, and it must stay that way.
  const columns = ["Competitor", "Pillar", "Blogs", "Magnets", "Funnels", "Emails", "SMS", "Video", "Verdict"];
  ok("reader: all nine columns render", columns.every((c) => open.includes(c)), columns.filter((c) => !open.includes(c)).join(","));
  ok("reader: table rows render", open.includes("ahead on depth") && open.includes("behind"));

  useUiStore.setState({ readerMessageId: null });
  Object.assign(useUiStore.getInitialState(), { readerMessageId: null });
}

/* ---------- the cover variable is honoured on every surface ---------- */
{
  ok("css: the cover colour is a variable, not the card colour", css.includes("var(--md-cover, var(--bg-raised))"),
    "expected .md-scroll to read --md-cover");
  ok("css: it still defaults to the card colour", css.includes("--md-cover, var(--bg-raised)"));
  ok("css: the reading measure is a class, not global", css.includes(".doc-measure :is(p,"),
    "expected a scoped measure rule");
  ok("css: the measure is overridable", css.includes("var(--doc-measure, 46rem)"));

  /* The table scroller must let a table take its natural width, or it never overflows and the
   * cells squeeze instead of the pane scrolling. */
  ok("css: tables keep their natural width inside the scroller", /\.md-scroll > table \{[^}]*width: auto/.test(css));
  ok("css: and still fill a narrow pane", /\.md-scroll > table \{[^}]*min-width: 100%/.test(css));
}

console.log(`\n${pass} passed, ${fails.length} failed`);
if (fails.length) {
  console.log("\nFAILURES:");
  fails.forEach((f) => console.log("  x " + f));
  process.exitCode = 1;
}
