import { create } from "zustand";
import { ASSET_BY_ID, producerAssetIdFor } from "../data/assetCatalog";
import type { AssetDefinition, FieldDef } from "../data/types";
import { findNextAskable, resolveContext } from "../lib/fieldResolution";
import { useChatSessionsStore } from "../store/chatSessionsStore";
import type { ContextEntry, ContextStore } from "../store/chatStore.types";
import {
  buildRunKeywords,
  createChatSession,
  createRun,
  fetchAssetReadiness,
  fetchCompetitorBriefing,
  fetchRunContext,
  getChatSession,
  listHeadlineSlots,
  listSourceRuns,
  runCompetitorAnalysis,
  saveCompetitorAnalysis,
  saveHeadlineSelection,
  saveStageOutput,
  scrapePage,
  seedRunContext,
  streamGenerateStage,
  streamRefineStage,
  suggestHeadlines,
  updateChatSession,
} from "./pipelineApi";
import { ApiFaultError, StreamTruncatedError } from "./pipelineApi";
import type {
  ApiFault,
  AssetReadiness,
  CompetitorAnalysisResult,
  HeadlineCandidate,
  PrepassEvent,
  SourceRunSummary,
} from "./pipelineApi";
import {
  CLIENT_PROFILE_SOURCES,
  COMPETITOR_BRIEFING_STAGES,
  COMPETITOR_CONSENT_FIELDS_BY_PHASE,
  FIELD_TO_FACT_BY_PHASE,
  GATED_COMPETITOR_IDS_BY_PHASE,
  NEW_PAGE_OPTIONS,
  PHASE_META,
  PREPASS_BY_MAIN_ASSET_BY_PHASE,
  SCRAPE_SOURCES,
  SUB_SERVICE_FIELD,
  competitorStageFor,
  stageAt,
  stagesFor,
  totalStagesFor,
} from "./pipelineData";
import type { PipelinePhase } from "./pipelineData";
import { SUB_SERVICE_FACT } from "../data/phase2Catalog";
import { SERVER_COMPOSED_CONTEXT_KEYS } from "../data/assetCatalog";

export type NavStatus = "Ready" | "Awaiting Input" | "Generating…" | "Awaiting Review";
export type ActiveStatus = "running" | "hitl" | null;
export type MessageSavePhase = "idle" | "saving" | "saved" | "error";
export type PipelineMessageKind =
  | "text"
  | "question"
  | "generation"
  | "competitor"
  | "competitor-consent"
  | "context-choice"
  /** At least ten suggested topics for a field the operator would otherwise have typed cold. */
  | "headline-choice"
  | "scrape"
  /** Phase 2 only: which finished Phase 1 run this sub-service run builds on. */
  | "source-run"
  /** Phase 2 only: what an approved competitor set says about the market, read before the stage's
   * own intake. Informational — there is nothing to approve, so it has no save gate. */
  | "briefing"
  /** The documents a stage needs, checked against the run, before it is entered out of order. */
  | "stage-gate"
  /** A stage the operator stopped part-way, and what they can do next. */
  | "stage-stopped";

/** A `kind: "stage-gate"` card: the documents `assetId` needs, checked against this run, shown
 * before the stage is entered out of order.
 *
 * The pipeline's default is to run in order and this is what lets an operator skip anyway — see
 * `docs/Asset_Dependency_Map.md`. Every judgement on the card comes from the server
 * (`GET /runs/{id}/readiness/{asset}`) rather than from the catalog here, because only the run
 * knows what it already holds: the same stage is one paste away from running on one run and four
 * stages away on another.
 *
 * What the card must never do is offer to satisfy the competitor scan. It runs itself inside the
 * stage, which is why `readiness.prepass_fields` is a separate list from `readiness.dependencies`
 * — `lead_magnet`'s competitor list is a *required* field that nothing upstream produces. */
export interface StageGateState {
  assetId: string;
  /** Index into this phase's stage list, i.e. where entering parks `currentIndex`. */
  index: number;
  status: "loading" | "pending" | "entered" | "error";
  readiness?: AssetReadiness;
  error?: string;
  /** The context key whose paste box is open, if any. One at a time: pasting a document is the
   * slow path and two open boxes read as two halves of one answer. */
  providing?: string | null;
  /** The context key currently being written, so its button can say so. */
  seeding?: string | null;
  seedError?: string;
}

/** The auto-run competitor-analysis prepass attached to a generation message, for the 10 stages
 * that have one. Rendered as a status chip on the generation card — it has no review gate of its
 * own (the operator reviews the main asset that consumed it), but it IS saved to the Context
 * Store alongside that asset, since it's a real asset_definitions row in its own right. */
export interface PrepassState {
  assetId: string;
  label: string;
  status: "running" | "done" | "skipped";
  content?: string;
  error?: string;
}

/** A page being read on the operator's behalf, in place of asking them to paste it — see
 * `SCRAPE_SOURCES` in `pipelineData.ts`. Held on its own message so the read is visible while it
 * happens and inspectable afterwards: this text becomes the page the CRO audit quotes, so "what
 * exactly did it read?" has to be answerable without leaving the chat. */
export interface ScrapeState {
  /** The intake field this read is filling. */
  fieldId: string;
  fieldLabel: string;
  url: string;
  status: "running" | "done" | "error";
  finalUrl?: string;
  title?: string | null;
  content?: string;
  wordCount?: number;
  charCount?: number;
  /** Page came back too thin to trust (usually client-rendered) — the operator is asked to paste
   * instead, and this text is offered as a starting point. */
  lowContent?: boolean;
  /** Which reader answered — "claude" means the site refused a direct read (or rendered its copy in
   * the browser) and Anthropic's fetcher was used instead. */
  source?: string;
  warnings?: string[];
  error?: string;
}

/** The inputs one competitor-analysis run was made with. Held on the message so a re-run repeats
 * the same search rather than falling back to the run-level profile — for Pillar Page the topic is
 * the whole point of the search, and it lives nowhere but here. */
export interface CompetitorRunInputs {
  target_url: string;
  niche?: string;
  location?: string;
  service?: string;
}

/** The permission step in front of a mid-intake competitor search: what would be searched for, and
 * which field the result would fill. Asked rather than assumed, because an operator who already has
 * a curated competitor list should not have to sit through a search to get past it. */
export interface CompetitorConsentState {
  competitorAssetId: string;
  label: string;
  /** The topic the search would be run on — this stage's own head term / working title.
   *
   * One topic, even where the field holds several: a multi-select gate (blog, podcast) answers its
   * field with a numbered list, and a search run on five titles at once returns nothing useful.
   * `firstSelectedTopic` takes the first, which the suggestion service ordered by demand. */
  topic: string;
  /** How many topics that field actually holds, when it holds more than one. The card says so —
   * an operator who picked five and is shown one is otherwise entitled to think four were lost. */
  topicCount?: number;
  /** What is being searched for, for the card's own sentence: "pillar pages", "blogs", "webinars". */
  subject: string;
  targetUrl: string;
  location?: string;
  niche?: string;
  /** The intake field the approved analysis fills. */
  fieldId: string;
  status: "pending" | "accepted" | "declined";
}

/** The competitor briefing attached to a `kind: "briefing"` message.
 *
 * It is a reading of an approved competitor listing, not a verified record of one, and it is never
 * saved to the Context Store — the analysis it was read from is what gets persisted. So it carries
 * its own status rather than a `savePhase`: there is nothing here to approve or reject, only
 * something to have read before answering the next few questions. */
export interface BriefingState {
  assetId: string;
  title: string;
  blurb: string;
  status: "running" | "done" | "error";
  summary?: string;
  error?: string;
}

/** The suggestion gate attached to a `kind: "headline-choice"` message.
 *
 * Held on the message, not in a single slice of store state, for the same reason every other card
 * here is: an operator can edit an earlier answer and land back on this field, and the first card
 * has to stay in the transcript as history (`superseded`) while a second one asks again. A
 * store-level "current suggestions" would be overwritten by the second and lose the first.
 *
 * `rejected` accumulates across re-rolls rather than being replaced, so "show me ten more" keeps
 * excluding everything already turned down — otherwise round three re-offers round one. */
export interface HeadlineChoiceState {
  slot: string;
  status: "loading" | "pending" | "chosen" | "error";
  /** What the whole batch is anchored on — Phase 1's service or Phase 2's sub-service. Shown on
   * the card, because "are these actually about my service?" is the question an operator has when
   * a machine hands them ten headlines. */
  serviceAnchor?: string;
  /** Where the anchor came from, so a broad one can be labelled as broad rather than silently
   * producing broad topics. */
  anchorSource?: string;
  label?: string;
  subject?: string;
  channel?: string;
  charBudget?: string;
  multi?: boolean;
  suggestedSelection?: number;
  candidates?: HeadlineCandidate[];
  /** True when a keyword report backed these. False means framework-only grounding — still usable,
   * but no candidate carries demand evidence, and the card says so rather than staying quiet. */
  groundedInKeywords?: boolean;
  webSearchUsed?: boolean;
  /** Dropped before the operator saw them, for not being about the anchored service. Surfaced as a
   * count: a batch that comes back short should say why rather than looking thin. */
  rejectedCount?: number;
  /** Every headline offered so far in this gate, so a re-roll can exclude all of them. */
  rejected?: string[];
  /** How many rows the last re-roll actually added, after duplicates of what is already on screen
   * were dropped. Zero is the one value worth rendering: the operator clicked, waited, and the list
   * did not change, and a card that says nothing there looks broken rather than exhausted. */
  lastAdded?: number;
  chosenIds?: string[];
  /** Set when the operator wrote their own instead of picking one. */
  ownHeadline?: string;
  error?: string;
  /** A re-roll is in flight over an already-rendered batch — the list stays visible and usable
   * rather than collapsing back to a spinner. */
  reloading?: boolean;
  /** Incremented on every request for this card. The progress panel keys off it so a retry
   * restarts the phase script from the top instead of resuming mid-sequence - a freshly
   * restarted call still showing the final step would be reporting work it has not done. */
  attempt?: number;
}

/** A field an announcement message offers to edit. `label` is carried rather than looked up so
 * the chip reads the same as the question did, even for a field on an earlier stage's asset. */
export interface EditableFieldRef {
  fieldId: string;
  label: string;
}

export interface PipelineMessage {
  id: string;
  role: "user" | "assistant";
  kind: PipelineMessageKind;
  text?: string;
  assetId?: string;
  field?: FieldDef;
  answered?: boolean;
  streaming?: boolean;
  answers?: Record<string, string>;
  prepass?: PrepassState;
  /** Populated on `kind: "competitor"` messages — the parsed listing the operator reviews. */
  competitor?: CompetitorAnalysisResult;
  competitorError?: string;
  /** What that listing was searched for, so "Re-run" repeats the same search. */
  competitorInputs?: CompetitorRunInputs;
  /** Set when this analysis is filling an intake field mid-stage rather than gating the stage:
   * approving it answers that field and the intake carries on from there. */
  competitorFillsFieldId?: string;
  /** Populated on `kind: "competitor-consent"` — the offer to run the search. */
  consent?: CompetitorConsentState;
  /** Populated on `kind: "scrape"` — a page being read in place of a paste. */
  scrape?: ScrapeState;
  /** This card has been overtaken by an edit: a question re-asked, or a draft whose answers were
   * changed and which is being regenerated. It stays in the transcript as history but stops
   * claiming to be current, and stops offering actions that would act on stale answers. */
  superseded?: boolean;
  /** Set on a question card pushed to re-ask a field the operator chose to change, so it reads as
   * an edit rather than as the same question being asked twice. */
  editing?: boolean;
  /** Fields this message reports as auto-filled, each offered as an edit affordance — the answers
   * the operator never got asked about are exactly the ones they most need a way to correct. */
  editableFields?: EditableFieldRef[];
  /** Populated on `kind: "context-choice"` — an upstream output the operator can accept or replace. */
  contextChoice?: { label: string; text: string };
  contextChoiceStatus?: "pending" | "accepted" | "overridden";
  /** Populated on `kind: "headline-choice"` — the suggested topics for this field. */
  headlines?: HeadlineChoiceState;
  /** Populated on `kind: "source-run"` — the Phase 1 runs on offer, and which was taken. */
  sourceRuns?: SourceRunSummary[];
  sourceRunStatus?: "loading" | "pending" | "chosen" | "standalone" | "error";
  sourceRunError?: string;
  chosenSourceRunId?: string | null;
  /** Populated on `kind: "briefing"` — the competitor briefing for the stage about to run. */
  briefing?: BriefingState;
  /** Populated on `kind: "stage-gate"` — what the stage still needs, and the operator's options. */
  gate?: StageGateState;
  /** Populated on `kind: "stage-stopped"` — which stage was abandoned, and whether a generation
   * was actually in flight when it happened (so the card can say a request was cancelled rather
   * than implying one was). */
  stopped?: { assetId: string; label: string; wasGenerating: boolean };
  /** Set on the question for a stage's page-source field, where "there is no such page yet" is a
   * real answer — see `NEW_PAGE_OPTIONS`. Carries the subject so the offer can name the service the
   * page would be for rather than asking in the abstract. */
  pageSource?: { assetId: string; subject?: string };
  /** Set when a resumed chat's draft was cut off mid-stream by the tab closing. The partial text
   * is kept (it may be most of the asset) but the card warns and offers a regeneration. */
  interrupted?: boolean;
  generationError?: string;
  savePhase?: MessageSavePhase;
  saveError?: string;
  refining?: boolean;
  refineSubmitted?: boolean;
  /** Which phase's leg of the chat this card belongs to.
   *
   * One chat now carries both legs (see `setPhase`), so everything derived from the transcript —
   * what is awaiting review, whether the resume banner is needed, which card a button may act on —
   * has to read only the leg the operator is currently in. Absent on messages written before
   * phases were stamped; those chats only ever had one, so an unstamped card is always current. */
  phase?: PipelinePhase;
  /** Phase 2 track this card belongs to. Legacy cards without this value belong to the active track. */
  trackId?: string;
  createdAt: number;
}

/** The cards belonging to one leg of the chat. Unstamped messages predate per-phase stamping and
 * come from single-phase chats, so they match whichever phase is asked for. */
export function messagesInPhase(messages: PipelineMessage[], phase: PipelinePhase, trackId?: string | null): PipelineMessage[] {
  return messages.filter(
    (m) =>
      (m.phase ?? phase) === phase &&
      (phase !== "phase2" || !trackId || !m.trackId || m.trackId === trackId),
  );
}

function messagesInCurrentTrack(state: Pick<PipelineState, "messages" | "phase" | "activePhase2TrackId">): PipelineMessage[] {
  return messagesInPhase(state.messages, state.phase, state.phase === "phase2" ? state.activePhase2TrackId : null);
}

/** The field the intake is waiting on, wherever it is defined.
 *
 * Almost always one of the current asset's own fields. The exception is Phase 2's opening
 * sub-service question, which belongs to the run rather than to any asset — so every path that
 * resolves "the question on screen" has to go through here, or that one question silently accepts
 * pill clicks while ignoring anything typed into the answer bar. */
export function fieldBeingAsked(state: {
  intake: IntakeFlow | null;
  phase: PipelinePhase;
}): FieldDef | undefined {
  const awaiting = state.intake?.awaitingFieldId;
  if (!awaiting) return undefined;
  if (awaiting === SUB_SERVICE_FIELD.field_id) return SUB_SERVICE_FIELD;
  return state.intake?.asset.fields.find((f) => f.field_id === awaiting);
}

interface IntakeFlow {
  asset: AssetDefinition;
  answers: Record<string, unknown>;
  awaitingFieldId: string | null;
}

let idCounter = 0;
const nextId = () => `p${Date.now()}_${idCounter++}`;

interface PipelineState {
  started: boolean;
  /** Which sequence of stages this chat is walking *right now*.
   *
   * A chat is no longer tied to one phase — Phase 2 continues the same conversation rather than
   * opening a new one — but `currentIndex`, `runId` and `context` all mean something different in
   * each. So switching parks the outgoing phase's half of the state in `phaseSlots` and swaps in
   * the incoming one, leaving the transcript alone (see `setPhase`). */
  phase: PipelinePhase;
  /** The other phase's parked cursor, so switching back resumes rather than restarts. Empty until
   * the operator has switched at least once. */
  phaseSlots: PhaseSlots;
  /** Independent Phase 2 child runs selected in this chat. */
  phase2Tracks: Record<string, Phase2Track>;
  activePhase2TrackId: string | null;
  phase2TrackSlots: Record<string, PhaseSlot>;
  runId: string | null;
  /** Phase 2 only: the Phase 1 run this one builds on, chosen before stage 01.
   *
   * Held so the run can be created *with* it — `source_run_id` is set at creation and is what makes
   * every later context read fall through to that run. Null on a Phase 1 chat, and on a Phase 2 chat
   * the operator chose to run standalone. */
  sourceRunId: string | null;
  navStatus: NavStatus;
  messages: PipelineMessage[];
  context: ContextStore;
  currentIndex: number;
  /** Where to go back to once a re-run of an earlier stage is approved.
   *
   * A re-run moves `currentIndex` *backwards* onto the stage being redone, which is what makes
   * its intake and generation run again. Without remembering the way back, approving it would
   * advance to `index + 1` and re-run every stage after it in sequence — turning one corrected
   * asset into a full replay of the pipeline. Null whenever no re-run is in flight. */
  rerunReturnIndex: number | null;
  /** The intake parked when the re-run started, restored when it is approved.
   *
   * A re-run is allowed while a *later* stage is mid-intake — that is most of the time, and
   * forbidding it would make the feature useless. But entering the re-run has to clear `intake`,
   * so without parking it the operator's half-answered questions on the stage they were on are
   * silently discarded. */
  rerunReturnIntake: SerializedIntake | null;
  activeStatus: ActiveStatus;
  progress: number;
  intake: IntakeFlow | null;
  clientProfile: Record<string, string>;
  /** Which half of the current stage is active. `"competitor"` means the gated competitor
   * sub-step is running or awaiting approval; `null` means the stage's own intake/generation. */
  subStep: "competitor" | null;
  sessionId: string | null;
  sessionTitle: string;
  isLoadingSession: boolean;
  /** Which history row is currently being opened, so the sidebar can mark that row specifically
   * rather than greying the whole list. */
  loadingSessionId: string | null;
  /** The previous answer to seed the input box with, when the operator is editing rather than
   * answering fresh — correcting one word of a URL should not mean retyping it. Only set for short
   * typed answers; a whole scraped page is not something to drop into a one-line box. Consumed by
   * `PipelineInputBar` and only while its `fieldId` is the field being asked. */
  editSeed: { fieldId: string; value: string } | null;
  /** The classified failure currently being shown as a dialog, or null. Held at run level rather
   * than on a message because the faults worth interrupting for — no credit, bad key, no network —
   * are about the account, not about the card the operator happened to be looking at. */
  fault: ApiFault | null;
  /** A stage the operator asked to start at before the run was ready to open one.
   *
   * Only Phase 2 needs it: its first two questions — which Phase 1 run this sits under, and which
   * sub-service it is for — belong to the run rather than to any stage, and the run row cannot even
   * be created before the first is answered. So "start at Blog" on a fresh Phase 2 chat parks the
   * request here and the preamble opens its gate when it finishes, instead of the request being
   * dropped or the preamble being skipped. */
  pendingStartAssetId: string | null;

  start: () => void;
  submitAnswer: (value: string | number | boolean) => void;
  submitFreeform: (raw: string) => void;
  skipField: () => void;
  requestRefine: (messageId: string) => void;
  cancelRefine: (messageId: string) => void;
  submitRefine: (messageId: string, note: string) => Promise<void>;
  saveStage: (messageId: string) => Promise<void>;
  retryGeneration: (messageId: string) => Promise<void>;
  /** Re-run one asset from the top: re-ask its own questions, then regenerate it.
   *
   * For the case Retry does not cover. Retry re-sends the *same* answers, which only helps a
   * transient failure; a merely disappointing asset usually needs a different input — another
   * target service, another headline, another lead-magnet concept. */
  rerunStage: (messageId: string) => void;
  /** Save the reviewed listing. `approved` is what the operator chose on the card — a subset of the
   * search result, or their own list; omitted, the whole search result is saved. */
  saveCompetitorStep: (messageId: string, approved?: CompetitorAnalysisResult) => Promise<void>;
  retryCompetitorStep: (messageId: string) => Promise<void>;
  /** Re-read the page for a scrape that failed, while its field is still the one being asked. */
  retryPageScrape: (messageId: string) => Promise<void>;
  /** Run the competitor research the consent card offered. */
  acceptCompetitorResearch: (messageId: string) => Promise<void>;
  /** Decline it and answer the field by hand instead. */
  declineCompetitorResearch: (messageId: string) => void;
  /** Re-ask one field the operator already answered, keeping every other answer in the stage.
   * Works both mid-intake and on a generated-but-unsaved draft (which is then regenerated). */
  editField: (fieldId: string) => void;
  /** Abandon an edit in progress and go back to whatever the intake was waiting on. */
  cancelEdit: () => void;
  /** Dismiss the fault dialog. The inline error on the card stays, so the stage is still retryable. */
  dismissFault: () => void;
  acceptContextChoice: (messageId: string) => void;
  overrideContextChoice: (messageId: string) => void;
  /** Take the selected suggestion(s) as this field's answer and carry on with the intake. */
  chooseHeadlines: (messageId: string, ids: string[]) => Promise<void>;
  /** Use a headline the operator typed instead of any of the suggestions. */
  writeOwnHeadline: (messageId: string, headline: string) => Promise<void>;
  /** Another batch, excluding everything already offered in this gate. */
  rerollHeadlines: (messageId: string) => Promise<void>;
  /** Re-request suggestions after the first attempt failed. */
  retryHeadlines: (messageId: string) => Promise<void>;
  /** Phase 2: build this run on `runId`'s approved assets, or on nothing when it is null. */
  chooseSourceRun: (messageId: string, runId: string | null) => Promise<void>;
  /** Re-list the Phase 1 runs after a failed load. */
  retrySourceRuns: (messageId: string) => Promise<void>;
  /** Switch the active Phase 2 child run without leaving this chat. */
  selectPhase2Track: (trackId: string) => void;
  /** Add one Phase 2 child run to an existing workspace. */
  addPhase2Track: (label: string) => Promise<void>;
  /** Continue this chat in the other phase, parking the outgoing one in `phaseSlots`. */
  setPhase: (phase: PipelinePhase) => void;
  /** `discardUnsaved` skips the final write for the chat being left — used when that chat is
   * being deleted, where flushing would either resurrect it or 404 against a deleted row. */
  startNewChat: (opts?: { discardUnsaved?: boolean }) => void;
  /** Empty **the phase on screen** and start that phase again at Stage 01.
   *
   * Scoped to one leg on purpose. A chat carries both (see `setPhase`), and the usual shape of the
   * work is a finished Phase 1 run with a Phase 2 leg under way on top of it — so an operator who
   * wants the sub-service started over is not asking for the fifteen assets it inherits from to be
   * thrown away. The other phase's cards, its parked `phaseSlot` and its run are left exactly as
   * they were, and `TopNav` names the phase it would clear so the button cannot mislead.
   *
   * What goes is this leg's transcript, its intake, its context copy, its cursor and its `runs`
   * link. What survives everywhere is what was approved: those are `context_entries` rows against a
   * `runs` row, and nothing here touches the database.
   *
   * The chat's history row is never deleted — deleting a chat is the sidebar's job, behind its own
   * warning. A cleared leg is written back over the row like any other change.
   *
   * Not undoable, which is why `TopNav` puts a modal in front of it rather than the inline confirm
   * `stopStage` uses. */
  clearPhase: () => void;
  loadSession: (sessionId: string) => Promise<void>;
  /** Re-enter the current stage on a resumed chat that came back with nothing to act on — see
   * `selectNeedsResume`. */
  resumeStage: () => void;
  /** Open a stage out of sequence, checking first what it still needs.
   *
   * The entry point for a client who arrives with their own ICP, or who wants the Offer Ladder and
   * nothing before it. Never enters the stage on its own: it pushes a `stage-gate` card, because
   * "use the ICP this run already has" and "use the one I'm about to paste" are not a decision the
   * pipeline should make silently on the operator's behalf. */
  startAtStage: (assetId: string) => void;
  /** Re-read readiness for a gate that failed to load, or that a reopened chat left mid-load. */
  retryStageGate: (messageId: string) => Promise<void>;
  /** Open the paste box for one missing document. */
  provideDependency: (messageId: string, contextKey: string) => void;
  /** Close it again without supplying anything. */
  cancelProvideDependency: (messageId: string) => void;
  /** File the pasted document under `contextKey` for this run, then re-check the gate. */
  submitProvidedDependency: (messageId: string, contextKey: string, content: string) => Promise<void>;
  /** Enter the stage the gate is for, with whatever the run now holds. */
  enterGatedStage: (messageId: string) => Promise<void>;
  /** Go and run the first asset the gate says is still missing, instead of this one. */
  runProducerFirst: (messageId: string, assetId: string) => void;
  /** There is no page for this service yet: answer the page-source field and its content field with
   * the prompt's own "NEW PAGE" sentinels, which switches the stage to build-from-scratch. */
  declareNewPage: (messageId: string) => void;
  /** Abandon this stage and open the one that builds a page from nothing instead. */
  skipStageForNewPage: (messageId: string) => void;
  /** Stop the stage in progress and free the run to start a different one.
   *
   * Abandons work in flight only: intake answers, an unsaved draft, an in-flight stream (which is
   * genuinely aborted, not just hidden). Nothing already approved is touched, which is what makes
   * it safe enough to be one click with an inline confirm rather than a modal. */
  stopStage: () => void;
}

/** `IntakeFlow` with its asset collapsed to an id, which is how it is both persisted and parked —
 * the full `AssetDefinition` is static catalog data, not per-session state. */
interface SerializedIntake {
  assetId: string;
  answers: Record<string, unknown>;
  awaitingFieldId: string | null;
}

function serializeIntake(intake: IntakeFlow | null): SerializedIntake | null {
  if (!intake) return null;
  return {
    assetId: intake.asset.asset_id,
    answers: intake.answers,
    awaitingFieldId: intake.awaitingFieldId,
  };
}

/** One phase's cursor, parked while the operator works in the other phase of the same chat.
 *
 * The transcript and the client profile are shared — a chat is one conversation about one client,
 * and Phase 2 continues it. Everything that indexes into a *phase's own* stage list is not: index 4
 * is Offers in Phase 1 and Blog in Phase 2, the two write to different `runs` rows, and both
 * sequences produce assets under the same ids. So exactly that half of the state is lifted out on
 * the way out of a phase and put back on the way in. */
interface PhaseSlot {
  started: boolean;
  runId: string | null;
  sourceRunId: string | null;
  currentIndex: number;
  /** Parked with the cursor it belongs to: switching phases mid-re-run and back must not lose
   * the way home, or approving the re-run would replay the rest of that leg. */
  rerunReturnIndex?: number | null;
  rerunReturnIntake?: SerializedIntake | null;
  subStep: "competitor" | null;
  intake: SerializedIntake | null;
  context: ContextStore;
  progress: number;
}

export interface Phase2Track {
  id: string;
  label: string;
  runId: string;
  sourceRunId: string | null;
  status: "active" | "queued" | "complete" | "error";
  error?: string;
}

type PhaseSlots = Partial<Record<PipelinePhase, PhaseSlot>>;

/** What actually gets persisted to `chat_sessions.state` — a plain-data mirror of the store,
 * with `intake.asset` collapsed to its id (re-hydrated from `ASSET_BY_ID` on load) since the
 * full `AssetDefinition` is static catalog data, not per-session state. */
interface PipelineSnapshot {
  started: boolean;
  /** Absent on chats saved before phases existed; those are all Phase 1 runs, so `loadSession`
   * defaults it rather than treating the gap as an error. */
  phase?: PipelinePhase;
  runId: string | null;
  /** Absent on Phase 1 chats and on chats saved before Phase 2 existed. */
  sourceRunId?: string | null;
  navStatus: NavStatus;
  messages: PipelineMessage[];
  context: ContextStore;
  currentIndex: number;
  /** Absent on chats saved before re-run existed, and on any chat with none in flight. */
  rerunReturnIndex?: number | null;
  rerunReturnIntake?: SerializedIntake | null;
  activeStatus: ActiveStatus;
  progress: number;
  clientProfile: Record<string, string>;
  subStep: "competitor" | null;
  intake: SerializedIntake | null;
  /** Absent on chats that never switched phase, and on every chat saved before a chat could. */
  phaseSlots?: PhaseSlots;
  phase2Tracks?: Record<string, Phase2Track>;
  activePhase2TrackId?: string | null;
  phase2TrackSlots?: Record<string, PhaseSlot>;
}

function serializeSnapshot(state: PipelineState): PipelineSnapshot {
  return {
    started: state.started,
    phase: state.phase,
    runId: state.runId,
    sourceRunId: state.sourceRunId,
    navStatus: state.navStatus,
    messages: state.messages,
    context: state.context,
    currentIndex: state.currentIndex,
    rerunReturnIndex: state.rerunReturnIndex,
    rerunReturnIntake: state.rerunReturnIntake,
    activeStatus: state.activeStatus,
    progress: state.progress,
    clientProfile: state.clientProfile,
    subStep: state.subStep,
    intake: serializeIntake(state.intake),
    phaseSlots: state.phaseSlots,
    phase2Tracks: state.phase2Tracks,
    activePhase2TrackId: state.activePhase2TrackId,
    phase2TrackSlots: state.phase2TrackSlots,
  };
}

function hydrateIntake(raw: unknown): IntakeFlow | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as { assetId?: string; answers?: Record<string, unknown>; awaitingFieldId?: string | null };
  const asset = r.assetId ? ASSET_BY_ID[r.assetId] : undefined;
  if (!asset) return null;
  return { asset, answers: r.answers ?? {}, awaitingFieldId: r.awaitingFieldId ?? null };
}

const INTERRUPTED_GENERATION =
  "This generation was still running when the chat was closed, so it never finished. Retry to generate it again.";
const INTERRUPTED_COMPETITOR =
  "This competitor analysis was still running when the chat was closed, so it never finished.";
const INTERRUPTED_SCRAPE =
  "This page was still being read when the chat was closed, so the read never finished.";
const INTERRUPTED_SOURCE_RUNS =
  "The list of Phase 1 runs was still loading when the chat was closed.";
const INTERRUPTED_BRIEFING =
  "This summary was still being written when the chat was closed. The competitor analysis above is unaffected.";
const INTERRUPTED_HEADLINES =
  "Topic suggestions were still being generated when the chat was closed, so they never arrived.";
const HEADLINE_TIMED_OUT =
  "Topic suggestions took too long to come back. The search may be slow right now - trying again often works.";
/** The backend's own default for a run with no client name yet. Recognised rather than assumed away:
 * it is a real value in the database on every run created before runs carried a name, and it must
 * never be mistaken for one. */
const PLACEHOLDER_CLIENT_NAME = "Untitled Client";
const EMPTY_GENERATION =
  "This generation finished with no output — the stream ended before anything was written. Retry to generate it again.";

/** Put a reopened chat back into a state the operator can actually act on.
 *
 * Anything that was mid-flight when the tab closed is dead on arrival — the stream it was reading
 * from is long gone. Restored as-is it renders as a permanently spinning card with no buttons,
 * which strands the chat. So each in-flight card is converted into whichever *resumable* form
 * fits: a partial draft keeps its text and gains a regenerate option, an empty one becomes a
 * plain retry, and an unfinished competitor step becomes a re-run. */
function repairInterruptedMessages(messages: PipelineMessage[]): PipelineMessage[] {
  return messages.map((m) => {
    // A prepass chip left "running" would spin forever; drop it rather than restore the lie.
    const prepass = m.prepass?.status === "running" ? undefined : m.prepass;

    // Same for a page read: the fetch died with the tab. Converted to an error card, which is
    // both true and actionable — its field's question is either already asked below it or will be
    // asked when the stage is re-entered.
    if (m.kind === "scrape" && m.scrape?.status === "running") {
      return {
        ...m,
        prepass,
        scrape: { ...m.scrape, status: "error" as const, error: INTERRUPTED_SCRAPE },
      };
    }

    if (m.kind === "competitor" && !m.competitor && !m.competitorError) {
      return { ...m, prepass, streaming: false, competitorError: INTERRUPTED_COMPETITOR };
    }

    // Suggestions that were still being generated when the tab closed. This one strands the chat
    // hardest of any card here — the gate is what the intake is blocked on, and its loading state
    // has no buttons at all — so it is restored as the retryable failure it is. The error card
    // offers both exits: try again, or type the topic yourself.
    if (m.kind === "headline-choice" && m.headlines?.status === "loading") {
      return {
        ...m,
        prepass,
        headlines: { ...m.headlines, status: "error" as const, reloading: false, error: INTERRUPTED_HEADLINES },
      };
    }

    // A re-roll that died mid-flight over a batch that had already arrived: the batch is still
    // good and still choosable, so only the in-flight flag is cleared.
    if (m.kind === "headline-choice" && m.headlines?.reloading) {
      return { ...m, prepass, headlines: { ...m.headlines, reloading: false } };
    }

    // The Phase 1 run listing was still loading, so the card would spin with nothing to click. Its
    // retry is the same call, so it is restored as the retryable failure it is.
    if (m.kind === "source-run" && m.sourceRunStatus === "loading") {
      return { ...m, prepass, sourceRunStatus: "error" as const, sourceRunError: INTERRUPTED_SOURCE_RUNS };
    }

    // A briefing that never arrived. Nothing downstream depends on it — the stage's intake either
    // already carried on or will when the chat is resumed — so it is marked done-with-nothing rather
    // than restored as a failure demanding action.
    if (m.kind === "briefing" && m.briefing?.status === "running") {
      return {
        ...m,
        prepass,
        briefing: { ...m.briefing, status: "error" as const, error: INTERRUPTED_BRIEFING },
      };
    }

    if (m.streaming) {
      const hasDraft = Boolean(m.text?.trim());
      return hasDraft
        ? { ...m, prepass, streaming: false, interrupted: true }
        : { ...m, prepass, streaming: false, generationError: INTERRUPTED_GENERATION };
    }

    // A finished-looking generation with nothing in it: a stream that was cut before its first
    // token, snapshotted by a build that read that as success. It renders as a draft card with an
    // empty body and a Save button over an asset that does not exist, so it is restored as the
    // retry it always was. New runs can no longer reach this state — see `StreamTruncatedError`.
    if (m.kind === "generation" && !m.text?.trim() && !m.generationError) {
      return { ...m, prepass, generationError: EMPTY_GENERATION };
    }

    return prepass === m.prepass ? m : { ...m, prepass };
  });
}

/** What the pipeline diagram should be doing on a chat that has just been reopened.
 *
 * `activeStatus` is what drives every animation in `PipelineDiagram` — the breathing glow, the
 * rotating stage icon, the shimmering progress bar, the pulsing badge, the travelling connector.
 * Restoring it as `null` (which is what "no generation is streaming right now" literally means)
 * froze the whole flowchart on a resumed chat, even though the stage was very much still live and
 * waiting on the operator. So the state is re-derived from what the chat is actually waiting for,
 * exactly as the equivalent live session would have set it:
 *
 *   - a question or context choice outstanding -> `running` / "Awaiting Input", same as `beginMainIntake`
 *   - a draft or competitor listing to approve  -> `hitl`    / "Awaiting Review", same as `streamIntoMessage`
 *   - nothing outstanding                       -> `null`, and the resume banner offers the way on
 *
 * The scan runs newest-first because the last card that still wants something is the one the
 * operator is actually parked on.
 */
function deriveResumeActivity(
  messages: PipelineMessage[],
  intake: IntakeFlow | null,
  currentIndex: number,
  snapshotProgress: number,
  phase: PipelinePhase,
  trackId?: string | null,
): { activeStatus: ActiveStatus; navStatus: NavStatus; progress: number } {
  const awaitingInput = {
    activeStatus: "running" as const,
    navStatus: "Awaiting Input" as const,
    // A stage sitting in intake shows a just-started bar (`beginMainIntake` uses 5); never 0,
    // which would render as an empty rail under an animated node.
    progress: Math.max(snapshotProgress, 5),
  };
  const awaitingReview = {
    activeStatus: "hitl" as const,
    navStatus: "Awaiting Review" as const,
    progress: 100,
  };

  if (currentIndex >= totalStagesFor(phase)) {
    return { activeStatus: null, navStatus: "Ready", progress: 100 };
  }

  // Only this phase's leg. A chat that walked Phase 1 and moved on to Phase 2 still carries every
  // Phase 1 card, and a draft left unsaved back there is history — not something the reopened chat
  // is waiting on.
  const own = messagesInPhase(messages, phase, trackId);
  for (let i = own.length - 1; i >= 0; i--) {
    const m = own[i];
    // Superseded cards are history, not work. A re-run marks every unsaved draft and every intake
    // question of the stage being replaced `superseded` (see `rerunStage`), and `GenerationStream`
    // stops rendering their actions — so a card matched here would park the run on something the
    // operator cannot see, let alone act on. That is the shape the re-run dead-end took: return to
    // Stage 09, find a superseded Stage 08 draft still reading `savePhase !== "saved"`, and report
    // "Awaiting Review" of a hidden card with no way forward.
    if (m.superseded) continue;
    if (m.kind === "context-choice" && m.contextChoiceStatus === "pending") return awaitingInput;
    // A suggestion gate is waiting on the operator whether the batch has arrived or not: while
    // it loads there is nothing else for them to do, and once it has they have to choose.
    if (m.kind === "headline-choice" && m.headlines?.status !== "chosen") return awaitingInput;
    if (m.kind === "competitor-consent" && m.consent?.status === "pending") return awaitingInput;
    // A Phase 2 chat closed on its opening question is parked on it, exactly like any other question.
    if (m.kind === "source-run" && (m.sourceRunStatus === "pending" || m.sourceRunStatus === "error")) {
      return awaitingInput;
    }
    // A gate owns the turn whether its readiness has arrived or not: while it loads there is
    // nothing else to do, and once it has, entering the stage is the operator's call.
    if (m.kind === "stage-gate" && m.gate && m.gate.status !== "entered") return awaitingInput;
    // A stopped stage is the operator's turn by definition: they stopped it to choose something
    // else. Counted so a reopened chat does not report itself as mid-run.
    if (m.kind === "stage-stopped") return awaitingInput;
    if (m.kind === "question" && !m.answered && intake?.awaitingFieldId === m.field?.field_id) return awaitingInput;
    if (m.kind === "competitor" && m.savePhase !== "saved") return awaitingReview;
    if (m.kind === "generation" && !m.refineSubmitted && m.savePhase !== "saved") return awaitingReview;
  }

  // Intake was mid-flight when the snapshot was taken — the question card had not been pushed yet,
  // but the field it was about to ask for is right there in the restored intake.
  if (intake?.awaitingFieldId) return awaitingInput;

  return { activeStatus: null, navStatus: "Ready", progress: snapshotProgress };
}

/** True when a reopened chat has no card to review, no question pending, and no stage running —
 * i.e. it was snapshotted in the gap between finishing one stage and asking the next stage's
 * first question, and would otherwise sit there with no way forward. */
export function selectNeedsResume(s: PipelineState): boolean {
  if (!s.started || s.isLoadingSession || s.activeStatus === "running") return false;
  if (s.currentIndex >= totalStagesFor(s.phase)) return false;
  if (s.intake?.awaitingFieldId) return false;
  const own = messagesInCurrentTrack(s);
  if (own.some((m) => m.streaming)) return false;

  const actionable = own.some((m) => {
    // See `deriveResumeActivity` — a superseded card is history and cannot be acted on, so counting
    // it as actionable is what suppressed the resume banner on a chat that genuinely needed it.
    if (m.superseded) return false;
    if (m.kind === "context-choice") return m.contextChoiceStatus === "pending";
    if (m.kind === "stage-gate") return !!m.gate && m.gate.status !== "entered";
    if (m.kind === "stage-stopped") return true;
    if (m.kind === "headline-choice") return m.headlines?.status !== "chosen";
    if (m.kind === "competitor-consent") return m.consent?.status === "pending";
    if (m.kind === "source-run") return m.sourceRunStatus === "pending" || m.sourceRunStatus === "error";
    if (m.kind === "competitor") return m.savePhase !== "saved";
    if (m.kind === "generation") return !m.refineSubmitted && m.savePhase !== "saved";
    return false;
  });
  return !actionable;
}

/** First user-typed answer if there is one, else the current stage's label, so a chat gets a
 * meaningful history-sidebar title well before the strategist types any freeform text. */
function deriveTitle(messages: PipelineMessage[]): string | undefined {
  const firstUserText = messages.find((m) => m.role === "user" && m.text?.trim())?.text;
  const firstStageAssetId = messages.find((m) => m.assetId)?.assetId;
  // Straight from the catalog rather than a phase's stage list: the label of an asset is the same
  // in either phase, and a title has no business caring which sequence produced it.
  const stageLabel = firstStageAssetId ? ASSET_BY_ID[firstStageAssetId]?.label : undefined;
  const base = (firstUserText ?? stageLabel)?.trim();
  if (!base) return undefined;
  return base.length > 60 ? `${base.slice(0, 57)}…` : base;
}

let persistTimer: ReturnType<typeof setTimeout> | null = null;
let persistInFlight = false;
let persistQueued = false;

/** Bumped every time the pane is pointed at a different chat. A write that started before the
 * switch can then tell whether the store is still on the chat it was written for — comparing
 * message arrays cannot, since a streaming generation replaces that array constantly. */
let chatEpoch = 0;

/** Debounced autosave: coalesces the bursty `set()` calls a streaming generation produces into
 * one PUT after ~800ms of quiet, so a chat's history entry always reflects where it was left. */
function schedulePersist() {
  if (persistTimer) clearTimeout(persistTimer);
  persistTimer = setTimeout(() => {
    persistTimer = null;
    void persistNow();
  }, 800);
}

function clearPendingPersist() {
  if (persistTimer) {
    clearTimeout(persistTimer);
    persistTimer = null;
  }
  persistQueued = false;
}

function reportPersistError(err: unknown) {
  console.error("Failed to autosave chat session", err);
  // Surface it in the sidebar rather than only the console: a failing autosave is exactly why
  // history can look empty, and silence there is what hid it.
  useChatSessionsStore.setState({
    error: err instanceof Error ? err.message : String(err),
    loaded: true,
  });
}

/** The write itself, against a snapshot the caller captured.
 *
 * Takes `state` rather than reading the store so a chat can still be written out *after* the
 * store has been pointed at a different one — which is what makes leaving a chat non-destructive.
 * `adopt` says whether a newly created session id belongs to the chat currently in the pane: true
 * for an ordinary autosave, false for the parting write of a chat being navigated away from. */
async function writeSnapshot(state: PipelineState, adopt: boolean): Promise<void> {
  const snapshot = serializeSnapshot(state) as unknown as Record<string, unknown>;
  const title = deriveTitle(state.messages) ?? state.sessionTitle;
  const epoch = chatEpoch;

  if (state.sessionId) {
    await updateChatSession(state.sessionId, { title, state: snapshot });
    if (usePipelineStore.getState().sessionId === state.sessionId) {
      usePipelineStore.setState({ sessionTitle: title });
    }
  } else {
    const created = await createChatSession(title);
    // Write the state before adopting the id, so the row is never left as an empty "New chat"
    // stub if this is the final write of a chat the operator is walking away from.
    await updateChatSession(created.id, { title, state: snapshot });
    // Adopt the id only if the store is still on this same, still-unsaved chat — the operator may
    // have switched away while the POST was open, and adopting then would point the *new* chat's
    // autosave at the old chat's history row.
    if (adopt && chatEpoch === epoch && usePipelineStore.getState().sessionId === null) {
      usePipelineStore.setState({ sessionId: created.id, sessionTitle: created.title });
    }
  }

  useChatSessionsStore.setState({ error: null });
  await useChatSessionsStore.getState().refresh();
}

async function persistNow(): Promise<void> {
  // One pass at a time: a second pass entered while the first is still POSTing /chat-sessions
  // would see `sessionId` as null too and create a duplicate history entry for the same chat.
  if (persistInFlight) {
    persistQueued = true;
    return;
  }

  const state = usePipelineStore.getState();
  if (state.isLoadingSession) return;
  // `started` gates *creating* a row, so a chat nobody has begun never appears in the history. A
  // chat that already has one is written whatever state it is in — including the empty state
  // `clearPhase` leaves when it takes the last leg out of a chat. Skipped there, the row would keep
  // the old transcript and hand it back on the next reload: cleared on screen, intact in the
  // database.
  if (!state.started && !state.sessionId) return;

  persistInFlight = true;
  try {
    await writeSnapshot(state, true);
  } catch (err) {
    reportPersistError(err);
  } finally {
    persistInFlight = false;
    if (persistQueued) {
      persistQueued = false;
      schedulePersist();
    }
  }
}

/** Final write for the chat being left, issued *before* the store is repointed.
 *
 * Without it, switching chats discards up to ~800ms of the one being left — and the discarded
 * write is the most recent one (the answer just typed, the draft just saved), which is exactly
 * what an operator expects to find when they come back to it. Fire-and-forget: `writeSnapshot`
 * has already captured everything it needs, and a slow save must not delay opening another chat. */
function flushLeavingChat(state: PipelineState): void {
  const hadUnsaved = persistTimer !== null;
  clearPendingPersist();
  chatEpoch += 1;
  if (!hadUnsaved || !state.started || state.isLoadingSession) return;
  // An unsaved chat mid-create already has a write covering it; a second one would race that
  // POST and leave two history rows for the same chat.
  if (persistInFlight && !state.sessionId) return;
  void writeSnapshot(state, false).catch(reportPersistError);
}

function push(
  get: () => PipelineState,
  set: (partial: Partial<PipelineState>) => void,
  message: Omit<PipelineMessage, "id" | "createdAt">,
): PipelineMessage {
  const state = get();
  const full: PipelineMessage = {
    phase: state.phase,
    ...(state.phase === "phase2" && state.activePhase2TrackId ? { trackId: state.activePhase2TrackId } : {}),
    ...message,
    id: nextId(),
    createdAt: Date.now(),
  };
  set({ messages: [...get().messages, full] });
  schedulePersist();
  return full;
}

/** The message behind a card's button, but only while that card is still live.
 *
 * A chat carries both phases now, so the transcript can hold a Phase 1 draft awaiting review above
 * a Phase 2 stage in progress. Those older cards keep their text and their copy/export controls —
 * a finished deliverable is still worth reading — but *acting* on one would apply it to the phase
 * the chat has since moved to: saving that draft would write it into the Phase 2 run, and accepting
 * an old context choice would answer a field of an intake that no longer exists. So every
 * message-driven action resolves its message through here and does nothing when it is history. */
function liveMessage(state: PipelineState, messageId: string): PipelineMessage | undefined {
  const message = state.messages.find((m) => m.id === messageId);
  if (!message) return undefined;
  return messagesInCurrentTrack(state).some((candidate) => candidate.id === message.id) ? message : undefined;
}

function patchMessage(
  get: () => PipelineState,
  set: (partial: Partial<PipelineState>) => void,
  id: string,
  patch: Partial<PipelineMessage>,
) {
  set({ messages: get().messages.map((m) => (m.id === id ? { ...m, ...patch } : m)) });
  schedulePersist();
}

function markQuestionAnswered(
  get: () => PipelineState,
  set: (partial: Partial<PipelineState>) => void,
  fieldId: string,
) {
  set({
    messages: get().messages.map((m) =>
      m.kind === "question" && m.field?.field_id === fieldId ? { ...m, answered: true } : m,
    ),
  });
}

/** Clear the answers that were derived from `fieldId`, so a re-walk fills them again from its new
 * value. Returns the fields cleared, for the announcement.
 *
 * Editing an answer in place is only honest if what was computed *from* it goes too. Three
 * relationships exist in the catalog today:
 *   - a page read from a URL (`SCRAPE_SOURCES`) — a new URL means a new page, not the old text
 *   - `conditional_children` — flipping "Is this a regulated field?" to Yes has to un-skip the
 *     follow-up that a No answer auto-filled as "N/A"
 *   - `conditional_on` — the same relationship declared from the child's side
 * Anything else the operator typed is theirs and is left alone. */
function invalidateDependents(
  asset: AssetDefinition,
  answers: Record<string, unknown>,
  fieldId: string,
): FieldDef[] {
  const field = asset.fields.find((f) => f.field_id === fieldId);
  const dependentIds = new Set<string>(field?.conditional_children ?? []);

  for (const [contentFieldId, urlFieldId] of Object.entries(SCRAPE_SOURCES)) {
    if (urlFieldId === fieldId) dependentIds.add(contentFieldId);
  }
  for (const candidate of asset.fields) {
    if (candidate.conditional_on?.field === fieldId) dependentIds.add(candidate.field_id);
  }

  const cleared: FieldDef[] = [];
  for (const dependent of asset.fields) {
    if (!dependentIds.has(dependent.field_id)) continue;
    if (answers[dependent.field_id] === undefined) continue;
    delete answers[dependent.field_id];
    cleared.push(dependent);
  }
  return cleared;
}

/** Clear the answers this stage took from the run's sub-service, so a re-walk fills them from the
 * corrected one. Returns the fields cleared, for the announcement.
 *
 * `invalidateDependents`' counterpart for the one input that is a run-level fact rather than a
 * stage answer. The fields in `FIELD_TO_FACT_BY_PHASE` are never asked — the walk fills them from
 * the profile as it passes (`auto-known` in `lib/fieldResolution.ts`) — and `planField` treats any
 * answer that already exists as final. So an edit that left them standing would be silently
 * ignored: the operator would see the sub-service change and the stage would still generate for the
 * old one.
 */
function clearFactFilledAnswers(
  phase: PipelinePhase,
  asset: AssetDefinition,
  answers: Record<string, unknown>,
  fact: string,
): FieldDef[] {
  const fieldToFact = FIELD_TO_FACT_BY_PHASE[phase];
  const cleared: FieldDef[] = [];
  for (const field of asset.fields) {
    if (fieldToFact[field.field_id] !== fact) continue;
    if (answers[field.field_id] === undefined) continue;
    delete answers[field.field_id];
    cleared.push(field);
  }
  return cleared;
}

/** The generation card an edit would replace: the newest draft for the current stage that hasn't
 * been approved yet. Nothing is editable once a stage is saved — its output is in the Context Store
 * and later stages have been built on it, so the way back is a new run, not a quiet re-answer. */
function editableDraft(state: PipelineState): PipelineMessage | undefined {
  const stage = stageAt(state.phase, state.currentIndex);
  return [...state.messages]
    .reverse()
    .find(
      (m) =>
        m.kind === "generation" &&
        m.assetId === stage?.asset.asset_id &&
        !m.streaming &&
        !m.superseded &&
        m.savePhase !== "saved",
    );
}

/** True when an answer can be changed right now: nothing is mid-flight, and the stage is either
 * still taking intake or sitting on an unapproved draft. */
export function selectCanEditAnswers(s: PipelineState): boolean {
  if (s.isLoadingSession || s.subStep === "competitor") return false;
  if (s.messages.some((m) => m.streaming || m.scrape?.status === "running")) return false;
  // A competitor search in flight, or a consent card still waiting on a yes/no. Both are decisions
  // the operator is in the middle of; changing an earlier answer underneath one would leave the
  // search benchmarking a topic that no longer exists.
  if (s.messages.some((m) => m.kind === "competitor" && !m.competitor && !m.competitorError)) return false;
  if (s.messages.some((m) => m.kind === "competitor-consent" && m.consent?.status === "pending")) return false;
  return Boolean(s.intake) || Boolean(editableDraft(s));
}

/** Whether an asset can be re-run right now.
 *
 * Shares `selectCanEditAnswers`'s reasons for refusing — nothing mid-stream, nothing mid-search,
 * no consent card outstanding — but not its final condition. That one requires a *live* intake or an
 * editable draft, because editing a single answer only makes sense against the stage in hand. A
 * re-run deliberately targets a stage the operator has already finished and moved past, which is the
 * whole point of it.
 */
export function selectCanRerun(s: PipelineState): boolean {
  if (s.isLoadingSession || s.subStep === "competitor") return false;
  if (s.messages.some((m) => m.streaming || m.scrape?.status === "running")) return false;
  if (s.messages.some((m) => m.kind === "competitor" && !m.competitor && !m.competitorError)) return false;
  if (s.messages.some((m) => m.kind === "competitor-consent" && m.consent?.status === "pending")) return false;
  // A suggestion gate mid-flight owns the input bar; re-entering a stage under it would strand it.
  if (s.messages.some((m) => m.kind === "headline-choice" && m.headlines?.status === "loading")) return false;
  return s.started;
}

/** True when **the leg on screen** holds something to clear.
 *
 * Read per phase, not per chat, because that is the scope `clearPhase` acts at: a Phase 2 leg one
 * stage in is clearable while the Phase 1 run above it is untouched, and — the case that matters —
 * a chat whose Phase 1 is complete offers nothing to clear the moment the operator switches to a
 * Phase 2 leg that has not started. Offered there, the control would warn about destroying nothing,
 * and a warning that turns out to be empty is how an operator learns to click through the next one
 * without reading it. */
export function selectCanClearPhase(s: PipelineState): boolean {
  if (s.isLoadingSession) return false;
  if (messagesInCurrentTrack(s).length > 0) return true;
  return s.intake !== null || s.subStep !== null || s.currentIndex > 0 || s.runId !== null;
}

/** True when the *other* leg of this chat holds something — which decides whether clearing empties
 * the chat or only one phase of it, and so what the button is allowed to call itself.
 *
 * Strict on the stamp, unlike `messagesInPhase`: an unstamped card belongs to the leg on screen, and
 * counting it here would claim work for a phase that never ran. */
export function selectOtherPhaseHasWork(s: PipelineState): boolean {
  const other: PipelinePhase = s.phase === "phase1" ? "phase2" : "phase1";
  return s.messages.some((m) => m.phase === other) || s.phaseSlots[other] !== undefined;
}

/** True when there is a stage in progress to stop.
 *
 * "In progress" is anything short of approved: a question waiting, a competitor sub-step, a stream
 * running, or a finished draft nobody has saved. All four are states an operator can decide they
 * are done with, and offering the button in only some of them would make it look broken in the
 * others.
 *
 * Approved work is never in scope. Stopping does not touch a saved generation or anything already
 * in the Context Store — it abandons work in flight, which is why it can be a single click. */
export function selectCanStop(s: PipelineState): boolean {
  if (!s.started || s.isLoadingSession) return false;
  if (s.currentIndex >= totalStagesFor(s.phase)) return false;

  // Phase 2 opens with two questions that belong to the *run* rather than to any stage: which
  // Phase 1 run this builds on, and which sub-service it is for. There is no asset in progress to
  // abandon at either, and stopping would clear the intake while leaving the question on screen
  // with nothing behind it — the sub-service card is pushed without an `assetId`, so the supersede
  // pass does not even reach it.
  if (s.intake?.awaitingFieldId === SUB_SERVICE_FIELD.field_id) return false;
  const own = messagesInCurrentTrack(s);
  if (own.some((m) => !m.superseded && m.kind === "source-run" && m.sourceRunStatus !== "chosen" && m.sourceRunStatus !== "standalone")) {
    return false;
  }

  if (s.intake || s.subStep) return true;
  if (s.activeStatus !== null) return true;
  return own.some((m) => !m.superseded && m.kind === "generation" && m.savePhase !== "saved");
}

/** The stage `stopStage` would act on, or undefined. Read from what is actually happening rather
 * than from `currentIndex` alone: a retry or a refine streams into a card for an *earlier* stage,
 * and the button has to name and stop that one rather than whatever the cursor sits on. */
export function selectStoppableStage(s: PipelineState): { assetId: string; label: string } | undefined {
  const own = messagesInCurrentTrack(s);
  const streaming = own.find((m) => m.streaming && m.assetId);
  const assetId =
    streaming?.assetId ??
    s.intake?.asset.asset_id ??
    own.filter((m) => !m.superseded && m.kind === "generation" && m.savePhase !== "saved").pop()?.assetId ??
    stageAt(s.phase, Math.min(s.currentIndex, totalStagesFor(s.phase) - 1)).asset.asset_id;
  if (!assetId) return undefined;
  // Phase 2's opening sub-service question belongs to the run, not to a stage — there is no asset
  // to abandon there, and the stage list has no entry to name.
  const stage = stagesFor(s.phase).find((st) => st.asset.asset_id === assetId);
  return stage ? { assetId, label: stage.asset.label } : undefined;
}

/** One chip per field, first mention wins — the same field can be auto-filled on more than one
 * pass through the walk. */
function dedupeFields(fields: EditableFieldRef[]): EditableFieldRef[] {
  const seen = new Set<string>();
  return fields.filter((f) => (seen.has(f.fieldId) ? false : (seen.add(f.fieldId), true)));
}

function formatUserAnswer(value: string | number | boolean): string {
  if (typeof value === "boolean") return value ? "Yes" : "No";
  return String(value);
}

function coerceAnswer(field: FieldDef, raw: string): string | number | boolean {
  if (field.kind === "number") {
    const n = Number(raw);
    return Number.isFinite(n) ? n : raw;
  }
  if (field.kind === "boolean_flag") {
    if (/^(y|yes|true)$/i.test(raw)) return true;
    if (/^(n|no|false)$/i.test(raw)) return false;
  }
  return raw;
}

/** Resolves every field on `asset` to its final string value for the real generation call:
 * user-entered answers pass through as-is; auto-context markers (`[[context: Label]]`, set by
 * `findNextAskable`) are replaced with the ACTUAL upstream saved text, not the marker itself —
 * this is what actually gets substituted into the real prompt's INPUTS block. */
function resolveFinalAnswers(
  asset: AssetDefinition,
  context: ContextStore,
  answers: Record<string, unknown>,
): Record<string, string> {
  const result: Record<string, string> = {};
  for (const field of asset.fields) {
    const raw = answers[field.field_id];
    if (typeof raw === "string" && raw.startsWith("[[context:")) {
      const resolved = resolveContext(field, context);
      result[field.field_id] = resolved?.text ?? "";
    } else if (raw === undefined || raw === null) {
      result[field.field_id] = "";
    } else if (typeof raw === "boolean") {
      result[field.field_id] = raw ? "Yes" : "No";
    } else {
      result[field.field_id] = String(raw);
    }
  }
  return result;
}

async function ensureRun(get: () => PipelineState, set: (partial: Partial<PipelineState>) => void): Promise<string> {
  const existing = get().runId;
  if (existing) return existing;
  // `sourceRunId` must be passed here rather than set later: it is what the backend links the new
  // run to at creation, and every inherited context read depends on the link already existing. On a
  // Phase 2 chat the run is normally created up front by `chooseSourceRun`, so this is the fallback
  // for a Phase 1 chat (no link) or a resumed one whose run was never created.
  // Named from the run profile when ICP has already captured it. The name is what Phase 2's
  // "which Phase 1 run?" picker shows, and "Untitled Client" is not something an operator can
  // choose between when there are three of them.
  const { run_id } = await createRun(get().clientProfile.client_name || PLACEHOLDER_CLIENT_NAME, get().sourceRunId);
  set({ runId: run_id });
  return run_id;
}

async function createPhase2Tracks(
  get: () => PipelineState,
  set: (partial: Partial<PipelineState>) => void,
  labels: string[],
): Promise<void> {
  const sourceRunId = get().sourceRunId;
  const firstRunId = await ensureRun(get, set);
  const created: Phase2Track[] = [
    {
      id: firstRunId,
      label: labels[0],
      runId: firstRunId,
      sourceRunId,
      status: "active",
    },
  ];

  const results = await Promise.allSettled(
    labels.slice(1).map(async (label) => {
      const { run_id: runId } = await createRun(
        get().clientProfile.client_name || PLACEHOLDER_CLIENT_NAME,
        sourceRunId,
      );
      return { id: runId, label, runId, sourceRunId, status: "queued" as const };
    }),
  );
  for (const [index, result] of results.entries()) {
    if (result.status === "fulfilled") {
      created.push(result.value);
      continue;
    }
    created.push({
      id: `failed-${labels[index + 1].toLowerCase().replace(/[^a-z0-9]+/g, "-")}`,
      label: labels[index + 1],
      runId: "",
      sourceRunId,
      status: "error",
      error: result.reason instanceof Error ? result.reason.message : String(result.reason),
    });
  }

  set({
    phase2Tracks: Object.fromEntries(created.map((track) => [track.id, track])),
    activePhase2TrackId: firstRunId,
  });
  if (created.length > 1) {
    push(get, set, {
      role: "assistant",
      kind: "text",
      text:
        `Created ${created.length} independent Phase 2 tracks: ${created.map((track) => track.label).join(", ")}. ` +
        "This chat is now working on the first track; use the track switcher to move between them without repeating Phase 1.",
    });
  }
  const failed = created.filter((track) => track.status === "error");
  if (failed.length) {
    push(get, set, {
      role: "assistant",
      kind: "text",
      text: `Could not create ${failed.map((track) => track.label).join(", ")} tracks. You can add them again after this run finishes.`,
    });
  }
}

function startCreepingProgress(get: () => PipelineState, set: (partial: Partial<PipelineState>) => void): () => void {
  set({ progress: 8 });
  const id = setInterval(() => {
    const p = get().progress;
    if (p < 90) set({ progress: Math.min(90, p + 2) });
  }, 500);
  return () => clearInterval(id);
}

/** Streams `run`'s output into an existing message, driving activeStatus/navStatus/progress
 * around it. Shared by fresh generation, refinement, and retry. */
async function streamIntoMessage(
  get: () => PipelineState,
  set: (partial: Partial<PipelineState>) => void,
  messageId: string,
  run: (onChunk: (chunk: string) => void, signal: AbortSignal) => Promise<void>,
): Promise<void> {
  set({ activeStatus: "running", navStatus: "Generating…" });
  const stop = startCreepingProgress(get, set);
  const controller = new AbortController();
  generationRequests.set(messageId, controller);

  try {
    await run((chunk) => {
      const current = get().messages.find((m) => m.id === messageId);
      patchMessage(get, set, messageId, { text: (current?.text ?? "") + chunk });
    }, controller.signal);
    patchMessage(get, set, messageId, { streaming: false });
    set({ activeStatus: "hitl", progress: 100, navStatus: "Awaiting Review" });
  } catch (err) {
    // A stop the operator asked for is not a failure and not a truncated draft. `stopStage` has
    // already superseded this card and put the run back to Ready, so anything set here would
    // overwrite that with "Awaiting Review" of a card that is no longer on offer.
    if (controller.signal.aborted) return;
    // A stream cut after the first tokens leaves a real, partial draft. It gets the same treatment
    // as one interrupted by the tab closing — kept, flagged incomplete, regenerable — rather than
    // being replaced by an error card that hides the text it did produce.
    if (err instanceof StreamTruncatedError && err.receivedText) {
      patchMessage(get, set, messageId, { streaming: false, interrupted: true });
      set({ activeStatus: "hitl", progress: 100, navStatus: "Awaiting Review" });
      return;
    }
    const msg = recordFailure(set, err);
    patchMessage(get, set, messageId, { streaming: false, generationError: msg });
    set({ activeStatus: "hitl", progress: 100, navStatus: "Awaiting Review" });
  } finally {
    stop();
    if (generationRequests.get(messageId) === controller) generationRequests.delete(messageId);
  }
}

/** Who to bill the next API call to. Read at call time rather than closed over, because a chat's
 * session row and its run are both created lazily — a call made ten seconds later may be
 * attributable when this one was not. */
function attribution(get: () => PipelineState): { chatSessionId: string | null; runId: string | null } {
  const s = get();
  return { chatSessionId: s.sessionId, runId: s.runId };
}

/** Streaming options that drive the competitor-analysis prepass chip on a generation message.
 * Shared by fresh generation and retry so both render the prepass identically. */
function prepassOptions(
  get: () => PipelineState,
  set: (partial: Partial<PipelineState>) => void,
  messageId: string,
  assetId: string,
) {
  const paired = PREPASS_BY_MAIN_ASSET_BY_PHASE[get().phase][assetId];
  return {
    clientProfile: get().clientProfile,
    // Selects the prompt file the backend builds this stage from. Set here rather than at each call
    // site so a new generation path cannot forget it and silently produce the Phase 1 asset.
    phase: get().phase,
    ...attribution(get),
    onPrepassStart: () => {
      if (!paired) return;
      patchMessage(get, set, messageId, {
        prepass: { assetId: paired.assetId, label: paired.label, status: "running" },
      });
    },
    onPrepass: (event: PrepassEvent) => {
      patchMessage(get, set, messageId, {
        prepass: {
          assetId: event.asset_id,
          label: paired?.label ?? event.asset_id,
          status: event.skipped ? "skipped" : "done",
          content: event.content,
          error: event.error,
        },
      });
    },
  };
}

/** The URL to read for a scrapeable field, or null if this run has nothing usable.
 *
 * "NEW PAGE" is the answer the CRO prompt itself tells the operator to give when there is no
 * existing page, and `skipField` writes "N/A" — neither is a URL, and both must fall through to
 * the ordinary question rather than being fetched. */
function pageUrlFromAnswer(raw: unknown): string | null {
  if (typeof raw !== "string") return null;
  const value = raw.trim();
  if (!value || value.startsWith("[[context:")) return null;
  if (/^(n\/a|none|unknown|skip|new page)/i.test(value)) return null;

  try {
    const parsed = new URL(value.includes("://") ? value : `https://${value}`);
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return null;
    // A hostname with no dot is either a typo or an internal host; the backend refuses the latter
    // anyway, and refusing it here keeps the operator from watching a request fail for nothing.
    if (!parsed.hostname.includes(".")) return null;
    return parsed.toString();
  } catch {
    return null;
  }
}

/** Read the page behind a scrapeable field, then either fill the field and carry on, or fall back
 * to asking the operator to paste.
 *
 * Failure is never fatal and never silent: the card says what went wrong, offers a retry, and the
 * ordinary question is asked underneath it. The one outcome ruled out is filling the field with
 * something thin or wrong, which would send the CRO audit off to critique a page the client does
 * not have. */
async function runPageScrape(
  get: () => PipelineState,
  set: (partial: Partial<PipelineState>) => void,
  index: number,
  asset: AssetDefinition,
  answers: Record<string, unknown>,
  field: FieldDef,
  url: string,
  nextFieldIndex: number,
): Promise<void> {
  // No awaiting field while the read is in flight: the input bar is keyed off `awaitingFieldId`,
  // and letting a paste land mid-read would race the fetch for the same answer.
  set({ intake: { asset, answers, awaitingFieldId: null }, navStatus: "Generating…", activeStatus: "running" });

  const message = push(get, set, {
    role: "assistant",
    kind: "scrape",
    assetId: asset.asset_id,
    scrape: { fieldId: field.field_id, fieldLabel: field.label, url, status: "running" },
  });

  const askInstead = (scrape: ScrapeState) => {
    patchMessage(get, set, message.id, { scrape });
    set({ intake: { asset, answers, awaitingFieldId: field.field_id }, navStatus: "Awaiting Input" });
    push(get, set, { role: "assistant", kind: "question", assetId: asset.asset_id, field });
  };

  try {
    const page = await scrapePage(url);
    const base: ScrapeState = {
      fieldId: field.field_id,
      fieldLabel: field.label,
      url,
      status: "done",
      finalUrl: page.final_url,
      title: page.title,
      content: page.content,
      wordCount: page.word_count,
      charCount: page.char_count,
      lowContent: page.low_content,
      source: page.source,
      warnings: page.warnings,
    };

    if (page.low_content || !page.content.trim()) {
      askInstead({ ...base, status: "error", error: page.warnings[0] ?? "Almost no text came back from that page." });
      return;
    }

    patchMessage(get, set, message.id, { scrape: base });
    answers[field.field_id] = page.content;
    markQuestionAnswered(get, set, field.field_id);
    advanceIntake(get, set, index, asset, answers, nextFieldIndex);
  } catch (err) {
    askInstead({
      fieldId: field.field_id,
      fieldLabel: field.label,
      url,
      status: "error",
      error: err instanceof Error ? err.message : String(err),
    });
  }
}

async function runStage(
  get: () => PipelineState,
  set: (partial: Partial<PipelineState>) => void,
  index: number,
  answers: Record<string, string>,
): Promise<void> {
  const stage = stagesFor(get().phase)[index];
  set({ currentIndex: index });

  const message = push(get, set, {
    role: "assistant",
    kind: "generation",
    assetId: stage.asset.asset_id,
    text: "",
    streaming: true,
    savePhase: "idle",
    answers,
  });

  await streamIntoMessage(get, set, message.id, (onChunk, signal) =>
    streamGenerateStage(stage.asset.asset_id, answers, onChunk, {
      ...prepassOptions(get, set, message.id, stage.asset.asset_id),
      signal,
    }),
  );
}

/** Fold any client-identifying answers this stage collected into the run-level profile, so a
 * later stage that only asks for a topic (blog / webinar / podcast) can still tell its
 * competitor prepass which site to benchmark. First non-empty value wins — the profile describes
 * one client and must not flip mid-run. */
function captureClientProfile(
  get: () => PipelineState,
  set: (partial: Partial<PipelineState>) => void,
  answers: Record<string, unknown>,
) {
  const profile = { ...get().clientProfile };
  let changed = false;

  for (const [fieldId, key] of Object.entries(CLIENT_PROFILE_SOURCES)) {
    if (profile[key]) continue;
    const raw = answers[fieldId];
    if (typeof raw !== "string") continue;
    const value = raw.trim();
    if (!value || value.startsWith("[[context:") || ["N/A", "NONE", "UNKNOWN"].includes(value.toUpperCase())) continue;
    profile[key] = value;
    changed = true;
  }

  if (changed) set({ clientProfile: profile });
}

/** Shared tail of "an answer just landed": drop whatever was derived from it, say so, and continue.
 *
 * The walk restarts at field 0 rather than at the field after this one. With `already-answered`
 * skipping, that lands on exactly the same next question on the normal path — and on an edit it is
 * what lets the walk re-fill a hole that sits *earlier* in the list than the field just answered. */
function applyAnswerAndAdvance(
  get: () => PipelineState,
  set: (partial: Partial<PipelineState>) => void,
  intake: IntakeFlow,
  field: FieldDef,
) {
  const cleared = invalidateDependents(intake.asset, intake.answers, field.field_id);
  if (cleared.length) {
    push(get, set, {
      role: "assistant",
      kind: "text",
      text: `${field.label} changed, so ${cleared.map((f) => f.label).join(" and ")} ${
        cleared.length === 1 ? "no longer applies" : "no longer apply"
      } — sorting that out now.`,
    });
  }
  advanceIntake(get, set, get().currentIndex, intake.asset, intake.answers, 0);
}

/** Stages after which the keyword clustering prepass runs.
 *
 * ICP in Phase 1, because that is where the run first knows its service, market and brand.
 *
 * Phase 2 has no entry here at all, and that is not an omission. It skips ICP (it inherits the
 * parent's), and its very first stage — Pillar Page — carries a headline gate of its own, so
 * anything keyed off a stage *save* would fire after the gate that needed it. Phase 2 therefore
 * triggers earlier and from somewhere else entirely: the moment the operator names the sub-service,
 * in `submitAnswer`, before stage 0 begins.
 *
 * Both legs cluster exactly once, on their own service, and neither can read the other's —
 * `PHASE_SCOPED_CONTEXT_KEYS` on the backend refuses to inherit `keyword_clusters` down the
 * `source_run_id` chain even though every other key is inherited.
 */
const KEYWORD_PREPASS_AFTER: Partial<Record<PipelinePhase, string>> = { phase1: "icp" };

/** Build this run's keyword cluster report, if it has not already got a current one.
 *
 * Fire-and-forget by design. Failure is announced, not raised: without a keyword report the
 * headline gates still work — they fall back to framework-only grounding and say so on the card —
 * so a keyword provider outage costs evidence, not the run.
 */
async function runKeywordPrepass(
  get: () => PipelineState,
  set: (partial: Partial<PipelineState>) => void,
) {
  const runId = get().runId;
  const phase = get().phase;
  if (!runId) return;

  try {
    const result = await buildRunKeywords(runId, get().clientProfile, phase, {
      chatSessionId: get().sessionId,
    });
    if (result.status === "skipped") return; // no service anchored yet — nothing to say

    const clusters = result.clusters.length;
    push(get, set, {
      role: "assistant",
      kind: "text",
      text:
        `Mapped search demand for ${result.service}: ${result.total_keywords} keywords in ` +
        `${clusters} topic ${clusters === 1 ? "cluster" : "clusters"}. Topic suggestions from here ` +
        `on are built from this.`,
    });
  } catch (err) {
    push(get, set, {
      role: "assistant",
      kind: "text",
      text:
        "Couldn't map search demand for this run — topic suggestions will still work, but they " +
        `won't carry search volumes. (${err instanceof Error ? err.message : String(err)})`,
    });
  }
}

/** Intake answers reduced to the strings the suggestion API takes.
 *
 * `intake.answers` holds whatever `submitAnswer` was given — numbers from `number` fields, booleans
 * from `boolean_flag` — so it cannot be handed over as-is. Only the values that read as text can
 * name a service, which is all the anchor resolver is looking for.
 */
function stringAnswers(answers: Record<string, unknown> | undefined): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [key, value] of Object.entries(answers ?? {})) {
    if (typeof value === "string") out[key] = value;
    else if (typeof value === "number") out[key] = String(value);
  }
  return out;
}

/** Fetch (or re-fetch) the suggestions for one headline-choice card.
 *
 * `exclude` carries every headline already offered in this gate, so a re-roll changes the angle
 * instead of rewording round one.
 *
 * A failure is deliberately *not* fatal to the stage. The card falls back to "write your own",
 * which is exactly the question the operator would have been asked without this feature — so a
 * suggestion service that is down costs them convenience, not the run.
 */
/** In-flight suggestion calls, by the card that owns them.
 *
 * Kept outside the store because an `AbortController` is not state to render or persist — it is a
 * handle on a live socket, meaningless once the tab reloads. What it buys is that "Start over"
 * actually means it: the previous request is cancelled rather than left running to resolve later
 * and overwrite the batch the operator asked for.
 */
const headlineRequests = new Map<string, AbortController>();

/** In-flight generation streams, by the id of the message being streamed into.
 *
 * Kept outside the store for the same reason as `headlineRequests`: a controller is not state to
 * render or persist. It exists so `stopStage` can actually stop the request — a "Stop" that only
 * cleared the UI would leave the stream running to completion, still writing tokens the operator
 * has decided they do not want and still being billed for them. The entry is deleted in the
 * `finally` of the call that created it, so an abandoned controller cannot accumulate. */
const generationRequests = new Map<string, AbortController>();

/** Abort every generation stream belonging to `messageIds`, and report whether any was live. */
function abortGenerations(messageIds: Iterable<string>): boolean {
  let stopped = false;
  for (const id of messageIds) {
    const controller = generationRequests.get(id);
    if (!controller) continue;
    controller.abort(new DOMException("Stopped by the operator", "AbortError"));
    generationRequests.delete(id);
    stopped = true;
  }
  return stopped;
}

/** The same for a suggestion batch still in flight. Abandoning the gate that asked for one is the
 * same waste as abandoning a stream: nothing will read the result, and it is still being paid for. */
function abortHeadlineBatches(messageIds: Iterable<string>): void {
  for (const id of messageIds) {
    const controller = headlineRequests.get(id);
    if (!controller) continue;
    controller.abort(new DOMException("Stopped by the operator", "AbortError"));
    headlineRequests.delete(id);
  }
}

/** The rows a new batch adds to the ones already on screen: new topics only, re-keyed so no two
 * rows in the merged list share an id.
 *
 * Both halves are load-bearing. The backend numbers each batch `c1..cN` from scratch, so appending
 * one verbatim would put two `c3`s in the list — and every id here is a selection handle
 * (`picked`, `chosenIds`) and a React key, so a collision means clicking one row highlights two and
 * "Use this one" can build the wrong topic.
 *
 * The duplicate filter is a second line rather than the first: `ground_candidates` on the backend
 * already drops anything matching the `exclude` list it was handed. This catches what that cannot —
 * a batch that repeats itself, and the window where a re-roll was requested before the previous
 * one landed, so the exclude list it went out with was a round behind. Comparison is on the same
 * key the backend uses: letters and digits only, so a repeat wearing different punctuation is
 * still a repeat.
 */
function headlineKey(headline: string): string {
  return headline.toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
}

function mergeCandidates(kept: HeadlineCandidate[], incoming: HeadlineCandidate[]): HeadlineCandidate[] {
  const seen = new Set(kept.map((c) => headlineKey(c.headline)));
  const usedIds = new Set(kept.map((c) => c.id));
  const added: HeadlineCandidate[] = [];

  for (const candidate of incoming) {
    const key = headlineKey(candidate.headline);
    if (!key || seen.has(key)) continue;
    seen.add(key);

    let id = candidate.id;
    let n = kept.length + added.length + 1;
    while (!id || usedIds.has(id)) id = `c${n++}`;
    usedIds.add(id);
    added.push({ ...candidate, id });
  }
  return added;
}

/** How long a suggestion call may run before the card gives up on it.
 *
 * Generous, because the honest ceiling is high: a web search plus ten candidates against a 45KB
 * framework legitimately takes tens of seconds, and killing a call that was about to succeed wastes
 * the spend and the operator's wait. But unbounded is worse — a request that never settles leaves
 * the gate spinning with no buttons, and the gate is what the whole intake is blocked on. At the
 * timeout the card becomes a normal error card, which already has Try again on it.
 */
const HEADLINE_TIMEOUT_MS = 120_000;

async function loadHeadlineSuggestions(
  get: () => PipelineState,
  set: (partial: Partial<PipelineState>) => void,
  messageId: string,
  opts: { reroll?: boolean } = {},
) {
  const message = liveMessage(get(), messageId);
  const state = message?.headlines;
  if (!message || !state) return;

  // Supersede whatever this card had running. Without this a second call races the first.
  headlineRequests.get(messageId)?.abort();
  const controller = new AbortController();
  headlineRequests.set(messageId, controller);
  const timeout = setTimeout(() => controller.abort(new Error(HEADLINE_TIMED_OUT)), HEADLINE_TIMEOUT_MS);

  const alreadyOffered = state.rejected ?? [];
  patchMessage(get, set, messageId, {
    headlines: {
      ...state,
      // A re-roll keeps the current list on screen and usable while the next one loads; a first
      // load has nothing to keep, so it shows the loading state proper.
      status: opts.reroll ? state.status : "loading",
      reloading: opts.reroll,
      error: undefined,
      // Stamped so the progress panel can restart its phase script on a retry rather than resuming
      // mid-sequence — a restarted call showing "checking every one is on-brief" would be a lie.
      attempt: (state.attempt ?? 0) + 1,
    },
  });

  try {
    const result = await suggestHeadlines(state.slot, {
      runId: get().runId,
      profile: get().clientProfile,
      // This stage's answers so far. The anchor is resolved from its own service field first,
      // which is the whole point — without these the backend falls back to run-level facts.
      answers: stringAnswers(get().intake?.answers),
      phase: get().phase,
      exclude: alreadyOffered,
      chatSessionId: get().sessionId,
      signal: controller.signal,
    });

    const current = liveMessage(get(), messageId)?.headlines ?? state;
    // "Show me 10 more" means *more*, so a re-roll appends its batch below the batch already on
    // screen instead of replacing it. Replacing was the old behaviour and it threw away topics the
    // operator had just been reading — including, often, the one they were about to pick, with no
    // way back to it short of re-rolling until it happened to reappear.
    //
    // A first load (and a retry after a failure) still starts from empty: there is nothing to keep,
    // and a retry appending to a batch that errored would double it.
    const kept = opts.reroll ? (current.candidates ?? []) : [];
    const added = mergeCandidates(kept, result.candidates);
    patchMessage(get, set, messageId, {
      headlines: {
        ...current,
        status: "pending",
        reloading: false,
        serviceAnchor: result.service_anchor,
        anchorSource: result.anchor_source,
        label: result.label,
        subject: result.subject,
        channel: result.channel,
        charBudget: result.char_budget,
        multi: result.multi,
        suggestedSelection: result.suggested_selection,
        candidates: [...kept, ...added],
        groundedInKeywords: result.grounded_in_keywords,
        webSearchUsed: result.web_search_used,
        // Accumulated across rounds for the same reason the list is: the line under the list counts
        // everything this gate dropped for being off-anchor, not just whatever the last call did.
        rejectedCount: (opts.reroll ? (current.rejectedCount ?? 0) : 0) + result.rejected_count,
        // Everything *offered*, including any row the merge just dropped as a near-duplicate — the
        // point of the list is that the next call never proposes it again, and a headline the
        // backend returned twice is exactly one it needs telling about.
        rejected: [...alreadyOffered, ...result.candidates.map((c) => c.headline)],
        lastAdded: opts.reroll ? added.length : undefined,
        chosenIds: undefined,
        error: undefined,
      },
    });
  } catch (err) {
    // Superseded rather than failed: a newer call for this same card aborted this one and now owns
    // the card's state. Writing an error here would stamp "cancelled" over a request that is
    // running perfectly well, and the operator would see their own retry report a failure.
    if (headlineRequests.get(messageId) !== controller) return;

    const timedOut = controller.signal.aborted;
    const current = liveMessage(get(), messageId)?.headlines ?? state;
    patchMessage(get, set, messageId, {
      headlines: {
        ...current,
        // A failed re-roll leaves the batch that already worked on screen; a failed first load has
        // nothing to fall back to but the write-your-own field.
        status: opts.reroll && current.candidates?.length ? "pending" : "error",
        reloading: false,
        error: timedOut ? HEADLINE_TIMED_OUT : recordFailure(set, err),
      },
    });
  } finally {
    clearTimeout(timeout);
    if (headlineRequests.get(messageId) === controller) headlineRequests.delete(messageId);
  }
}

/** Re-read the slot table and apply it to any gate still waiting on an answer.
 *
 * A suggestion card is snapshotted into the chat with everything it was built from — its
 * candidates and its `multi` flag alike — so a slot that later becomes multi-select leaves every
 * already-open gate single-select, restored that way on every reopen. Restarting both halves does
 * not touch it: the stale value is in the saved chat, not in either process. This is the one place
 * that can put it right without making the operator throw the chat away.
 *
 * Config only. The candidates on screen are the operator's to keep — they were paid for, they are
 * still perfectly good topics, and refetching them here would spend a call to replace a list
 * nobody complained about. Best-effort: a card left on the old config is still answerable.
 */
async function refreshPendingHeadlineGates(get: () => PipelineState, set: (partial: Partial<PipelineState>) => void) {
  const pending = get().messages.filter((m) => m.kind === "headline-choice" && m.headlines?.status === "pending");
  if (!pending.length) return;

  try {
    const bySlot = new Map((await listHeadlineSlots()).map((s) => [s.slot, s]));
    for (const message of pending) {
      const state = message.headlines;
      const cfg = state?.slot ? bySlot.get(state.slot) : undefined;
      if (!state || !cfg) continue;
      if (cfg.multi === Boolean(state.multi) && cfg.suggested_selection === state.suggestedSelection) continue;
      patchMessage(get, set, message.id, {
        headlines: {
          ...state,
          multi: cfg.multi,
          suggestedSelection: cfg.suggested_selection,
          channel: cfg.channel,
          charBudget: cfg.char_budget,
        },
      });
    }
  } catch (err) {
    // Not surfaced: the gate is answerable either way, and a warning here would be about a
    // difference the operator never knew existed.
    console.warn("Could not refresh the headline slot config for this chat", err);
  }
}

/** Shared tail of every way a headline gate is settled: record the choice, file it so the rest of
 * the leg stays on theme, answer the field, and continue the intake.
 *
 * The selection is written to the Context Store as well as to the answer because the two bind
 * different things — the answer binds this stage, `selected_headlines` binds the ones after it.
 * That write is best-effort: an operator who has chosen a topic should not be blocked from
 * generating because a bookkeeping row failed. It does double duty, though — its response carries
 * the canonical rendering of the selection, which is what this stage's answer becomes.
 */
async function settleHeadlineChoice(
  get: () => PipelineState,
  set: (partial: Partial<PipelineState>) => void,
  messageId: string,
  answer: string,
  selected: Record<string, unknown>[],
  source: "suggested" | "operator",
) {
  const message = liveMessage(get(), messageId);
  const field = message?.field;
  const state = message?.headlines;
  const { intake } = get();
  if (!message || !field || !state || !intake) return;

  intake.answers[field.field_id] = answer;
  markQuestionAnswered(get, set, field.field_id);

  const runId = get().runId;
  if (runId) {
    try {
      const saved = await saveHeadlineSelection(runId, state.slot, selected, source, get().phase);
      // The backend's rendering wins, and it is richer than the one above: on a multi-select slot
      // it carries each pick's format and mechanic (lead magnets) or keyword, intent and funnel
      // stage (blog topics) under its numbered line. That detail is the whole reason a stage can
      // build ten different assets or write five posts that don't compete with each other — a bare
      // list of titles would leave it guessing at every one. `render_selection` in
      // `app/services/headlines.py` owns the format so both sides agree on it.
      if (saved.rendered) intake.answers[field.field_id] = saved.rendered;
    } catch (err) {
      // Logged, not surfaced: the chosen topic is already in `intake.answers` and the stage will
      // generate correctly. What is missing is the cross-stage theme binding, and the per-pick
      // detail — the local rendering above still names every pick, in order.
      console.warn("Could not record the headline selection for later stages", err);
    }
  }

  const fromIndex = intake.asset.fields.findIndex((f) => f.field_id === field.field_id) + 1;
  advanceIntake(get, set, get().currentIndex, intake.asset, intake.answers, fromIndex);
}

/** What the page in question would be *for*, read off an answer given earlier in the same intake.
 *
 * Blank rather than guessed when the field has not been answered — the offer reads "a page for this
 * service" instead of naming the wrong one, and a service named wrongly here is worse than not
 * naming it, because it is the thing the operator is being asked to confirm does not exist. */
function subjectOf(
  intake: { asset: AssetDefinition; answers: Record<string, unknown> },
  option: { subjectFieldId: string },
): string | undefined {
  const raw = intake.answers[option.subjectFieldId];
  if (typeof raw !== "string") return undefined;
  const value = raw.trim();
  if (!value || value.startsWith("[[context:") || /^(n\/a|none|unknown)$/i.test(value)) return undefined;
  return value;
}

function advanceIntake(
  get: () => PipelineState,
  set: (partial: Partial<PipelineState>) => void,
  index: number,
  asset: AssetDefinition,
  answers: Record<string, unknown>,
  fromIndex: number,
) {
  const result = findNextAskable(asset, get().context, answers, fromIndex, {
    values: get().clientProfile,
    // Phase 2 adds one fact to the Phase 1 set: the sub-service the whole run is for, which answers
    // the "target service" field on four of its seven stages rather than being asked four times.
    fieldToFact: FIELD_TO_FACT_BY_PHASE[get().phase],
  });

  // Announce reused answers as they are skipped, so a stage that asks nothing still shows *why* —
  // and offer each one for editing, since a question that was never asked is the one an operator has
  // no other way to correct.
  if (result.autoKnownFields.length) {
    push(get, set, {
      role: "assistant",
      kind: "text",
      text: `Reusing from earlier in this run: ${result.autoKnownFields.map((f) => f.label).join(", ")}.`,
      editableFields: dedupeFields(result.autoKnownFields),
    });
  }

  if (result.field) {
    // A field whose content is just "whatever is on that page" gets read rather than asked, as
    // long as the URL question earlier in this same intake produced a usable answer.
    //
    // Not guarded against having read this page before in the chat: a re-entered stage starts with
    // an empty answers map, so it genuinely needs the content again, and re-reading it is both
    // cheap and more current than reusing an older read. There is no loop to guard against either —
    // a successful read fills the field and the walk moves past it.
    const scrapeField = result.field;
    const urlFieldId = SCRAPE_SOURCES[scrapeField.field_id];
    const pageUrl = urlFieldId ? pageUrlFromAnswer(answers[urlFieldId]) : null;
    if (pageUrl) {
      void runPageScrape(get, set, index, asset, answers, scrapeField, pageUrl, result.index + 1);
      return;
    }

    // Ask permission to research this field rather than either searching unbidden or making the
    // operator paste a list they may not have.
    if (offerCompetitorResearch(get, set, asset, answers, scrapeField)) return;

    set({ intake: { asset, answers, awaitingFieldId: result.field.field_id } });

    // A topic the operator would otherwise type cold: offer suggestions first. The gate sits here
    // rather than inside the question card because it has to be able to *not* appear — a field with
    // no slot, or a run with no service anchored yet, falls straight through to the normal
    // question, which is still a perfectly good way to answer it.
    const headlineField = result.field;
    const headlineSlot = headlineField.kind === "headline_choice" ? headlineField.headlineSlot : undefined;
    if (headlineSlot) {
      const card = push(get, set, {
        role: "assistant",
        kind: "headline-choice",
        assetId: asset.asset_id,
        field: headlineField,
        headlines: { slot: headlineSlot, status: "loading" },
      });
      void loadHeadlineSuggestions(get, set, card.id);
      return;
    }

    if (result.plan?.action === "confirm-context") {
      // Resolvable, but the operator gets first refusal on it.
      push(get, set, {
        role: "assistant",
        kind: "context-choice",
        assetId: asset.asset_id,
        field: result.field,
        contextChoice: { label: result.plan.label, text: result.plan.text },
        contextChoiceStatus: "pending",
      });
      return;
    }

    // A stage whose page-source field has a real "there is no such page" answer offers it on the
    // question itself rather than as a card in front of it. The common path — the client does have
    // a page — stays exactly one action: type the URL. See `NEW_PAGE_OPTIONS`.
    const newPage = NEW_PAGE_OPTIONS[asset.asset_id];
    const pageSource =
      newPage && result.field.field_id === newPage.urlFieldId
        ? { assetId: asset.asset_id, subject: subjectOf({ asset, answers }, newPage) }
        : undefined;

    push(get, set, {
      role: "assistant",
      kind: "question",
      assetId: asset.asset_id,
      field: result.field,
      pageSource,
    });
    return;
  }

  set({ intake: null });
  captureClientProfile(get, set, answers);
  const autoLabels = Array.from(new Set(result.autoContextLabels));
  if (autoLabels.length) {
    push(get, set, {
      role: "assistant",
      kind: "text",
      text: `Auto-filled from Context Store: ${autoLabels.join(", ")}`,
      editableFields: dedupeFields(result.autoContextFields),
    });
  }

  const finalAnswers = resolveFinalAnswers(asset, get().context, answers);
  void runStage(get, set, index, finalAnswers);
}

/** A readable name for a context key no asset owns — a document the operator supplied by hand.
 * `email_sequence_copy` reads as "Email sequence copy" wherever a producing asset's label would
 * otherwise go. */
function humaniseContextKey(key: string): string {
  const words = key.replace(/_/g, " ").trim();
  return words ? words[0].toUpperCase() + words.slice(1) : key;
}

/** Pull any context this asset reads that the in-session store is missing back out of the database.
 *
 * The in-memory context covers the normal path (everything approved earlier in this chat). This
 * covers the gaps: a chat resumed where the snapshot predates a key, or a run whose stages were
 * approved in an earlier session. Failures are non-fatal — a key that cannot be fetched simply
 * stays unresolved, and the operator is asked for it as they would have been anyway. */
async function hydrateContextFromDb(
  get: () => PipelineState,
  set: (partial: Partial<PipelineState>) => void,
  asset: AssetDefinition,
): Promise<void> {
  const runId = get().runId;
  if (!runId) return; // nothing saved yet — nothing to read back

  // context key the field looks up -> asset_id the database stores it under. The two differ for
  // every stage that writes more than one key, which is most of them: `cro_rewritten_copy` and
  // `cro_terminology_map` both live in the single row written by `cro`. Requesting the field's own
  // key would 404 on exactly the documents Phase 2 exists to inherit.
  //
  // The key itself is asked for as well, and takes precedence, because there is a second way a
  // document gets into the store: `POST /runs/{id}/context` files one the operator supplied by
  // hand, under the key the field asks for. Producer-only, a run that skipped the CRO stage and
  // pasted the rewrite instead would be asked for it again on every later stage — and
  // `email_sequence_copy`, which no asset writes, could never be read back at all.
  const wanted = new Map<string, string | null>();
  for (const field of asset.fields) {
    if (resolveContext(field, get().context)) continue;
    const keys = field.context_keys?.length ? field.context_keys : [field.context_key ?? ""];
    for (const key of keys) {
      if (!key || key === "unresolved_context_key") continue;
      wanted.set(key, producerAssetIdFor(key) ?? null);
    }
  }
  if (!wanted.size) return;

  const lookups = [...new Set([...wanted.keys(), ...wanted.values()].filter((k): k is string => !!k))];
  const fetched = new Map(
    await Promise.all(lookups.map(async (id) => [id, await fetchRunContext(runId, id)] as const)),
  );

  const additions: ContextStore = {};
  for (const [key, producer] of wanted) {
    const own = fetched.get(key);
    const entry = own ?? (producer ? fetched.get(producer) : null);
    if (!entry) continue;
    const from = own ? producer ?? key : (producer as string);
    additions[key] = {
      assetId: from,
      label: ASSET_BY_ID[from]?.label ?? humaniseContextKey(key),
      text: entry.content,
    };
  }
  if (Object.keys(additions).length) {
    set({ context: { ...additions, ...get().context } }); // in-session values win on conflict
  }
}

/** Start a stage's own intake + generation (the part that produces the main deliverable). */
function beginMainIntake(get: () => PipelineState, set: (partial: Partial<PipelineState>) => void, index: number) {
  const stage = stagesFor(get().phase)[index];
  set({ currentIndex: index, subStep: null, activeStatus: "running", progress: 5, navStatus: "Awaiting Input" });
  void hydrateContextFromDb(get, set, stage.asset).finally(() => {
    advanceIntake(get, set, index, stage.asset, {}, 0);
  });
}

/** Run a gated competitor sub-step and park it for review.
 *
 * Unlike a main stage this asks no intake questions — everything it needs (target URL, niche,
 * region) is already in the run-level client profile from ICP, which is the whole reason it can
 * slot in before its parent stage's own intake. */
/** Record a failure, and raise the dialog for the ones the operator cannot fix from the card.
 *
 * The line is `blocks_run`: an exhausted credit balance, a rejected key, or no route to the API will
 * fail every remaining stage identically, so saying it once, loudly, beats a dozen inline "Generation
 * failed" cards. A stage-specific fault (a too-long prompt, a truncated draft) stays inline, where
 * the retry button is. Returns the message for the inline card either way — nothing is swallowed.
 */
function recordFailure(set: (partial: Partial<PipelineState>) => void, err: unknown): string {
  if (err instanceof ApiFaultError) {
    if (err.fault.blocks_run) set({ fault: err.fault });
    return err.fault.title === err.fault.message ? err.fault.message : `${err.fault.title} — ${err.fault.message}`;
  }
  return err instanceof Error ? err.message : String(err);
}

async function runCompetitorStep(
  get: () => PipelineState,
  set: (partial: Partial<PipelineState>) => void,
  competitor: { assetId: string; label: string },
  opts: { inputs?: CompetitorRunInputs; fillsFieldId?: string } = {},
) {
  const profile = get().clientProfile;
  const inputs: CompetitorRunInputs =
    opts.inputs ?? {
      target_url: profile.website_url,
      niche: profile.industry,
      location: profile.region,
      // `service` is what the competitor prompt searches on. In Phase 2 that is the sub-service the
      // run is for, and it is the entire difference between this search and the Phase 1 one: without
      // it the backend has nothing to put in `{SERVICE}` and returns the client's whole-market
      // competitors instead of the ones competing on this sub-service.
      service: profile[SUB_SERVICE_FACT],
    };

  const message = push(get, set, {
    role: "assistant",
    kind: "competitor",
    assetId: competitor.assetId,
    savePhase: "idle",
    competitorInputs: inputs,
    competitorFillsFieldId: opts.fillsFieldId,
  });

  set({ activeStatus: "running", navStatus: "Generating…" });
  const stop = startCreepingProgress(get, set);
  try {
    const result = await runCompetitorAnalysis(competitor.assetId, inputs, get().phase, attribution(get));
    patchMessage(get, set, message.id, { competitor: result, competitorError: undefined });
    set({ activeStatus: "hitl", progress: 100, navStatus: "Awaiting Review" });
  } catch (err) {
    const msg = recordFailure(set, err);
    patchMessage(get, set, message.id, { competitorError: msg });
    set({ activeStatus: "hitl", progress: 100, navStatus: "Awaiting Review" });
  } finally {
    stop();
  }
}

/** The first item of a multi-select answer, or the answer itself when it is a single line.
 *
 * A multi-select suggestion gate renders its answer as `1. Headline` with the pick's keyword and
 * intent on indented lines beneath it (`render_selection` in `app/services/headlines.py`). That is
 * what the stage needs to build every pick, and it is not something a competitor search can be run
 * on. The backend guards its own path the same way — see `_search_subject` in
 * `app/services/competitor.py`. */
function firstSelectedTopic(answer: string): string {
  for (const line of answer.split("\n")) {
    // Detail lines are indented under their item; a leading space is what marks them.
    if (!line.trim() || /^\s/.test(line)) continue;
    return line.trim().replace(/^(?:\d+[.)]|[-*\u2022])\s*/, "").trim();
  }
  return "";
}

/** How many items that answer holds — 1 for a plain single-line answer. */
function countSelectedTopics(answer: string): number {
  const items = answer.split("\n").filter((line) => line.trim() && !/^\s/.test(line));
  return items.length || 1;
}

/** Offer to research this field instead of asking for it, when the walk reaches a field listed in
 * this phase's `COMPETITOR_CONSENT_FIELDS`. Returns false when the offer can't be made — no topic to
 * search on,
 * or no client URL to benchmark against — in which case the field is asked the ordinary way. */
function offerCompetitorResearch(
  get: () => PipelineState,
  set: (partial: Partial<PipelineState>) => void,
  asset: AssetDefinition,
  answers: Record<string, unknown>,
  field: FieldDef,
): boolean {
  const config = COMPETITOR_CONSENT_FIELDS_BY_PHASE[get().phase][field.field_id];
  if (!config) return false;

  const profile = get().clientProfile;
  const answer = String(answers[config.topicFieldId] ?? "").trim();
  const topic = firstSelectedTopic(answer);
  const topicCount = countSelectedTopics(answer);
  const targetUrl = String(answers["client_website_url"] ?? "").trim() || profile.website_url || "";
  // Both are load-bearing for the search prompt: without the topic it would return whoever ranks
  // for anything, and without a client URL the backend has nothing to benchmark against.
  if (!topic || topic.startsWith("[[context:") || !targetUrl) return false;

  const label = ASSET_BY_ID[config.competitorAssetId]?.label ?? config.competitorAssetId;
  set({ intake: { asset, answers, awaitingFieldId: null }, navStatus: "Awaiting Input", activeStatus: "running" });
  push(get, set, {
    role: "assistant",
    kind: "competitor-consent",
    assetId: config.competitorAssetId,
    field,
    consent: {
      competitorAssetId: config.competitorAssetId,
      label,
      topic,
      topicCount,
      targetUrl,
      location: profile.region,
      niche: profile.industry,
      fieldId: field.field_id,
      subject: config.subject,
      status: "pending",
    },
  });
  return true;
}

/** Ask which sub-service this Phase 2 run is for.
 *
 * A run-level question, not a stage's: it is asked once, before stage 01, and then answers the
 * "target service" field on four of the seven stages (`PHASE2_FIELD_TO_SUB_SERVICE`) instead of being
 * asked again on each. It is also what every competitor search in the run is run against.
 *
 * Carried on `intake` with the *first stage's* asset, so the existing question card, answer bar,
 * choice pills and hint line all work unchanged — `submitAnswer` recognises the field id and files
 * the answer to the run profile rather than to that stage's answers. */
function askSubService(get: () => PipelineState, set: (partial: Partial<PipelineState>) => void) {
  const stage = stageAt(get().phase, 0);
  set({
    intake: { asset: stage.asset, answers: {}, awaitingFieldId: SUB_SERVICE_FIELD.field_id },
    activeStatus: "running",
    navStatus: "Awaiting Input",
    progress: 4,
  });
  push(get, set, { role: "assistant", kind: "question", field: SUB_SERVICE_FIELD });
}

/** Offer the Phase 1 runs this sub-service run could be built on.
 *
 * Phase 2 reads the ICP, CRO rewrite and value ladder its parent run approved, so the parent has to
 * be chosen before anything else — including before the run row exists, since the link is set at
 * creation. Listing failures are not fatal: the card offers "start without a Phase 1 run", which
 * simply means every inherited document gets asked for instead. */
async function offerSourceRun(get: () => PipelineState, set: (partial: Partial<PipelineState>) => void) {
  const message = push(get, set, {
    role: "assistant",
    kind: "source-run",
    sourceRunStatus: "loading",
  });
  set({ activeStatus: "running", navStatus: "Awaiting Input", progress: 3 });

  try {
    const runs = await listSourceRuns();
    patchMessage(get, set, message.id, {
      sourceRuns: runs,
      sourceRunStatus: "pending",
    });
  } catch (err) {
    patchMessage(get, set, message.id, {
      sourceRunStatus: "error",
      sourceRunError: err instanceof Error ? err.message : String(err),
    });
  }
}

/** Open a Phase 2 run: settle which Phase 1 run it inherits from, then ask the sub-service.
 *
 * Two ways in. A chat that has just walked Phase 1 already answers the first question by existing —
 * the run on screen is the parent — so the link is announced and the picker skipped; asking an
 * operator to choose between their runs while they are sitting inside one is noise. A chat that
 * opens straight into Phase 2 has nothing to inherit yet and gets the picker.
 *
 * A failed `createRun` is not fatal: `ensureRun` retries at the first save with the same
 * `sourceRunId`, so the only cost is that inherited context stays unreadable until then. */
async function beginPhase2(get: () => PipelineState, set: (partial: Partial<PipelineState>) => void) {
  const inherited = get().sourceRunId;
  if (!inherited) {
    await offerSourceRun(get, set);
    return;
  }

  push(get, set, {
    role: "assistant",
    kind: "text",
    text: "Building on this chat's Phase 1 run. Its approved assets — ICP, CRO copy, value ladder and the rest — are read as this run needs them, and each one is offered for you to accept or replace rather than being used silently.",
  });

  try {
    const { run_id } = await createRun(get().clientProfile.client_name || PLACEHOLDER_CLIENT_NAME, inherited);
    set({ runId: run_id });
  } catch (err) {
    console.error("Failed to create the Phase 2 run", err);
  }

  askSubService(get, set);
}

/** Summarise the competitor set just approved, then start the stage's own intake.
 *
 * Only for the two stages whose next questions are about the thing the briefing reports on (see
 * `COMPETITOR_BRIEFING_STAGES`). A failure here does not block the stage: the briefing is a reading
 * of an analysis the operator has already read and approved, so a failed one costs them a
 * convenience, not an input. The intake starts either way.
 */
async function runCompetitorBriefing(
  get: () => PipelineState,
  set: (partial: Partial<PipelineState>) => void,
  assetId: string,
  competitorOutput: string,
) {
  const spec = COMPETITOR_BRIEFING_STAGES[assetId];
  if (!spec || !competitorOutput.trim()) {
    beginMainIntake(get, set, get().currentIndex);
    return;
  }

  const message = push(get, set, {
    role: "assistant",
    kind: "briefing",
    assetId,
    briefing: { assetId, title: spec.title, blurb: spec.blurb, status: "running" },
  });
  set({ activeStatus: "running", navStatus: "Generating…" });

  try {
    const summary = await fetchCompetitorBriefing(
      assetId,
      competitorOutput,
      get().clientProfile[SUB_SERVICE_FACT] ?? "",
      attribution(get),
    );
    patchMessage(get, set, message.id, {
      briefing: { assetId, title: spec.title, blurb: spec.blurb, status: "done", summary },
    });
  } catch (err) {
    patchMessage(get, set, message.id, {
      briefing: {
        assetId,
        title: spec.title,
        blurb: spec.blurb,
        status: "error",
        error: err instanceof Error ? err.message : String(err),
      },
    });
  }

  beginMainIntake(get, set, get().currentIndex);
}

/** Enter stage `index`. Stages with a gated competitor sub-step run that first and pause for its
 * own approval; everything else goes straight to its own intake. */
/** Patch one gate card's state, leaving the rest of the message alone. */
function patchGate(
  get: () => PipelineState,
  set: (partial: Partial<PipelineState>) => void,
  messageId: string,
  patch: Partial<StageGateState>,
) {
  const message = liveMessage(get(), messageId);
  if (!message?.gate) return;
  patchMessage(get, set, messageId, { gate: { ...message.gate, ...patch } });
}

/** Ask the server what the stage on this gate still needs, and put the answer on the card.
 *
 * Read-only and free, so it is called again after every paste rather than the card patching its own
 * copy: the two numbers that matter — whether the stage is blocked, and which assets are still
 * worth running — are computed against the whole run, and a document pasted for one field routinely
 * clears a branch of three stages behind it. Recomputing that here would be a second implementation
 * of the walk in `dependencies.py`, and the one that was wrong would be this one.
 */
async function loadStageReadiness(
  get: () => PipelineState,
  set: (partial: Partial<PipelineState>) => void,
  messageId: string,
): Promise<void> {
  const gate = liveMessage(get(), messageId)?.gate;
  if (!gate) return;
  patchGate(get, set, messageId, { status: gate.readiness ? gate.status : "loading", error: undefined });
  try {
    const runId = await ensureRun(get, set);
    const readiness = await fetchAssetReadiness(runId, gate.assetId, get().phase);
    patchGate(get, set, messageId, { status: "pending", readiness, error: undefined });
  } catch (err) {
    console.error("Could not read stage readiness", err);
    patchGate(get, set, messageId, {
      status: "error",
      error: err instanceof Error ? err.message : "Could not check what this stage needs.",
    });
  }
}

/** Push the gate for `assetId` and park the run on it.
 *
 * `intake` is cleared and `activeStatus` dropped because the operator has just moved the run
 * somewhere else: a question left standing from wherever they were would still be the field the
 * answer bar submits into, and their next keystroke would answer a stage they are no longer in.
 */
async function openStageGate(
  get: () => PipelineState,
  set: (partial: Partial<PipelineState>) => void,
  assetId: string,
): Promise<void> {
  const stages = stagesFor(get().phase);
  const index = stages.findIndex((s) => s.asset.asset_id === assetId);
  if (index < 0) return;

  set({
    currentIndex: index,
    subStep: null,
    intake: null,
    activeStatus: null,
    progress: 0,
    navStatus: "Awaiting Input",
    editSeed: null,
  });

  const card = push(get, set, {
    role: "assistant",
    kind: "stage-gate",
    assetId,
    gate: { assetId, index, status: "loading" },
  });
  await loadStageReadiness(get, set, card.id);
}

/** Stage 01, or the stage the operator asked to start at instead.
 *
 * Both of the run's opening paths land here, which is what makes "start at Blog" work on a Phase 2
 * chat: the request survives the two run-level questions that have to be answered before any stage
 * can be opened at all. */
function beginRequestedStage(
  get: () => PipelineState,
  set: (partial: Partial<PipelineState>) => void,
) {
  const requested = get().pendingStartAssetId;
  if (!requested) {
    beginStage(get, set, 0);
    return;
  }
  set({ pendingStartAssetId: null });
  void openStageGate(get, set, requested);
}

function beginStage(get: () => PipelineState, set: (partial: Partial<PipelineState>) => void, index: number) {
  const stage = stagesFor(get().phase)[index];
  const competitor = competitorStageFor(get().phase, stage.asset.asset_id);

  if (competitor && GATED_COMPETITOR_IDS_BY_PHASE[get().phase].has(stage.asset.asset_id)) {
    set({ currentIndex: index, subStep: "competitor", navStatus: "Generating…" });
    void runCompetitorStep(get, set, competitor);
    return;
  }
  beginMainIntake(get, set, index);
}

/** Put the card the returned-to stage is waiting on back at the bottom of the transcript.
 *
 * This is the other half of "I cannot move forward with Stage 09", and the half that is not a state
 * bug at all. A re-run inserts a whole stage's worth of cards — its intake questions, its draft, its
 * save notices — *after* the card the operator was parked on. The state stays correct
 * (`intake.awaitingFieldId` still names the field, and the question card is still there, unanswered)
 * but it is now scrolled tens of messages out of view, and `GenerationStream` auto-scrolls to the
 * bottom. So the operator reads the last thing on screen — "Next up is Stage 09" — sees no question
 * under it, and concludes the stage is dead. Measured on a real session: the pending question for
 * `primary_service_pillar_page_being_supported` sat ~40 messages above the end, above two blog
 * re-runs.
 *
 * Cloning the pending card to the end and superseding the original moves the question to where the
 * operator is actually looking. It costs nothing and asks nothing again: the clone carries whatever
 * the original had already loaded, so a headline gate keeps its fetched candidates rather than
 * paying for a fresh suggestion call, and a context choice keeps its text.
 */
function resurfacePendingCard(
  get: () => PipelineState,
  set: (partial: Partial<PipelineState>) => void,
  index: number,
) {
  const state = get();
  const stage = stagesFor(state.phase)[index];
  if (!stage) return;

  // Scoped to the stage being returned to, and to its competitor sub-step, which is a separate
  // asset id on the same stage. Unscoped, this reaches for the newest pending card anywhere in the
  // leg and can resurface a card belonging to the stage that was just re-run — which is not what
  // the operator is being sent back to, and would put the wrong question under the wrong heading.
  const competitor = competitorStageFor(state.phase, stage.asset.asset_id);
  const ownsIt = (m: PipelineMessage) =>
    m.assetId === stage.asset.asset_id || (!!competitor && m.assetId === competitor.assetId);

  const isPending = (m: PipelineMessage) => {
    if (m.superseded || !ownsIt(m)) return false;
    if (m.kind === "question") return !m.answered;
    if (m.kind === "headline-choice") return m.headlines?.status !== "chosen";
    if (m.kind === "context-choice") return m.contextChoiceStatus === "pending";
    if (m.kind === "stage-gate") return !!m.gate && m.gate.status !== "entered";
    if (m.kind === "stage-stopped") return true;
    if (m.kind === "competitor-consent") return m.consent?.status === "pending";
    return false;
  };

  // `intake.awaitingFieldId` is the authoritative answer to "what is this stage waiting on", so the
  // card for that field wins outright. The newest pending card for the stage is the fallback, for a
  // gate that owns the turn without the intake naming a field (a consent card, say).
  const own = messagesInCurrentTrack(state);
  const awaiting = state.intake?.awaitingFieldId;
  const pending =
    (awaiting ? [...own].reverse().find((m) => isPending(m) && m.field?.field_id === awaiting) : undefined) ??
    [...own].reverse().find(isPending);
  if (!pending) return;
  // Already the last thing on screen: nothing was inserted after it, so it needs no help.
  if (state.messages[state.messages.length - 1]?.id === pending.id) return;

  set({ messages: state.messages.map((m) => (m.id === pending.id ? { ...m, superseded: true } : m)) });
  const { id: _id, createdAt: _createdAt, ...content } = pending;
  push(get, set, { ...content, superseded: false });
}

/** The asset ids this leg has actually approved output for.
 *
 * The authority on "is this stage done", and deliberately not `currentIndex`. The two used to be
 * treated as the same thing, which was true only while the run was strictly sequential: now that a
 * stage can be started out of order or skipped, every stage the cursor passed over would otherwise
 * report as "✓ Saved" — jump from stage 02 to stage 09 and seven stages the run never built would
 * claim to be finished, and the progress bar would agree.
 *
 * Superseded cards count. A superseded *saved* generation is a real Context Store version that a
 * later re-run replaced, and the stage it belongs to has certainly been executed. */
export function approvedAssetIds(messages: PipelineMessage[], phase: PipelinePhase, trackId?: string | null): Set<string> {
  return new Set(
    messagesInPhase(messages, phase, trackId)
      .filter((m) => m.kind === "generation" && m.savePhase === "saved" && m.assetId)
      .map((m) => m.assetId as string),
  );
}

/** The next stage in execution sequence: the first one this leg has no approved output for.
 *
 * Read off the transcript rather than off `context`, and scoped to the current phase, because
 * `context` is not a record of what *this* leg produced: a Phase 2 run inherits its Phase 1 run's
 * context (that is what `source_run_id` is for), and every Phase 2 stage id — `pillar_page`,
 * `blog`, `lead_magnet` — also exists in Phase 1. Keyed off `context`, a fresh Phase 2 leg would
 * see its own stage 01 as already executed and skip the whole phase.
 *
 * Superseded cards are counted, not skipped: a superseded *saved* generation is a real Context
 * Store version that a later re-run has replaced, and the stage it belongs to has certainly been
 * executed.
 */
function nextUnexecutedIndex(state: PipelineState): number {
  const approved = approvedAssetIds(state.messages, state.phase);
  const stages = stagesFor(state.phase);
  for (let i = 0; i < stages.length; i++) {
    if (!approved.has(stages[i].asset.asset_id)) return i;
  }
  return stages.length;
}

/** Enter `index` as the stage now being worked on, asking its first question.
 *
 * Split out of `resumeStage` because the re-run return path needs exactly the same thing: both
 * arrive at a stage that the run has already advanced its `currentIndex` past the start of, but
 * whose first question was never asked — one because the tab closed in the gap, the other because
 * the operator hit Re-run on the previous stage while standing in it. */
function enterStage(get: () => PipelineState, set: (partial: Partial<PipelineState>) => void, index: number) {
  const state = get();
  const stage = stagesFor(state.phase)[index];

  // Skip straight to the stage's own intake when its competitor sub-step was already approved: this
  // stage has been part-way entered before, and re-entering `beginStage` would re-run — and
  // re-charge for — a prepass whose output is already in the Context Store.
  const competitor = competitorStageFor(state.phase, stage.asset.asset_id);
  if (competitor && state.context[competitor.assetId]) {
    beginMainIntake(get, set, index);
    return;
  }
  beginStage(get, set, index);
}

export const usePipelineStore = create<PipelineState>((set, get) => ({
  started: false,
  phase: "phase1",
  phaseSlots: {},
  phase2Tracks: {},
  activePhase2TrackId: null,
  phase2TrackSlots: {},
  sourceRunId: null,
  runId: null,
  navStatus: "Ready",
  messages: [],
  context: {},
  currentIndex: 0,
  rerunReturnIndex: null,
  rerunReturnIntake: null,
  activeStatus: null,
  progress: 0,
  intake: null,
  clientProfile: {},
  subStep: null,
  sessionId: null,
  sessionTitle: "New chat",
  isLoadingSession: false,
  loadingSessionId: null,
  editSeed: null,
  fault: null,
  pendingStartAssetId: null,

  start: () => {
    if (get().started) return;
    set({ started: true });

    // Phase 2 has two run-level questions before its first stage: which Phase 1 run it builds on,
    // and which sub-service it is for. Both are properties of the run rather than of any stage, and
    // the first has to be answered before the run row is created — see `offerSourceRun`.
    if (get().phase === "phase2") {
      void beginPhase2(get, set);
      return;
    }
    beginRequestedStage(get, set);
  },

  chooseSourceRun: async (messageId, runId) => {
    const message = liveMessage(get(), messageId);
    if (!message || message.sourceRunStatus === "chosen" || message.sourceRunStatus === "standalone") return;

    const chosen = message.sourceRuns?.find((r) => r.run_id === runId);
    patchMessage(get, set, messageId, {
      sourceRunStatus: runId ? "chosen" : "standalone",
      chosenSourceRunId: runId,
      sourceRunError: undefined,
    });
    set({ sourceRunId: runId });

    push(get, set, {
      role: "assistant",
      kind: "text",
      text: runId
        ? `Building on “${chosen?.chat_title || chosen?.company_name || "the selected run"}”. Its approved assets — ICP, CRO copy, value ladder and the rest — are read as this run needs them, and each one is offered for you to accept or replace rather than being used silently.`
        : "Starting without a Phase 1 run. Nothing is inherited, so every document a stage needs will be asked for.",
    });

    // Created up front rather than lazily on first save: the link is set at creation, and stage 01's
    // intake reads inherited context before anything has been saved.
    try {
      const { run_id } = await createRun(
        chosen?.company_name || get().clientProfile.client_name || PLACEHOLDER_CLIENT_NAME,
        runId,
      );
      set({ runId: run_id });
      // Carried over so the sub-service run does not re-ask a name its parent already established.
      // Guarded against the placeholder: a run created before runs carried a name is stored as
      // "Untitled Client", and adopting that would auto-answer every "Client Name" question in the
      // run with it — putting the placeholder into the finished deliverables rather than asking.
      if (chosen?.company_name && chosen.company_name !== PLACEHOLDER_CLIENT_NAME) {
        set({ clientProfile: { client_name: chosen.company_name, ...get().clientProfile } });
      }
    } catch (err) {
      // Not fatal — `ensureRun` will try again at the first save. The run simply has no id yet, and
      // inherited context stays unavailable until it does.
      patchMessage(get, set, messageId, {
        sourceRunError: err instanceof Error ? err.message : String(err),
      });
    }

    askSubService(get, set);
  },

  retrySourceRuns: async (messageId) => {
    if (!liveMessage(get(), messageId)) return;
    patchMessage(get, set, messageId, { sourceRunStatus: "loading", sourceRunError: undefined });
    try {
      const runs = await listSourceRuns();
      patchMessage(get, set, messageId, { sourceRuns: runs, sourceRunStatus: "pending" });
    } catch (err) {
      patchMessage(get, set, messageId, {
        sourceRunStatus: "error",
        sourceRunError: err instanceof Error ? err.message : String(err),
      });
    }
  },

  selectPhase2Track: (trackId) => {
    const state = get();
    if (state.phase !== "phase2" || state.activePhase2TrackId === trackId) return;
    const track = state.phase2Tracks[trackId];
    if (!track) return;

    const parked: PhaseSlot = {
      started: state.started,
      runId: state.runId,
      sourceRunId: state.sourceRunId,
      currentIndex: state.currentIndex,
      rerunReturnIndex: state.rerunReturnIndex,
      rerunReturnIntake: state.rerunReturnIntake,
      subStep: state.subStep,
      intake: serializeIntake(state.intake),
      context: state.context,
      progress: state.progress,
    };
    const next = state.phase2TrackSlots[trackId];
    const tracks: Record<string, Phase2Track> = Object.fromEntries(
      Object.values(state.phase2Tracks).map((item) => [
        item.id,
        item.id === trackId
          ? { ...item, status: item.status === "complete" ? "complete" : "active" }
          : item.id === state.activePhase2TrackId && item.status === "active"
            ? { ...item, status: "queued" }
            : item,
      ]),
    );
    set({
      phase2Tracks: tracks,
      phase2TrackSlots: { ...state.phase2TrackSlots, [state.activePhase2TrackId ?? trackId]: parked },
      activePhase2TrackId: trackId,
      runId: next?.runId ?? track.runId,
      sourceRunId: next?.sourceRunId ?? track.sourceRunId,
      currentIndex: next?.currentIndex ?? 0,
      rerunReturnIndex: next?.rerunReturnIndex ?? null,
      rerunReturnIntake: next?.rerunReturnIntake ?? null,
      subStep: next?.subStep ?? null,
      intake: hydrateIntake(next?.intake ?? null),
      context: next?.context ?? {},
      progress: next?.progress ?? 0,
      activeStatus: null,
      navStatus: "Ready",
      clientProfile: { ...state.clientProfile, [SUB_SERVICE_FACT]: track.label },
    });
    push(get, set, {
      role: "assistant",
      kind: "text",
      text: `Switched to the ${track.label} track. Its stages, approvals and child run are independent of the other sub-services.`,
    });
    const slot = get().phase2TrackSlots[trackId];
    if (slot) {
      set(deriveResumeActivity(get().messages, hydrateIntake(slot.intake), slot.currentIndex, slot.progress, "phase2", trackId));
    } else {
      beginStage(get, set, 0);
    }
  },

  addPhase2Track: async (label) => {
    const cleanLabel = label.trim();
    const state = get();
    if (state.phase !== "phase2" || !cleanLabel) return;
    if (
      Object.values(state.phase2Tracks).some(
        (track) => track.status !== "error" && track.label.toLowerCase() === cleanLabel.toLowerCase(),
      )
    ) return;

    try {
      const { run_id: runId } = await createRun(
        state.clientProfile.client_name || PLACEHOLDER_CLIENT_NAME,
        state.sourceRunId,
      );
      const track: Phase2Track = {
        id: runId,
        label: cleanLabel,
        runId,
        sourceRunId: state.sourceRunId,
        status: "queued",
      };
      set({ phase2Tracks: { ...get().phase2Tracks, [track.id]: track } });
      push(get, set, {
        role: "assistant",
        kind: "text",
        text: `Added a new ${cleanLabel} Phase 2 track. Existing sub-service assets and approvals were left untouched.`,
      });
    } catch (err) {
      push(get, set, {
        role: "assistant",
        kind: "text",
        text: `Could not add the ${cleanLabel} track: ${err instanceof Error ? err.message : String(err)}`,
      });
    }
  },

  submitAnswer: (value) => {
    const { intake } = get();
    if (!intake?.awaitingFieldId) return;

    // The sub-service belongs to the run, not to the stage whose asset `intake` happens to carry.
    // Filed to the client profile so every stage that asks for it reads it from there, then the run
    // proper begins.
    if (intake.awaitingFieldId === SUB_SERVICE_FIELD.field_id) {
      const subServices = String(value)
        .split(/[,;\n]/)
        .map((item) => item.trim())
        .filter(Boolean)
        .filter((item, index, items) => items.indexOf(item) === index);
      if (!subServices.length) return;
      const subService = subServices[0];
      // Set on the first pass through this branch and never cleared, so its presence is what tells
      // a correction apart from the opening answer — the two have to end differently.
      const previous = get().clientProfile[SUB_SERVICE_FACT];
      markQuestionAnswered(get, set, SUB_SERVICE_FIELD.field_id);
      push(get, set, { role: "user", kind: "text", text: subService });
      set({ clientProfile: { ...get().clientProfile, [SUB_SERVICE_FACT]: subService }, editSeed: null });

      if (previous) {
        // A correction from `editField`. The walk it interrupted is still standing, so it resumes
        // where it was rather than restarting the run at stage 01 — which would throw away every
        // stage already approved. What cannot stand is the answers the old sub-service filled in.
        const cleared = clearFactFilledAnswers(get().phase, intake.asset, intake.answers, SUB_SERVICE_FACT);
        set({ intake: { ...intake, awaitingFieldId: null } });
        if (cleared.length) {
          push(get, set, {
            role: "assistant",
            kind: "text",
            text: `Sub-service changed to ${subService}, so ${cleared
              .map((f) => f.label)
              .join(" and ")} ${cleared.length === 1 ? "is" : "are"} being filled in again from it.`,
          });
        }
        // Re-anchored on the new sub-service: the clusters behind every later topic suggestion were
        // built from the old one.
        void runKeywordPrepass(get, set);
        advanceIntake(get, set, get().currentIndex, intake.asset, intake.answers, 0);
        return;
      }

      set({ intake: null });
      // The child runs must exist before Stage 01 starts. Otherwise its first cards would be
      // stamped without a track id and the operator could briefly see them in every track.
      void createPhase2Tracks(get, set, subServices).then(() => {
        // Phase 2's keyword prepass, and the only moment it can run: the sub-service is the fact the
        // whole leg's clustering is anchored on, and Phase 2's first stage carries a headline gate,
        // so anything triggered off a stage save would arrive after the gate that needed it.
        void runKeywordPrepass(get, set);
        beginRequestedStage(get, set);
      });
      return;
    }

    const field = intake.asset.fields.find((f) => f.field_id === intake.awaitingFieldId);
    if (!field) return;

    intake.answers[field.field_id] = value;
    markQuestionAnswered(get, set, field.field_id);
    push(get, set, { role: "user", kind: "text", text: formatUserAnswer(value) });
    set({ editSeed: null });

    applyAnswerAndAdvance(get, set, intake, field);
  },

  submitFreeform: (raw) => {
    const { intake } = get();
    if (!intake?.awaitingFieldId) return;
    const field = fieldBeingAsked(get());
    if (!field) return;
    const trimmed = raw.trim();
    if (!trimmed) return;

    if (/^skip$/i.test(trimmed) && !field.required) {
      get().skipField();
      return;
    }
    get().submitAnswer(coerceAnswer(field, trimmed));
  },

  skipField: () => {
    const { intake } = get();
    if (!intake?.awaitingFieldId) return;
    const field = intake.asset.fields.find((f) => f.field_id === intake.awaitingFieldId);
    if (!field) return;

    intake.answers[field.field_id] = typeof field.default !== "undefined" ? field.default : "N/A";
    markQuestionAnswered(get, set, field.field_id);
    push(get, set, { role: "user", kind: "text", text: "Skipped" });
    set({ editSeed: null });

    applyAnswerAndAdvance(get, set, intake, field);
  },

  requestRefine: (messageId) => {
    patchMessage(get, set, messageId, { refining: true });
  },

  cancelRefine: (messageId) => {
    patchMessage(get, set, messageId, { refining: false });
  },

  submitRefine: async (messageId, note) => {
    const trimmed = note.trim();
    if (!trimmed) return;
    const message = liveMessage(get(), messageId);
    if (!message?.assetId || !message.text) return;

    patchMessage(get, set, messageId, { refining: false, refineSubmitted: true });
    push(get, set, { role: "user", kind: "text", text: trimmed });

    const newMessage = push(get, set, {
      role: "assistant",
      kind: "generation",
      assetId: message.assetId,
      text: "",
      streaming: true,
      savePhase: "idle",
      answers: message.answers,
    });

    const assetId = message.assetId;
    const previousDraft = message.text;
    await streamIntoMessage(get, set, newMessage.id, (onChunk, signal) =>
      streamRefineStage(assetId, previousDraft, trimmed, onChunk, signal, get().phase, attribution(get)),
    );
  },

  rerunStage: (messageId) => {
    const state = get();
    if (!selectCanRerun(state)) return;

    const message = liveMessage(state, messageId);
    const assetId = message?.assetId;
    if (!assetId) return;

    const stages = stagesFor(state.phase);
    const index = stages.findIndex((s) => s.asset.asset_id === assetId);
    if (index < 0) return;
    const stage = stages[index];

    // Where to come back to. Only recorded when the re-run is of an *earlier* stage: re-running the
    // one the operator is already on should carry on forward normally, and storing the same index
    // as a "return" would park the run on a stage it had just approved.
    const returnIndex = index < state.currentIndex ? state.currentIndex : null;

    // Unsaved cards for this asset become history at once — not just the one whose button was
    // pressed. A stage that was refined, or retried, or edited leaves several drafts behind, and any
    // of them still offering Save would let the operator approve an old draft after asking for a
    // new one.
    //
    // An *approved* generation card is deliberately left alone. It is not a stale draft — it is a
    // real Context Store version, and `GenerationStream` hides the whole action row of a superseded
    // card, which would take its Download and Share buttons with it. The previous version is
    // precisely the thing an operator may still want a copy of while judging the replacement.
    set({
      messages: state.messages.map((m) => {
        if (
          m.assetId !== assetId ||
          (m.phase ?? state.phase) !== state.phase ||
          (state.phase === "phase2" && m.trackId && m.trackId !== state.activePhase2TrackId)
        ) return m;
        if (m.kind === "generation") {
          return m.savePhase === "saved" ? m : { ...m, superseded: true, refining: false };
        }
        // Intake history: these questions are about to be asked again, so the old cards must stop
        // offering their own edit affordances.
        if (m.kind === "headline-choice" || m.kind === "question") {
          return { ...m, superseded: true };
        }
        return m;
      }),
      intake: null,
      editSeed: null,
      rerunReturnIndex: returnIndex,
      // Only parked when there is somewhere to go back to; a re-run of the current stage just
      // restarts it, and its own intake is the thing being replaced.
      rerunReturnIntake: returnIndex !== null ? serializeIntake(state.intake) : null,
    });

    push(get, set, {
      role: "assistant",
      kind: "text",
      text:
        `Re-running Stage ${String(stage.stageNumber).padStart(2, "0")}: ${stage.asset.label}. ` +
        "I'll ask its own questions again — everything approved in earlier stages is kept, and the " +
        "previous version stays in the Context Store rather than being overwritten." +
        // `returnIndex` can be one past the last stage, when the phase was already complete — in
        // which case there is no stage to name and the honest sentence is a different one.
        (returnIndex === null
          ? ""
          : stages[returnIndex]
            ? ` When it's approved we'll pick back up at Stage ${String(stages[returnIndex].stageNumber).padStart(2, "0")}.`
            : ` ${PHASE_META[state.phase].label} is otherwise complete, so we'll return there once it's approved.`),
    });

    // `beginMainIntake`, not `beginStage`: the gated competitor search is a separately approved
    // asset of its own, and its result is already in the Context Store where this stage's intake
    // reads it. Re-running it would spend another web-search call to re-answer a question the
    // operator did not complain about — and if they *do* want a fresh competitor set, that card has
    // its own Re-run.
    beginMainIntake(get, set, index);
  },

  retryGeneration: async (messageId) => {
    const message = liveMessage(get(), messageId);
    if (!message?.assetId || !message.answers) return;
    const assetId = message.assetId;
    const answers = message.answers;

    patchMessage(get, set, messageId, {
      generationError: undefined,
      interrupted: false,
      streaming: true,
      text: "",
      prepass: undefined,
    });
    await streamIntoMessage(get, set, messageId, (onChunk, signal) =>
      streamGenerateStage(assetId, answers, onChunk, {
        ...prepassOptions(get, set, messageId, assetId),
        signal,
      }),
    );
  },

  saveStage: async (messageId) => {
    const message = liveMessage(get(), messageId);
    if (!message?.assetId || !message.text) return;
    const stage = stagesFor(get().phase).find((s) => s.asset.asset_id === message.assetId);
    if (!stage) return;

    patchMessage(get, set, messageId, { savePhase: "saving", saveError: undefined });

    try {
      const runId = await ensureRun(get, set);

      // The competitor prepass is its own asset_definitions row, so its output is saved as its
      // own ContextEntry — approved implicitly by the operator approving the asset built on it.
      const prepass = message.prepass;
      const extraContext: Record<string, ContextEntry> = {};
      if (prepass?.status === "done" && prepass.content) {
        try {
          await saveStageOutput(runId, prepass.assetId, prepass.content);
          extraContext[prepass.assetId] = {
            assetId: prepass.assetId,
            label: prepass.label,
            text: prepass.content,
          };
        } catch (err) {
          // Never block the main asset's save on the prepass's — the operator approved the
          // asset in front of them, and its own ContextEntry is what downstream stages read.
          console.error("Failed to save competitor prepass output", err);
        }
      }

      await saveStageOutput(runId, message.assetId, message.text, message.answers);
      patchMessage(get, set, messageId, { savePhase: "saved", interrupted: false });

      const entry: ContextEntry = { assetId: stage.asset.asset_id, label: stage.asset.label, text: message.text };
      set({
        context: {
          ...get().context,
          ...extraContext,
          // Every key this stage publishes points at the document it just produced — except the ones
          // the *server* composes, which are a different document entirely. Pointing
          // `cro_client_settings` at the 80KB rewrite would hand the sub-key extractor a haystack
          // instead of the one-line-per-setting sheet it is written against, and whatever it dug out
          // of the prose would then be believed over the real row sitting in the database. Left out,
          // the next stage that wants one reads it back through `hydrateContextFromDb`.
          ...Object.fromEntries(
            stage.asset.writesContextKeys
              .filter((key) => !SERVER_COMPOSED_CONTEXT_KEYS.has(key))
              .map((key) => [key, entry]),
          ),
          [stage.asset.asset_id]: entry,
        },
      });
      schedulePersist();

      const phase = get().phase;
      const total = totalStagesFor(phase);

      // ICP is the first point at which the run knows what service it is for, in what market, under
      // what brand — everything the keyword expansion needs. So the clustering prepass fires here,
      // once, and every later headline gate reads the report it produced. Deliberately not awaited:
      // it takes a minute, nothing in the next stage's opening questions depends on it, and the
      // first gate that does need it is several questions away.
      if (KEYWORD_PREPASS_AFTER[phase] === message.assetId) {
        void runKeywordPrepass(get, set);
      }

      // A re-run of an earlier stage is finished: go back to where the operator was, rather than
      // advancing. Without this, approving one corrected asset would walk forward from its index and
      // re-run every stage after it — a full replay of the pipeline off a single button.
      //
      // Where to go next is computed, not remembered. `currentIndex` cannot be trusted here: a
      // re-run ends in `beginMainIntake`, which parks `currentIndex` on the stage being re-run, so
      // the old `currentIndex + 1` fallthrough meant "the stage after the one just re-run" — it
      // would re-enter and regenerate Stage 08 after a re-run of Stage 07, overwriting an asset
      // nobody asked about. And `rerunReturnIndex` cannot be trusted either: it is a single slot, so
      // starting a second re-run before the first is approved overwrites it, after which the same
      // fallthrough runs.
      //
      // `nextUnexecutedIndex` has neither failure mode. It asks the transcript which stages already
      // have an approved output and returns the first that does not — which is exactly "the next
      // asset remaining to execute", and is the same answer on the ordinary forward path (approve
      // Stage 08, go to Stage 09) as on any re-run path.
      const returnIndex = get().rerunReturnIndex;
      const isRerun = returnIndex !== null;
      const target = nextUnexecutedIndex(get());
      const done = target >= total;

      if (isRerun) {
        // The parked intake belongs to whichever stage the operator was standing in. Restored only
        // when that is still where we are going; carried onto a different stage it would seed the
        // wrong asset's answers.
        const restored = returnIndex === target ? hydrateIntake(get().rerunReturnIntake) : null;
        set({
          rerunReturnIndex: null,
          rerunReturnIntake: null,
          currentIndex: done ? total : target,
          intake: restored,
        });
        // Derived rather than asserted. The stage being returned to might be mid-intake, sitting on
        // an unapproved draft, or finished — and `deriveResumeActivity` is the one place that already
        // works that out from the transcript. Setting "Ready" here would leave the input bar disabled
        // over an unanswered question.
        set(deriveResumeActivity(get().messages, restored, target, done ? 100 : get().progress, phase, get().activePhase2TrackId));

        const stage = done ? null : stageAt(phase, target);
        push(get, set, {
          role: "assistant",
          kind: "text",
          text: stage
            ? `Saved as a new version. Next up is Stage ${String(stage.stageNumber).padStart(2, "0")}: ${stage.asset.label}.`
            : `Saved as a new version. ${PHASE_META[phase].label} is complete.`,
        });
        // Every asset built after the one just replaced was built on its *previous* version. Which
        // of them still holds is a judgement about content, not something the pipeline can compute —
        // so it names the risk and leaves the call to the operator, rather than quietly leaving them
        // stale or re-running eleven assets uninvited.
        const rerunStageNumber = stagesFor(phase).find((s) => s.asset.asset_id === message.assetId)?.stageNumber;
        if (rerunStageNumber !== undefined && rerunStageNumber < totalStagesFor(phase)) {
          push(get, set, {
            role: "assistant",
            kind: "text",
            text:
              `Assets generated after Stage ${String(rerunStageNumber).padStart(2, "0")} were built on ` +
              "its previous version. Re-run any of them too if this change affects them.",
          });
        }

        // ...and then actually hand the operator that stage. Restoring `currentIndex` is not the same
        // as being able to work on it: the common re-run is "approve Stage 08, move to Stage 09, and
        // immediately spot something wrong with 08", which fires Re-run while Stage 09 is still in
        // the gap between `currentIndex` advancing and its first question being asked. Nothing here
        // ever asked that question, so the run came back to a named stage with no card, no pending
        // field, and `navStatus: "Ready"` — a dead end reachable by the most ordinary sequence in
        // the pipeline.
        //
        // Gated on `selectNeedsResume` rather than on the intake being empty, because that selector
        // is already the definition of "this leg has nothing to act on": a stage returned to
        // mid-question, or sitting on its own unapproved draft, fails it and is left exactly as it
        // was. Only the genuinely empty case is started.
        if (!done && selectNeedsResume(get())) {
          enterStage(get, set, target);
        } else if (!done) {
          // The other case, and the one that actually reads as "Stage 09 shows no questions": the
          // stage was already part-way through, so there is nothing to start — its question is just
          // buried above everything the re-run added. Bring it back into view.
          resurfacePendingCard(get, set, target);
        }
        return;
      }

      if (!done) {
        beginStage(get, set, target);
      } else {
        const activeTrackId = get().activePhase2TrackId;
        set({
          activeStatus: null,
          currentIndex: total,
          navStatus: "Ready",
          ...(phase === "phase2" && activeTrackId && get().phase2Tracks[activeTrackId]
            ? { phase2Tracks: { ...get().phase2Tracks, [activeTrackId]: { ...get().phase2Tracks[activeTrackId], status: "complete" } } }
            : {}),
        });
        push(get, set, {
          role: "assistant",
          kind: "text",
          text: `All ${total} ${PHASE_META[phase].label} assets have been generated and saved to the Context Store. ${PHASE_META[phase].label} is complete.`,
        });
      }
    } catch (err) {
      const msg = recordFailure(set, err);
      patchMessage(get, set, messageId, { savePhase: "error", saveError: msg });
    }  },

  acceptCompetitorResearch: async (messageId) => {
    const message = liveMessage(get(), messageId);
    const consent = message?.consent;
    const intake = get().intake;
    if (!consent || consent.status !== "pending" || !intake) return;

    patchMessage(get, set, messageId, { consent: { ...consent, status: "accepted" } });
    push(get, set, {
      role: "assistant",
      kind: "text",
      text: `Searching for competitors' ${consent.subject} on “${consent.topic}”. This takes a minute — it verifies each page rather than trusting search snippets.`,
    });

    await runCompetitorStep(
      get,
      set,
      { assetId: consent.competitorAssetId, label: consent.label },
      {
        // `service` is what the competitor prompt searches on ({SERVICE} in 05_SEO_Pillar_Page.md),
        // so the pillar page's own topic goes here — that is the whole point of asking first.
        inputs: {
          target_url: consent.targetUrl,
          niche: consent.niche,
          location: consent.location,
          service: consent.topic,
        },
        fillsFieldId: consent.fieldId,
      },
    );
  },

  declineCompetitorResearch: (messageId) => {
    const message = liveMessage(get(), messageId);
    const consent = message?.consent;
    const field = message?.field;
    const intake = get().intake;
    if (!consent || consent.status !== "pending" || !field || !intake) return;

    patchMessage(get, set, messageId, { consent: { ...consent, status: "declined" } });
    // Straight to the ordinary question for the same field — the operator has their own list.
    set({ intake: { ...intake, awaitingFieldId: field.field_id }, navStatus: "Awaiting Input" });
    push(get, set, { role: "assistant", kind: "question", assetId: intake.asset.asset_id, field });
  },

  editField: (fieldId) => {
    const state = get();
    if (!selectCanEditAnswers(state)) return;

    // Mid-intake the live answers are in `intake`. After generation they are on the draft card —
    // rebuilt into an intake so the edit re-runs the same walk, and the draft marked superseded so
    // it can't be approved on the strength of answers that no longer hold.
    let intake = state.intake;
    let regenerating = false;
    if (!intake) {
      const draft = editableDraft(state);
      const asset = draft?.assetId ? ASSET_BY_ID[draft.assetId] : undefined;
      if (!draft || !asset) return;
      intake = { asset, answers: { ...(draft.answers ?? {}) }, awaitingFieldId: null };
      regenerating = true;
      patchMessage(get, set, draft.id, { superseded: true, refining: false });
    }

    // The sub-service is a run-level fact, so unlike every other editable answer it sits on no
    // asset's field list (see `SUB_SERVICE_FIELD`). Without this case the lookup below misses and
    // the click does nothing at all — a dead "Edit" on the one answer a whole Phase 2 run is
    // anchored on, and the answer with the least other ways to correct it.
    const isSubService = fieldId === SUB_SERVICE_FIELD.field_id;
    const field = isSubService ? SUB_SERVICE_FIELD : intake.asset.fields.find((f) => f.field_id === fieldId);
    if (!field) return;

    // The question that was open is not the question any more. Left as-is it would render as
    // "answered", which it isn't.
    const openFieldId = state.intake?.awaitingFieldId;
    set({
      messages: get().messages.map((m) => {
        if (m.kind === "question" && !m.answered && m.field?.field_id === openFieldId && openFieldId !== fieldId) {
          return { ...m, superseded: true };
        }
        // An unresolved context choice blocks the input bar until it is answered, which would leave
        // the operator unable to type the very correction they just asked for. It is re-offered by
        // the re-walk, since its field still has no answer.
        if (m.kind === "context-choice" && (m.contextChoiceStatus ?? "pending") === "pending") {
          return { ...m, superseded: true };
        }
        return m;
      }),
    });

    // Earlier cards for this same field become history: the answer they show is about to be
    // replaced, and two cards both reading "answered" would be ambiguous.
    // Seed the box with what they said before, unless it is long enough that a textarea full of it
    // would be worse than an empty one (a pasted page, an ICP document).
    // The sub-service's answer lives on the run's client profile rather than in this stage's
    // answers — it was filed there by `submitAnswer` so that every stage reads it from one place.
    const previous = isSubService ? state.clientProfile[SUB_SERVICE_FACT] : intake.answers[fieldId];
    const seedable =
      (typeof previous === "string" || typeof previous === "number") &&
      String(previous).length <= 2000 &&
      !String(previous).startsWith("[[context:");

    set({
      messages: get().messages.map((m) =>
        m.kind === "question" && m.field?.field_id === fieldId ? { ...m, superseded: true } : m,
      ),
      intake: { ...intake, awaitingFieldId: fieldId },
      activeStatus: "running",
      navStatus: "Awaiting Input",
      progress: Math.max(state.progress, 5),
      editSeed: seedable ? { fieldId, value: String(previous) } : null,
    });

    push(get, set, {
      role: "assistant",
      kind: "text",
      text: isSubService
        ? // Not "everything else is kept": the sub-service is what silently answers the target-service
          // field on four of Phase 2's seven stages, so those answers go with it (see
          // `clearFactFilledAnswers`). Stages already approved into the Context Store keep the old
          // one — the way back to those is a new run, not this edit.
          `Changing the sub-service. Anything this run answered from it will be filled in again from the new one; stages already saved keep the old one.`
        : regenerating
          ? `Changing ${field.label}. Once it's updated I'll regenerate this stage from the corrected answers.`
          : `Changing ${field.label}. Everything else you've answered is kept.`,
    });
    // For a field the pipeline offers to research, "change this" means "offer me the choice again" —
    // not "now paste it yourself", which would take away the option the operator had the first time.
    if (
      COMPETITOR_CONSENT_FIELDS_BY_PHASE[get().phase][fieldId] &&
      offerCompetitorResearch(get, set, intake.asset, intake.answers, field)
    ) {
      return;
    }

    push(get, set, {
      role: "assistant",
      kind: "question",
      // The sub-service card carries no `assetId`, matching how `askSubService` first asked it:
      // it belongs to the run, not to whichever stage happens to be open when it is corrected.
      ...(isSubService ? {} : { assetId: intake.asset.asset_id }),
      field,
      editing: true,
    });
  },

  dismissFault: () => set({ fault: null }),

  cancelEdit: () => {
    const { intake } = get();
    if (!intake?.awaitingFieldId) return;
    const fieldId = intake.awaitingFieldId;

    // `editField` superseded every card for this field on the way in, on the promise that a new
    // answer was coming. It isn't, so the newest answered one is put back — otherwise the answer
    // that still stands is left reading "replaced", and its Edit goes with it, stranding the
    // operator on an answer they can no longer reach.
    const messages = get().messages;
    const restoreIndex = messages.reduce(
      (last, m, i) =>
        m.kind === "question" && m.answered && !m.editing && m.field?.field_id === fieldId ? i : last,
      -1,
    );

    // The old answer was never cleared, so the walk simply skips this field again and lands back on
    // whatever was actually outstanding — no need to remember where the operator was.
    set({
      messages: messages.map((m, i) => {
        if (i === restoreIndex) return { ...m, superseded: false };
        return m.kind === "question" && m.editing && !m.answered && m.field?.field_id === fieldId
          ? { ...m, superseded: true }
          : m;
      }),
    });
    push(get, set, { role: "assistant", kind: "text", text: "Left that answer as it was." });
    set({ editSeed: null });
    advanceIntake(get, set, get().currentIndex, intake.asset, intake.answers, 0);
  },

chooseHeadlines: async (messageId, ids) => {
    const message = liveMessage(get(), messageId);
    const state = message?.headlines;
    if (!state?.candidates?.length || !ids.length) return;

    const chosen = state.candidates.filter((c) => ids.includes(c.id));
    if (!chosen.length) return;

    // The card renders the chosen list from here, so patch before the await — otherwise the
    // buttons stay live through a network round-trip and a second click double-answers the field.
    patchMessage(get, set, messageId, {
      headlines: { ...state, status: "chosen", chosenIds: ids, reloading: false },
    });

    const selected = chosen.map((c) => ({
      headline: c.headline,
      primary_keyword: c.primary_keyword,
      funnel: c.funnel,
      intent: c.intent,
      search_volume: c.search_volume,
      ...c.extras,
    }));
    // Single-select renders as the bare headline (that is what the master prompts have always been
    // handed for these fields); multi-select as a numbered list carrying each item's extras. The
    // backend owns that rendering so both sides agree on it — this is only the fallback for a run
    // with no `runId` yet, where nothing was persisted to render from.
    const answer =
      chosen.length === 1 && !state.multi
        ? chosen[0].headline
        : chosen.map((c, i) => `${i + 1}. ${c.headline}`).join("\n");

    push(get, set, {
      role: "user",
      kind: "text",
      text: chosen.length === 1 ? chosen[0].headline : `Selected ${chosen.length}:\n${answer}`,
    });
    set({ editSeed: null });

    await settleHeadlineChoice(get, set, messageId, answer, selected, "suggested");
  },

  writeOwnHeadline: async (messageId, headline) => {
    const trimmed = headline.trim();
    if (!trimmed) return;
    const message = liveMessage(get(), messageId);
    const state = message?.headlines;
    if (!state) return;

    patchMessage(get, set, messageId, {
      headlines: { ...state, status: "chosen", ownHeadline: trimmed, reloading: false },
    });
    push(get, set, { role: "user", kind: "text", text: trimmed });
    set({ editSeed: null });

    await settleHeadlineChoice(get, set, messageId, trimmed, [{ headline: trimmed }], "operator");
  },

  rerollHeadlines: async (messageId) => {
    await loadHeadlineSuggestions(get, set, messageId, { reroll: true });
  },

  retryHeadlines: async (messageId) => {
    await loadHeadlineSuggestions(get, set, messageId);
  },

  acceptContextChoice: (messageId) => {
    const message = liveMessage(get(), messageId);
    const { intake } = get();
    const field = message?.field;
    if (!message?.contextChoice || !field || !intake) return;

    // Same marker `findNextAskable` writes for a silent auto-fill, so `resolveFinalAnswers`
    // substitutes the real upstream text at generation time through the one existing path.
    intake.answers[field.field_id] = `[[context: ${message.contextChoice.label}]]`;
    patchMessage(get, set, messageId, { contextChoiceStatus: "accepted" });

    const fromIndex = intake.asset.fields.findIndex((f) => f.field_id === field.field_id) + 1;
    advanceIntake(get, set, get().currentIndex, intake.asset, intake.answers, fromIndex);
  },

  overrideContextChoice: (messageId) => {
    const message = liveMessage(get(), messageId);
    const field = message?.field;
    if (!field) return;

    // Leave `intake.awaitingFieldId` on this same field and ask for it as a normal question —
    // the operator's typed/pasted answer then flows through `submitAnswer` unchanged.
    patchMessage(get, set, messageId, { contextChoiceStatus: "overridden" });
    push(get, set, {
      role: "assistant",
      kind: "question",
      assetId: message?.assetId,
      field,
    });
  },

  saveCompetitorStep: async (messageId, approved) => {
    const message = liveMessage(get(), messageId);
    const found = message?.competitor;
    // What the operator approved: all of the search's result (the default), a ticked subset of it,
    // or their own list — the last one also reachable from a failed search, where `found` is empty.
    const result = approved ?? found;
    if (!message?.assetId || !result) return;
    const assetId = message.assetId;

    patchMessage(get, set, messageId, { savePhase: "saving", saveError: undefined });
    try {
      const runId = await ensureRun(get, set);
      const saved = await saveCompetitorAnalysis(runId, assetId, { ...result, asset_id: assetId });
      // The card then shows the listing that was actually saved, not the one that was offered — and
      // so does the transcript on reload, which persists this message rather than the card's state.
      patchMessage(get, set, messageId, { savePhase: "saved", competitor: result, competitorError: undefined });

      // File the approved analysis under its own asset id so the paired main asset's
      // `competitor_analysis` field auto-resolves from context instead of asking the operator.
      // Uses the backend's own rendering so this is byte-identical to what the prompt receives.
      const label =
        competitorStageFor(get().phase, stageAt(get().phase, get().currentIndex).asset.asset_id)?.label ?? assetId;
      set({
        context: {
          ...get().context,
          [assetId]: { assetId, label, text: saved.context_text },
        },
      });
      schedulePersist();

      // Two shapes of approval. A mid-intake analysis answers the field it was run for and the
      // intake carries straight on; a gating one (CRO's) hands off to the stage's intake, which has
      // not started yet. Restarting the intake in the first case would wipe every answer given so
      // far, which is why this branches on the field rather than on the stage.
      const intake = get().intake;
      if (message.competitorFillsFieldId && intake) {
        const fieldId = message.competitorFillsFieldId;
        const field = intake.asset.fields.find((f) => f.field_id === fieldId);
        intake.answers[fieldId] = saved.context_text;
        markQuestionAnswered(get, set, fieldId);
        if (field) applyAnswerAndAdvance(get, set, intake, field);
        else advanceIntake(get, set, get().currentIndex, intake.asset, intake.answers, 0);
        return;
      }

      // Two Phase 2 stages read the market before they ask their own questions: the briefing goes
      // between the approved listing and the first question, which is the only place it is any use.
      const mainAssetId = stageAt(get().phase, get().currentIndex).asset.asset_id;
      if (COMPETITOR_BRIEFING_STAGES[mainAssetId]) {
        // The raw JSON holds every competitor the search found. Once the operator has narrowed or
        // replaced that list it would brief on competitors they excluded, so the saved text goes.
        const narrowed = result !== found;
        const source = !narrowed && result.raw_output ? result.raw_output : saved.context_text;
        await runCompetitorBriefing(get, set, mainAssetId, source);
        return;
      }
      beginMainIntake(get, set, get().currentIndex);
    } catch (err) {
      const msg = recordFailure(set, err);
      patchMessage(get, set, messageId, { savePhase: "error", saveError: msg });
    }
  },

  retryPageScrape: async (messageId) => {
    const message = liveMessage(get(), messageId);
    const scrape = message?.scrape;
    const intake = get().intake;
    // Only retry into the question this read was for. If the operator has already pasted the copy
    // and moved on, the retry would overwrite an answer they gave deliberately.
    if (!scrape || !intake || intake.awaitingFieldId !== scrape.fieldId) return;

    patchMessage(get, set, messageId, { scrape: { ...scrape, status: "running", error: undefined } });
    set({ intake: { ...intake, awaitingFieldId: null }, navStatus: "Generating…", activeStatus: "running" });

    try {
      const page = await scrapePage(scrape.url);
      const filled: ScrapeState = {
        ...scrape,
        status: "done",
        finalUrl: page.final_url,
        title: page.title,
        content: page.content,
        wordCount: page.word_count,
        charCount: page.char_count,
        lowContent: page.low_content,
        source: page.source,
        warnings: page.warnings,
        error: undefined,
      };

      if (page.low_content || !page.content.trim()) {
        patchMessage(get, set, messageId, {
          scrape: { ...filled, status: "error", error: page.warnings[0] ?? "Almost no text came back from that page." },
        });
        set({ intake: { ...intake, awaitingFieldId: scrape.fieldId }, navStatus: "Awaiting Input" });
        return;
      }

      patchMessage(get, set, messageId, { scrape: filled });
      // Filled directly rather than through `submitAnswer`, which would echo the whole page back
      // as a user chat bubble. The card above already says what was read, and offers to show it.
      intake.answers[scrape.fieldId] = page.content;
      markQuestionAnswered(get, set, scrape.fieldId);
      const fromIndex = intake.asset.fields.findIndex((f) => f.field_id === scrape.fieldId) + 1;
      advanceIntake(get, set, get().currentIndex, intake.asset, intake.answers, fromIndex);
    } catch (err) {
      patchMessage(get, set, messageId, {
        scrape: { ...scrape, status: "error", error: err instanceof Error ? err.message : String(err) },
      });
      set({ intake: { ...intake, awaitingFieldId: scrape.fieldId }, navStatus: "Awaiting Input" });
    }
  },

  retryCompetitorStep: async (messageId) => {
    const message = liveMessage(get(), messageId);
    if (!message?.assetId) return;
    const assetId = message.assetId;
    const profile = get().clientProfile;
    // The inputs the first attempt used, when it recorded them. Falling back to the profile alone
    // would drop the topic on a Pillar Page re-run and quietly search for something else.
    const inputs = message.competitorInputs ?? {
      target_url: profile.website_url,
      niche: profile.industry,
      location: profile.region,
    };

    patchMessage(get, set, messageId, { competitorError: undefined, competitor: undefined, savePhase: "idle" });
    set({ activeStatus: "running", navStatus: "Generating…" });
    const stop = startCreepingProgress(get, set);
    try {
      const result = await runCompetitorAnalysis(assetId, inputs, get().phase, attribution(get));
      patchMessage(get, set, messageId, { competitor: result });
    } catch (err) {
      const msg = recordFailure(set, err);
      patchMessage(get, set, messageId, { competitorError: msg });
    } finally {
      stop();
      set({ activeStatus: "hitl", progress: 100, navStatus: "Awaiting Review" });
    }
  },

  /** Point the workspace at the other phase, without leaving the chat.
   *
   * Phase 2 is the continuation of the Phase 1 engagement, not a separate errand: same client, same
   * operator, and the run it builds on is the one that was just walked. So the chat stays put — one
   * row in the sidebar, one client, both phases — and the switch is a move *within* it.
   *
   * What the operator sees, though, is a clear screen. Each phase keeps its own transcript (see
   * `messagesInPhase` and `GenerationStream`): Phase 2's opening question read at the bottom of
   * fifteen finished Phase 1 assets would be buried, and switching back has to show Phase 1 as it
   * was left, not as it was left plus somebody else's run.
   *
   * What cannot carry on is the phase-scoped half of the state. `currentIndex` is a position in one
   * phase's stage list (index 4 is Offers in Phase 1 and Blog in Phase 2), the two phases write to
   * different `runs` rows, and both produce assets under the same ids. That half is parked in
   * `phaseSlots` on the way out and restored on the way in, so switching back resumes where it left
   * off instead of restarting.
   *
   * Context is copied, not shared, into a Phase 2 leg that is starting fresh: Phase 2 should see
   * what Phase 1 produced — that is what its source run gives it anyway — but Phase 2's own
   * `pillar_page` must not overwrite Phase 1's in a chat the operator may switch back to.
   *
   * Before anything has been generated there is no cursor to park and no transcript to divide, so
   * the phase simply changes in place. */
  setPhase: (phase) => {
    const state = get();
    if (state.phase === phase) return;

    if (!state.started) {
      set({
        phase,
        currentIndex: 0,
        rerunReturnIndex: null,
        rerunReturnIntake: null,
        progress: 0,
        intake: null,
        subStep: null,
        navStatus: "Ready",
        // A "start at this stage" request belongs to the phase it was made in — index 4 is Offers
        // in Phase 1 and Blog in Phase 2, and several ids exist in both sequences.
        pendingStartAssetId: null,
      });
      return;
    }

    // Cards written before phases were stamped can only have come from the phase being left; say so
    // now, while that is still known, rather than leaving them to match both legs forever.
    const messages = state.messages.map((m) => (m.phase ? m : { ...m, phase: state.phase }));

    const parked: PhaseSlot = {
      started: state.started,
      runId: state.runId,
      sourceRunId: state.sourceRunId,
      currentIndex: state.currentIndex,
      rerunReturnIndex: state.rerunReturnIndex,
      rerunReturnIntake: state.rerunReturnIntake,
      subStep: state.subStep,
      intake: serializeIntake(state.intake),
      context: state.context,
      progress: state.progress,
    };
    const phaseSlots: PhaseSlots = { ...state.phaseSlots, [state.phase]: parked };
    const resuming = phaseSlots[phase];

    set({
      messages,
      phaseSlots,
      phase,
      started: true,
      editSeed: null,
      runId: resuming?.runId ?? null,
      // Entering Phase 2 out of the leg that produced the Phase 1 run: that run is the parent, and
      // `beginPhase2` announces the link instead of asking which run to build on.
      sourceRunId: resuming ? resuming.sourceRunId : phase === "phase2" ? parked.runId : null,
      currentIndex: resuming?.currentIndex ?? 0,
      rerunReturnIndex: resuming?.rerunReturnIndex ?? null,
      rerunReturnIntake: resuming?.rerunReturnIntake ?? null,
      subStep: resuming?.subStep ?? null,
      intake: hydrateIntake(resuming?.intake ?? null),
      context: resuming?.context ?? (phase === "phase2" ? { ...state.context } : {}),
      progress: resuming?.progress ?? 0,
      activeStatus: null,
      navStatus: "Ready",
      pendingStartAssetId: null,
    });

    if (resuming) {
      const stage = stageAt(phase, resuming.currentIndex);
      const done = resuming.currentIndex >= totalStagesFor(phase);
      push(get, set, {
        role: "assistant",
        kind: "text",
        text: done
          ? `Back in ${PHASE_META[phase].label}, which is complete — all ${totalStagesFor(phase)} assets are saved.`
          : `Back in ${PHASE_META[phase].label}, at Stage ${String(stage.stageNumber).padStart(2, "0")}: ${stage.asset.label}.`,
      });
      // Re-derived rather than restored from the slot, for the same reason a reopened chat re-derives
      // it: the parked status may name a stream that no longer exists.
      set(deriveResumeActivity(get().messages, get().intake, resuming.currentIndex, resuming.progress, phase, get().activePhase2TrackId));
      return;
    }

    push(get, set, {
      role: "assistant",
      kind: "text",
      text: `Switching to ${PHASE_META[phase].label} — ${PHASE_META[phase].scope.toLowerCase()}, ${totalStagesFor(phase)} assets. Starting with a clear screen; ${PHASE_META[state.phase].label} is kept in this chat and comes back on the toggle.`,
    });

    if (phase === "phase2") {
      void beginPhase2(get, set);
      return;
    }
    beginStage(get, set, 0);
  },

  startNewChat: (opts) => {
    if (opts?.discardUnsaved) {
      clearPendingPersist();
      chatEpoch += 1; // an autosave already in flight must not adopt its id into this blank chat
    } else {
      flushLeavingChat(get());
    }
    set({
      sessionId: null,
      loadingSessionId: null,
      sessionTitle: "New chat",
      started: false,
      // A blank chat opens in the phase the operator is working in, rather than snapping back to
      // Phase 1 — "New chat" means a new client, not a change of pipeline.
      phase: get().phase,
      phaseSlots: {},
      phase2Tracks: {},
      activePhase2TrackId: null,
      phase2TrackSlots: {},
      runId: null,
      sourceRunId: null,
      navStatus: "Ready",
      messages: [],
      context: {},
      currentIndex: 0,
      rerunReturnIndex: null,
      rerunReturnIntake: null,
      activeStatus: null,
      progress: 0,
      intake: null,
      clientProfile: {},
      subStep: null,
      pendingStartAssetId: null,
    });
  },

  clearPhase: () => {
    const state = get();
    const phase = state.phase;
    const other: PipelinePhase = phase === "phase1" ? "phase2" : "phase1";
    // An unstamped card predates per-phase stamping and can only have come from the leg on screen —
    // the same reading `messagesInPhase` and `setPhase` take.
    const own = (m: PipelineMessage) => (m.phase ?? phase) === phase;

    // Abort first, while `messages` still holds the ids the controllers are keyed by: a clear that
    // only emptied the pane would leave the stream running to completion, still writing tokens
    // nobody will read and still being billed for. This leg's requests only.
    abortGenerations(state.messages.filter(own).map((m) => m.id));
    abortHeadlineBatches(state.messages.filter(own).map((m) => m.id));

    const kept = state.messages.filter((m) => !own(m));
    // Dropping this phase's *own* slot matters as much as keeping the other's: a stale entry from an
    // earlier visit to this phase is still in `phaseSlots` (see `setPhase`, which parks the outgoing
    // leg without removing the incoming one), and leaving it there would restore the pre-clear
    // cursor the moment the operator switched away and back.
    const phaseSlots: PhaseSlots = { ...state.phaseSlots };
    delete phaseSlots[phase];
    const otherHasWork = kept.length > 0 || phaseSlots[other] !== undefined;

    // Phase 2 is the continuation of the Phase 1 leg in the same chat, so a cleared Phase 2 restarts
    // where a freshly entered one would: linked to that run, holding a copy of its context. Clearing
    // Phase 1 starts from nothing, which is what a Phase 1 leg starts from.
    const parent = phase === "phase2" ? phaseSlots.phase1 : undefined;

    // The client is a property of the chat, not of a leg, so it survives while the other leg is
    // still working on it. The sub-service is Phase 2's own and goes with Phase 2. An emptied chat
    // keeps neither: "the client name was wrong" is one of the reasons to clear in the first place.
    const profile = { ...state.clientProfile };
    if (phase === "phase2") delete profile[SUB_SERVICE_FACT];

    set({
      messages: kept,
      phaseSlots,
      ...(phase === "phase2"
        ? { phase2Tracks: {}, activePhase2TrackId: null, phase2TrackSlots: {} }
        : {}),
      runId: null,
      sourceRunId: parent?.runId ?? null,
      context: parent ? { ...parent.context } : {},
      clientProfile: otherHasWork ? profile : {},
      currentIndex: 0,
      rerunReturnIndex: null,
      rerunReturnIntake: null,
      intake: null,
      subStep: null,
      editSeed: null,
      activeStatus: null,
      progress: 0,
      navStatus: "Ready",
      pendingStartAssetId: null,
      // The chat carries on while the other leg holds something; with nothing left it goes back to
      // the welcome screen, where the phase and the stage to start at are chosen.
      started: otherHasWork,
    });

    if (!otherHasWork) {
      // Written back over the history row rather than deleted — see `persistNow`, which writes a
      // chat that already has a row even once it is no longer `started`, so an emptied chat does
      // not come back on the next reload.
      schedulePersist();
      return;
    }

    push(get, set, {
      role: "assistant",
      kind: "text",
      text: `${PHASE_META[phase].label} cleared — starting again at Stage 01. ${PHASE_META[other].label} is untouched: its cards, its approved assets and its run are all still here, and the toggle brings them back.`,
    });

    if (phase === "phase2") {
      void beginPhase2(get, set);
      return;
    }
    beginStage(get, set, 0);
  },

  loadSession: async (sessionId) => {
    if (get().isLoadingSession) return;
    // Opening the chat that is already open would only discard unsaved in-flight edits for it.
    if (sessionId === get().sessionId) return;

    flushLeavingChat(get());
    set({ isLoadingSession: true, loadingSessionId: sessionId });
    try {
      const detail = await getChatSession(sessionId);
      const snap = detail.state as Partial<PipelineSnapshot>;
      const messages = repairInterruptedMessages(snap.messages ?? []);
      // A chat is "started" once it has anything in it. An empty snapshot (a session row created
      // before its first write landed) goes back to the welcome screen rather than an empty pane
      // with no way to begin.
      const started = snap.started ?? messages.length > 0;
      const intake = hydrateIntake(snap.intake);
      const currentIndex = snap.currentIndex ?? 0;
      const phase: PipelinePhase = snap.phase ?? "phase1";
      // Not `snap.navStatus`/`snap.activeStatus`: the snapshot may say "Generating…" for a stream
      // that died with the tab, and a stale status leaves the diagram either frozen or spinning
      // on something that is no longer running.
      const activity = deriveResumeActivity(
        messages,
        intake,
        currentIndex,
        snap.progress ?? 0,
        phase,
        snap.activePhase2TrackId ?? null,
      );

      set({
        sessionId: detail.id,
        sessionTitle: detail.title,
        started,
        phase,
        runId: snap.runId ?? null,
        sourceRunId: snap.sourceRunId ?? null,
        messages,
        context: snap.context ?? {},
        currentIndex,
        rerunReturnIndex: snap.rerunReturnIndex ?? null,
        rerunReturnIntake: snap.rerunReturnIntake ?? null,
        intake,
        navStatus: activity.navStatus,
        activeStatus: activity.activeStatus,
        progress: activity.progress,
        clientProfile: snap.clientProfile ?? {},
        subStep: snap.subStep ?? null,
        phaseSlots: snap.phaseSlots ?? {},
        phase2Tracks: snap.phase2Tracks ?? {},
        activePhase2TrackId: snap.activePhase2TrackId ?? null,
        phase2TrackSlots: snap.phase2TrackSlots ?? {},
        // Never restored: it is a request in flight, not a property of the chat. A gate that was
        // actually opened is a card in the transcript and comes back with the messages.
        pendingStartAssetId: null,
      });
      useChatSessionsStore.setState({ error: null });
      // After the state is set, not before: it reads the messages it is about to patch.
      void refreshPendingHeadlineGates(get, set);
    } catch (err) {
      console.error("Failed to open chat session", err);
      useChatSessionsStore.setState({
        error: err instanceof Error ? err.message : String(err),
      });
    } finally {
      set({ isLoadingSession: false, loadingSessionId: null });
    }
  },

  resumeStage: () => {
    const state = get();
    if (!selectNeedsResume(state)) return;

    const index = Math.min(state.currentIndex, totalStagesFor(state.phase) - 1);
    const stage = stagesFor(state.phase)[index];
    push(get, set, {
      role: "assistant",
      kind: "text",
      text: `Picking up where this chat left off — Stage ${String(stage.stageNumber).padStart(2, "0")}: ${stage.asset.label}.`,
    });

    enterStage(get, set, index);
  },

  startAtStage: (assetId) => {
    const state = get();
    const stages = stagesFor(state.phase);
    if (!stages.some((s) => s.asset.asset_id === assetId)) return;

    // Phase 2 cannot open any stage until its two run-level questions are answered — the run row
    // itself is created from the first of them. So the request is parked and the preamble runs; see
    // `beginRequestedStage`.
    if (!state.started) {
      set({ started: true, pendingStartAssetId: state.phase === "phase2" ? assetId : null });
      if (state.phase === "phase2") {
        void beginPhase2(get, set);
        return;
      }
    }
    void openStageGate(get, set, assetId);
  },

  retryStageGate: async (messageId) => {
    await loadStageReadiness(get, set, messageId);
  },

  provideDependency: (messageId, contextKey) => {
    patchGate(get, set, messageId, { providing: contextKey, seedError: undefined });
  },

  cancelProvideDependency: (messageId) => {
    patchGate(get, set, messageId, { providing: null, seedError: undefined });
  },

  submitProvidedDependency: async (messageId, contextKey, content) => {
    const text = content.trim();
    if (!text) return;
    const gate = liveMessage(get(), messageId)?.gate;
    if (!gate || gate.seeding) return;

    patchGate(get, set, messageId, { seeding: contextKey, seedError: undefined });
    try {
      const runId = await ensureRun(get, set);
      const saved = await seedRunContext(runId, contextKey, text, "Supplied by the operator");

      // Into the session store as well as the database. The stage's intake resolves its fields
      // against this, not against the readiness report — without it the document would be filed
      // and the very next question would ask for it anyway.
      const producer = saved.producer ?? contextKey;
      const label = ASSET_BY_ID[producer]?.label ?? contextKey;
      set({
        context: {
          ...get().context,
          [contextKey]: { assetId: producer, label, text },
        },
      });
      patchGate(get, set, messageId, { seeding: null, providing: null });
      await loadStageReadiness(get, set, messageId);
    } catch (err) {
      console.error("Could not file the supplied document", err);
      patchGate(get, set, messageId, {
        seeding: null,
        seedError: err instanceof Error ? err.message : "Could not save that document.",
      });
    }
  },

  enterGatedStage: async (messageId) => {
    const message = liveMessage(get(), messageId);
    const gate = message?.gate;
    if (!gate || gate.status === "entered") return;

    const stages = stagesFor(get().phase);
    const stage = stages[gate.index];
    if (!stage || stage.asset.asset_id !== gate.assetId) return;

    patchGate(get, set, messageId, { status: "entered", providing: null });

    // A competitor scan this run has already paid for. `enterStage` decides whether to run the
    // prepass from the *session* context, which a reopened chat does not have — so on a resumed run
    // it would search and be charged again for a listing already in the store. Readiness has just
    // said whether there is one; this is the only place that knows both.
    const prepassId = gate.readiness?.prepass;
    const prepassReady = gate.readiness?.prepass_fields.some((f) => f.ready) ?? false;
    if (prepassId && prepassReady && !get().context[prepassId]) {
      const runId = get().runId;
      const stored = runId ? await fetchRunContext(runId, prepassId) : null;
      if (stored?.content) {
        set({
          context: {
            ...get().context,
            [prepassId]: {
              assetId: prepassId,
              label: competitorStageFor(get().phase, gate.assetId)?.label ?? prepassId,
              text: stored.content,
            },
          },
        });
      }
    }

    enterStage(get, set, gate.index);
  },

  declareNewPage: (messageId) => {
    const state = get();
    const message = liveMessage(state, messageId);
    const intake = state.intake;
    if (!message?.pageSource || message.answered || message.superseded || !intake) return;

    const option = NEW_PAGE_OPTIONS[intake.asset.asset_id];
    if (!option || intake.awaitingFieldId !== option.urlFieldId) return;

    // Both fields at once. "What is the URL of the page" and "paste that page's copy" are the same
    // question when there is no page, and answering only the first leaves the walk asking the
    // second — which is where an operator, having just said there is no page, types "N/A" and the
    // prompt reads it as a page whose copy is the letters N/A.
    intake.answers[option.urlFieldId] = option.urlAnswer;
    intake.answers[option.contentFieldId] = option.contentAnswer;
    markQuestionAnswered(get, set, option.urlFieldId);
    push(get, set, { role: "user", kind: "text", text: "No existing page — build it from scratch." });

    const subject = subjectOf(intake, option);
    push(get, set, {
      role: "assistant",
      kind: "text",
      text: `Building ${subject ? `the ${subject} page` : "the page"} from scratch. The rewrite switches to build-from-scratch mode — no before/after comparison, everything else the same — and still produces the page copy, the locked sections and the terminology map that ${ASSET_BY_ID[option.insteadAssetId]?.label ?? "the page build"} reads.`,
      editableFields: [{ fieldId: option.urlFieldId, label: message.field?.label ?? option.urlFieldId }],
    });

    const fields = intake.asset.fields;
    const after =
      Math.max(
        fields.findIndex((f) => f.field_id === option.urlFieldId),
        fields.findIndex((f) => f.field_id === option.contentFieldId),
      ) + 1;
    set({ intake: { ...intake, awaitingFieldId: null } });
    advanceIntake(get, set, get().currentIndex, intake.asset, intake.answers, after);
  },

  skipStageForNewPage: (messageId) => {
    const state = get();
    const message = liveMessage(state, messageId);
    const intake = state.intake;
    if (!message?.pageSource || message.answered || message.superseded || !intake) return;

    const option = NEW_PAGE_OPTIONS[intake.asset.asset_id];
    if (!option) return;

    const target = ASSET_BY_ID[option.insteadAssetId]?.label ?? option.insteadAssetId;
    const abandoned = intake.asset.label;

    // The question is left in the transcript as history, not as something still being asked: the
    // stage it belonged to is being abandoned, and `startAtStage` clears the intake behind it.
    patchMessage(get, set, messageId, { superseded: true });
    push(get, set, { role: "user", kind: "text", text: `Skip ${abandoned} — go straight to ${target}.` });
    push(get, set, {
      role: "assistant",
      kind: "text",
      text: `Leaving ${abandoned}. ${target} is what builds a page, so it can be run on its own — but ${abandoned} is what would have written the copy it builds from, so it will ask for the page copy, the locked sections and the terminology words instead. The next card lists exactly what it needs and lets you paste each one, run ${abandoned} after all, or start anyway and answer as it asks. Nothing here is lost: ${abandoned} is still in the pipeline to run later.`,
    });

    get().startAtStage(option.insteadAssetId);
  },

  stopStage: () => {
    const state = get();
    if (!selectCanStop(state)) return;
    const target = selectStoppableStage(state);
    if (!target) return;

    // The stage's competitor sub-step is a separate asset id on the same stage, so abandoning the
    // stage has to abandon its unsaved prepass too — otherwise a half-reviewed competitor card is
    // left offering Save for a stage nobody is working on.
    const competitor = competitorStageFor(state.phase, target.assetId);
    const own = (m: PipelineMessage) =>
      (m.phase ?? state.phase) === state.phase &&
      (m.assetId === target.assetId || (!!competitor && m.assetId === competitor.assetId));

    const wasGenerating = abortGenerations(
      state.messages.filter((m) => own(m) && m.streaming).map((m) => m.id),
    );
    // A suggestion batch still in flight for this stage is the same waste as the stream: nothing
    // will read its result, because the gate that asked for it is about to become history.
    abortHeadlineBatches(
      state.messages.filter((m) => own(m) && m.kind === "headline-choice").map((m) => m.id),
    );

    set({
      messages: state.messages.map((m) => {
        if (!own(m) || m.superseded) return m;
        // An approved generation is a real Context Store version, not a stale draft — see the same
        // carve-out in `rerunStage`. Superseding it would hide its own Download and Share buttons.
        if (m.kind === "generation") {
          return m.savePhase === "saved" ? m : { ...m, superseded: true, streaming: false, refining: false };
        }
        if (m.kind === "competitor") {
          return m.savePhase === "saved" ? m : { ...m, superseded: true };
        }
        if (
          m.kind === "question" ||
          m.kind === "headline-choice" ||
          m.kind === "context-choice" ||
          m.kind === "competitor-consent" ||
          m.kind === "scrape" ||
          m.kind === "briefing" ||
          m.kind === "stage-gate"
        ) {
          return { ...m, superseded: true };
        }
        return m;
      }),
      intake: null,
      subStep: null,
      editSeed: null,
      // A re-run's parked return point belongs to the stage being abandoned. Left set, the next
      // stage to finish would send the operator back to a stage they walked out of.
      rerunReturnIndex: null,
      rerunReturnIntake: null,
      activeStatus: null,
      progress: 0,
      navStatus: "Ready",
    });

    push(get, set, { role: "user", kind: "text", text: `Stop ${target.label}.` });
    push(get, set, {
      role: "assistant",
      kind: "stage-stopped",
      assetId: target.assetId,
      stopped: { assetId: target.assetId, label: target.label, wasGenerating },
    });
  },

  runProducerFirst: (messageId, assetId) => {
    const gate = liveMessage(get(), messageId)?.gate;
    if (!gate || gate.status === "entered") return;

    const label = ASSET_BY_ID[assetId]?.label ?? assetId;
    if (!stagesFor(get().phase).some((s) => s.asset.asset_id === assetId)) {
      // A Phase 1 asset asked for from inside Phase 2, which is the normal shape of this: Phase 2
      // inherits those documents rather than rebuilding them. Nothing to run here, so say which of
      // the two remaining options applies rather than silently doing nothing.
      push(get, set, {
        role: "assistant",
        kind: "text",
        text: `${label} is not one of ${PHASE_META[get().phase].label}'s stages — it comes from the Phase 1 run this one builds on. Either switch phase to build it there, or paste it above.`,
      });
      return;
    }

    patchGate(get, set, messageId, { status: "entered", providing: null });
    push(get, set, {
      role: "assistant",
      kind: "text",
      text: `Running ${label} first. ${ASSET_BY_ID[gate.assetId]?.label ?? gate.assetId} is still there to start once it is approved.`,
    });
    void openStageGate(get, set, assetId);
  },

}));
