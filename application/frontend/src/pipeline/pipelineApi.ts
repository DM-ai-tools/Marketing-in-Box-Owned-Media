/** Thin client for the two pipeline persistence routes (`app/routers/pipeline.py`). Proxied
 * by Vite's `/api` -> FastAPI rewrite, same pattern as `lib/icpApi.ts`.
 *
 * Every generation and competitor call carries the `phase`. It is not cosmetic: it selects the prompt
 * file and the INPUTS block the backend builds, so a Phase 2 stage sent without it comes back as the
 * Phase 1 asset — a page for the headline service where the operator asked for one for a sub-service,
 * with nothing in the output to say so. */
import type { PipelinePhase } from "./pipelineData";
import type { PrefillFieldIn } from "../lib/contextSearch";

export interface CreateRunResponse {
  run_id: string;
  client_id: string;
}

/** Start a run.
 *
 * `sourceRunId` makes it a linked sub-service run: it is created under the source run's *client*
 * with `source_run_id` set, which is what lets a Phase 2 stage read the Phase 1 run's approved
 * assets (`fetchRunContext` falls through to it) while keeping its own outputs on its own run — so a
 * Phase 2 pillar page never replaces the Phase 1 one, and two sub-services off the same parent
 * cannot see each other's work. */
export async function createRun(
  companyName = "Untitled Client",
  sourceRunId?: string | null,
): Promise<CreateRunResponse> {
  const res = await fetch("/api/pipeline/runs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ company_name: companyName, source_run_id: sourceRunId ?? null }),
  });
  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    throw new Error(`Failed to start pipeline run (${res.status}): ${detail || res.statusText}`);
  }
  return res.json();
}

export interface SaveStageResponse {
  run_id: string;
  asset_id: string;
  version: number;
  status: string;
  saved_at: string;
}

/** `answers` is the intake behind this output, sent so the server can publish what it composes from
 * the answers rather than from the document — today, the CRO stage's client-level settings (see
 * `app/services/cro_settings.py`). Optional because a stage approved from a pasted draft genuinely
 * has none, which the server treats as "nothing to publish" rather than as an empty answer set. */
export async function saveStageOutput(
  runId: string,
  assetId: string,
  content: string,
  answers?: Record<string, string>,
): Promise<SaveStageResponse> {
  const res = await fetch(`/api/pipeline/runs/${runId}/stages/${assetId}/save`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(answers ? { content, answers } : { content }),
  });
  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    throw new Error(`Failed to save stage (${res.status}): ${detail || res.statusText}`);
  }
  return res.json();
}

/** Emitted once a competitor-analysis prepass finishes (or is skipped) for a stage that has
 * one — see `_run_competitor_prepass` in `app/routers/pipeline.py`. */
export interface PrepassEvent {
  asset_id: string;
  target_field_id: string;
  skipped: boolean;
  content?: string;
  error?: string;
  inputs?: Record<string, string>;
}

/** A backend-classified failure — see `app/services/api_errors.py`. `code` is stable enough to
 * branch on; `title`/`message` are written for the operator; `detail` is the raw SDK string, kept
 * for the technical-details expander because a request_id is what support asks for. */
export interface ApiFault {
  code: string;
  title: string;
  message: string;
  retryable: boolean;
  /** True when the failure is about the account or the connection rather than this stage — every
   * other stage will hit it too, which is what justifies interrupting with a dialog. */
  blocks_run: boolean;
  detail: string;
  action_url?: string | null;
  action_label?: string | null;
}

/** Thrown wherever a call fails with a classified fault, so callers can render the dialog instead of
 * having to re-parse a message string. `message` stays human-readable for the inline card. */
export class ApiFaultError extends Error {
  readonly fault: ApiFault;

  constructor(fault: ApiFault) {
    super(fault.message || fault.title);
    this.name = "ApiFaultError";
    this.fault = fault;
  }
}

/** Pull a fault out of whatever an endpoint returned, or null if it isn't one. */
export function faultFromUnknown(value: unknown): ApiFault | null {
  if (!value || typeof value !== "object") return null;
  const candidate = value as Partial<ApiFault>;
  if (typeof candidate.code !== "string" || typeof candidate.title !== "string") return null;
  return {
    code: candidate.code,
    title: candidate.title,
    message: candidate.message ?? "",
    retryable: Boolean(candidate.retryable),
    blocks_run: Boolean(candidate.blocks_run),
    detail: candidate.detail ?? "",
    action_url: candidate.action_url ?? null,
    action_label: candidate.action_label ?? null,
  };
}

/** The response body ended before the backend's terminating `done` event.
 *
 * Which means the connection died mid-generation — an API server restarted by its own file watcher,
 * a dropped network, a proxy that gave up on a stage that takes minutes. Its own error type because
 * the two halves of it need opposite handling: with no text received there is nothing to show and
 * the stage must be retried, while a partial draft is worth keeping and marking incomplete. Left
 * undetected, the empty half rendered as a finished draft card with nothing in it — Save It and
 * Refine offered over an asset that was never generated. */
export class StreamTruncatedError extends Error {
  /** True when at least one text delta arrived before the connection died. */
  readonly receivedText: boolean;

  constructor(receivedText: boolean) {
    super(
      receivedText
        ? "The connection to the API dropped before this draft finished, so it is incomplete."
        : "The connection to the API dropped before the generation produced anything. Nothing was received — retry to generate it again.",
    );
    this.name = "StreamTruncatedError";
    this.receivedText = receivedText;
  }
}

/** A stream route answered with a non-2xx status before any SSE body began.
 *
 * Carries the status, which the retry rules below need and a message string cannot supply: 503 from
 * a backend that is still booting is worth retrying by itself, while 409 ("already generating") and
 * 404 ("no such stage in this phase") never are. Parsing that back out of a sentence would be
 * guessing at something the response already stated. */
export class StreamHttpError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "StreamHttpError";
    this.status = status;
  }
}

/** How the backend's terminating `done` event ended.
 *
 * `truncated` is the one that changes what the operator is shown: the model hit its output ceiling
 * mid-document, so what arrived is a real partial deliverable that must not be approved, checked or
 * assembled as a finished one. Decided on the server (`_done_event` in `app/routers/pipeline.py`) so
 * "what counts as cut off" lives in one place. */
export interface StreamCompletion {
  stopReason: string | null;
  truncated: boolean;
}

/** How many times a stream that produced *nothing* is restarted before the failure is reported.
 *
 * Only the empty case is retried, and that is the whole safety argument: once a single delta has
 * arrived the call is generating and has been billed, so restarting it would pay twice and throw
 * away real text. Three attempts total covers the failure this exists for — an API process
 * restarted by its own file watcher, a proxy that dropped the connection on handshake — without
 * turning a genuine outage into a minute of silent waiting. */
const MAX_TRANSIENT_RETRIES = 2;

/** Backoff before attempt `n` (0-based), in ms. Jittered, because every card in a chat that
 * reconnects on the same schedule reconnects in the same instant. */
function retryBackoffMs(attempt: number): number {
  return (attempt === 0 ? 600 : 2000) + Math.floor(Math.random() * 400);
}

/** Whether restarting could plausibly work, given nothing was received.
 *
 * Deliberately a short allow-list. Anything not named here is reported to the operator rather than
 * retried: a 400 for a prompt over the context window, a rejected key, an exhausted credit balance
 * and a mistyped asset id all fail identically on the second attempt, and retrying them only
 * delays the one message that would let somebody fix it. */
function isTransient(err: unknown): boolean {
  // The body ended before `done` and before any text — see `StreamTruncatedError`.
  if (err instanceof StreamTruncatedError) return !err.receivedText;
  // A fault the backend already classified. `retryable` is its own judgement (`api_errors.py`) and
  // is not second-guessed here.
  if (err instanceof ApiFaultError) return err.fault.retryable;
  if (err instanceof StreamHttpError) return err.status === 408 || err.status === 429 || err.status >= 500;
  // `fetch` itself failing — no route to the host, DNS, a connection refused mid-handshake.
  if (err instanceof TypeError) return true;
  return false;
}

function delay(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) return reject(new DOMException("Aborted", "AbortError"));
    const timer = setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    function onAbort() {
      clearTimeout(timer);
      reject(new DOMException("Aborted", "AbortError"));
    }
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

export interface StreamStageOptions {
  /** Run-level client facts, so a stage that only collects a topic (blog / webinar / podcast)
   * can still have its competitor prepass benchmark against the right site. */
  clientProfile?: Record<string, string>;
  /** Which pipeline is being run. Selects the prompt file behind this stage. */
  phase?: PipelinePhase;
  /** Who to bill this call to in the usage ledger (`app/services/usage.py`). Both optional: a
   * brand-new chat's first call can precede its session row, and the run row only exists once a
   * stage is approved. Unattributed spend is still recorded — see the monitor's own bucket for it. */
  chatSessionId?: string | null;
  runId?: string | null;
  onPrepassStart?: (competitorAssetId: string) => void;
  onPrepass?: (event: PrepassEvent) => void;
  /** How the stream ended. Called once, on a clean finish only — a failure throws instead, and a
   * stream that never reached `done` has nothing to report. The caller uses `truncated` to decide
   * whether this is a draft to review or one to offer Continue on. */
  onDone?: (completion: StreamCompletion) => void;
  /** A stream that produced nothing is being restarted. `attempt` counts from 2 (the first retry),
   * so it can be shown as-is. Purely for the UI — nothing branches on it. */
  onRetry?: (attempt: number, of: number) => void;
  /** CRO's assembly of the client's real page: the template it found, the assembly starting, its
   * report, or its failure. Only a replica stage with a page to rebuild emits these — never the
   * pillar page, never a copy-only CRO. The assembled HTML is not on the wire; it is read from
   * `GET /runs/{run_id}/replica/output` (`fetchReplicaOutput`). */
  onReplica?: (event: ReplicaStreamEvent) => void;
  /** Image generation that failed for this stage (a brief rejected, OpenAI unreachable). The stage
   * still runs without that image; these say so on the card. */
  onImageNotes?: (notes: string[]) => void;
  /** An upstream document sent as its relevant sections rather than whole. */
  onContextRetrieval?: (report: ContextRetrievalReport) => void;
  /** The primary and secondary keywords this stage was told to write with. */
  onKeywordTargets?: (targets: KeywordTargets) => void;
  /** A page Refine's report: the page is changed by exact edits, never retyped, and this says how
   * many landed and why any did not. Only a built pillar page's Refine sends it. */
  onRevisionEdits?: (report: RevisionEditReport) => void;
  /** The Pillar Page being built by Manus: started, progress every poll, done, or a fallback to
   * Claude with the reason. Only the pillar page, and only with a Manus key on the server. */
  onManus?: (event: ManusBuildEvent) => void;
  /** Pillar Page variants: which take on the page this draft is (`lib/pageVariants.ts`). */
  variant?: PageVariantId;
  /** With a variant: build it with Manus rather than Claude ("Build with Manus"). */
  useManus?: boolean;
  /** What a Pillar Page variant measured before writing: the hosted page or the beat sheet. */
  onPageVariant?: (event: PageVariantMeasure) => void;
  signal?: AbortSignal;
}

/** The four takes on a Pillar Page (`app/services/page_variants.py`). */
export type PageVariantId = "reference" | "competitor" | "skills" | "framework";

/** One page, measured (`page_compare.PageOutline`). Counted off the page, never judged. */
export interface PageOutline {
  label: string;
  url: string | null;
  words: number;
  h1: string;
  sections: string[];
  ctas: number;
  internal_links: number;
  images: number;
  faq: boolean;
  schema_types: string[];
  proof_mentions: number;
  keywords_covered: string[];
  keywords_checked: number;
  error: string | null;
}

export interface BeatSheet {
  competitors: PageOutline[];
  topics: { topic: string; covered_by: string[] }[];
  target_words: number | null;
  max_words: number | null;
  median_words: number | null;
  faq_share: string;
  schema_seen: string[];
}

/** The `page_variant` stream event. */
export interface PageVariantMeasure {
  variant: PageVariantId;
  label: string;
  reference: PageOutline | null;
  beat_sheet: BeatSheet | null;
  notes: string[];
}

/** One draft on the comparison card (`POST /pipeline/pillar/variants/compare`). */
export interface VariantPageRow extends PageOutline {
  variant: string;
  has_page: boolean;
  /** [covered, of] the beat sheet's topics, or null with no beat sheet. */
  topics_covered: [number, number] | null;
}

/** `current` is the client's live page: the outline the reference variant measured when there is
 * one (no read), else read by the server from `answers` (free, then 1 credit if walled off). Null
 * when the page does not exist yet; a row with `error` when it could not be read. */
export async function comparePageVariants(
  drafts: { variant: string; label: string; text: string }[],
  opts: { topics?: string[]; keywords?: string[]; current?: PageOutline | null; answers?: Record<string, string> } = {},
): Promise<{ pages: VariantPageRow[]; current: VariantPageRow | null }> {
  const res = await fetch("/api/pipeline/pillar/variants/compare", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      drafts,
      topics: opts.topics ?? [],
      keywords: opts.keywords ?? [],
      current: opts.current ?? null,
      answers: opts.answers ?? {},
    }),
  });
  const body = await unwrap<{ pages: VariantPageRow[]; current?: VariantPageRow | null }>(res, "Compare the page variants");
  return { pages: body.pages, current: body.current ?? null };
}

/** The `manus` stream event (`app/services/manus_page.py`). `status` is `started` | `resumed` |
 * `running` | `waiting` | `stopped` | `done` | `fallback`. */
export interface ManusBuildEvent {
  status: string;
  task_url?: string;
  /** What the agent last said it was doing. */
  brief?: string;
  elapsed?: number;
  credits?: number | null;
  /** On `fallback`: why Manus did not deliver, and that Claude is building the page instead. */
  message?: string;
}

/** The `revision_edits` event. `rejected` rows are [start of the text to change, why it was not]. */
export interface RevisionEditReport {
  applied: number;
  rejected: [string, string][];
  /** [finding, why] — requests the model left alone on purpose (a layout change under the lock).
   * Absent from a server that predates it. */
  skipped?: [string, string][];
  unreadable: boolean;
  /** Set on every "Fix with Refine": copy and links could change, the page's layout could not. */
  layout_locked?: boolean;
}

/** What `replica_template` says about the client page the assembly will rebuild. */
export interface ReplicaTemplateInfo {
  available: boolean;
  reason?: string | null;
  source_url?: string | null;
  /** Which reader answered: `direct` (free) or `context.dev` (the rendered fallback, 1 credit). */
  source?: string;
  open_slots?: number;
  locked_slots?: number;
  notes?: string[];
}

/** The assembly report — the `replica` event, and the same fields `GET .../replica/output` returns
 * alongside the HTML. Rows stay as the server's tuples: they are shown, not branched on. */
export interface ReplicaReport {
  source_url: string;
  filled: number;
  kept: number;
  intact: boolean;
  rejected: string[][];
  warnings: string[][];
  checks: [string, boolean, string][];
  structural: string[][];
}

export type ReplicaStreamEvent =
  | ({ type: "replica_template" } & ReplicaTemplateInfo)
  | { type: "replica_start" }
  | ({ type: "replica"; asset_id?: string } & ReplicaReport)
  | { type: "replica_error"; message: string; code?: string };

type SseEvent =
  | { type: "delta"; text: string }
  | { type: "done"; stop_reason?: string | null; truncated?: boolean }
  | ({ type: "error"; message: string } & Partial<ApiFault>)
  | { type: "prepass_start"; asset_id: string }
  | ({ type: "prepass" } & PrepassEvent)
  | { type: "social_prepass"; skipped?: boolean; content?: string; error?: string }
  | { type: "keyword_prepass"; target_field_id: string; skipped?: boolean; content?: string; error?: string }
  | ReplicaStreamEvent
  | { type: "image_notes"; notes: string[] }
  | ({ type: "context_retrieval" } & ContextRetrievalReport)
  | ({ type: "keyword_targets" } & KeywordTargets)
  | ({ type: "manus" } & ManusBuildEvent)
  | ({ type: "revision_edits" } & RevisionEditReport)
  | ({ type: "page_variant" } & PageVariantMeasure);

/** The field `social_prepass` fills, so a continuation can hand back what the first attempt paid
 * for. Named here rather than inferred, because the event — unlike `prepass` — does not say which
 * field it answered: it only ever answers this one (see `_run_social_data_prepass`). */
export const SOCIAL_POST_DATA_FIELD_ID = "raw_post_data_source";

/** The `asset_id` a `keyword_prepass` is filed under (`app/services/content_keywords.py`). */
export const KEYWORD_DATA_PREPASS_ID = "content_keyword_data";

/** Mutable progress handed into the reader, so the retry wrapper can tell "nothing arrived" from
 * "it was generating and then broke". Those two need opposite handling and a thrown error cannot
 * always distinguish them — a `fetch` failure mid-body is a bare `TypeError` either way. */
interface StreamProgress {
  receivedText: boolean;
}

/** Shared SSE-body reader for the generation routes below — same event shape/parsing as
 * `lib/icpApi.ts`'s `streamIcp`. */
async function consumeSseStream(
  res: Response,
  onChunk: (chunk: string) => void,
  opts: StreamStageOptions = {},
  progress: StreamProgress = { receivedText: false },
): Promise<void> {
  if (!res.ok || !res.body) {
    const body = await res.text().catch(() => "");
    // FastAPI puts a refusal written for the operator in `detail` — the 409 from `_claim_stream` is
    // three sentences of it. Wrapping that in `Generation failed (409): {"detail":…}` buries the
    // one part worth reading, the same argument `scrapePage` makes for its own errors.
    let detail = body;
    try {
      const parsed = JSON.parse(body) as { detail?: unknown };
      if (typeof parsed.detail === "string" && parsed.detail.trim()) detail = parsed.detail;
    } catch {
      // Not JSON (a proxy's error page) — the raw body is all there is.
    }
    throw new StreamHttpError(res.status, detail || `Generation failed (${res.status} ${res.statusText}).`);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    let sepIndex: number;
    while ((sepIndex = buffer.indexOf("\n\n")) !== -1) {
      const rawEvent = buffer.slice(0, sepIndex);
      buffer = buffer.slice(sepIndex + 2);
      const line = rawEvent.split("\n").find((l) => l.startsWith("data:"));
      if (!line) continue;
      const jsonStr = line.slice(5).trim();
      if (!jsonStr) continue;

      let event: SseEvent;
      try {
        event = JSON.parse(jsonStr);
      } catch {
        continue;
      }

      if (event.type === "delta") {
        progress.receivedText = true;
        onChunk(event.text);
      }
      else if (event.type === "prepass_start") opts.onPrepassStart?.(event.asset_id);
      else if (event.type === "prepass") opts.onPrepass?.(event);
      else if (event.type === "social_prepass") {
        // Routed through the same handler as the competitor prepass, under the field it fills, so
        // the store has one place that collects "inputs this run already paid for".
        if (!event.skipped && event.content) {
          opts.onPrepass?.({
            asset_id: "social_post_data",
            target_field_id: SOCIAL_POST_DATA_FIELD_ID,
            skipped: false,
            content: event.content,
          });
        }
      }
      else if (event.type === "keyword_prepass") {
        // Content Marketing Strategy's DataForSEO keyword fetch: kept for replay like the social one.
        if (!event.skipped && event.content) {
          opts.onPrepass?.({
            asset_id: KEYWORD_DATA_PREPASS_ID,
            target_field_id: event.target_field_id,
            skipped: false,
            content: event.content,
          });
        }
      }
      else if (
        event.type === "replica_template" ||
        event.type === "replica_start" ||
        event.type === "replica" ||
        event.type === "replica_error"
      ) {
        // A `replica_error` is the extra artifact failing, never the stage: the document has
        // already streamed in full. So it is reported, not thrown.
        opts.onReplica?.(event);
      }
      else if (event.type === "keyword_targets") {
        const { type: _t, ...targets } = event;
        if (targets.groups?.length) opts.onKeywordTargets?.(targets);
      }
      else if (event.type === "context_retrieval") {
        if (event.fields?.length) opts.onContextRetrieval?.({ fields: event.fields, notes: event.notes ?? [] });
      }
      else if (event.type === "image_notes") {
        if (event.notes?.length) opts.onImageNotes?.(event.notes);
      }
      else if (event.type === "manus") {
        const { type: _t, ...build } = event;
        opts.onManus?.(build);
      }
      else if (event.type === "revision_edits") {
        const { type: _t, ...report } = event;
        opts.onRevisionEdits?.(report);
      }
      else if (event.type === "page_variant") {
        const { type: _t, ...measure } = event;
        opts.onPageVariant?.(measure);
      }
      else if (event.type === "error") {
        const fault = faultFromUnknown(event);
        throw fault ? new ApiFaultError(fault) : new Error(event.message);
      }
      else if (event.type === "done") {
        opts.onDone?.({
          stopReason: event.stop_reason ?? null,
          // Defaulted rather than required: a `done` from a backend that predates this field is a
          // finished stream, which is what it always meant.
          truncated: Boolean(event.truncated),
        });
        return;
      }
    }
  }

  // The body ended without `done` and without an `error` event. Both of those are things the
  // backend always sends before finishing (see `_generation_sse_stream`), so reaching here means
  // the stream was cut rather than completed — never a successful generation to be reviewed.
  throw new StreamTruncatedError(progress.receivedText);
}

/** Open a stream, read it, and restart it a bounded number of times if it produced nothing.
 *
 * The one rule that makes this safe to have at all: **a retry only ever happens when not a single
 * delta arrived.** Past that point the call is generating, it has been billed, and a restart would
 * pay for a second document and splice it onto the first. An operator-requested stop is not a
 * failure either, so an aborted signal is re-thrown untouched. */
async function streamWithRetry(
  open: () => Promise<Response>,
  onChunk: (chunk: string) => void,
  opts: StreamStageOptions,
): Promise<void> {
  for (let attempt = 0; ; attempt++) {
    const progress: StreamProgress = { receivedText: false };
    try {
      await consumeSseStream(await open(), onChunk, opts, progress);
      return;
    } catch (err) {
      if (progress.receivedText) throw err;
      if (opts.signal?.aborted) throw err;
      if (attempt >= MAX_TRANSIENT_RETRIES || !isTransient(err)) throw err;
      opts.onRetry?.(attempt + 2, MAX_TRANSIENT_RETRIES + 1);
      await delay(retryBackoffMs(attempt), opts.signal);
    }
  }
}

/** Real generation for one stage: the actual master prompt for `assetId` (see
 * `app/services/generation.py`), filled in from `answers`, streamed as Markdown text deltas. */
export async function streamGenerateStage(
  assetId: string,
  answers: Record<string, string>,
  onChunk: (chunk: string) => void,
  opts: StreamStageOptions = {},
): Promise<void> {
  await streamWithRetry(
    () =>
      fetch(`/api/pipeline/generate/${assetId}/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          answers,
          client_profile: opts.clientProfile ?? {},
          phase: opts.phase ?? "phase1",
          chat_session_id: opts.chatSessionId ?? null,
          run_id: opts.runId ?? null,
          variant: opts.variant ?? null,
          use_manus: Boolean(opts.useManus),
        }),
        signal: opts.signal,
      }),
    onChunk,
    opts,
  );
}

/** Finish a draft the model's output ceiling cut off, streamed as the text to **append** to
 * `partialDraft` rather than as a replacement for it.
 *
 * `prefilled` carries the prepass output the cut-off attempt already paid for, keyed by the field
 * it filled, so continuing does not re-run a competitor web search — and so the continuation is
 * finishing the document against the same INPUTS the partial was written from. */
export async function streamContinueStage(
  assetId: string,
  answers: Record<string, string>,
  partialDraft: string,
  onChunk: (chunk: string) => void,
  opts: StreamStageOptions & { prefilled?: Record<string, string> } = {},
): Promise<void> {
  await streamWithRetry(
    () =>
      fetch(`/api/pipeline/continue/${assetId}/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          answers,
          partial_draft: partialDraft,
          client_profile: opts.clientProfile ?? {},
          phase: opts.phase ?? "phase1",
          chat_session_id: opts.chatSessionId ?? null,
          run_id: opts.runId ?? null,
          prefilled: opts.prefilled ?? {},
          variant: opts.variant ?? null,
        }),
        signal: opts.signal,
      }),
    onChunk,
    opts,
  );
}

/** Revision of a previous draft per an operator's requested change — a different, smaller
 * prompt than the original generation (see `build_revision_prompt` in `generation.py`). */
export async function streamRefineStage(
  assetId: string,
  previousDraft: string,
  note: string,
  onChunk: (chunk: string) => void,
  signal?: AbortSignal,
  phase: PipelinePhase = "phase1",
  attribution: { chatSessionId?: string | null; runId?: string | null } = {},
  /** Set for "Fix with Refine": the revision is then handed the business facts the findings were
   * checked against, so it can use the real price or offer instead of guessing one. */
  businessFix?: { clientProfile: Record<string, string> },
  /** The same two callbacks a generation gets. A revision is asked for the whole document, so it
   * can be cut off by the ceiling exactly as a generation can. `sourceCopy` is the copy the page was
   * built from; a built page is refined by edits written against it (see `page_revision.py`). */
  opts: Pick<StreamStageOptions, "onDone" | "onRetry" | "onRevisionEdits" | "variant"> & { sourceCopy?: string } = {},
): Promise<void> {
  await streamWithRetry(
    () =>
      fetch(`/api/pipeline/refine/${assetId}/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          previous_draft: previousDraft,
          note,
          phase,
          chat_session_id: attribution.chatSessionId ?? null,
          run_id: attribution.runId ?? null,
          business_fix: Boolean(businessFix),
          client_profile: businessFix?.clientProfile ?? {},
          source_copy: opts.sourceCopy ?? null,
          variant: opts.variant ?? null,
        }),
        signal,
      }),
    onChunk,
    { ...opts, signal },
  );
}

export interface RunContextResult {
  run_id: string;
  context_key: string;
  version: number;
  content: string;
}

/** Read an approved stage output back from the database. Returns null when nothing is stored yet
 * (a 404 here is an ordinary "not generated" answer, not a failure worth surfacing). */
/** An answer to an intake question, found in a document this run already approved. Advisory: the
 * question card offers it, the operator confirms it. See `app/services/vector_context.py`. */
export interface FieldSuggestion {
  field_id: string;
  answer: string;
  /** The words in the source that state the answer, verbatim. The server drops any answer whose
   * quote is not in the section it names. */
  quote: string;
  context_key: string;
  source_label: string;
  section_path: string;
  score: number;
}

export interface PrefillResult {
  status: "ok" | "off" | "unavailable" | "empty" | "error";
  suggestions: FieldSuggestion[];
  notes: string[];
  dropped: number;
}

/** Suggested answers for the questions a stage is about to ask. Always resolves: a failure is
 * `null`, and the intake asks as it always has. */
export async function prefillSuggestions(
  runId: string,
  assetId: string,
  body: { fields: PrefillFieldIn[]; phase: PipelinePhase; chat_session_id: string | null },
  signal?: AbortSignal,
): Promise<PrefillResult | null> {
  try {
    const res = await fetch(`/api/pipeline/runs/${runId}/prefill/${assetId}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal,
    });
    if (!res.ok) {
      console.warn(`Prefill for ${assetId} answered ${res.status}`);
      return null;
    }
    return await res.json();
  } catch (err) {
    if (!signal?.aborted) console.warn(`Prefill for ${assetId} failed`, err);
    return null;
  }
}

/** A large upstream document sent to this stage as its relevant sections rather than whole
 * (`vector_context.RETRIEVAL_FIELDS`). Reported so the card can say so. */
export interface ContextRetrievalReport {
  fields: {
    field_id: string;
    context_key: string;
    sections: number;
    total_sections: number;
    chars_before: number;
    chars_after: number;
  }[];
  notes: string[];
}

export async function fetchRunContext(runId: string, contextKey: string): Promise<RunContextResult | null> {
  const res = await fetch(`/api/pipeline/runs/${runId}/context/${contextKey}`);
  if (res.status === 404) return null;
  if (!res.ok) {
    console.warn(`Could not read context ${contextKey} (${res.status})`);
    return null;
  }
  return res.json();
}

/** A live page read server-side, so an intake field that wants a whole page of copy doesn't have
 * to be pasted by hand — see `app/services/scraper.py`. */
export interface ScrapedPage {
  url: string;
  final_url: string;
  title?: string | null;
  meta_description?: string | null;
  content: string;
  char_count: number;
  word_count: number;
  truncated: boolean;
  /** Set when so little text came back that the page is probably client-rendered. Not an error —
   * the caller decides whether to use it or ask the operator to paste instead. */
  low_content: boolean;
  /** Which reader answered: "direct" (the backend's own fetch) or "claude" (Anthropic's fetcher,
   * used when a site refuses server-side requests or renders its copy in the browser). */
  source?: string;
  warnings: string[];
}

/** Read the page at `url`.
 *
 * Unlike the other calls here, the failure message is lifted out of FastAPI's `detail` and thrown
 * bare: every `ScrapeError` the backend raises is already written for the operator ("returned HTTP
 * 403 — if the page is behind a login, paste the copy instead"), and wrapping it in
 * `Read page failed (422): {"detail":…}` would bury the one sentence they need. */
export async function scrapePage(url: string): Promise<ScrapedPage> {
  const res = await fetch("/api/pipeline/scrape", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url }),
  });
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    let detail = body;
    try {
      const parsed = JSON.parse(body) as { detail?: unknown };
      if (typeof parsed.detail === "string") detail = parsed.detail;
    } catch {
      // Not JSON (a proxy error page, say) — fall back to the raw body.
    }
    throw new Error(detail || `Could not read the page (${res.status} ${res.statusText}).`);
  }
  return res.json();
}

/** One competitor row as the UI renders it. The raw model JSON is parsed server-side and never
 * reaches the client — see `parse_analysis` in `app/services/competitor.py`. */
export interface CompetitorRow {
  rank: number;
  domain: string;
  name: string;
  page_url?: string | null;
  verification_confidence: string;
  offering_summary?: string | null;
  /** Published starting price, verbatim ("From $1,500/mo"). Offers stage only. */
  starting_price?: string | null;
  /** One stage-specific classifier: lead-magnet type, blog content focus, podcast topical focus. */
  category?: string | null;
  similarity_score?: number | null;
  avg_position?: number | null;
  intersections?: number | null;
}

export interface CompetitorAnalysisResult {
  asset_id: string;
  target_url: string;
  /** The generating model's exact JSON. Carried so `saveCompetitorAnalysis` can persist it on the
   * analysis row; deliberately never rendered — the operator reviews `competitors` and `notes`. */
  raw_output?: string;
  service?: string | null;
  niche?: string | null;
  location?: string | null;
  requested_count: number;
  returned_count: number;
  competitors: CompetitorRow[];
  notes?: string | null;
}

/** Run a gated competitor stage. Nothing is persisted until `saveCompetitorAnalysis`. */
export async function runCompetitorAnalysis(
  assetId: string,
  inputs: { target_url?: string; niche?: string; location?: string; service?: string },
  phase: PipelinePhase = "phase1",
  attribution: { chatSessionId?: string | null; runId?: string | null } = {},
): Promise<CompetitorAnalysisResult> {
  const res = await fetch(`/api/pipeline/competitor/${assetId}/run`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      target_url: inputs.target_url ?? "",
      niche: inputs.niche ?? "",
      location: inputs.location ?? "",
      // In Phase 2 this is the sub-service, and it is what the search is actually run on.
      service: inputs.service ?? "",
      phase,
      chat_session_id: attribution.chatSessionId ?? null,
      run_id: attribution.runId ?? null,
    }),
  });
  return unwrap(res, "Competitor analysis");
}

export interface SaveCompetitorResponse {
  run_id: string;
  asset_id: string;
  analysis_id: string;
  competitor_count: number;
  version: number;
  saved_at: string;
  /** The prose written to `context_entries` — the exact text the paired main prompt will receive,
   * returned so the UI's own context store holds the identical string rather than a near-copy. */
  context_text: string;
}

/** Persist the reviewed analysis. The rows are echoed back rather than regenerated so what gets
 * stored is exactly what the operator approved. */
export async function saveCompetitorAnalysis(
  runId: string,
  assetId: string,
  result: CompetitorAnalysisResult,
  /** The validation's ranking (`CompetitorValidation.markdown`), carried into the stage's prompt. */
  validationMd = "",
): Promise<SaveCompetitorResponse> {
  const res = await fetch(`/api/pipeline/runs/${runId}/competitor/${assetId}/save`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      target_url: result.target_url,
      service: result.service ?? null,
      niche: result.niche ?? null,
      location: result.location ?? null,
      notes: result.notes ?? null,
      competitors: result.competitors,
      // Persisted on the analysis row for audit/re-parsing. Never rendered.
      raw_output: result.raw_output ?? "",
      validation_md: validationMd,
    }),
  });
  return unwrap(res, "Save competitor analysis");
}

export type ValidationVerdict = "implement_now" | "test_first" | "differentiate" | "avoid" | "not_a_fit";

/** One number with where it came from: `measured`, `judged` or `unavailable` (never a guessed 0). */
export interface ValidationMetric {
  key: string;
  label: string;
  value: string;
  score: number | null;
  source: "measured" | "judged" | "unavailable";
  detail: string;
}

export interface ValidatedCompetitor {
  rank: number;
  name: string;
  domain: string;
  page_url: string | null;
  play: string;
  proof: number | null;
  fit: number | null;
  feasibility: number | null;
  total: number | null;
  verdict: ValidationVerdict;
  verdict_reason: string;
  metrics: ValidationMetric[];
  blockers: { reason: string; source: "rule" | "judged"; quote: string }[];
  what_to_take: string;
  how_to_prove: string;
  /** Steps to adapt `what_to_take` for this client's asset. Absent on a report scored before it. */
  how_to_apply?: string;
  top_keywords: { keyword: string; position: number | null; volume: number | null }[];
  /** Organic-traffic band vs the client's own domain: `above` / `peer` / `below`. */
  business_level: "above" | "peer" | "below" | null;
  page_count: number | null;
  keyword_count: number | null;
  /** Every keyword scanned for this domain, shown on hover. Absent on a report scored before it. */
  scanned_keywords?: ScannedKeyword[];
}

/** One keyword a domain ranks for, as DataForSEO reported it. */
export interface ScannedKeyword {
  keyword: string;
  position: number | null;
  volume: number | null;
  url?: string;
}

export interface CompetitorValidation {
  asset_id: string;
  location: string;
  client_traffic: number | null;
  client_top10: number | null;
  client_page_count: number | null;
  client_keyword_count: number | null;
  /** The client's own scanned keywords, for the baseline line's hover. */
  client_keywords?: ScannedKeyword[];
  /** Keywords scanned per domain (the top N by estimated traffic). */
  scanned_limit?: number;
  competitors: ValidatedCompetitor[];
  plays: {
    play: string;
    count: number;
    share: number;
    combined_traffic: number | null;
    average_fit: number | null;
    verdict: ValidationVerdict;
    competitors: string[];
  }[];
  market_gaps: string[];
  sources: string[];
  notes: string[];
  judge: "ok" | "unavailable";
  dropped_quotes: number;
  markdown: string;
}

/** `POST /pipeline/competitor/{assetId}/validate` — proof, fit and feasibility for this client.
 * One DataForSEO request per competitor plus one judge call; stores nothing. */
export async function validateCompetitors(
  assetId: string,
  body: {
    run_id: string | null;
    phase: PipelinePhase;
    chat_session_id?: string | null;
    target_url: string;
    location: string;
    service: string;
    client_profile: Record<string, string>;
    competitors: CompetitorRow[];
  },
): Promise<CompetitorValidation> {
  const res = await fetch(`/api/pipeline/competitor/${assetId}/validate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return unwrap(res, "Competitor validation");
}

/** Thin client for `app/routers/chat_sessions.py` — the chat-history sidebar's persistence.
 * `state` is opaque to the backend; it mirrors `pipelineStore`'s own shape (see
 * `serializeSnapshot`/`hydrateSnapshot` in `pipelineStore.ts`). */
export interface ChatSessionSummary {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
}

export interface ChatSessionDetail extends ChatSessionSummary {
  state: Record<string, unknown>;
}

async function unwrap<T>(res: Response, action: string): Promise<T> {
  if (!res.ok) {
    const body = await res.text().catch(() => "");

    // Parsing is kept separate from throwing on purpose. Folding them into one try/catch means the
    // `catch` sees both a non-JSON body *and* the errors raised deliberately below — and since
    // `JSON.parse` fails with a `SyntaxError`, no `instanceof Error` test can tell them apart.
    let parsed: { detail?: unknown } | null = null;
    try {
      parsed = JSON.parse(body) as { detail?: unknown };
    } catch {
      parsed = null; // not JSON — the status line is all there is to say
    }

    if (parsed) {
      // A classified fault arrives as FastAPI's `detail` object.
      const fault = faultFromUnknown(parsed.detail);
      if (fault) throw new ApiFaultError(fault);
      // A plain-string `detail` is a sentence written for the operator (an unrecognised location,
      // an empty provider response). Surfacing it as-is beats wrapping the raw JSON body in a
      // status line, which is what the fall-through below would do.
      if (typeof parsed.detail === "string" && parsed.detail.trim()) throw new Error(parsed.detail);
    }

    throw new Error(`${action} failed (${res.status}): ${body || res.statusText}`);
  }
  return res.json();
}

/** Build (or reuse) this run's keyword cluster report — the search-demand evidence every headline
 * suggestion is grounded in.
 *
 * Runs once per run as a prepass after ICP, not per stage: one clustering pass costs real money at
 * the keyword provider, and running it per stage would hand the blog and the pillar page different
 * keyword universes for the same service, so the topics chosen at one stage would stop lining up
 * with the topics chosen at the next.
 *
 * `phase` is load-bearing rather than routine here. Phase 1 clusters the headline service ("Social
 * Media Marketing"); Phase 2 clusters the sub-service ("Meta Ads"). They are different keyword sets
 * and neither approximates the other, which is why the backend also refuses to inherit this key
 * down the `source_run_id` chain.
 */
export interface KeywordClusterSummary {
  name: string;
  intent: string | null;
  funnel: string | null;
  content_type: string | null;
  recommended_content: string | null;
  recommended_url: string | null;
  primary_keyword: string | null;
  keyword_count: number;
  total_volume: number;
}

export interface KeywordReportResult {
  run_id: string;
  /** `skipped` is not a failure — the run does not know its service yet, so there is nothing to
   * cluster and the caller carries on without keyword grounding. */
  status: "built" | "reused" | "skipped";
  phase: string;
  service: string | null;
  provider: string | null;
  version: number | null;
  total_keywords: number;
  clusters: KeywordClusterSummary[];
  warnings: string[];
  reason?: string | null;
  /** The country the volumes are for (countries only). Empty for the stub provider. */
  location?: string;
  /** The best-ranked keywords (`keyword_rank.py`). Absent on reports read before ranking existed. */
  ranked?: RankedKeyword[];
  /** The ranked keywords as primary keywords, each with its secondary keywords. */
  groups?: KeywordGroup[];
  /** Alternate names seeded per service (`service_synonyms.py`). */
  synonyms?: Record<string, string[]>;
  /** Every seed the demand map was fetched from, alternate names included. */
  seeds?: string[];
}

/** One keyword, ranked on searches, estimated traffic and search momentum. Missing numbers are
 * null, never 0. */
export interface RankedKeyword {
  keyword: string;
  volume: number | null;
  difficulty: number | null;
  /** % change in searches (quarter, else year). */
  trend: number | null;
  est_traffic: number | null;
  virality: number | null;
  score: number | null;
  rank: number;
  intent?: string | null;
}

/** A primary keyword (a core search term) and its secondaries (the same search with a modifier). */
export interface KeywordGroup {
  primary: RankedKeyword;
  secondary: RankedKeyword[];
  combined_volume: number;
}

/** What one stage was told to target (the `keyword_targets` stream event). */
export interface KeywordTargets {
  service: string;
  location: string;
  groups: KeywordGroup[];
  synonyms: Record<string, string[]>;
}

export async function buildRunKeywords(
  runId: string,
  profile: Record<string, string>,
  phase: PipelinePhase = "phase1",
  attribution: { chatSessionId?: string | null } = {},
): Promise<KeywordReportResult> {
  const res = await fetch(`/api/pipeline/runs/${runId}/keywords/build`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      phase,
      profile,
      chat_session_id: attribution.chatSessionId ?? null,
    }),
  });
  return unwrap(res, "Keyword clustering");
}

/** One suggested headline, with the evidence behind it.
 *
 * The evidence fields are not decoration — they are what makes this a choice rather than a lottery.
 * `search_volume` is real demand from this run's own keyword set (null when the candidate is not
 * grounded in it), `framework_formula` and `curiosity_elements` are how the headline framework was
 * applied, and `trend_evidence` is what a live search actually found, or null when nothing was
 * verified. A null trend is the honest answer and is rendered as one. */
export interface HeadlineCandidate {
  id: string;
  headline: string;
  primary_keyword: string | null;
  source_cluster: string | null;
  intent: string | null;
  funnel: string | null;
  traffic_temperature: string | null;
  framework_formula: string | null;
  curiosity_elements: string[];
  specificity: string | null;
  why_it_works: string | null;
  trend_evidence: string | null;
  char_count: number;
  channel_limit_ok: boolean;
  checklist_pass: boolean;
  checklist_notes: string;
  search_volume: number | null;
  difficulty: number | null;
  /** False when the candidate's keyword is not in this run's cleaned keyword set. It still may be
   * the right pick — but it carries no demand evidence, and the card says so rather than letting it
   * pass as measured. */
  grounded: boolean;
  extras: Record<string, unknown>;
}

export interface HeadlineSuggestions {
  slot: string;
  asset_id: string;
  phase: string;
  service_anchor: string;
  /** Where the anchor came from — a stage field, a run-level fact, or the client's industry as
   * a last resort. Shown on the card: an anchor that fell through to `industry` is a category
   * rather than a service, and the operator should see that instead of wondering why the topics
   * are broad. */
  anchor_source: string;
  label: string;
  subject: string;
  channel: string;
  char_budget: string;
  multi: boolean;
  suggested_selection: number;
  candidates: HeadlineCandidate[];
  grounded_in_keywords: boolean;
  web_search_used: boolean;
  rejected_count: number;
}

/** Ask for at least `count` candidate headlines for one slot.
 *
 * `exclude` is what makes "show me more" mean something: the headlines already turned down are sent
 * back so the next batch changes the angle rather than the wording. */
export async function suggestHeadlines(
  slot: string,
  args: {
    runId?: string | null;
    profile: Record<string, string>;
    /** Answers collected for this stage so far. The anchor comes first from this stage's own
     * service field, so omitting these leaves the backend with only run-level facts. */
    answers?: Record<string, string>;
    phase?: PipelinePhase;
    count?: number;
    exclude?: string[];
    operatorNote?: string;
    chatSessionId?: string | null;
    /** Abandons the request. Load-bearing for the retry controls: without it a restart races the
     * call it was meant to replace, and whichever resolves *last* wins — so a retry could be
     * overwritten seconds later by the stale response it was issued to escape. */
    signal?: AbortSignal;
  },
): Promise<HeadlineSuggestions> {
  const res = await fetch(`/api/pipeline/headlines/${slot}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    signal: args.signal,
    body: JSON.stringify({
      run_id: args.runId ?? null,
      phase: args.phase ?? "phase1",
      profile: args.profile,
      answers: args.answers ?? {},
      count: args.count ?? 10,
      exclude: args.exclude ?? [],
      operator_note: args.operatorNote ?? "",
      chat_session_id: args.chatSessionId ?? null,
    }),
  });
  return unwrap(res, "Headline suggestions");
}

/** One row of the backend's slot table (`SLOTS` in `app/services/headlines.py`). */
export interface HeadlineSlotConfig {
  slot: string;
  asset_id: string;
  label: string;
  subject: string;
  channel: string;
  char_budget: string;
  multi: boolean;
  suggested_selection: number;
}

/** The slot table as it stands now. No model call and no run needed — it is the config itself.
 *
 * Read on hydration so a gate restored from a chat snapshot follows the *current* config rather
 * than the one it was created under. A slot that becomes multi-select after a card was already on
 * screen would otherwise stay single-select in that chat forever, through any number of restarts,
 * because the card's behaviour was saved along with its candidates. */
export async function listHeadlineSlots(): Promise<HeadlineSlotConfig[]> {
  const res = await fetch("/api/pipeline/headlines/slots");
  return unwrap(res, "Headline slots");
}

export interface SaveHeadlineSelectionResponse {
  run_id: string;
  slot: string;
  asset_id: string;
  version: number;
  rendered: string;
}

/** Record what the operator chose, so later stages in the same leg stay on the chosen theme.
 *
 * Separate from the intake answer on purpose: the answer binds this stage, and this binds the rest
 * of the leg. Phase-scoped on the backend, so a Phase 2 run builds its own rather than inheriting
 * the topics chosen for the parent service. */
export async function saveHeadlineSelection(
  runId: string,
  slot: string,
  selected: Record<string, unknown>[],
  source: "suggested" | "operator",
  phase: PipelinePhase = "phase1",
): Promise<SaveHeadlineSelectionResponse> {
  const res = await fetch(`/api/pipeline/runs/${runId}/headlines/select`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ slot, phase, selected, source }),
  });
  return unwrap(res, "Headline selection");
}


// --------------------------------------------------------------------------------------
// Slide decks - the webinar stage's Step 5 brief as a .pptx
// --------------------------------------------------------------------------------------

/** The page CRO assembled from the client's real markup, as stored. Free — the generation already
 * happened. 404 (thrown) when nothing has been assembled for the run. */
export async function fetchReplicaOutput(runId: string): Promise<ReplicaReport & { html: string }> {
  const res = await fetch(`/api/pipeline/runs/${runId}/replica/output`);
  return unwrap(res, "Assembled page");
}

// ------------------------------------------------------------------------------------------------
// The deployable funnel — `app/routers/funnel_build.py`
// ------------------------------------------------------------------------------------------------

export interface FunnelBuildPlan {
  title: string;
  files: { key: string; title: string; url_path: string; file_path: string; screens: number }[];
  emails: { key: string; title: string; subject: string }[];
}

export interface FunnelBuildReady {
  funnel: boolean;
  hub: boolean;
  buildable: boolean;
  plan: FunnelBuildPlan | null;
  built: boolean;
  built_at: string | null;
}

export interface FunnelPageIssue {
  severity: "error" | "warn" | "info";
  quote: string;
  why: string;
  fix: string;
}

/** One built page or email, without its HTML (`GET …/funnel-build` carries that). */
export interface FunnelBuiltItem {
  key: string;
  kind: "page" | "email";
  title: string;
  file_path: string;
  url_path: string;
  subject: string;
  notes: string;
  score: number | null;
  verdict: string;
  issues: FunnelPageIssue[];
  error: string;
  /** Every change requested on this file, and how many edits each placed. */
  revisions?: { note: string; applied: number }[];
  html?: string;
}

export type FunnelBuildEvent =
  | { type: "plan"; plan: FunnelBuildPlan; hub: boolean }
  | { type: "inputs"; forms: { action: string; page_url: string }[]; notes: string[]; branded: boolean }
  | { type: "item_start"; key: string; kind: "page" | "email" }
  | { type: "item_done"; item: FunnelBuiltItem }
  | { type: "done"; version: number; items: FunnelBuiltItem[]; engine?: FunnelBuildEngine }
  | { type: "error"; message: string }
  /** The Manus task building the pages: its status every poll, then "done" or "fallback". */
  | FunnelManusEvent;

export interface FunnelManusEvent {
  type: "manus";
  status: string;
  task_url?: string;
  elapsed?: number;
  brief?: string;
  credits?: number | null;
  /** On "fallback": why Manus could not build the pages, so Claude did. */
  reason?: string;
}

/** Which builder made the pages. */
export interface FunnelBuildEngine {
  pages: "manus" | "claude";
  task_url?: string;
  credits?: number | null;
  manus_fallback?: string;
}

export async function fetchFunnelBuildReady(runId: string): Promise<FunnelBuildReady> {
  const res = await fetch(`/api/pipeline/runs/${runId}/funnel-build/ready`);
  return unwrap(res, "Funnel build check");
}

export async function fetchFunnelBuild(runId: string): Promise<{ items: FunnelBuiltItem[]; built_at: string }> {
  const res = await fetch(`/api/pipeline/runs/${runId}/funnel-build`);
  return unwrap(res, "Deployable funnel");
}

interface FunnelBuildBody {
  client_profile: Record<string, string>;
  phase: string;
  chat_session_id: string | null;
  include_emails?: boolean;
  /** Build only these pages / emails and merge them into the stored build (a rebuild). */
  only?: string[];
}

/** Build every page and email, reporting each as it finishes. Resolves once the build is stored. */
export async function streamFunnelBuild(
  runId: string,
  body: FunnelBuildBody,
  onEvent: (event: FunnelBuildEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(`/api/pipeline/runs/${runId}/funnel-build/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok || !res.body) {
    await unwrap(res, "Funnel build");
    return;
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let sep: number;
    while ((sep = buffer.indexOf("\n\n")) !== -1) {
      const raw = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);
      const line = raw.split("\n").find((l) => l.startsWith("data:"));
      if (!line) continue; // a keepalive comment
      try {
        onEvent(JSON.parse(line.slice(5).trim()) as FunnelBuildEvent);
      } catch {
        // A malformed event is skipped; the final `done` carries every result again.
      }
    }
  }
}

export interface FunnelRevisionResult {
  item: FunnelBuiltItem;
  /** How many of the edits landed. 0 means the file is exactly as it was. */
  applied: number;
  not_applied: { excerpt: string; why: string }[];
  /** The reply held no readable edit list, so nothing was changed. */
  unreadable: boolean;
}

/** Apply a requested change to one built page or email, as exact edits — nothing else changes. */
export async function reviseFunnelItem(
  runId: string,
  key: string,
  body: FunnelBuildBody & { note: string },
): Promise<FunnelRevisionResult> {
  const res = await fetch(`/api/pipeline/runs/${runId}/funnel-build/items/${encodeURIComponent(key)}/revise`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return unwrap(res, "Requested change");
}

export function funnelBuildZipUrl(runId: string): string {
  return `/api/pipeline/runs/${runId}/funnel-build/zip`;
}

// ------------------------------------------------------------------------------------------------
// Blog keywords — `app/services/blog_keywords.py`
// ------------------------------------------------------------------------------------------------

export interface BlogCompetitorRank {
  domain: string;
  position: number | null;
  url: string;
}

/** One keyword, measured against the client's reference page and the competing pages. */
export interface BlogKeywordOpportunity {
  keyword: string;
  dataforseo_volume: number | null;
  dataforseo_difficulty: number | null;
  /** The same as `dataforseo_volume` / `_difficulty`, under the name every card reads. */
  volume: number | null;
  difficulty: number | null;
  cpc: number | null;
  intent: string | null;
  /** The client's reference page's position for it, when it ranks. */
  reference_position: number | null;
  competitors: BlogCompetitorRank[];
  /** gap: competitors rank and the client does not · improve: the client ranks past page one ·
   * new: nobody compared ranks · owned: the client already ranks top 10. */
  status: "gap" | "improve" | "new" | "owned";
  score: number;
  sources: string[];
}

export interface BlogComparedPage {
  role: "reference" | "competitor";
  url: string;
  domain: string;
  ranked: number | null;
  on_topic: number | null;
  error: string;
}

export interface BlogKeywordSuggestions {
  seed: string;
  location: string;
  current: BlogKeywordOpportunity | null;
  primary: BlogKeywordOpportunity[];
  secondary: BlogKeywordOpportunity[];
  compared: BlogComparedPage[];
  /** "listing": the Blog's approved competitors · "serp": Google's top results for the seed. */
  competitor_source: string;
  dataforseo: { status: "ok" | "unconfigured" | "error"; error: string };
  notes: string[];
}

/** Better keywords for a blog post, compared against the client's page and the competing pages. */
export async function fetchBlogKeywords(body: {
  seed: string;
  region: string;
  current_keyword: string;
  run_id: string | null;
  website_url: string;
}): Promise<BlogKeywordSuggestions> {
  const res = await fetch(`/api/pipeline/keywords/blog`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return unwrap(res, "Blog keyword suggestions");
}

/** One competitor webinar page, as `POST /pipeline/webinar/sources` reports it. */
export interface WebinarSource {
  name: string;
  domain: string;
  url: string | null;
  confidence: string;
  /** Why this page was chosen, in the competitor research's own words. */
  reason: string;
  status: "read" | "failed" | "not_read";
  title: string | null;
  chars: number;
  excerpt: string;
  error: string;
}

export interface WebinarSourcesResult {
  sources: WebinarSource[];
  /** The field's answer: every page read, with its reason. "" when none was read. */
  block: string;
  notes: string[];
}

/** `POST /pipeline/webinar/sources` — the competitor webinar pages the approved research found, read
 * for free. Stores nothing. See `app/services/webinar_sources.py`. */
export async function fetchWebinarSources(listing: string): Promise<WebinarSourcesResult> {
  const res = await fetch("/api/pipeline/webinar/sources", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ listing }),
  });
  return unwrap(res, "Competitor webinar sources");
}

export interface StandaloneHtml {
  html: string;
  inlined: number;
  bytes: number;
  failed: { url: string; reason: string }[];
}

/** The page with every image written into it as a `data:` URI, so a downloaded file renders on a
 * device that cannot reach the image URLs. Free; stores nothing. */
export async function makeStandaloneHtml(html: string): Promise<StandaloneHtml> {
  const res = await fetch(`/api/pipeline/html/standalone`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ html }),
  });
  return unwrap(res, "Embedding images");
}

export interface SlideDeckSummary {
  index: number;
  title: string;
  slide_count: number;
  filename: string;
}

export interface SlideDecksResponse {
  decks: SlideDeckSummary[];
  bundle_filename: string;
}

/** What decks this webinar document contains. Free, and builds nothing.
 *
 * Asked before the button is offered, so a webinar whose Step 5 never ran shows no download rather
 * than a button that 422s when pressed. A 422 here is the ordinary "no brief" answer, so the caller
 * treats it as "nothing to offer" rather than surfacing it as a failure. */
export async function listSlideDecks(text: string, runId?: string | null): Promise<SlideDecksResponse | null> {
  const res = await fetch("/api/pipeline/slides", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text, run_id: runId ?? null }),
  });
  if (res.status === 422) return null;
  return unwrap(res, "List slide decks");
}

/** The deck itself. A `.pptx`, or a `.zip` when the document holds several and none was named.
 *
 * The response is binary, so this returns the blob and its filename rather than going through
 * `unwrap`. The filename comes off `Content-Disposition` - the server decided whether this is one
 * deck or a bundle, and re-deriving that here would be a second place to get it wrong. */
export async function fetchSlideDeck(
  text: string,
  runId?: string | null,
  index?: number,
): Promise<{ blob: Blob; filename: string }> {
  const query = typeof index === "number" ? `?index=${index}` : "";
  const res = await fetch(`/api/pipeline/slides/pptx${query}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text, run_id: runId ?? null }),
  });
  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    throw new Error(`Slide deck download failed (${res.status}): ${detail || res.statusText}`);
  }
  const disposition = res.headers.get("Content-Disposition") ?? "";
  const match = /filename="([^"]+)"/.exec(disposition);
  return { blob: await res.blob(), filename: match?.[1] ?? "webinar-slides.pptx" };
}

// Chat sessions are per-account: every route below resolves the owner from the session cookie
// (`app/routers/chat_sessions.py`), so these are the calls that must actually carry it.
// `credentials: "include"` is stated rather than left to the default for the same reason
// `authApi.ts` states it: same-origin's implicit default is correct only while the app and the API
// share an origin, and a deployment that splits them would otherwise turn every one of these into
// a silent 401 — a sidebar that is empty because the cookie never arrived looks exactly like a
// sidebar that is empty because the account is new.
const withSession: RequestInit = { credentials: "include" };

export async function listChatSessions(): Promise<ChatSessionSummary[]> {
  const res = await fetch("/api/chat-sessions", withSession);
  return unwrap(res, "List chat sessions");
}

export async function createChatSession(title = "New chat"): Promise<ChatSessionDetail> {
  const res = await fetch("/api/chat-sessions", {
    ...withSession,
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
  });
  return unwrap(res, "Create chat session");
}

export async function getChatSession(sessionId: string): Promise<ChatSessionDetail> {
  const res = await fetch(`/api/chat-sessions/${sessionId}`, withSession);
  return unwrap(res, "Load chat session");
}

export async function updateChatSession(
  sessionId: string,
  payload: { title?: string; state: Record<string, unknown> },
): Promise<ChatSessionDetail> {
  const res = await fetch(`/api/chat-sessions/${sessionId}`, {
    ...withSession,
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return unwrap(res, "Save chat session");
}

export async function deleteChatSession(sessionId: string): Promise<void> {
  const res = await fetch(`/api/chat-sessions/${sessionId}`, { ...withSession, method: "DELETE" });
  if (!res.ok && res.status !== 204) {
    const detail = await res.text().catch(() => "");
    throw new Error(`Delete chat session failed (${res.status}): ${detail || res.statusText}`);
  }
}

// --------------------------------------------------------------------------------------
// Phase 2
// --------------------------------------------------------------------------------------

export interface SourceRunAsset {
  context_key: string;
  version: number;
  chars: number;
}

/** A finished run a Phase 2 sub-service run can be started against. */
export interface SourceRunSummary {
  run_id: string;
  client_id: string;
  company_name: string;
  created_at: string;
  updated_at: string;
  /** The chat it was built in, so the picker can show the operator the title from their own
   * history sidebar rather than a run UUID. */
  chat_title?: string | null;
  /** What that run actually holds. This is the answer to "what would Phase 2 inherit?", so it is
   * shown on the card rather than being summarised as a count. */
  assets: SourceRunAsset[];
}

/** Runs whose approved context a new Phase 2 run could inherit, newest first.
 *
 * A failure here is not fatal: Phase 2 can still be run standalone, asking for each document it
 * would otherwise have inherited. So the picker degrades to "start without a Phase 1 run" rather
 * than blocking the run from starting at all.
 */
export async function listSourceRuns(): Promise<SourceRunSummary[]> {
  // Signed-in only, and scoped to this account's own runs — see `list_source_runs`.
  const res = await fetch("/api/pipeline/source-runs", withSession);
  if (!res.ok) {
    console.warn(`Could not list source runs (${res.status})`);
    return [];
  }
  return res.json();
}

/** The operator briefing over an approved competitor listing — see `app/services/insights.py`.
 *
 * Read before the stage's own intake on the two stages whose next questions depend on it (Blog's
 * topic/keyword/awareness answers, Content Marketing's cluster design). */
export async function fetchCompetitorBriefing(
  assetId: string,
  competitorOutput: string,
  subService: string,
  attribution: { chatSessionId?: string | null; runId?: string | null } = {},
): Promise<string> {
  const res = await fetch(`/api/pipeline/competitor-briefing/${assetId}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      competitor_output: competitorOutput,
      sub_service: subService,
      chat_session_id: attribution.chatSessionId ?? null,
      run_id: attribution.runId ?? null,
    }),
  });
  const body = await unwrap<{ asset_id: string; summary: string }>(res, "Competitor briefing");
  return body.summary;
}

/** One upstream document a stage reads, and whether this run already has it — the row the
 * start-at-a-stage gate is built from. Mirrors `DependencyStatus` in `app/routers/pipeline.py`. */
export interface DependencyStatus {
  field_id: string;
  label: string;
  context_key: string;
  sub_key: string | null;
  required: boolean;
  fallback: string;
  /** The asset whose output lands under `context_key`, or null when nothing writes it — in which
   * case "run the producer first" is not one of the operator's options and pasting is. */
  producer: string | null;
  ready: boolean;
  /** The key the document was actually stored under, which is what to fetch it back with: the
   * context key for a pasted document, the producing asset's id for an approved stage. */
  stored_under: string | null;
  seeded: boolean;
  source: "this_run" | "inherited" | null;
  from_run_id: string | null;
  version: number | null;
  chars: number | null;
  /** A field with no upstream key at all — the stage asks for it every time. Not a missing
   * document, and deliberately excluded from `blocked`. */
  manual: boolean;
}

/** A field the stage's own competitor prepass fills. Never something to run first or to supply:
 * the search happens inside the stage. */
export interface PrepassFieldStatus {
  field_id: string;
  label: string;
  context_key: string;
  required: boolean;
  ready: boolean;
  version: number | null;
}

export interface AssetReadiness {
  run_id: string;
  asset_id: string;
  phase: string;
  blocked: boolean;
  dependencies: DependencyStatus[];
  prepass: string | null;
  prepass_fields: PrepassFieldStatus[];
  /** Assets that would have to run, in order, to fill what is still missing — already filtered
   * against this run, so a run that holds the ICP is not told to produce one. */
  run_first: string[];
  seedable: string[];
  writes: string[];
}

/** What `assetId` still needs before it can run on this run. Free and read-only.
 *
 * This is what makes starting at stage 09 possible without running the eight before it: the answer
 * says, per document, whether the run already has it (and from where), whether it can be pasted,
 * and which assets would otherwise have to run. See `docs/Asset_Dependency_Map.md`. */
export async function fetchAssetReadiness(
  runId: string,
  assetId: string,
  phase: PipelinePhase,
): Promise<AssetReadiness> {
  const res = await fetch(
    `/api/pipeline/runs/${runId}/readiness/${assetId}?phase=${encodeURIComponent(phase)}`,
  );
  return unwrap<AssetReadiness>(res, "Stage readiness");
}

export interface SeedContextResponse {
  run_id: string;
  context_key: string;
  version: number;
  chars: number;
  producer: string | null;
}

/** File a document under a context key by hand, so a stage can run without the stage that would
 * normally have produced it.
 *
 * Written exactly like an approved stage output — same table, same versioning — and marked
 * `seeded` so the one thing that differs is visible: nothing generated it and nothing reviewed it.
 * It does not lock anything out; running the real stage later appends a higher version and wins. */
export async function seedRunContext(
  runId: string,
  contextKey: string,
  content: string,
  note?: string,
): Promise<SeedContextResponse> {
  const res = await fetch(`/api/pipeline/runs/${runId}/context`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ context_key: contextKey, content, note: note ?? null }),
  });
  return unwrap<SeedContextResponse>(res, "Provide document");
}

// --------------------------------------------------------------------------------------
// The client's industry — see `app/services/industry_voice.py`
// --------------------------------------------------------------------------------------

export type IndustrySource = "inferred_confirmed" | "operator_picked" | "operator_typed";

export interface IndustryProfileResult {
  bucket: string;
  bucket_label: string;
  label: string;
  source: IndustrySource;
  confidence?: number | null;
  rationale?: string;
}

/** Guess the client's own industry from intake answers, or map one the operator typed. Stores
 * nothing. Null means "no confident guess" — ask the operator outright. */
export async function inferIndustry(
  answers: Record<string, string>,
  typed?: string,
): Promise<IndustryProfileResult | null> {
  const res = await fetch("/api/pipeline/industry/infer", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ answers, typed: typed ?? null }),
  });
  const body = await unwrap<{ profile: IndustryProfileResult | null }>(res, "Work out the client's industry");
  return body.profile;
}

/** File the confirmed industry on the run. A correction appends a new version; the latest wins. */
export async function saveRunIndustry(
  runId: string,
  profile: Pick<IndustryProfileResult, "bucket" | "label" | "source" | "confidence" | "rationale">,
): Promise<IndustryProfileResult> {
  const res = await fetch(`/api/pipeline/runs/${runId}/industry`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      bucket: profile.bucket,
      label: profile.label,
      source: profile.source,
      confidence: profile.confidence ?? null,
      rationale: profile.rationale ?? "",
    }),
  });
  const body = await unwrap<{ profile: IndustryProfileResult }>(res, "Save the client's industry");
  return body.profile;
}

/** Record which sub-service a Phase 2 run is for.
 *
 * The sub-service is a run-level fact the UI holds, and nothing on the server knows it. The Plan of
 * Action covers every sub-service at once and has to name each one, so every Phase 2 run files its
 * own. A correction appends a new version; the latest wins. */
export async function saveRunSubService(runId: string, label: string): Promise<void> {
  const res = await fetch(`/api/pipeline/runs/${runId}/sub-service`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ label }),
  });
  await unwrap<{ run_id: string; label: string }>(res, "Save the run's sub-service");
}

/** The run's industry, inherited from its source run when it has none of its own; null when neither
 * has one, which is the cue to ask. */
export async function fetchRunIndustry(runId: string): Promise<IndustryProfileResult | null> {
  const res = await fetch(`/api/pipeline/runs/${runId}/industry`);
  if (res.status === 404) return null;
  if (!res.ok) {
    console.warn(`Could not read the run's industry (${res.status})`);
    return null;
  }
  const body = (await res.json()) as { profile: IndustryProfileResult };
  return body.profile;
}

// --------------------------------------------------------------------------------------
// Checking a draft against the client's business — see `app/services/asset_check.py`
// --------------------------------------------------------------------------------------

export type CheckName = "offer_relevance" | "funnel_relevance" | "business_logic" | "virality" | "page_readiness";
export type CheckSeverity = "error" | "warn" | "info";

export interface CheckFinding {
  check: CheckName;
  severity: CheckSeverity;
  /** Verbatim from the draft. Empty for a finding about the whole draft ("names no offer"). */
  quote: string;
  why: string;
  fix: string;
  source: "rule" | "judge";
}

export interface CheckResult {
  check: CheckName;
  score: number | null;
  verdict: string;
  findings: CheckFinding[];
}

export interface AssetCheckReport {
  asset_id: string;
  checks: CheckResult[];
  facts_used: string[];
  facts_missing: string[];
  /** "ok", or "unavailable" when only the rule findings could be produced. */
  judge: "ok" | "unavailable" | "skipped";
  /** Why the review step was unavailable, when it was. */
  judge_error?: string;
  dropped_quotes: number;
  virality_basis: "" | "benchmarked" | "rubric only";
  duration_ms: number;
}

export interface ScannedService {
  name: string;
  url: string;
  /** The broader service it sits under, when the site groups it. Empty for a top-level one. */
  parent: string;
}

export interface ServiceScanResult {
  source_url: string;
  services: ScannedService[];
  notes: string[];
}

/** The services (Phase 1) or sub-services (Phase 2) the page at `url` links to. One free fetch and
 * one small model call; stores nothing. A page that cannot be read is a 422 with the reason. */
export async function scanServices(
  url: string,
  opts: {
    phase?: string;
    focus?: string;
    runId?: string | null;
    chatSessionId?: string | null;
    signal?: AbortSignal;
  } = {},
): Promise<ServiceScanResult> {
  const res = await fetch("/api/pipeline/services/scan", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      url,
      phase: opts.phase ?? "phase1",
      focus: opts.focus ?? "",
      run_id: opts.runId ?? null,
      chat_session_id: opts.chatSessionId ?? null,
    }),
    signal: opts.signal,
  });
  return unwrap<ServiceScanResult>(res, "Find this site's services");
}

/** One keyword of a suggested sub-service. `volume` is DataForSEO's monthly searches, or null when
 * it was not measured — never 0, which would claim a measured absence of demand. */
export interface KeywordVolume {
  keyword: string;
  volume: number | null;
}

/** A sub-service the Plan of Action could brief: one the client's site already has, or a new one. */
export interface PlanSubServiceCandidate {
  name: string;
  status: "existing" | "new";
  /** The existing page's URL, or "" for a new one. */
  url: string;
  /** The page's real path, or a proposed one. */
  slug: string;
  parent: string;
  why: string;
  keywords: KeywordVolume[];
  combined_volume: number | null;
}

export interface SubServiceDiscovery {
  source_url: string;
  primary_service: string;
  candidates: PlanSubServiceCandidate[];
  /** "dataforseo" when the volumes are measured, "unavailable" when they could not be. */
  volume_source: string;
  location: string;
  /** Why the volumes are missing, when they are: DataForSEO unconfigured, rejected, out of balance. */
  volume_error?: string;
  notes: string[];
}

/** The Plan of Action's sub-services: what the client's site already has under the primary service,
 * what it should add, and real search volumes. Two small model calls and one DataForSEO request;
 * stores nothing. See `app/services/sub_service_discovery.py`. */
export async function discoverSubServices(
  primaryService: string,
  opts: { url?: string | null; region?: string; runId?: string | null; chatSessionId?: string | null } = {},
): Promise<SubServiceDiscovery> {
  const res = await fetch("/api/pipeline/plan/sub-services/discover", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      primary_service: primaryService,
      url: opts.url ?? "",
      region: opts.region ?? "",
      run_id: opts.runId ?? null,
      chat_session_id: opts.chatSessionId ?? null,
    }),
  });
  return unwrap<SubServiceDiscovery>(res, "Suggest sub-services");
}

/** One suggested secondary term. A missing number is null, never 0. */
export interface SecondaryKeyword {
  keyword: string;
  volume: number | null;
  difficulty: number | null;
  cpc: number | null;
  /** DataForSEO only: commercial / informational / navigational / transactional. */
  intent: string | null;
}

export type KeywordProvider = "dataforseo";

export interface ProviderSuggestions {
  provider: KeywordProvider;
  /** `ok` | `unconfigured` | `error`. */
  status: string;
  keywords: SecondaryKeyword[];
  error: string;
}

/** `POST /pipeline/keywords/secondary` — DataForSEO's suggestions for one head term. */
export interface SecondarySuggestions {
  head_term: string;
  /** The country it was asked for; "" when the region named none. */
  location: string;
  dataforseo: ProviderSuggestions;
}

/** Secondary / cluster terms for a Pillar Page head term, from DataForSEO. Always resolves for a
 * provider failure: the result carries its own status. */
export async function fetchSecondaryKeywords(headTerm: string, region: string): Promise<SecondarySuggestions> {
  const res = await fetch("/api/pipeline/keywords/secondary", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ head_term: headTerm, region }),
  });
  return unwrap<SecondarySuggestions>(res, "Suggest secondary keywords");
}

/** File the sub-services confirmed for the plan, so a Phase 2 run built on this one offers them. */
export async function savePlanSubServices(runId: string, content: string): Promise<{ names: string[] }> {
  const res = await fetch(`/api/pipeline/runs/${runId}/plan-sub-services`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
  });
  return unwrap<{ names: string[] }>(res, "Save the plan's sub-services");
}

/** Check a draft (saved or not) against the client's offers, funnel, business rules and predicted
 * virality. Stores nothing; one low-effort model call plus free rule checks. */
export interface BuyerPhrase {
  keyword: string;
  volume: number | null;
  difficulty: number | null;
  trend: number | null;
  score: number | null;
}

export interface OutcomeCluster {
  name: string;
  phrases: BuyerPhrase[];
  combined_volume: number;
  /** An outcome of what sets the service apart from its plain version (the "AI" of AI SEO). */
  distinct?: boolean;
}

export interface AvoidItem {
  term: string;
  kind: "competitor" | "jargon" | "claim";
  reason: string;
  alternative: string;
  term_volume: number | null;
  alternative_volume: number | null;
}

/** Suggestions for CRO's buyer-language questions (`app/services/buyer_language.py`). */
export interface BuyerLanguageResult {
  mode: "outcome" | "avoid";
  location: string;
  clusters: OutcomeCluster[];
  avoid: AvoidItem[];
  sources: string[];
  notes: string[];
}

export async function fetchBuyerLanguage(body: {
  mode: "outcome" | "avoid";
  services: string[];
  region: string;
  landing_url: string;
  industry: string;
  run_id: string | null;
  phase: string;
  chat_session_id: string | null;
}): Promise<BuyerLanguageResult> {
  const res = await fetch("/api/pipeline/keywords/buyer-language", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return unwrap<BuyerLanguageResult>(res, "Suggest buyer language");
}

/** One point of a deliverable's summary. `section` is one of the document's own headings, or "". */
export interface SummaryPoint {
  text: string;
  section: string;
}

/** The reader's Summary tab: what the document contains, as checked points
 * (`app/services/asset_summary.py`). Throws an `ApiFaultError` with the server's sentence. */
export async function summariseDocument(
  assetId: string,
  text: string,
  opts: { label?: string; phase?: string; runId?: string | null; chatSessionId?: string | null } = {},
): Promise<{ points: SummaryPoint[] }> {
  const res = await fetch(`/api/pipeline/summary/${assetId}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      text,
      label: opts.label ?? "",
      phase: opts.phase ?? "phase1",
      run_id: opts.runId ?? null,
      chat_session_id: opts.chatSessionId ?? null,
    }),
  });
  return unwrap<{ points: SummaryPoint[] }>(res, "Summarise this document");
}

/** An uploaded file's text, for the clip button on a file question. Stores nothing. */
export interface ExtractedFile {
  filename: string;
  kind: "pdf" | "docx" | "markdown" | "text";
  text: string;
  /** Characters in the whole file, before any cut. */
  chars: number;
  pages: number | null;
  truncated: boolean;
}

export async function extractFileText(file: File, signal?: AbortSignal): Promise<ExtractedFile> {
  const body = new FormData();
  body.append("file", file, file.name);
  const res = await fetch("/api/pipeline/files/extract", { method: "POST", body, signal });
  return unwrap<ExtractedFile>(res, "Read this file");
}

export async function checkAsset(
  assetId: string,
  text: string,
  opts: {
    runId?: string | null;
    phase?: string;
    clientProfile?: Record<string, string>;
    chatSessionId?: string | null;
    /** The report on the draft this one was refined from, so the re-check builds on it. */
    previous?: CheckResult[];
    signal?: AbortSignal;
  } = {},
): Promise<AssetCheckReport> {
  const res = await fetch(`/api/pipeline/check/${assetId}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      text,
      run_id: opts.runId ?? null,
      phase: opts.phase ?? "phase1",
      client_profile: opts.clientProfile ?? {},
      chat_session_id: opts.chatSessionId ?? null,
      previous: opts.previous ?? null,
    }),
    signal: opts.signal,
  });
  return unwrap<AssetCheckReport>(res, "Check this asset");
}
