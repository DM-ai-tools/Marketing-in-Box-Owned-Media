import { ASSET_CATALOG } from "./assetCatalog";
import type { AssetDefinition, FieldDef } from "./types";

/** The Phase 2 asset definitions, derived from the Phase 1 ones.
 *
 * Phase 2 builds the same kinds of asset one level down, for a single *sub-service* — Google Ads,
 * LinkedIn, Meta Ads — of the headline service Phase 1 covered. Its prompt files are, with two
 * exceptions, the Phase 1 prompt re-pointed at a sub-service: three are byte-identical to their
 * Phase 1 counterparts and three differ by a single INPUTS label. So the intake is derived from
 * Phase 1's with a declared delta rather than written out a second time. Hand-writing seven more
 * definitions would mean seven more field lists to keep in step with the prompts, and a field added
 * to a Phase 1 asset later would silently never reach its Phase 2 twin.
 *
 * The same delta shape exists on the backend (`PHASE2_OVERRIDES` in `app/services/generation.py`),
 * which owns the labels the prompts actually receive. `tests/test_phase2.py` pins that side; the
 * `relabel` entries here exist so the *question* an operator is asked matches the input it fills.
 *
 * What the delta covers:
 *   - `dropFields` — inputs the Phase 2 prompt file does not have. Asking them would collect
 *     answers with nowhere to go.
 *   - `relabel` — inputs it words differently, because it is asking about a sub-service.
 *   - `fromSubService` — inputs the sub-service itself answers, so they are not asked at all.
 *   - `gatherContext` — inputs that ask for a set of documents, not one.
 *   - `askOutright` — inputs Phase 2 must collect from the operator, never inherit.
 *   - `rewordHelp` — inputs that behave as they do in Phase 1 but must explain themselves
 *     differently, because the document behind them comes from somewhere else here.
 *   - which carried-over inputs stop to ask before being reused — see `askBeforeReusing` below.
 */

/** Context keys a Phase 2 run produces for itself, stage by stage, in its own seven-stage order.
 *
 * The distinction this draws is the one that decides whether an operator gets asked about a
 * carried-over input. A key in here was approved a few cards ago in this same run — re-asking
 * "do you want to use the pillar page you just approved?" is noise. Everything else a Phase 2 stage
 * reads is inherited from the parent Phase 1 run, which the operator may well want to override:
 * that is a document from another engagement, possibly months old, describing the headline service
 * rather than this sub-service.
 */
const PHASE2_PRODUCED_KEYS: ReadonlySet<string> = new Set(
  ["icp", "cro", "pillar_page", "funnel", "lead_magnet", "blog", "sms_sequence", "content_marketing_strategy", "funnel_hub_media"]
    .flatMap((assetId) => {
      const asset = ASSET_CATALOG.find((a) => a.asset_id === assetId);
      if (!asset) throw new Error(`Phase 2 references unknown asset_id "${assetId}"`);
      // Its own output keys, plus the competitor listing it gates on — that analysis is reviewed and
      // approved on its own card inside this run, so the stage consuming it must not re-ask.
      return [...asset.writesContextKeys, asset.pairedCompetitorAssetId ?? ""];
    })
    .filter(Boolean),
);

interface Phase2Delta {
  dropFields?: readonly string[];
  relabel?: Readonly<Record<string, string>>;
  fromSubService?: readonly string[];
  /** field_id -> the upstream outputs to gather into it. For the two inputs whose prompts ask for a
   * *set* of documents rather than one ("any Plan of Action, funnel, lead magnet or ROI calculator
   * already built"; "the folder containing this client's existing strategy docs"). In Phase 1 both
   * are plain questions, because nothing upstream of them has been built when they are asked; in
   * Phase 2 they sit at stages 06 and 07, after the documents they are asking about exist. */
  gatherContext?: Readonly<Record<string, readonly string[]>>;
  /** field_id -> the Phase 2 helpText for an input that keeps its Phase 1 behaviour but not its
   * Phase 1 explanation.
   *
   * The narrowest of the field deltas, and the only one that changes nothing but what the operator
   * reads. It exists because a carried-over `overridable` field announces where its document came
   * from, and a card that misstates that provenance is worse than one that says nothing. No delta
   * uses it today: its one user was the CRO stage's ICP card, which said the document came from the
   * parent run — and Phase 2 now builds its own ICP. */
  rewordHelp?: Readonly<Record<string, string>>;
  /** field_id -> the Phase 2 wording for an input that must be *asked*, never resolved from
   * context — with the Phase 2 helpText, since the reason it is asked is Phase 2's alone.
   *
   * Stronger than `askBeforeReusing`, which offers the inherited document with an override. This
   * says the inherited document is the wrong document and must not be offered at all: it is for the
   * headline service the parent Phase 1 run covered, not for this run's sub-service, and the two
   * are not interchangeable. `askBeforeReusing` would still put "use it" one click away as the
   * default, which is the wrong default when the answer is always no.
   */
  askOutright?: Readonly<Record<string, { helpText: string; placeholder?: string }>>;
  /** Inputs answered from the parent Phase 1 run's `cro_client_settings` document rather than asked.
   *
   * The mirror image of `askOutright`, and for the mirror-image reason. `askOutright` marks an
   * inherited document that is *always the wrong document* for a sub-service. These are the answers
   * that are always the *right* ones: how the client sells, who buys, which claim tier they sit in,
   * how they may talk about price, and the words they use for a buyer and a first appointment. None
   * of that is a property of the service — it is a property of the client — so a Phase 2 run that
   * asked for it again would be asking the operator to re-derive an answer the engagement settled
   * during Phase 1, with nothing to check it against and every chance of answering differently.
   *
   * Each id names both the field and the `sub_key` it reads, because `cro_settings.py` keys the
   * document by field_id for exactly this. The field becomes an ordinary `context_reference`, so it
   * travels the normal auto-fill path: found -> filled in and announced with everything else the
   * walk resolved, absent -> asked. Absent is the expected case for a parent run approved before
   * that document existed, and asking is the right answer there rather than a guess. */
  fromParentSettings?: readonly string[];
  description?: string;
}

/** The client-level settings the CRO stage resolves once and every later run inherits.
 *
 * Mirrors `CLIENT_SETTING_FIELD_IDS` in `app/services/cro_settings.py`, which composes the document
 * these read out of, and `cro_client_settings` in `lib/contextSubKeys.ts`, which knows how to find
 * one entry in it. `tests/test_cro_settings.py` parses this file to prove the three agree — a field
 * listed here and not captured there is a question that silently goes back to being asked, which is
 * the failure mode with no symptom.
 *
 * `client_name` and `client_website_url` are absent because they are already run-level facts, and
 * the four `locked_*` fields because a locked heading is locked on *that page* — carrying one across
 * would pin a section the sub-service page has no reason to carry. */
/** The context key `app/services/cro_settings.py` writes the document under. */
export const CRO_CLIENT_SETTINGS_KEY = "cro_client_settings";

export const CRO_CLIENT_SETTINGS = [
  "client_industry",
  "sub_vertical_niche",
  "buyer_type",
  "sales_motion",
  "geo_mode",
  "region_location",
  "claim_substantiation_tier",
  "pricing_disclosure_mode",
  "pricing_facts",
  "testimonials_before_after_permitted",
  "word_for_the_reader",
  "word_for_the_thing_being_chosen_between",
  "word_for_the_first_commitment_step",
  "word_for_the_business",
  "words_the_buyer_uses_for_the_outcome",
  "words_to_avoid",
  "tone_of_voice",
  "proof_assets_available",
  "primary_conversion_goal",
  "secondary_conversion_goal",
] as const;

/** Context keys a Phase 2 leg must never take from Phase 1: the audience documents.
 *
 * Phase 1's ICP profiles the buyer of the headline service and its value ladder is priced for that
 * buyer. Phase 2 builds its own ICP for the sub-service, so neither may stand in for it — not in the
 * server's inheritance (`PHASE_SCOPED_CONTEXT_KEYS` in `app/routers/pipeline.py`, the same three
 * keys) and not in the in-memory copy a Phase 2 leg starts from (`phase2StartingContext`). What
 * still carries across describes the client, not the service: brand design, CRO client settings,
 * industry. */
export const PHASE1_ONLY_CONTEXT_KEYS: ReadonlySet<string> = new Set(["icp", "offers", "offer_ladder"]);

/** The Phase 1 context a new Phase 2 leg starts with: everything but the audience documents. */
export function phase2StartingContext<T>(phase1: Record<string, T>): Record<string, T> {
  return Object.fromEntries(Object.entries(phase1).filter(([key]) => !PHASE1_ONLY_CONTEXT_KEYS.has(key)));
}

const DELTAS: Record<string, Phase2Delta> = {
  // Stage 01: an ICP built for the sub-service, from scratch. The prompt is `ICP.md` with the service
  // input relabelled and a SUB-SERVICE SCOPE section added (`Phase2/ICP_phase2.md`); the sub-service
  // answers that input, so the operator is not asked to type the name they picked a card ago. The
  // answer stays editable, which is where a price or terms get added.
  icp: {
    relabel: { service_product_price_terms: "Sub-Service + Price/Terms" },
    fromSubService: ["service_product_price_terms"],
    description:
      "Ideal Customer Profile for one sub-service: the buyer who searches for, compares and pays for this sub-service specifically. Built from scratch, not re-pointed from the Phase 1 ICP.",
  },
  // Phase 2's stage 01, and the stage that makes the rest of the phase self-sufficient.
  //
  // It is here because of what the Pillar Page prompt is: a design replicator with no service input
  // of any kind, which lays out whatever copy it is handed. The copy therefore *is* the subject of
  // the page — so with no CRO stage in this phase, a run carefully pointed at "Meta Ads" could only
  // be handed the parent run's rewrite of the client's headline service, and came back a Social
  // Media Marketing page. The alternative on offer before this was for the operator to write the
  // page copy themselves and paste it in.
  //
  // It always runs in NEW PAGE mode, and that is not a limitation but the actual situation: this
  // phase exists precisely because the client has no page for the sub-service yet. The two
  // sentinels are filled from `NEW_PAGE_OPTIONS` in `pipeline/pipelineData.ts` rather than asked —
  // see `PHASE2_CONSTANT_ANSWERS` there, which also seeds `page_scope: "SUB-SERVICE"`.
  //
  // Seeding the sub-service into `target_service_or_sub_service` is what *names* the subject; what
  // holds the output to it is the SCOPE LOCK section in the Phase 2 prompt file. It is needed
  // because some of this stage's strategy material arrives at the parent's scope:
  // `fromParentSettings` below deliberately carries over the outcome vocabulary and proof assets
  // that run settled. The ICP no longer does — it is this phase's own stage 01. All of that is right about the *client*
  // and wrong about the *service*, and the lock is what says so — keep the buyer-level content,
  // re-point the service-level content at the sub-service, and never let the parent's term reach the
  // H1, title, keyword target or offer name. Pinned in `tests/test_phase2_cro_scope.py`.
  cro: {
    fromSubService: ["target_service_or_sub_service"],
    fromParentSettings: CRO_CLIENT_SETTINGS,
    description:
      "Writes the page copy for one sub-service from scratch, in this client's own vocabulary and claim rules — the document every HTML stage in this phase is then built from.",
  },
  pillar_page: {
    // Phase 2 uses the standalone v1.0 design prompt. It designs the page from approved copy and
    // nothing else: there is no keyword block, no cluster terms, no competitor pillar-page
    // benchmark and no locked-sections input anywhere in that file — which is also why Phase 2's
    // Pillar Page has no competitor research step where Phase 1's has one.
    dropFields: [
      "primary_keyword_head_term",
      "secondary_cluster_terms_optional",
      "internal_cluster_pages_to_link_optional",
      "competitor_analysis_pillar_page",
      "cro_locked_sections",
    ],
    // `improved_page_content` is not listed here, and the reason is the whole point of this phase's
    // stage 01. It is the one input that decides what this page is *about*: nothing else on this
    // stage names a service — the sub-service reaches four of Phase 2's seven stages through
    // `fromSubService`, and this is not one of them, because the design prompt has no service input
    // at all, by design ("design replicator, not design inventor"; all vocabulary comes from the
    // content file). So the copy handed to it *is* the subject.
    //
    // While Phase 2 had no CRO stage, that copy could only be the parent Phase 1 run's rewrite of
    // the client's *headline* service — so a run carefully pointed at "Meta Ads" came back a Social
    // Media Marketing page — or something the operator wrote and pasted in themselves, which is what
    // this delta used to demand with an `askOutright`. Now Phase 2 runs its own CRO stage first, the
    // copy is produced in-run for this sub-service, `cro` is in `PHASE2_PRODUCED_KEYS`, and the
    // field fills from the Context Store silently like any other document approved two stages ago.
    // Re-asking for it would be asking the operator to replace something they had just approved.
    description:
      "Full page design brief/build for one sub-service, replicating a reference visual style around the sub-service's approved copy, and delivered with a reusable design-token set.",
  },
  funnel: {
    relabel: { target_service_if_different_from_pillar_page: "Target Sub-Service (if different from pillar page)" },
    fromSubService: ["target_service_if_different_from_pillar_page"],
  },
  lead_magnet: {
    fromSubService: ["target_service_offer"],
  },
  blog: {},
  sms_sequence: {},
  content_marketing_strategy: {
    relabel: { primary_service_pillar_page_being_supported: "Primary Sub-Service / Pillar Page Being Supported" },
    fromSubService: ["primary_service_pillar_page_being_supported"],
    // Stage 06, so the funnel (02) and lead magnet (03) it is asking about were built earlier in
    // this same run. Offered together rather than described in prose: the prompt folds these into
    // the cluster architecture instead of re-briefing them, which it can only do if it has them.
    gatherContext: { existing_content_assets_optional: ["funnel_stages", "lead_magnet"] },
  },
  funnel_hub_media: {
    relabel: { service_or_product_line_being_funnel_mapped: "sub-service or product line being funnel-mapped" },
    fromSubService: ["service_or_product_line_being_funnel_mapped"],
    // The last stage, and its governing rule is to ground every funnel it maps in this client's
    // real strategy documents. The funnel and the design tokens have their own inputs on this asset,
    // so the reference folder is the rest of what this run built: the content strategy (topic
    // clusters, keyword work) and the lead magnet.
    gatherContext: { reference_folder_knowledge_base: ["content_marketing_strategy", "lead_magnet"] },
  },
};

/** Should this field stop and offer the operator a choice, rather than filling in silently?
 *
 * Only for a document inherited from the parent Phase 1 run. Deliberately not for `sub_key` fields:
 * those resolve to a single word from the CRO terminology map ("client", "package"), they exist so
 * design and copy use the same vocabulary, and three consecutive cards asking whether to keep using
 * the word "client" would bury the two choices on the same stage that actually matter.
 */
function askBeforeReusing(field: FieldDef): boolean {
  if (field.kind !== "context_reference") return false;
  const key = field.context_key;
  if (!key || key === "unresolved_context_key") return false;
  if (field.sub_key) return false;
  // `icp_*`-style wildcards name a family of keys; the family is what matters here.
  const base = key.endsWith("_*") ? key.slice(0, -2) : key;
  return !PHASE2_PRODUCED_KEYS.has(key) && !PHASE2_PRODUCED_KEYS.has(base);
}

/** The run-level fact every Phase 2 run is built around, and the fields it answers.
 *
 * Kept separate from Phase 1's `CLIENT_PROFILE_SOURCES`, which warns against putting a
 * "target service" field in it precisely because that field means something different per asset. In
 * Phase 2 it does not: a run *is* one sub-service, and every field listed in a delta's
 * `fromSubService` is asking which one. Answers stay editable — the walk announces what it reused
 * and offers each field for editing.
 */
export const SUB_SERVICE_FACT = "sub_service";

export const PHASE2_FIELD_TO_SUB_SERVICE: Record<string, typeof SUB_SERVICE_FACT> = Object.fromEntries(
  Object.values(DELTAS).flatMap((delta) => (delta.fromSubService ?? []).map((fieldId) => [fieldId, SUB_SERVICE_FACT])),
);

function applyDelta(asset: AssetDefinition, delta: Phase2Delta): AssetDefinition {
  const dropped = new Set(delta.dropFields ?? []);
  const relabel = delta.relabel ?? {};

  const gather = delta.gatherContext ?? {};
  const askOutright = delta.askOutright ?? {};
  const fromSettings = new Set(delta.fromParentSettings ?? []);
  const rewordHelp = delta.rewordHelp ?? {};

  for (const fieldId of [
    ...dropped,
    ...Object.keys(relabel),
    ...(delta.fromSubService ?? []),
    ...Object.keys(gather),
    ...Object.keys(askOutright),
    ...Object.keys(rewordHelp),
    ...fromSettings,
  ]) {
    // A delta naming a field the asset does not have is a no-op that reads as deliberate — it would
    // sit there looking like Phase 2 handles a field it silently does not.
    if (!asset.fields.some((f) => f.field_id === fieldId)) {
      throw new Error(`Phase 2 delta for "${asset.asset_id}" names unknown field "${fieldId}"`);
    }
  }

  for (const fieldId of Object.keys(rewordHelp)) {
    // Both would write the field's helpText, and the loser would be silent. `askOutright` already
    // carries its own Phase 2 wording, and a dropped field has no card to read anything on.
    if (dropped.has(fieldId) || askOutright[fieldId]) {
      throw new Error(
        `Phase 2 delta for "${asset.asset_id}" rewords the help of field "${fieldId}", which it also drops or re-asks`,
      );
    }
  }

  for (const fieldId of Object.keys(askOutright)) {
    // Both would resolve as "dropped wins", silently — an input the operator was promised they'd be
    // asked for, that the prompt then never receives.
    if (dropped.has(fieldId) || gather[fieldId]) {
      throw new Error(
        `Phase 2 delta for "${asset.asset_id}" both asks for and auto-fills field "${fieldId}"`,
      );
    }
    if (fromSettings.has(fieldId)) {
      throw new Error(
        `Phase 2 delta for "${asset.asset_id}" both asks for and inherits field "${fieldId}"`,
      );
    }
  }

  for (const fieldId of fromSettings) {
    // A field cannot be answered from two places. `fromSubService` wins silently if both were set,
    // and the settings document would simply never be consulted for it — so say so instead.
    if (dropped.has(fieldId) || gather[fieldId] || (delta.fromSubService ?? []).includes(fieldId)) {
      throw new Error(
        `Phase 2 delta for "${asset.asset_id}" inherits field "${fieldId}" from two sources`,
      );
    }
  }

  return {
    ...asset,
    description: delta.description ?? asset.description,
    // Phase 2's Pillar Page has no competitor step; the rest keep theirs, and the three that have
    // one run it against the sub-service (see `app/services/competitor.py`).
    pairedCompetitorAssetId: dropped.has("competitor_analysis_pillar_page") ? undefined : asset.pairedCompetitorAssetId,
    fields: asset.fields
      .filter((f) => !dropped.has(f.field_id))
      .map((f) => {
        const label = relabel[f.field_id] ?? f.label;

        // Rebuilt as a plain operator input rather than patched, because `planField` routes on
        // `kind` and `source` before it looks at anything else: leaving either one pointing at the
        // context store would put the inherited document back on the card, whatever `overridable`
        // said. Constructing the field fresh is what guarantees every route to it is gone —
        // `context_key`, `sub_key`, `fallback` and all. Nothing conditional is carried across
        // because `ctx()` cannot produce it, so a context_reference never has any.
        const asked = askOutright[f.field_id];
        if (asked) {
          return {
            field_id: f.field_id,
            label,
            // The same kind `reference_design_source` uses on this stage: pasted document, answered
            // through the ordinary answer bar.
            kind: "file_attach",
            required: true,
            source: "user_input",
            helpText: asked.helpText,
            placeholder: asked.placeholder,
          } satisfies FieldDef;
        }

        // Patched, not rebuilt — the opposite of `askOutright` above, and deliberately so.
        // `planField` takes the context route on `kind === "context_reference"` *or*
        // `source === "auto_from_context"`, so moving the source alone is enough to make the field
        // resolve from the settings document, while the original `kind` survives. That matters:
        // seven of these are `enum_choice`, and `QuestionWidget` renders its one-click pills on
        // `kind`. Rebuilt as a `context_reference`, a parent run with no settings document would
        // drop the operator into a free-text box and ask them to hand-type
        // "2 PROFESSIONALLY REGULATED" — for the three fields where an approximate answer changes
        // what the copy is legally allowed to say.
        //
        // `fallback: "ask_user_if_missing"` is the other half of that: absent settings degrade to
        // the Phase 1 behaviour of asking, never to submitting the stage with the field blank.
        if (fromSettings.has(f.field_id)) {
          return {
            ...f,
            label,
            source: "auto_from_context",
            context_key: CRO_CLIENT_SETTINGS_KEY,
            sub_key: f.field_id,
            fallback: "ask_user_if_missing",
          } satisfies FieldDef;
        }

        const keys = gather[f.field_id];
        // A gathered input is always offered rather than filled silently: it is several documents at
        // once, and the operator is the one who knows whether that set is what the prompt should be
        // folding in.
        const overridable = f.overridable || askBeforeReusing(f) || Boolean(keys);
        const helpText = rewordHelp[f.field_id] ?? f.helpText;
        if (label === f.label && overridable === f.overridable && helpText === f.helpText && !keys) return f;
        return { ...f, label, overridable, helpText, ...(keys ? { context_keys: [...keys] } : {}) };
      }),
  };
}

/** asset_id -> its Phase 2 definition. Only the seven assets Phase 2 runs are present. */
export const PHASE2_ASSETS: Record<string, AssetDefinition> = Object.fromEntries(
  Object.entries(DELTAS).map(([assetId, delta]) => {
    const asset = ASSET_CATALOG.find((a) => a.asset_id === assetId);
    if (!asset) throw new Error(`Phase 2 references unknown asset_id "${assetId}"`);
    return [assetId, applyDelta(asset, delta)];
  }),
);

/** The sub-service choices offered at the start of a Phase 2 run.
 *
 * A list rather than a free-text box because this value is spliced verbatim into three competitor
 * searches and four prompts: "Google Ads" and "google ads mgmt" would return different markets for
 * the same run. It is a set of one-click pills, not a closed set — the answer bar stays live under a
 * choice question, so anything not listed is still typed in. That is the "Other" case, and it needs
 * no extra option to sit alongside these.
 */
export const SUB_SERVICE_CHOICES: readonly string[] = [
  "Google Ads",
  "Meta Ads",
  "LinkedIn",
  "TikTok",
  "Pinterest",
  "SEO",
  "Email Marketing",
  "Content Marketing",
  "YouTube",
  "Programmatic / Display",
];
