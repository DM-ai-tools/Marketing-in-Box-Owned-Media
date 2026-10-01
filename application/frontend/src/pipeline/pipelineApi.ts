/** Thin client for the two pipeline persistence routes (`app/routers/pipeline.py`). Proxied
 * by Vite's `/api` -> FastAPI rewrite, same pattern as `lib/icpApi.ts`.
 *
 * Every generation and competitor call carries the `phase`. It is not cosmetic: it selects the prompt
 * file and the INPUTS block the backend builds, so a Phase 2 stage sent without it comes back as the
 * Phase 1 asset — a page for the headline service where the operator asked for one for a sub-service,
 * with nothing in the output to say so. */
import type { PipelinePhase } from "./pipelineData";

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
  signal?: AbortSignal;
}

type SseEvent =
  | { type: "delta"; text: string }
  | { type: "done" }
  | ({ type: "error"; message: string } & Partial<ApiFault>)
  | { type: "prepass_start"; asset_id: string }
  | ({ type: "prepass" } & PrepassEvent);

/** Shared SSE-body reader for the two generation routes below — same event shape/parsing as
 * `lib/icpApi.ts`'s `streamIcp`. */
async function consumeSseStream(
  res: Response,
  onChunk: (chunk: string) => void,
  opts: StreamStageOptions = {},
): Promise<void> {
  if (!res.ok || !res.body) {
    const detail = await res.text().catch(() => "");
    throw new Error(`Generation failed (${res.status}): ${detail || res.statusText}`);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let receivedText = false;

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
        receivedText = true;
        onChunk(event.text);
      }
      else if (event.type === "prepass_start") opts.onPrepassStart?.(event.asset_id);
      else if (event.type === "prepass") opts.onPrepass?.(event);
      else if (event.type === "error") {
        const fault = faultFromUnknown(event);
        throw fault ? new ApiFaultError(fault) : new Error(event.message);
      }
      else if (event.type === "done") return;
    }
  }

  // The body ended without `done` and without an `error` event. Both of those are things the
  // backend always sends before finishing (see `_generation_sse_stream`), so reaching here means
  // the stream was cut rather than completed — never a successful generation to be reviewed.
  throw new StreamTruncatedError(receivedText);
}

/** Real generation for one stage: the actual master prompt for `assetId` (see
 * `app/services/generation.py`), filled in from `answers`, streamed as Markdown text deltas. */
export async function streamGenerateStage(
  assetId: string,
  answers: Record<string, string>,
  onChunk: (chunk: string) => void,
  opts: StreamStageOptions = {},
): Promise<void> {
  const res = await fetch(`/api/pipeline/generate/${assetId}/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      answers,
      client_profile: opts.clientProfile ?? {},
      phase: opts.phase ?? "phase1",
      chat_session_id: opts.chatSessionId ?? null,
      run_id: opts.runId ?? null,
    }),
    signal: opts.signal,
  });
  await consumeSseStream(res, onChunk, opts);
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
): Promise<void> {
  const res = await fetch(`/api/pipeline/refine/${assetId}/stream`, {
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
    }),
    signal,
  });
  await consumeSseStream(res, onChunk);
}

export interface RunContextResult {
  run_id: string;
  context_key: string;
  version: number;
  content: string;
}

/** Read an approved stage output back from the database. Returns null when nothing is stored yet
 * (a 404 here is an ordinary "not generated" answer, not a failure worth surfacing). */
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
    }),
  });
  return unwrap(res, "Save competitor analysis");
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

export type CheckName = "offer_relevance" | "funnel_relevance" | "business_logic" | "virality";
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

/** Check a draft (saved or not) against the client's offers, funnel, business rules and predicted
 * virality. Stores nothing; one low-effort model call plus free rule checks. */
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
