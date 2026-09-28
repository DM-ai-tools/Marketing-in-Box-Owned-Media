import { useEffect, useMemo, useRef, useState } from "react";
import { motion } from "framer-motion";
import { useShallow } from "zustand/react/shallow";
import { ASSET_BY_ID } from "../data/assetCatalog";
import { AssetExportMenuItems } from "../components/AssetExportButtons";
import { SlideDeckMenuItem } from "../components/SlideDeckMenuItem";
import { DocumentMeta, DocumentOutline, PreviewStrip, SummaryChips } from "../components/AssetDocumentView";
import { Hint } from "../components/Hint";
import { FieldHint } from "../components/FieldHint";
import { HtmlPreview } from "../components/HtmlPreview";
import { Markdown } from "../components/Markdown";
import { OverflowDivider, OverflowItem, OverflowMenu } from "../components/OverflowMenu";
import { countWords, parseAssetDocument, type AssetDocument } from "../lib/assetDocument";
import { summariseAsset } from "../lib/assetSummary";
import { buildPlanMindMapHtml } from "../lib/planMindMapHtml";
import { splitHtmlBlocks } from "../lib/htmlBlocks";
import { topicPreambleFor } from "../lib/topicSuggestions";
import { QuestionWidget } from "../components/QuestionWidget";
import { TypingIndicator } from "../components/TypingIndicator";
import { DeliverablesGrid } from "./DeliverablesGrid";
import { DemoTranscript } from "./DemoTranscript";
import { PlanMindMap } from "./PlanMindMap";
import { StreamProgress } from "./StreamProgress";
import { useUiStore } from "../store/uiStore";
import { BriefingCard } from "./BriefingCard";
import { CompetitorCard } from "./CompetitorCard";
import { CompetitorConsentCard } from "./CompetitorConsentCard";
import { ContextChoiceCard } from "./ContextChoiceCard";
import { HeadlineChoiceCard } from "./HeadlineChoiceCard";
import { SourceRunCard } from "./SourceRunCard";
import { StageGateCard } from "./StageGateCard";
import { EditAnswerButton } from "./EditAnswerButton";
import { ScrapeCard } from "./ScrapeCard";
import { NEW_PAGE_OPTIONS, PHASE_META, stageAt, stagesFor, totalStagesFor } from "./pipelineData";
import { PipelineInputBar } from "./PipelineInputBar";
import {
  fieldBeingAsked,
  messagesInPhase,
  selectCanRerun,
  selectCanStop,
  selectNeedsResume,
  selectStoppableStage,
  usePipelineStore,
} from "./pipelineStore";
import type { PipelineMessage } from "./pipelineStore";

function SaveIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none">
      <path
        d="M5 4h11l3 3v13H5V4z M8 4v6h8V4 M8 20v-7h8v7"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function RerunIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none">
      <path
        d="M20 12a8 8 0 1 1-2.34-5.66 M20 4v4h-4"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

/** The confirmation step for starting an asset over.
 *
 * Re-run is distinct from the two controls beside it, and the distinction is the reason it exists.
 * Retry re-sends the *same* answers, so it only recovers a transient failure. Refine hands the
 * model the draft plus a note, so it revises what is already there. Neither helps when the asset
 * came out wrong because an *input* was wrong — a different target service, a different chosen
 * headline, a different lead-magnet concept — which is the common case for a disappointing result.
 *
 * Confirmed rather than immediate, and that guard is unchanged from when this was a button: it
 * discards the draft on screen, spends a fresh generation, and on an approved asset writes another
 * Context Store version. It now lives behind the `⋯` menu, so the prompt renders in the row after
 * the menu has closed rather than inside it.
 */
function RerunConfirm({ messageId, onCancel }: { messageId: string; onCancel: () => void }) {
  const rerunStage = usePipelineStore((s) => s.rerunStage);
  const canRerun = usePipelineStore(selectCanRerun);

  return (
    <span className="flex flex-wrap items-center gap-2">
      <span className="text-[0.78rem] text-[var(--fg-muted)]">
        Re-run this asset? Its questions are asked again and this draft is replaced.
      </span>
      <motion.button
        type="button"
        disabled={!canRerun}
        onClick={() => {
          onCancel();
          rerunStage(messageId);
        }}
        whileTap={canRerun ? { scale: 0.97 } : undefined}
        title={canRerun ? undefined : "Not while something else is still running"}
        className="min-h-10 cursor-pointer rounded-full px-3 py-1.5 text-[0.78rem] font-semibold text-white disabled:cursor-not-allowed disabled:opacity-40 sm:min-h-0"
        style={{ backgroundColor: "var(--color-signal-orange)" }}
      >
        Yes, re-run
      </motion.button>
      <button
        type="button"
        onClick={onCancel}
        className="min-h-10 cursor-pointer rounded-full border border-[var(--border-strong)] px-3 py-1.5 text-[0.78rem] font-medium sm:min-h-0"
      >
        Cancel
      </button>
    </span>
  );
}

/** The asset whose deliverable is a structure. See `PlanSwitch`. */
const PLAN_ASSET_ID = "plan_of_action";

/** The asset whose Step 5 writes a slide-by-slide brief. See `SlideDeckMenuItem`. */
const WEBINAR_ASSET_ID = "webinar";

function MapIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden>
      <path
        d="M5 6h5M14 6h5M5 12h14M5 18h5M14 18h5M12 6v12"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
      />
    </svg>
  );
}

/** Build the standalone map and hand it to the browser as a download.
 *
 * A blob URL rather than a data URI: a full plan's map runs to a few hundred kilobytes, and a data
 * URI that long is refused outright by some browsers and silently truncated by others. Revoked on
 * the next tick, once the click has been dispatched. */
function downloadPlanMap(text: string, label: string): void {
  const built = buildPlanMindMapHtml(text, label);
  if (!built) return;
  const url = URL.createObjectURL(new Blob([built.html], { type: "text/html;charset=utf-8" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = built.filename;
  link.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 0);
}

function EditIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none">
      <path
        d="M4 20h4L18.5 9.5a2.12 2.12 0 0 0-3-3L5 17v3z M14 6l4 4"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function RefineForm({ messageId }: { messageId: string }) {
  const submitRefine = usePipelineStore((s) => s.submitRefine);
  const cancelRefine = usePipelineStore((s) => s.cancelRefine);
  const [note, setNote] = useState("");
  const [sending, setSending] = useState(false);

  const submit = async () => {
    if (!note.trim() || sending) return;
    setSending(true);
    await submitRefine(messageId, note);
  };

  return (
    <div className="mt-3 border-t border-[var(--border)] pt-3">
      <textarea
        autoFocus
        value={note}
        onChange={(e) => setNote(e.target.value)}
        placeholder="Describe what you'd like changed…"
        rows={3}
        className="w-full resize-none rounded-xl border border-[var(--border-strong)] bg-[var(--bg)] px-3 py-2 text-[0.85rem] outline-none focus:border-[var(--color-electric-blue)]"
      />
      <div className="mt-2 flex items-center gap-2">
        <motion.button
          type="button"
          onClick={submit}
          disabled={!note.trim() || sending}
          whileTap={{ scale: 0.97 }}
          className="min-h-10 cursor-pointer rounded-full px-3.5 py-1.5 text-[0.8rem] font-semibold text-white disabled:cursor-not-allowed disabled:opacity-50 sm:min-h-0"
          style={{ backgroundColor: "var(--color-electric-blue)" }}
        >
          Send &amp; Regenerate
        </motion.button>
        <button
          type="button"
          onClick={() => cancelRefine(messageId)}
          className="min-h-10 cursor-pointer rounded-full px-3 py-1.5 text-[0.8rem] font-medium text-[var(--fg-muted)] sm:min-h-0"
        >
          Cancel
        </button>
      </div>
    </div>
  );
}

/** The row under a finished generation: the one decision that is still open, and everything else
 * behind `⋯`.
 *
 * This used to render up to five equally-weighted pills — Save, Refine, Re-run, Download, Share —
 * under every one of fifteen cards, which is a large share of the chrome the transcript spent on
 * itself. Only the *arrangement* changed: every action still exists, still calls the same store
 * method, and Re-run still asks for confirmation first.
 *
 * The exports are in the menu in every branch on purpose. Save/Refine is the pipeline's own
 * workflow and closes once the asset is approved; getting the file out is a separate need that
 * outlives it — most often *after* approval, when the asset is ready to send to the client.
 */
function ActionRow({ message, label, stageNumber }: { message: PipelineMessage; label: string; stageNumber?: number }) {
  const saveStage = usePipelineStore((s) => s.saveStage);
  const requestRefine = usePipelineStore((s) => s.requestRefine);
  const canRerun = usePipelineStore(selectCanRerun);
  // Only used to look up the run's captured palette for a slide deck, so a deck built on a run
  // with no design yet comes out in neutral greys rather than being refused.
  const runId = usePipelineStore((s) => s.runId);
  const [confirmingRerun, setConfirmingRerun] = useState(false);

  // The refine form takes over the row entirely — the operator is mid-sentence, and a Download
  // button under a half-written note is a misclick waiting to happen.
  if (message.refining) return <RefineForm messageId={message.id} />;

  if (confirmingRerun) {
    return (
      <div className="mt-3 border-t border-[var(--border)] pt-3">
        <RerunConfirm messageId={message.id} onCancel={() => setConfirmingRerun(false)} />
      </div>
    );
  }

  const saved = message.savePhase === "saved";
  const saving = message.savePhase === "saving";
  // Refine is offered while the review is still open. Once the asset is approved, or a refine has
  // already been sent, the way back is Re-run — which is why that one is always in the menu.
  const offersReview = !saved && !message.refineSubmitted;

  const menu = (
    <OverflowMenu label={`More actions for ${label}`}>
      {(close) => (
        <>
          {offersReview && (
            <OverflowItem
              disabled={saving}
              onClick={() => {
                close();
                requestRefine(message.id);
              }}
            >
              <EditIcon />
              Refine / request changes
            </OverflowItem>
          )}
          <OverflowItem
            disabled={!canRerun}
            onClick={() => {
              close();
              setConfirmingRerun(true);
            }}
          >
            <RerunIcon />
            Re-run this asset
          </OverflowItem>
          {message.text && (
            <>
              <OverflowDivider />
              {/* Only where there is a map to download. The file is self-contained — inline CSS,
                  inline script, the plan's own text embedded — because a plan is the asset most
                  likely to be sent to somebody without a login. See `planMindMapHtml.ts`. */}
              {message.assetId === PLAN_ASSET_ID && (
                <OverflowItem
                  onClick={() => {
                    close();
                    downloadPlanMap(message.text ?? "", label);
                  }}
                >
                  <MapIcon />
                  Download plan map (.html)
                </OverflowItem>
              )}
              {/* Only on the webinar, and only once the server confirms the document holds a
                  parsable Step 5 brief — the row hides itself otherwise. */}
              {message.assetId === WEBINAR_ASSET_ID && (
                <SlideDeckMenuItem text={message.text} runId={runId} onDone={close} />
              )}
              <AssetExportMenuItems
                text={message.text}
                label={label}
                stageNumber={stageNumber}
                // The topics this asset was built on, and the ones passed over, at the top of the
                // file. Read at click time from the store rather than subscribed to here.
                preamble={() => topicPreambleFor(usePipelineStore.getState().messages, message)}
                assetId={message.assetId}
                onDone={close}
              />
            </>
          )}
        </>
      )}
    </OverflowMenu>
  );

  return (
    <div className="mt-3 border-t border-[var(--border)] pt-3">
      {message.savePhase === "error" && (
        <p className="mb-2 text-[0.78rem]" style={{ color: "var(--color-signal-orange)" }}>
          Save failed: {message.saveError}
        </p>
      )}
      <div className="flex flex-wrap items-center gap-2">
        {saved ? (
          <span
            className="flex items-center gap-1.5 rounded-full px-3 py-1.5 text-[0.78rem] font-semibold text-white"
            style={{ backgroundColor: "var(--color-signal-green)" }}
          >
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none">
              <path d="M5 12l5 5L20 7" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
            Saved to Context Store
          </span>
        ) : message.refineSubmitted ? (
          <span className="text-[0.78rem] text-[var(--fg-muted)]">Regenerating from your note…</span>
        ) : (
          <motion.button
            type="button"
            disabled={saving}
            onClick={() => saveStage(message.id)}
            whileTap={{ scale: 0.97 }}
            className="flex min-h-10 cursor-pointer items-center gap-1.5 rounded-full px-3.5 py-1.5 text-[0.8rem] font-semibold text-white disabled:cursor-not-allowed disabled:opacity-60 sm:min-h-0"
            style={{ backgroundColor: "var(--color-electric-blue)" }}
          >
            <SaveIcon />
            {saving ? "Saving…" : message.savePhase === "error" ? "Retry Save" : "Save It"}
          </motion.button>
        )}
        <span className="flex-1" />
        {menu}
      </div>
    </div>
  );
}

/** Status chip for the auto-run competitor-analysis prepass. It has no Save/Refine buttons of
 * its own — the operator reviews the asset built on top of it — but its output is viewable here
 * and is saved to the Context Store when that asset is approved. */
function PrepassChip({ prepass }: { prepass: NonNullable<PipelineMessage["prepass"]> }) {
  const [open, setOpen] = useState(false);

  if (prepass.status === "running") {
    return (
      <div className="mb-2.5 flex items-center gap-2 rounded-lg border border-[var(--border)] bg-[var(--bg-sunken)] px-2.5 py-1.5 text-[0.76rem]">
        <span className="h-1.5 w-1.5 shrink-0 rounded-full dot-pulsing" style={{ backgroundColor: "var(--color-electric-blue)" }} />
        <span className="text-[var(--fg-muted)]">
          Researching competitors — <span className="font-medium text-[var(--fg)]">{prepass.label}</span>…
        </span>
      </div>
    );
  }

  if (prepass.status === "skipped") {
    return (
      <div className="mb-2.5 rounded-lg border border-[var(--border)] bg-[var(--bg-sunken)] px-2.5 py-1.5 text-[0.76rem]">
        <span style={{ color: "var(--color-signal-orange)" }}>⚠ {prepass.label} unavailable</span>
        {prepass.error && <span className="text-[var(--fg-faint)]"> — {prepass.error}</span>}
        <span className="mt-0.5 block text-[var(--fg-faint)]">
          Generated without a competitor benchmark; no competitors were invented.
        </span>
      </div>
    );
  }

  return (
    <div className="mb-2.5 rounded-lg border border-[var(--border)] bg-[var(--bg-sunken)] px-2.5 py-1.5">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full cursor-pointer items-center gap-1.5 text-left text-[0.76rem]"
      >
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" className="shrink-0" style={{ color: "var(--color-signal-green)" }}>
          <path d="M5 12l5 5L20 7" stroke="currentColor" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
        <span className="flex-1 text-[var(--fg-muted)]">
          <span className="font-medium text-[var(--fg)]">{prepass.label}</span> complete — fed into this asset
        </span>
        <span className="shrink-0 text-[0.68rem] text-[var(--fg-faint)]">{open ? "Hide" : "View"}</span>
      </button>
      {open && (
        <pre className="pane-scroll mt-2 max-h-[45vh] overflow-auto whitespace-pre-wrap break-words border-t border-[var(--border)] pt-2 text-[0.7rem] leading-relaxed text-[var(--fg-muted)] sm:max-h-64">
          {prepass.content}
        </pre>
      )}
    </div>
  );
}

/** A generation's body.
 *
 * Three states, in order of how the operator meets them:
 *
 *   1. **Streaming** — the step tracker (`StreamProgress`), not the flood. Parsing here would split
 *      a document on headings that are still being written, and mounting an iframe for an unclosed
 *      fence would reload it on every chunk.
 *   2. **Structured** — the document's own sections as a closed outline. Every large prompt in
 *      `assets/Prompts/` declares its parts ("PART 0 … PART 3" on the CRO rewrite), and this reads
 *      them back out. See `lib/assetDocument.ts`.
 *   3. **Unstructured** — exactly what this function did before: Markdown, with any complete
 *      previewable HTML block lifted into a real renderer. This is the guaranteed fallback, and it
 *      is what a short document, an unusual one, or a failed parse all land on.
 */
/**
 * Map or document, for the Plan of Action.
 *
 * The map is the default because it is the only view in which the plan's shape is visible at all —
 * how many assets Phase 2 implies, which sub-service ranks first, what the month order is. But it
 * is a *view*, not a replacement: the operator who wants to walk the plan point by point, or copy a
 * brief out of it, needs the prose, and the switch is right there rather than hidden in a menu.
 *
 * Both are derived from the same text, so they cannot disagree — and a plan the tree cannot read
 * says so and leaves the document showing.
 */
function PlanSwitch({ text, label, doc }: { text: string; label: string; doc: AssetDocument }) {
  const [view, setView] = useState<"map" | "document">("map");

  const tab = (active: boolean) =>
    `cursor-pointer rounded-full px-2.5 py-[3px] text-[0.72rem] font-semibold transition-colors ${
      active ? "bg-[var(--accent)] text-[var(--accent-fg)]" : "text-[var(--fg-muted)] hover:text-[var(--fg)]"
    }`;

  return (
    <div className="mt-2">
      <div className="flex items-center gap-1.5">
        <div className="flex items-center gap-0.5 rounded-full border border-[var(--border-strong)] p-0.5">
          <button type="button" onClick={() => setView("map")} className={tab(view === "map")}>
            Plan map
          </button>
          <button type="button" onClick={() => setView("document")} className={tab(view === "document")}>
            Document
          </button>
        </div>
        <span className="text-[0.7rem] text-[var(--fg-faint)]">
          {view === "map" ? "click a node for that step in full" : "the plan as written, part by part"}
        </span>
      </div>
      <Hint>
        The map is drawn from this document, not generated separately — the two always match. Every
        word is still here: switch to <strong>Document</strong> to read the plan point by point.
      </Hint>
      {view === "map" ? (
        <PlanMindMap text={text} label={label} />
      ) : (
        <DocumentOutline doc={doc} label={label} />
      )}
    </div>
  );
}

function GenerationBody({ message, label }: { message: PipelineMessage; label: string }) {
  const text = message.text ?? "";
  const streaming = !!message.streaming;
  const openReader = useUiStore((s) => s.openReader);
  const docView = useUiStore((s) => s.docView);
  const isPlan = message.assetId === PLAN_ASSET_ID;

  // Every hook runs unconditionally and the branching happens below, so the hook order is stable
  // across a message's transition from streaming to finished.
  const doc = useMemo(() => (streaming ? null : parseAssetDocument(text)), [streaming, text]);
  const summary = useMemo(() => (doc ? summariseAsset(message.assetId, doc, text) : null), [doc, message.assetId, text]);
  const segments = useMemo(
    () => (doc && (!doc.structured || docView === "full") ? splitHtmlBlocks(text) : null),
    [doc, docView, text],
  );

  if (streaming) return <StreamProgress text={text} label={label} />;

  // `full` renders exactly what this card rendered before the outline existed. It is a real
  // preference rather than a fallback — see `DocView` — so it is checked before the outline.
  if (doc?.structured && summary && docView === "outline") {
    return (
      <div>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <DocumentMeta doc={doc} summary={summary} unit={doc.pattern === "block" ? "section" : "part"} />
          {/* On the document rather than in the action row, so a superseded draft — which gets no
              action row — can still be opened and read against the one that replaced it. */}
          <button
            type="button"
            onClick={() => openReader(message.id)}
            className="shrink-0 cursor-pointer rounded-full border border-[var(--border-strong)] px-2.5 py-1 text-[0.74rem] font-semibold hover:bg-[var(--hover)]"
          >
            Open document ↗
          </button>
        </div>
        <SummaryChips chips={summary.chips} />
        {summary.flags.some((f) => f.tone === "orange") && (
          <Hint>
            The amber count is every <code>[CLIENT TO CONFIRM: …]</code> placeholder in this
            document — the list waiting on the client. Hover it to read them, or open the document
            and jump to the implementation pack.
          </Hint>
        )}
        {/* Built files as thumbnails. A lead-magnet run emits ten standalone documents, and a
            fenced code block is the one form in which a designed artefact tells you nothing. */}
        <PreviewStrip previews={summary.previews} />
        {/* The Plan of Action is the one asset that is a *structure* rather than a piece of
            writing — three phases, a set of sub-services, a hundred named assets and an order to
            build them in — so it gets a diagram, with the document behind a visible switch rather
            than replaced. Every other asset goes straight to its outline. */}
        {isPlan ? (
          <PlanSwitch text={text} label={label} doc={doc} />
        ) : (
          <>
            <Hint>
              Each row is a part of the document this stage declares. Click one to read it here, or
              <strong> Open document</strong> for the whole thing with an outline beside it. Nothing
              has been shortened — this is the same text, closed.
            </Hint>
            <DocumentOutline doc={doc} label={label} />
          </>
        )}
      </div>
    );
  }

  if (!segments || !segments.some((s) => s.kind === "html")) {
    return (
      <div>
        <Markdown text={text} />
      </div>
    );
  }

  return (
    <div>
      {segments.map((segment, i) =>
        segment.kind === "html" ? (
          <HtmlPreview key={i} html={segment.html} label={label} />
        ) : (
          <Markdown key={i} text={segment.text} />
        ),
      )}
    </div>
  );
}

function GenerationCard({ message }: { message: PipelineMessage }) {
  const retryGeneration = usePipelineStore((s) => s.retryGeneration);
  const phase = usePipelineStore((s) => s.phase);
  const openReader = useUiStore((s) => s.openReader);
  const stage = stagesFor(phase).find((s) => s.asset.asset_id === message.assetId);

  /* An asset that was *already* approved when this card mounted opens collapsed; one the operator
   * approves while looking at it does not.
   *
   * The initialiser is what makes that distinction, and it is the whole trick: a reopened chat with
   * six saved stages renders six one-line rows instead of six full documents, while a card the
   * operator has just saved keeps its "Saved to Context Store" confirmation on screen — collapsing
   * it out from under them the instant they clicked would read as the card having gone wrong. */
  const [collapsed, setCollapsed] = useState(() => message.savePhase === "saved");

  /* Memoised, and it matters more than it looks. A stream delta replaces `messages`, so every card
   * in the transcript re-renders on every chunk — and a collapsed row counting its words inline
   * would walk its whole document each time, once per saved asset, many times a second. `countWords`
   * rather than `parseAssetDocument` for the same reason: the row needs the total, not the sections,
   * and the full parse also splits HTML blocks per section. */
  const savedWords = useMemo(() => (message.text ? countWords(message.text) : 0), [message.text]);

  const header = (
    <div className="mb-2 flex items-center gap-2">
      <span aria-hidden>{stage?.emoji}</span>
      <span className="text-[0.85rem] font-semibold">{stage?.asset.label}</span>
      <span className="rounded-full border border-[var(--border-strong)] px-1.5 py-[1px] text-[0.62rem] font-semibold text-[var(--fg-muted)]">
        Stage {String(stage?.stageNumber ?? 0).padStart(2, "0")}
      </span>
    </div>
  );

  if (collapsed && message.savePhase === "saved" && !message.streaming) {
    return (
      <div className="flex w-full min-w-0 max-w-[51rem] flex-wrap items-center gap-2 rounded-2xl border border-[var(--border)] bg-[var(--bg)] px-3 py-2 msg-rise">
        <svg
          width="13"
          height="13"
          viewBox="0 0 24 24"
          fill="none"
          aria-hidden
          className="shrink-0"
          style={{ color: "var(--color-signal-green)" }}
        >
          <path d="M5 12l5 5L20 7" stroke="currentColor" strokeWidth="2.8" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
        <span className="shrink-0 rounded-full border border-[var(--border-strong)] px-1.5 py-[1px] text-[0.6rem] font-semibold text-[var(--fg-muted)]">
          {String(stage?.stageNumber ?? 0).padStart(2, "0")}
        </span>
        <span aria-hidden>{stage?.emoji}</span>
        <span className="min-w-0 flex-1 truncate text-[0.82rem] font-semibold">{stage?.asset.label}</span>
        <span className="shrink-0 text-[0.7rem] tabular-nums text-[var(--fg-faint)]">
          {savedWords.toLocaleString()} words
        </span>
        {message.text && (
          <button
            type="button"
            onClick={() => openReader(message.id)}
            className="shrink-0 cursor-pointer rounded-full border border-[var(--border-strong)] px-2.5 py-1 text-[0.72rem] font-semibold hover:bg-[var(--hover)]"
          >
            Open ↗
          </button>
        )}
        <button
          type="button"
          onClick={() => setCollapsed(false)}
          className="shrink-0 cursor-pointer rounded-full px-2 py-1 text-[0.72rem] font-medium text-[var(--fg-muted)] hover:bg-[var(--hover)]"
        >
          Expand
        </button>
      </div>
    );
  }

  if (message.generationError) {
    return (
      <div
        className="w-full min-w-0 max-w-[51rem] rounded-2xl border-2 bg-[var(--bg-raised)] px-3 py-3 msg-rise @[30rem]:px-4 @[30rem]:py-3.5"
        style={{ borderColor: "var(--color-signal-orange)" }}
      >
        {header}
        {message.prepass && <PrepassChip prepass={message.prepass} />}
        <p className="text-[0.85rem]" style={{ color: "var(--color-signal-orange)" }}>
          Generation failed: {message.generationError}
        </p>
        <motion.button
          type="button"
          onClick={() => retryGeneration(message.id)}
          whileTap={{ scale: 0.97 }}
          className="mt-3 min-h-10 cursor-pointer rounded-full px-3.5 py-1.5 text-[0.8rem] font-semibold text-white sm:min-h-0"
          style={{ backgroundColor: "var(--color-electric-blue)" }}
        >
          Retry Generation
        </motion.button>
      </div>
    );
  }

  return (
    <div
      className="w-full min-w-0 max-w-[51rem] rounded-2xl border border-[var(--border)] bg-[var(--bg-raised)] px-3 py-3 msg-rise @[30rem]:px-4 @[30rem]:py-3.5"
      style={message.superseded ? { opacity: 0.6 } : undefined}
    >
      {header}
      {message.prepass && <PrepassChip prepass={message.prepass} />}
      {/* This draft was generated from answers the operator has since changed. Kept for comparison,
          but its Save button is gone: approving it would put superseded answers into the Context
          Store, and every later stage reads from there. */}
      {message.superseded && (
        <div className="mb-2.5 rounded-lg border border-[var(--border)] bg-[var(--bg-sunken)] px-2.5 py-1.5 text-[0.76rem] text-[var(--fg-muted)]">
          Replaced — an answer changed after this draft was generated, so it's been regenerated below.
        </div>
      )}
      {/* A draft cut off mid-write — by the tab closing, or by the connection dropping before the
          stream finished — is kept rather than discarded, since it may be most of the asset. But it
          must never be mistaken for a finished one, hence the warning and the regenerate button
          sitting above the ordinary Save/Refine row. */}
      {message.interrupted && message.savePhase !== "saved" && (
        <div
          className="mb-2.5 rounded-lg border px-2.5 py-2 text-[0.76rem]"
          style={{ borderColor: "var(--color-signal-orange)" }}
        >
          <span style={{ color: "var(--color-signal-orange)" }}>⚠ This draft is incomplete</span>
          <span className="text-[var(--fg-muted)]">
            {" "}
            — it stopped part-way through, so what's below is only as far as it got.
          </span>
          <button
            type="button"
            onClick={() => void retryGeneration(message.id)}
            className="mt-1.5 block cursor-pointer rounded-full border border-[var(--border-strong)] px-2.5 py-1 text-[0.74rem] font-semibold"
          >
            Generate it again
          </button>
        </div>
      )}
      <GenerationBody message={message} label={stage?.asset.label ?? "Generated page"} />
      {!message.streaming && !message.superseded && (
        <ActionRow
          message={message}
          label={stage?.asset.label ?? "Generated asset"}
          stageNumber={stage?.stageNumber}
        />
      )}
      {/* Offered only once the asset is approved, which is the point at which the card stops being
          something to act on and becomes something to keep. Before that, collapsing a draft the
          operator still has to review would hide the decision. */}
      {message.savePhase === "saved" && !message.streaming && (
        <button
          type="button"
          onClick={() => setCollapsed(true)}
          className="mt-2 cursor-pointer text-[0.74rem] font-medium text-[var(--fg-faint)] hover:text-[var(--fg)]"
        >
          Collapse this asset
        </button>
      )}
    </div>
  );
}

function QuestionCard({ message }: { message: PipelineMessage }) {
  const submitAnswer = usePipelineStore((s) => s.submitAnswer);
  const skipField = usePipelineStore((s) => s.skipField);
  const cancelEdit = usePipelineStore((s) => s.cancelEdit);
  const intake = usePipelineStore((s) => s.intake);
  const field = message.field;
  if (!field) return null;

  const isActive = !message.answered && !message.superseded && intake?.awaitingFieldId === field.field_id;
  const showWidget = field.kind === "enum_choice" || field.kind === "multi_select" || field.kind === "boolean_flag";

  return (
    <div
      className="w-full min-w-0 max-w-[35rem] rounded-2xl border border-[var(--border)] bg-[var(--bg-raised)] px-3 py-3 msg-rise @[30rem]:px-4 @[30rem]:py-3.5"
      style={message.superseded ? { opacity: 0.6 } : undefined}
    >
      <div className="flex items-baseline gap-2">
        <div className="flex-1 text-[0.92rem] font-medium">
          {message.editing && !message.superseded && (
            <span className="mr-1.5 text-[0.72rem] font-semibold uppercase tracking-wide" style={{ color: "var(--color-electric-blue)" }}>
              Editing
            </span>
          )}
          {field.label}
          {!field.required && <span className="ml-1.5 text-[0.72rem] font-normal text-[var(--fg-faint)]">optional</span>}
        </div>
        {/* Only the newest card for a field offers the edit — an older, superseded one would put the
            operator back into a change they already made. */}
        {message.answered && !message.superseded && <EditAnswerButton fieldId={field.field_id} label={field.label} />}
      </div>
      <FieldHint field={field} compact parts={isActive ? "all" : "hint"} />
      {isActive ? (
        <>
          {showWidget ? (
            <QuestionWidget field={field} onChoose={(v) => submitAnswer(v)} onSkip={skipField} />
          ) : (
            <p className="mt-2 text-[0.76rem] italic text-[var(--fg-faint)]">
              {message.editing ? "Type the corrected answer below…" : "Type your answer below…"}
            </p>
          )}
          {/* Under the answer line, not instead of it: the client usually does have a page, and
              that path stays a single action. */}
          {message.pageSource && !message.editing && <PageSourceOptions message={message} />}
          {message.editing && (
            <button
              type="button"
              onClick={cancelEdit}
              className="mt-2 cursor-pointer text-[0.74rem] font-medium text-[var(--fg-faint)] hover:text-[var(--fg)]"
            >
              Keep the previous answer
            </button>
          )}
        </>
      ) : (
        <p className="mt-2 text-[0.76rem] italic text-[var(--fg-faint)]">
          {message.superseded ? (message.answered ? "replaced" : "asked again below") : "answered"}
        </p>
      )}
    </div>
  );
}

/** The two answers to "what if there is no such page" — offered on the page-source question
 * itself, under the normal "type your answer" line.
 *
 * The case: the ICP is for Social Media Marketing for e-commerce and the client has never had a
 * page for that service. The CRO stage audits an existing page, its URL field is `required`, and
 * nothing said what to do — so the page nominated was usually the home page, which is not what the
 * audit is about *and* becomes the DESIGN.md source, quietly making every generated page in the
 * run a variation on the client's home page.
 *
 * Order is a recommendation, and the first option is the recommended one. Build-from-scratch is a
 * mode the master prompt already has, and it still produces the three documents the page build
 * reads — the rewritten copy, the locked sections, the terminology map. Skipping the stage does not
 * produce them, so it moves the copy problem to the next stage rather than solving it. Both are
 * offered because the second is what an operator who only wants a page will ask for, and the gate
 * it opens states that cost plainly rather than discovering it three questions later.
 */
function PageSourceOptions({ message }: { message: PipelineMessage }) {
  const declareNewPage = usePipelineStore((s) => s.declareNewPage);
  const skipStageForNewPage = usePipelineStore((s) => s.skipStageForNewPage);
  const phase = usePipelineStore((s) => s.phase);

  const source = message.pageSource;
  if (!source) return null;

  const option = NEW_PAGE_OPTIONS[source.assetId];
  if (!option) return null;

  const subject = source.subject;
  const instead = ASSET_BY_ID[option.insteadAssetId]?.label ?? option.insteadAssetId;
  const canSkipHere = stagesFor(phase).some((s) => s.asset.asset_id === option.insteadAssetId);

  return (
    <div className="mt-2.5 rounded-xl border border-dashed border-[var(--border)] bg-[var(--bg-sunken)] px-3 py-2.5">
      <div className="text-[0.76rem] font-semibold">
        No page for {subject ? `“${subject}”` : "this service"} yet?
      </div>
      <p className="mt-0.5 text-[0.72rem] leading-relaxed text-[var(--fg-muted)]">
        Don't nominate the home page — the audit would be about the wrong page, and it would become
        the design reference for every page this run builds.
      </p>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <motion.button
          type="button"
          onClick={() => declareNewPage(message.id)}
          whileTap={{ scale: 0.97 }}
          className="min-h-10 cursor-pointer rounded-full px-3.5 py-1.5 text-[0.78rem] font-semibold text-white sm:min-h-0"
          style={{ backgroundColor: "var(--color-electric-blue)" }}
        >
          There's no page — build it from scratch
        </motion.button>
        {canSkipHere && (
          <motion.button
            type="button"
            onClick={() => skipStageForNewPage(message.id)}
            whileHover={{ backgroundColor: "var(--hover)" }}
            whileTap={{ scale: 0.97 }}
            className="min-h-10 cursor-pointer rounded-full border border-[var(--border-strong)] px-3.5 py-1.5 text-[0.78rem] font-medium sm:min-h-0"
          >
            Skip to {instead}
          </motion.button>
        )}
      </div>
      <p className="mt-1.5 text-[0.68rem] leading-relaxed text-[var(--fg-faint)]">
        Building from scratch keeps this stage: it writes the page copy, the locked sections and the
        terminology map that {instead} needs. Skipping means {instead} asks you for those instead.
      </p>
    </div>
  );
}

/** The stop control, shown whenever a stage is in progress.
 *
 * One place for it rather than one per state. A stage can be mid-question, mid-competitor-scan,
 * mid-stream or sitting on an unsaved draft, and an operator who has decided to work on something
 * else wants out of all four — a button that appeared in only some of them would read as broken in
 * the rest. So it sits above the transcript, naming the stage it would stop.
 *
 * The confirm is inline and one extra click, not a modal. Mid-intake there is little to lose; mid-
 * stream there is a paid request in flight, and a misclick that threw away a half-written asset
 * would be the expensive kind of undoable. Saying which of the two it is makes the choice
 * informed rather than cautious by default.
 */
function StopStageBar() {
  const canStop = usePipelineStore(selectCanStop);
  // `useShallow`, not a bare selector. `selectStoppableStage` builds a fresh `{ assetId, label }`
  // on every call, and zustand v5 dropped the equality argument that used to absorb that: its
  // `useSyncExternalStore` snapshot is compared with `Object.is`, so a new object each time reads
  // as "the store changed" on every commit and re-renders forever. That is a "Maximum update depth
  // exceeded" throw, and with no boundary above it it took the whole app down to a white screen on
  // any chat with a stage in progress. Compare by value instead.
  const target = usePipelineStore(useShallow(selectStoppableStage));
  const streaming = usePipelineStore((s) => s.messages.some((m) => m.streaming));
  const stopStage = usePipelineStore((s) => s.stopStage);
  const [confirming, setConfirming] = useState(false);

  // Asking to confirm a stop that is no longer available would leave the prompt on screen with
  // nothing behind it — a stage that finished or was saved while the question was up.
  useEffect(() => {
    if (!canStop) setConfirming(false);
  }, [canStop]);

  if (!canStop || !target) return null;

  return (
    <div className="flex shrink-0 flex-wrap items-center gap-x-2 gap-y-1.5 border-b border-[var(--border)] bg-[var(--bg-sunken)] px-3 py-1.5 sm:px-6">
      <span className="text-[0.72rem] text-[var(--fg-muted)]">
        Working on <span className="font-semibold text-[var(--fg)]">{target.label}</span>
      </span>
      {confirming ? (
        <>
          <span className="text-[0.72rem] text-[var(--fg-muted)]">
            {streaming
              ? "Stop it? The request is cancelled and the part-written draft is discarded."
              : "Stop it? The answers given so far are discarded."}
          </span>
          <div className="ml-auto flex items-center gap-1.5">
            <button
              type="button"
              onClick={() => {
                stopStage();
                setConfirming(false);
              }}
              className="min-h-8 cursor-pointer rounded-full px-2.5 py-1 text-[0.72rem] font-semibold text-white sm:min-h-0"
              style={{ backgroundColor: "var(--color-signal-orange)" }}
            >
              Stop {target.label}
            </button>
            <button
              type="button"
              onClick={() => setConfirming(false)}
              className="min-h-8 cursor-pointer rounded-full border border-[var(--border-strong)] px-2.5 py-1 text-[0.72rem] font-medium sm:min-h-0"
            >
              Keep going
            </button>
          </div>
        </>
      ) : (
        <button
          type="button"
          onClick={() => setConfirming(true)}
          className="ml-auto min-h-8 cursor-pointer rounded-full border border-[var(--border-strong)] px-2.5 py-1 text-[0.72rem] font-medium text-[var(--fg-muted)] transition-colors hover:text-[var(--fg)] sm:min-h-0"
        >
          Stop this asset
        </button>
      )}
    </div>
  );
}

/** What is left where a stopped stage was.
 *
 * The stage is not gone: nothing was deleted from the pipeline and nothing approved was touched, so
 * the only honest thing to say is what was discarded and what the two ways forward are. It counts
 * as the operator's turn (see `selectNeedsResume`), which is what stops the resume banner appearing
 * underneath it to suggest continuing the stage they just walked out of. */
function StageStoppedCard({ message }: { message: PipelineMessage }) {
  const startAtStage = usePipelineStore((s) => s.startAtStage);
  const stopped = message.stopped;
  if (!stopped) return null;

  return (
    <div className="w-full min-w-0 max-w-[35rem] rounded-2xl border border-[var(--border)] bg-[var(--bg-raised)] px-3 py-3 msg-rise @[30rem]:px-4 @[30rem]:py-3.5">
      <div className="text-[0.92rem] font-medium">{stopped.label} stopped</div>
      <p className="mt-1 text-[0.8rem] leading-relaxed text-[var(--fg-muted)]">
        {stopped.wasGenerating
          ? "The request was cancelled and the part-written draft discarded. "
          : "The answers given for it were discarded. "}
        Nothing you had already approved has changed, and {stopped.label} is still in the pipeline to
        run whenever you want it.
      </p>
      <Hint>
        Pick any stage from the Asset Pipeline panel to start it — each one checks what it needs
        against this run first.
      </Hint>
      <div className="mt-2.5 flex flex-wrap items-center gap-2">
        <motion.button
          type="button"
          onClick={() => startAtStage(stopped.assetId)}
          whileTap={{ scale: 0.97 }}
          className="min-h-10 cursor-pointer rounded-full border border-[var(--border-strong)] px-3.5 py-1.5 text-[0.8rem] font-medium sm:min-h-0"
        >
          Start {stopped.label} again
        </motion.button>
      </div>
    </div>
  );
}

function TextBubble({ message }: { message: PipelineMessage }) {
  const isUser = message.role === "user";
  return (
    <div
      className={`min-w-0 max-w-[88%] rounded-2xl px-3.5 py-2.5 text-[0.92rem] leading-relaxed msg-rise @[30rem]:max-w-[34rem] @[30rem]:px-4 ${
        isUser ? "bg-[var(--accent)] text-[var(--accent-fg)]" : "border border-[var(--border)] bg-[var(--bg-raised)]"
      }`}
    >
      {message.text}
      {/* An answer that was filled in rather than asked about still needs a way to be corrected. */}
      {message.editableFields?.length ? (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {message.editableFields.map((f) => (
            <EditAnswerButton key={f.fieldId} fieldId={f.fieldId} label={f.label} variant="chip" />
          ))}
        </div>
      ) : null}
    </div>
  );
}

function WelcomeState() {
  const start = usePipelineStore((s) => s.start);
  const phase = usePipelineStore((s) => s.phase);
  const meta = PHASE_META[phase];
  const first = stagesFor(phase)[0];
  return (
    <div className="flex h-full flex-col items-center justify-center px-5 py-8 text-center sm:px-6">
      <h1 className="text-balance text-[1.25rem] font-semibold sm:text-[1.4rem] lg:text-[1.65rem]">
        Build your {meta.label} asset stack
      </h1>
      <p className="mt-2 max-w-[26rem] text-pretty text-[0.88rem] text-[var(--fg-muted)] sm:text-[0.92rem]">
        {totalStagesFor(phase)} assets for {meta.scope.toLowerCase()}, each built from its own real prompt. I'll ask a few
        questions per stage, generate, and save to your Context Store as you approve each one.
        {phase === "phase2"
          ? " First, which Phase 1 run this sits under and which sub-service it's for."
          : ""}
      </p>
      {/* The toggle lives in the pipeline pane, which is a tab away on a phone — so the phase is
          named again here, where the run is actually started. */}
      <p className="mt-2 text-[0.76rem] text-[var(--fg-faint)]">
        Switch phase from the Asset Pipeline panel.
      </p>
      <motion.button
        type="button"
        onClick={start}
        whileHover={{ y: -1 }}
        whileTap={{ scale: 0.97 }}
        className="mt-6 min-h-12 cursor-pointer rounded-full px-5 py-2.5 text-[0.9rem] font-semibold text-white sm:min-h-0"
        style={{ backgroundColor: "var(--color-electric-blue)" }}
      >
        {/* Phase 2 does not open on a stage. It asks which Phase 1 run this sub-service sits under
            and which sub-service it is, both of which belong to the run rather than to stage 01 —
            so promising the first asset here would misdescribe the next two cards. */}
        {phase === "phase2" ? "Start — pick the Phase 1 run" : `Generate ${first.asset.label} — Stage 01`}
      </motion.button>
    </div>
  );
}

/** Shown on a reopened chat that came back with nothing to act on — see `selectNeedsResume`.
 * Without it such a chat is a dead end: everything in it is already approved, but the next stage's
 * first question was never asked because the tab closed in between. */
function ResumeBanner() {
  const resumeStage = usePipelineStore((s) => s.resumeStage);
  const currentIndex = usePipelineStore((s) => s.currentIndex);
  const phase = usePipelineStore((s) => s.phase);
  const stage = stageAt(phase, currentIndex);

  return (
    <div className="flex justify-start">
      <div className="w-full min-w-0 max-w-[35rem] rounded-2xl border border-[var(--border)] bg-[var(--bg-raised)] px-3 py-3 msg-rise @[30rem]:px-4 @[30rem]:py-3.5">
        <div className="text-[0.9rem] font-medium">Pick up where you left off</div>
        <p className="mt-1 text-[0.8rem] leading-relaxed text-[var(--fg-muted)]">
          Everything generated so far is saved. Next up is Stage{" "}
          {String(stage.stageNumber).padStart(2, "0")} — {stage.asset.label}.
        </p>
        <motion.button
          type="button"
          onClick={resumeStage}
          whileTap={{ scale: 0.97 }}
          className="mt-3 min-h-10 cursor-pointer rounded-full px-3.5 py-1.5 text-[0.8rem] font-semibold text-white sm:min-h-0"
          style={{ backgroundColor: "var(--color-electric-blue)" }}
        >
          Continue — Stage {String(stage.stageNumber).padStart(2, "0")}
        </motion.button>
      </div>
    </div>
  );
}

function MessageRow({ message }: { message: PipelineMessage }) {
  let content: React.ReactNode;
  switch (message.kind) {
    case "generation":
      content = <GenerationCard message={message} />;
      break;
    case "question":
      content = <QuestionCard message={message} />;
      break;
    case "competitor":
      content = <CompetitorCard message={message} />;
      break;
    case "competitor-consent":
      content = <CompetitorConsentCard message={message} />;
      break;
    case "context-choice":
      content = <ContextChoiceCard message={message} />;
      break;
    case "headline-choice":
      content = <HeadlineChoiceCard message={message} />;
      break;
    case "scrape":
      content = <ScrapeCard message={message} />;
      break;
    case "source-run":
      content = <SourceRunCard message={message} />;
      break;
    case "briefing":
      content = <BriefingCard message={message} />;
      break;
    case "stage-gate":
      content = <StageGateCard message={message} />;
      break;
    case "stage-stopped":
      content = <StageStoppedCard message={message} />;
      break;
    default:
      content = <TextBubble message={message} />;
  }

  return (
    <div className={`flex min-w-0 ${message.role === "user" ? "justify-end" : "justify-start"}`}>{content}</div>
  );
}

/** Transcript / Deliverables.
 *
 * Only shown once something has been approved: before that the Deliverables tab is a tab onto an
 * empty state, which is a worse first impression than no tab. */
function WorkTabs({ savedCount }: { savedCount: number }) {
  const workTab = useUiStore((s) => s.workTab);
  const setWorkTab = useUiStore((s) => s.setWorkTab);

  const tab = (active: boolean) =>
    `-mb-px flex cursor-pointer items-center gap-1.5 border-b-2 px-3 py-1.5 text-[0.78rem] font-semibold transition-colors ${
      active
        ? "border-[var(--accent)] text-[var(--fg)]"
        : "border-transparent text-[var(--fg-faint)] hover:text-[var(--fg-muted)]"
    }`;

  return (
    <div className="flex shrink-0 gap-1 border-b border-[var(--border)] px-3 pt-1 sm:px-6" role="tablist">
      <button
        type="button"
        role="tab"
        aria-selected={workTab === "transcript"}
        onClick={() => setWorkTab("transcript")}
        className={tab(workTab === "transcript")}
      >
        Transcript
      </button>
      <button
        type="button"
        role="tab"
        aria-selected={workTab === "deliverables"}
        onClick={() => setWorkTab("deliverables")}
        className={tab(workTab === "deliverables")}
      >
        Deliverables
        <span className="rounded-full border border-[var(--border)] bg-[var(--bg-sunken)] px-1.5 text-[0.62rem] tabular-nums">
          {savedCount}
        </span>
      </button>
    </div>
  );
}

export function GenerationStream() {
  const started = usePipelineStore((s) => s.started);
  const allMessages = usePipelineStore((s) => s.messages);
  const phase = usePipelineStore((s) => s.phase);
  const activePhase2TrackId = usePipelineStore((s) => s.activePhase2TrackId);
  const isLoadingSession = usePipelineStore((s) => s.isLoadingSession);
  const needsResume = usePipelineStore(selectNeedsResume);
  const awaitingFieldId = usePipelineStore((s) => s.intake?.awaitingFieldId);
  const awaitingField = usePipelineStore(fieldBeingAsked);
  const demoMode = useUiStore((s) => s.demoMode);
  const workTab = useUiStore((s) => s.workTab);
  const scrollRef = useRef<HTMLDivElement>(null);

  // The transcript is one phase's leg, not the whole chat. Both legs live in the same session — the
  // same history row, the same client, the same Phase 1 run underneath — but Phase 2 is its own
  // piece of work, and reading its first question at the bottom of fifteen finished Phase 1 assets
  // buries it. So switching phase clears the screen rather than the chat: the other leg is still
  // there, exactly where it was left, one click away on the toggle.
  const messages = messagesInPhase(allMessages, phase, activePhase2TrackId);

  const lastText = messages[messages.length - 1]?.text;
  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [lastText, messages.length]);

  /* Ahead of every other branch, including the welcome state: the sample is a look at the
   * interface, and gating it behind having started a run would make it unavailable exactly when
   * somebody most wants to see what the app does. It reads no run state, so there is nothing for it
   * to be missing. */
  if (demoMode) {
    return (
      <div className="@container flex h-full min-w-0 flex-col">
        <div className="pane-scroll min-h-0 flex-1 overflow-y-auto px-3 py-4 sm:px-6 sm:py-6 lg:px-8">
          <DemoTranscript />
        </div>
      </div>
    );
  }

  if (isLoadingSession) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 px-5 text-center sm:px-6">
        <TypingIndicator />
        <p className="text-[0.85rem] text-[var(--fg-muted)]">Opening chat…</p>
      </div>
    );
  }

  if (!started) return <WelcomeState />;

  const awaitingContextChoice = messages.some(
    (m) => m.kind === "context-choice" && m.contextChoiceStatus === "pending" && !m.superseded,
  );
  const showInputBar =
    !!awaitingFieldId &&
    !!awaitingField &&
    !awaitingContextChoice &&
    awaitingField.kind !== "enum_choice" &&
    awaitingField.kind !== "boolean_flag";

  const savedCount = messages.filter(
    (m) => m.kind === "generation" && m.savePhase === "saved" && !m.superseded && m.text,
  ).length;
  // The tab only exists once something is approved, so a stale `workTab` must not strand the
  // operator on a surface with no way back to the conversation.
  const onDeliverables = workTab === "deliverables" && savedCount > 0;

  return (
    // A query container so the cards below size themselves against the transcript column rather than
    // the window: that column is ~62% of a desktop and 100% of a phone, and at `lg` exactly the
    // moment the window gets wider is the moment this pane gets *narrower* (the sidebar docks and
    // the diagram appears alongside). Viewport breakpoints would read that backwards.
    <div className="@container flex h-full min-w-0 flex-col">
      {savedCount > 0 && <WorkTabs savedCount={savedCount} />}
      {/* Above the transcript and outside the scroll area: a stop that scrolls away with the
          conversation is not there at the moment somebody wants it. Hidden on the Deliverables tab,
          which is finished work and has no stage in progress on screen. */}
      {!onDeliverables && <StopStageBar />}
      <div ref={scrollRef} className="pane-scroll min-h-0 flex-1 overflow-y-auto px-3 py-4 sm:px-6 sm:py-6 lg:px-8">
        {onDeliverables ? (
          <DeliverablesGrid />
        ) : (
          <div className="mx-auto flex w-full max-w-[55rem] flex-col gap-2.5 sm:gap-3">
            {messages.map((m) => (
              <MessageRow key={m.id} message={m} />
            ))}
            {needsResume && <ResumeBanner />}
          </div>
        )}
      </div>
      {/* Hidden on the Deliverables tab: the answer box belongs to the conversation, and leaving it
          under a grid of finished files invites an answer to a question that is off screen. */}
      {showInputBar && !onDeliverables && <PipelineInputBar />}
    </div>
  );
}
