"""The client's industry, as a controlled bucket, and the voice directive each bucket adds to a stage.

Every prompt in `assets/Prompts/` is written industry-agnostic, and until this module nothing told
the model how a regulated lender should sound next to a Shopify store — the pipeline's own voice,
which is a marketing agency's, bled into every client. The fix is not a copy of each prompt per
industry: Phase 2's prompt files started byte-identical to Phase 1's and drifted silently (the SCOPE
LOCK bug in `CLAUDE.md`), and N industry copies is that bug N times. It is one fenced block, composed
here, that `generation._prompt_parts` puts in front of every stage's INPUTS — the same shape as the
brand-token block, and for the same reason: a stated constraint, not a hoped-for judgement.

Three things decide what the block says, and all three are deliberate:

  * **The client's own industry, not its ICP's.** ICP's `industry` field asks for the *target
    customer's* industry. A finance client selling to tradespeople still has financial-promotion
    rules; an agency selling to clinics is still an agency. The ICP answer is passed to the
    classifier as a hint and labelled as one.
  * **A bucket, not free text.** Free text cannot drive a pack or a stage recommendation reliably.
    What the operator actually typed is kept as `label` and quoted in the block, so a bucket never
    loses the nuance ("Mortgage broking" is still said, under `regulated_finance`).
  * **The default is today.** `marketing_agency` renders no block at all, so a run that never
    answers the question — or answers it with the pipeline's historical client type — sends the
    prompt byte-for-byte as it was. `tests/test_industry_voice.py` pins that.

The pack is a default, never an override. The CRO stage settles tone, claim tier and testimonial
permission per client (`cro_settings.py`); an operator who answered those has decided, and
`_PRECEDENCE` says so in every block.

Three files have to agree: `BUCKETS` here, one `assets/voice_packs/<bucket>.md` per bucket, and
`INDUSTRY_PROFILES` in the frontend's `data/industryProfiles.ts`. `tests/test_industry_voice.py`
and `tests/test_industry_profiles_agree.py` read all three.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path

from app.services.claude_client import get_client

logger = logging.getLogger(__name__)

_PACKS_DIR = Path(__file__).resolve().parents[2] / "assets" / "voice_packs"

#: The context key a run's confirmed industry is stored under. Server-composed: written only by
#: `POST /pipeline/runs/{run_id}/industry`, inherited down `source_run_id` like any other entry, so
#: a Phase 2 leg reads its parent's answer without asking.
CONTEXT_KEY = "industry_profile"

#: bucket id -> the label an operator sees. Order is the order the choices are offered in.
BUCKETS: dict[str, str] = {
    "marketing_agency": "Marketing / Creative Agency",
    "regulated_finance": "Regulated Finance",
    "healthcare": "Healthcare",
    "legal_professional": "Legal & Professional Services",
    "b2b_saas": "B2B SaaS / Software",
    "e_commerce": "E-commerce / Retail",
    "local_service": "Local Service Business",
    "general": "Other industry",
}

DEFAULT_BUCKET = "marketing_agency"

#: The bucket for a typed answer that fits none of the others. Its pack names the industry verbatim
#: and asks the model to follow that industry's own conventions, rather than forcing a wrong bucket.
GENERAL_BUCKET = "general"

SOURCES = frozenset({"inferred_confirmed", "operator_picked", "operator_typed"})

#: Haiku 4.5, named here rather than imported from `generation` (which imports this module). It
#: takes no `output_config.effort` — see `generation.EFFORT_CAPABLE_MODELS`.
CLASSIFIER_MODEL = "claude-haiku-4-5-20251001"
_CLASSIFIER_MAX_TOKENS = 400

#: Below this, a guess is not offered for confirmation: the operator is asked outright instead. A
#: confirm card on a coin-flip invites a click-through on the wrong answer.
MIN_CONFIDENCE = 0.6

_BLOCK_NAME = "INDUSTRY_VOICE"

_PRECEDENCE = (
    "This block sets DEFAULTS for how the client's industry is expected to sound. It never overrides "
    "an answer in the INPUTS below: where INPUTS state a tone of voice, a claim substantiation tier, "
    "words to avoid, whether testimonials are permitted, or any other explicit instruction, INPUTS "
    "win. It changes voice and compliance posture only — the structure, sections and deliverables "
    "the master prompt specifies are unchanged."
)


@dataclass(frozen=True)
class IndustryProfile:
    bucket: str
    label: str
    source: str
    confidence: float | None = None
    rationale: str = ""

    @property
    def bucket_label(self) -> str:
        return BUCKETS.get(self.bucket, self.bucket)


def is_valid_bucket(bucket: str) -> bool:
    return bucket in BUCKETS


@lru_cache(maxsize=None)
def load_pack(bucket: str) -> str:
    """The pack text for `bucket`, or "" for the default (which has no file on purpose)."""
    if bucket == DEFAULT_BUCKET:
        return ""
    return (_PACKS_DIR / f"{bucket}.md").read_text(encoding="utf-8").strip()


def voice_block(profile: IndustryProfile | None) -> str:
    """The fenced directive a stage's prompt tail starts with — "" for no profile or the default."""
    if profile is None or profile.bucket == DEFAULT_BUCKET or profile.bucket not in BUCKETS:
        return ""
    label = " ".join(profile.label.split()) or profile.bucket_label
    return (
        f"===== BEGIN {_BLOCK_NAME} =====\n"
        f"CLIENT INDUSTRY: {label} (bucket: {profile.bucket_label})\n\n"
        f"HOW THIS BLOCK BINDS YOUR RESPONSE:\n{_PRECEDENCE}\n\n"
        f"{load_pack(profile.bucket)}\n"
        f"===== END {_BLOCK_NAME} =====\n\n"
    )


def render(profile: IndustryProfile) -> dict[str, object]:
    """The `ContextEntry.value`: `content` for every context reader, `fields` for this side."""
    lines = [
        f"- bucket = **{profile.bucket}**  ({profile.bucket_label})",
        f"- label = **{' '.join(profile.label.split())}**",
        f"- source = **{profile.source}**",
    ]
    if profile.rationale:
        lines.append(f"- rationale = **{' '.join(profile.rationale.split())}**")
    content = (
        "# Client industry\n\n"
        "The client's own industry, confirmed by the operator. It selects the voice pack every stage\n"
        "is written with and which stages are recommended.\n\n" + "\n".join(lines) + "\n"
    )
    return {"content": content, "fields": asdict(profile)}


def from_entry(value: object) -> IndustryProfile | None:
    """Rehydrate a stored entry, or None if it is not one this module wrote."""
    if not isinstance(value, dict):
        return None
    fields = value.get("fields")
    if not isinstance(fields, dict) or fields.get("bucket") not in BUCKETS:
        return None
    confidence = fields.get("confidence")
    return IndustryProfile(
        bucket=str(fields["bucket"]),
        label=str(fields.get("label") or BUCKETS[fields["bucket"]]),
        source=str(fields.get("source") or "operator_picked"),
        confidence=float(confidence) if isinstance(confidence, (int, float)) else None,
        rationale=str(fields.get("rationale") or ""),
    )


# ----------------------------------------------------------------------------------------------
# The classifier
# ----------------------------------------------------------------------------------------------

#: ICP intake field id -> how the classifier is told about it. The target-industry field is named
#: for what it is, so the model does not file a lender under "construction" because it lends to
#: builders.
_SIGNAL_FIELDS: tuple[tuple[str, str], ...] = (
    # CRO's own field, when the intake at hand is CRO's: the client's industry in the operator's words.
    ("client_industry", "Client's own industry, as the operator described it"),
    ("company_name", "Company name"),
    ("website_url", "Website"),
    ("company_type", "Company type"),
    ("business_model", "Business model"),
    ("offer_type", "Offer type"),
    ("service_product_price_terms", "What they sell"),
    ("market_region_country", "Market / region"),
    ("industry", "Industry of the customer they target (a hint only, NOT the client's own industry)"),
)


def _bucket_menu() -> str:
    return "\n".join(f"- {bucket}: {label}" for bucket, label in BUCKETS.items())


def _classifier_prompt(signals: dict[str, str], page: dict[str, str], typed: str | None) -> str:
    facts = [f"{label}: {signals[fid].strip()}" for fid, label in _SIGNAL_FIELDS if (signals.get(fid) or "").strip()]
    if page.get("title"):
        facts.append(f"Home page title: {page['title']}")
    if page.get("description"):
        facts.append(f"Home page meta description: {page['description']}")
    task = (
        f'The operator described the client\'s industry as: "{typed.strip()}". Map that description to '
        "the closest bucket. If none genuinely fits, use `general`."
        if typed
        else "Decide which bucket describes the CLIENT'S OWN business (what they sell and the rules "
        "they operate under), not the industry of the customers they sell to."
    )
    return (
        "You classify a marketing client into one industry bucket.\n\n"
        f"Buckets:\n{_bucket_menu()}\n\n"
        "`marketing_agency` is only for businesses that themselves sell marketing, advertising, design "
        "or creative services.\n\n"
        f"What is known about the client:\n" + ("\n".join(facts) or "(nothing)") + "\n\n"
        f"{task}\n\n"
        "Reply with ONLY a JSON object, no prose, no code fence:\n"
        '{"bucket": "<bucket id>", "label": "<the client\'s industry in 2-5 plain words>", '
        '"confidence": <0.0-1.0>, "rationale": "<one sentence citing the evidence>"}'
    )


_JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)


def _parse_reply(text: str) -> dict[str, object] | None:
    match = _JSON_OBJECT.search(text or "")
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


async def _home_page_signals(website_url: str) -> dict[str, str]:
    """Title + meta description from the free direct fetch only — never a paid reader. A failure
    costs the hint, never the classification."""
    if not (website_url or "").strip():
        return {}
    from app.services import scraper  # local: scraper pulls httpx and the Context.dev wrapper

    try:
        page = await scraper.read_direct(website_url)
    except Exception as exc:  # noqa: BLE001 — any fetch failure just means no page hint
        logger.info("Industry classifier: no home page hint for %r (%s)", website_url, exc)
        return {}
    return {"title": (page.title or "").strip(), "description": (page.meta_description or "").strip()}


async def infer_industry(
    signals: dict[str, str],
    *,
    typed: str | None = None,
    fetch_page: bool = True,
) -> IndustryProfile | None:
    """Guess the client's bucket from ICP intake answers, or map an operator's typed answer.

    Returns None — meaning "ask the operator outright" — when the call fails, the reply is not a
    known bucket, or the guess is under `MIN_CONFIDENCE`. A typed answer never returns None: the
    operator has already told us, so the worst case is `general` under their own words.
    """
    typed = (typed or "").strip() or None
    page = {} if typed or not fetch_page else await _home_page_signals(signals.get("website_url", ""))
    fallback = IndustryProfile(bucket=GENERAL_BUCKET, label=typed, source="operator_typed") if typed else None

    try:
        response = await get_client().messages.create(
            model=CLASSIFIER_MODEL,
            max_tokens=_CLASSIFIER_MAX_TOKENS,
            messages=[{"role": "user", "content": _classifier_prompt(signals, page, typed)}],
        )
        text = next((b.text for b in response.content if b.type == "text"), "")
    except Exception as exc:  # noqa: BLE001 — degrade to asking, never fail the intake
        logger.warning("Industry classifier call failed: %s", exc)
        return fallback

    data = _parse_reply(text)
    bucket = str((data or {}).get("bucket") or "")
    if not data or bucket not in BUCKETS:
        logger.info("Industry classifier returned no usable bucket: %r", text[:200])
        return fallback

    try:
        confidence = max(0.0, min(1.0, float(data.get("confidence") or 0.0)))
    except (TypeError, ValueError):
        confidence = 0.0
    label = " ".join(str(data.get("label") or "").split()) or BUCKETS[bucket]
    rationale = " ".join(str(data.get("rationale") or "").split())

    if typed:
        # The operator's words are the label; the model only picked the bucket.
        return IndustryProfile(bucket=bucket, label=typed, source="operator_typed", confidence=confidence, rationale=rationale)
    if confidence < MIN_CONFIDENCE:
        return None
    return IndustryProfile(bucket=bucket, label=label, source="inferred_confirmed", confidence=confidence, rationale=rationale)
