/** Reading one *entry* out of a map-shaped context value.
 *
 * Almost every context key names a whole document — the ICP, the CRO rewrite, the funnel — and a
 * field that reads it wants all of it. A few name one entry of a map an upstream stage resolved:
 * `cro_terminology_map`'s three word choices, which later stages read one at a time through
 * `ctx(..., { sub_key })` in `data/assetCatalog.ts`.
 *
 * The Context Store only ever holds the producing stage's document, so the entry has to be read
 * back out of it. Until it was, `sub_key` was decorative: every one of those fields resolved to the
 * entire producing document, which meant the Offers stage received the whole ~80KB CRO rewrite
 * four times over (once legitimately as its CRO framework input, three more times in fields whose
 * expected answer is a single word like "homeowner"). That inflated the stage's prompt to ~131k
 * input tokens and told the Value Ladder prompt that the buyer's word *was* an 80KB document.
 */

/** Where each sub-key is written inside its producing document, in that document's own words.
 *
 * The CRO rewrite states the whole map in one "Terminology map" / "Resolved terminology" row of its
 * PART 0 table — see the Step 0 instruction in
 * `assets/Prompts/Master_Prompt_Universal_Page_Rewrite_v1.md`, which asks for "the resolved
 * terminology you will use throughout (reader, offer unit, commitment step, …)" — so each entry is
 * found by its own label. Three phrasings of each are matched, because all three occur in real
 * output: the placeholder tokens the injected `CRO_Framework_Universal_v1.md` defines (`{BUYER}`,
 * `{OFFER_UNIT}`, `{ENGAGEMENT}`), the plain words the Step 0 instruction uses, and the INPUTS
 * block's own field labels, which a rewrite that restates its inputs verbatim echoes instead. */
const SUB_KEY_LABELS: Record<string, Record<string, string[]>> = {
  cro_terminology_map: {
    word_for_the_reader: ["buyer", "reader", "word for the reader"],
    word_for_the_thing_being_chosen_between: [
      "offer_unit",
      "offer unit",
      "offer-unit",
      "word for the thing being chosen between",
    ],
    word_for_the_first_commitment_step: [
      "engagement",
      "first commitment step",
      "commitment step",
      "word for the first commitment step",
    ],
  },

  /** How the client sells, resolved once on the CRO stage so a later run for another service does
   * not have to settle it again. See `app/services/cro_settings.py`, which writes the document.
   *
   * Unlike the terminology map, this document is not the model's prose — the server composes it,
   * one setting per line as `- field_id = **value**`, so the field_id spelling below is an exact
   * match rather than a hopeful one. The human spellings after it are the retrofit path: a CRO
   * stage approved *before* this key existed has no settings row, so the lookup falls back to the
   * producing stage's own document, and a rewrite that restates its INPUTS block states these under
   * their labels. Finding nothing there is fine and expected — the field is then asked, which is
   * exactly what every run did before this. */
  cro_client_settings: {
    client_industry: ["client_industry", "client industry"],
    sub_vertical_niche: ["sub_vertical_niche", "sub-vertical / niche", "sub-vertical"],
    buyer_type: ["buyer_type", "buyer type"],
    sales_motion: ["sales_motion", "sales motion"],
    geo_mode: ["geo_mode", "geo mode"],
    region_location: ["region_location", "region / location(s)", "region / location"],
    claim_substantiation_tier: ["claim_substantiation_tier", "claim substantiation tier"],
    pricing_disclosure_mode: ["pricing_disclosure_mode", "pricing disclosure mode"],
    pricing_facts: ["pricing_facts", "pricing facts"],
    testimonials_before_after_permitted: [
      "testimonials_before_after_permitted",
      "testimonials & before/after permitted in this jurisdiction",
      "testimonials & before/after permitted",
    ],
    word_for_the_reader: ["word_for_the_reader", "word for the reader"],
    word_for_the_thing_being_chosen_between: [
      "word_for_the_thing_being_chosen_between",
      "word for the thing being chosen between",
    ],
    word_for_the_first_commitment_step: [
      "word_for_the_first_commitment_step",
      "word for the first commitment step",
    ],
    word_for_the_business: ["word_for_the_business", "word for the business"],
    words_the_buyer_uses_for_the_outcome: [
      "words_the_buyer_uses_for_the_outcome",
      "words the buyer uses for the outcome",
    ],
    words_to_avoid: ["words_to_avoid", "words to avoid (internal jargon, banned phrases)", "words to avoid"],
    tone_of_voice: ["tone_of_voice", "tone of voice"],
    proof_assets_available: ["proof_assets_available", "proof assets available"],
    primary_conversion_goal: ["primary_conversion_goal", "primary conversion goal"],
    secondary_conversion_goal: ["secondary_conversion_goal", "secondary conversion goal"],
  },
};

/** Entries a document never states, worked out from one it does.
 *
 * Kept apart from `SUB_KEY_LABELS` on purpose: that table is the list of settings the server
 * captures (`CLIENT_SETTING_FIELD_IDS`, checked against it by `tests/test_cro_settings.py`), and a
 * derived answer is not one of them — nothing was asked, so there is nothing to capture.
 *
 * `regulated_field_flag` is the Plan of Action's "Is this a regulated field?". The CRO stage never
 * asks it in those words; it asks the claim substantiation tier, which is the same question at finer
 * grain. Tiers 2 (professionally regulated) and 3 (health-therapeutic) are the regulated fields the
 * plan means — medical, legal, financial, insurance. Tier 1 is consumer law, which binds every
 * business and is not what a "regulated field" names, so it reads as No. An unreadable tier derives
 * nothing, and the field is asked: a legal guardrail is not something to default. */
const DERIVED_SUB_KEYS: Record<string, Record<string, { from: string; derive: (value: string) => string | undefined }>> = {
  cro_client_settings: {
    regulated_field_flag: {
      from: "claim_substantiation_tier",
      derive: (tier) => {
        const level = /^\s*([0-3])\b/.exec(tier)?.[1];
        if (level === undefined) return undefined;
        return Number(level) >= 2 ? "Yes" : "No";
      },
    },
  },
};

/** The longest an extracted entry may be, per map-shaped key.
 *
 * A terminology entry is a word or short phrase, and anything longer is the extractor having matched
 * a sentence *about* the term rather than the term. It is discarded — falling back to asking the
 * operator beats filling the field with prose.
 *
 * Client settings get a higher ceiling because they hold different sized things: still short (an
 * enum choice, a region, a conversion goal), but "Book a free 30-minute strategy call, routed to the
 * contact form" is a legitimate answer that 120 would throw away. What the limit is really there to
 * catch is a whole pasted paragraph — a pricing schedule, a proof-asset inventory — and both
 * ceilings still catch that, which is the intended outcome: those are the two settings that
 * genuinely might not carry from one service to the next, so asking is right. */
const MAX_ENTRY_LENGTH: Record<string, number> = { cro_client_settings: 400 };
const DEFAULT_MAX_ENTRY_LENGTH = 120;

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function clean(value: string): string {
  return value
    .replace(/\*\*/g, "")
    .replace(/[`*_]/g, "")
    .replace(/^[\s"'“”]+|[\s"'“”.,;]+$/g, "")
    .trim();
}

/** The backticks and braces around a placeholder token are punctuation around the label, not part
 * of it, so `` `{BUYER}` = **x** `` is searched as ` BUYER  = **x** ` and one label spelling covers
 * both the token form and the plain word. */
function normalize(text: string): string {
  return text.replace(/[`{}]/g, " ");
}

/** `Reader = **Marketing Manager** (and, per ICP, the client services lead)` -> `Marketing Manager`
 *
 * Two passes, because the documents bold the resolved word and then qualify it: the bolded value is
 * the answer whenever there is one, and the looser read (up to the next clause break) is the
 * fallback for a row written as plain prose. Either way an explicit `=`/`:` after the label is
 * required — that, rather than the label alone, is what distinguishes the map's own statement of a
 * term from the thousands of later sentences that merely use the word. */
function valueForLabel(text: string, labels: string[], maxLength: number): string | undefined {
  const haystack = normalize(text);
  const boundary = "(?:^|[|.·•;\\-\\s])\\s*";
  const assignment = "\\s*[=:]\\s*";

  for (const pattern of ["\\*\\*([^*|\\n]+)\\*\\*", "([^|.·•;\\n]+)"]) {
    for (const label of labels) {
      const match = new RegExp(`${boundary}${escapeRegExp(label)}${assignment}${pattern}`, "i").exec(haystack);
      if (!match) continue;
      const value = clean(match[1]);
      if (value && value.length <= maxLength) return value;
    }
  }
  return undefined;
}

/** True when `contextKey` names a map whose entries are addressed by `sub_key`, rather than a
 * document to be passed through whole. */
export function isMapShapedContextKey(contextKey: string): boolean {
  return contextKey in SUB_KEY_LABELS || contextKey in DERIVED_SUB_KEYS;
}

/** The value of one entry of a map-shaped context document, or undefined when this document does
 * not state it.
 *
 * Undefined is a real answer, not a failure to try: the caller treats the field as unresolved and
 * asks the operator, which is the correct outcome for a single-word field the upstream document
 * never actually resolved. */
export function extractSubKey(contextKey: string, subKey: string, text: string): string | undefined {
  const derived = DERIVED_SUB_KEYS[contextKey]?.[subKey];
  if (derived) {
    const source = extractSubKey(contextKey, derived.from, text);
    return source ? derived.derive(source) : undefined;
  }

  const labels = SUB_KEY_LABELS[contextKey]?.[subKey];
  if (!labels || !text.trim()) return undefined;

  // The map lives in one row/section of a long document, so that row is searched first: "Reader ="
  // is specific inside the terminology row and merely likely everywhere else in an 80KB rewrite.
  const row = text.split("\n").find((line) => /terminolog/i.test(line));
  const max = MAX_ENTRY_LENGTH[contextKey] ?? DEFAULT_MAX_ENTRY_LENGTH;
  return (row && valueForLabel(row, labels, max)) || valueForLabel(text, labels, max);
}
