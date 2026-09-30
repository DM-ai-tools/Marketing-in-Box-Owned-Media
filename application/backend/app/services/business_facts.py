"""What the client's business actually is, as far as this run has recorded it.

The asset check (`asset_check.py`) judges a draft against the client's business. That only works if
"the business" is something measured rather than something the checker imagines. So this module
collects the facts the run already holds — the offer ladder, the pricing and claim rules the CRO
stage settled, the industry, the ICP, the funnel — into one `BusinessFacts`, and says plainly which
of them are missing.

A missing fact is reported as missing, never replaced with a guess. A price check with no prices on
record does not run; it is listed under `missing` so the operator can see why nothing was flagged.

Pure: no network, no database. The router hands in a lookup that reads context entries, which keeps
the inheritance rules (`PHASE_SCOPED_CONTEXT_KEYS`) where they already live.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

#: Reads one context key for the run and returns its stored value, or None. Supplied by the router.
Lookup = Callable[[str], Awaitable[object | None]]


@dataclass(frozen=True)
class Offer:
    title: str
    format: str = ""
    rung: str = ""
    price: str = ""
    estimated_value: str = ""
    ascends_to: str = ""

    @property
    def amounts(self) -> tuple[float, ...]:
        return money_amounts(f"{self.price} {self.estimated_value}")


@dataclass
class BusinessFacts:
    service: str = ""
    industry: str = ""
    offers: list[Offer] = field(default_factory=list)
    #: The CRO stage's client-level settings, by field id (`cro_settings.CLIENT_SETTING_FIELD_IDS`).
    settings: dict[str, str] = field(default_factory=dict)
    icp: str = ""
    funnel: str = ""
    #: The keyword report's `clean_keywords` rows: keyword, volume, difficulty, …
    keywords: list[dict[str, object]] = field(default_factory=list)
    #: `social_audit.engagement_stats` output, per account. Empty when no posts were fetched.
    social: list[dict[str, object]] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)

    @property
    def claim_tier(self) -> int | None:
        """0 GENERAL … 3 HEALTH-THERAPEUTIC, read off the leading digit of the CRO answer."""
        match = re.match(r"\s*([0-3])", self.settings.get("claim_substantiation_tier", ""))
        return int(match.group(1)) if match else None

    @property
    def pricing_mode(self) -> str:
        """A PUBLISHED … E QUOTE-ONLY, as its letter. "" when not on record."""
        match = re.match(r"\s*([A-E])\b", self.settings.get("pricing_disclosure_mode", "").upper())
        return match.group(1) if match else ""

    @property
    def prices_forbidden(self) -> bool:
        return self.pricing_mode in {"D", "E"}

    @property
    def testimonials(self) -> str:
        """YES / NO / UNSURE, or "" when not on record."""
        value = self.settings.get("testimonials_before_after_permitted", "").strip().upper()
        return value.split()[0] if value else ""

    @property
    def words_to_avoid(self) -> list[str]:
        raw = self.settings.get("words_to_avoid", "")
        words = [w.strip(" \"'“”.") for w in re.split(r"[,;/\n]|\bor\b", raw)]
        return [w for w in words if len(w) >= 3 and w.lower() not in {"n/a", "none"}]

    @property
    def allowed_amounts(self) -> set[float]:
        """Every amount the client has actually put on record: pricing facts and the ladder."""
        amounts = set(money_amounts(self.settings.get("pricing_facts", "")))
        for offer in self.offers:
            amounts.update(offer.amounts)
        return amounts

    def summary(self) -> str:
        """The facts as the judge reads them. Plain text, labelled, missing ones named."""
        lines = [f"Service being marketed: {self.service or '(not on record)'}"]
        lines.append(f"Client industry: {self.industry or '(not on record)'}")
        if self.offers:
            lines.append("Offer ladder (the client's real offers — nothing else is on sale):")
            for o in self.offers:
                bits = [f"[{o.format}]" if o.format else "", f'"{o.title}"']
                if o.rung:
                    bits.append(f"({o.rung})")
                if o.price:
                    bits.append(f"— Price: {o.price}")
                if o.ascends_to:
                    bits.append(f"— Ascends to: {o.ascends_to}")
                lines.append("  - " + " ".join(b for b in bits if b))
        labels = {
            "pricing_facts": "Pricing facts",
            "pricing_disclosure_mode": "Pricing disclosure mode",
            "claim_substantiation_tier": "Claim substantiation tier",
            "testimonials_before_after_permitted": "Testimonials / before-after permitted",
            "proof_assets_available": "Proof assets available",
            "words_to_avoid": "Words to avoid",
            "tone_of_voice": "Tone of voice",
            "buyer_type": "Buyer type",
            "word_for_the_reader": "Word for the reader",
            "word_for_the_first_commitment_step": "Word for the first commitment step",
        }
        for key, label in labels.items():
            if self.settings.get(key):
                lines.append(f"{label}: {self.settings[key]}")
        if self.missing:
            lines.append("NOT ON RECORD (do not assume these): " + ", ".join(self.missing))
        return "\n".join(lines)


# --------------------------------------------------------------------------------------
# Money
# --------------------------------------------------------------------------------------

_MONEY = re.compile(
    r"(?:A\$|AU\$|AUD\s?|US\$|USD\s?|NZ\$|CA\$|£|€|₹|\$)\s?(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d{1,2}))?\s?([kK])?\b"
)


def money_amounts(text: str) -> tuple[float, ...]:
    """Every currency amount in `text`, as numbers. "$1,200" and "$1.2k" are both 1200."""
    out: list[float] = []
    for match in _MONEY.finditer(text or ""):
        value = float(match.group(1).replace(",", "") + (f".{match.group(2)}" if match.group(2) else ""))
        if match.group(3):
            value *= 1000
        out.append(value)
    return tuple(out)


def money_matches(text: str) -> list[re.Match[str]]:
    return list(_MONEY.finditer(text or ""))


def amount_of(match: re.Match[str]) -> float:
    value = float(match.group(1).replace(",", "") + (f".{match.group(2)}" if match.group(2) else ""))
    return value * 1000 if match.group(3) else value


# --------------------------------------------------------------------------------------
# The offer ladder
# --------------------------------------------------------------------------------------

# `1. **[Diagnostic] "The Four Causes Scorecard"**` — the Value Ladder prompt's fixed item line. The
# bold and the quotes are both optional, because real outputs have dropped either.
_ITEM = re.compile(
    r'^\s*\d{1,2}\.\s+\**\s*\[(?P<format>[^\]]+)\]\s*:?\s*[“"](?P<title>[^”"]+)[”"]',
)
_ITEM_UNQUOTED = re.compile(r"^\s*\d{1,2}\.\s+\*\*\s*\[(?P<format>[^\]]+)\]\s*:?\s*(?P<title>[^*]+?)\s*\*\*")
_FIELD = re.compile(r"^\s*[-*]\s*(?P<name>Price|Estimated Value|Ascends To)\s*:\s*(?P<value>.+)$", re.IGNORECASE)
_RUNG = re.compile(r"^#{1,3}\s*(?:WISH\s*\d+\s*[—–-]\s*)?(?P<name>.*?\(Rung\s*\d+\).*?)\s*$", re.IGNORECASE)


def parse_offer_ladder(text: str) -> list[Offer]:
    """The offers in a Value Ladder document, in order. [] for anything else.

    Deterministic, because the prompt fixes the item format (`N. [Format]: "Title"` followed by
    `Price:`, `Estimated Value:`, `Ascends To:` lines). A model reading the ladder back would be a
    second place for an offer to be invented.
    """
    offers: list[dict[str, str]] = []
    rung = ""
    for line in (text or "").splitlines():
        heading = _RUNG.match(line)
        if heading:
            rung = heading.group("name").strip()
            continue
        item = _ITEM.match(line) or _ITEM_UNQUOTED.match(line)
        if item:
            offers.append({"title": item.group("title").strip(), "format": item.group("format").strip(), "rung": rung})
            continue
        fld = _FIELD.match(line)
        if fld and offers:
            key = {"price": "price", "estimated value": "estimated_value", "ascends to": "ascends_to"}[fld.group("name").lower()]
            offers[-1].setdefault(key, fld.group("value").strip())

    # A ladder that closes with a summary repeats offers already described above it, usually with
    # fewer fields. One offer, first description wins, gaps filled from the repeat.
    merged: dict[str, dict[str, str]] = {}
    for o in offers:
        key = o["title"].casefold()
        if key in merged:
            for k, v in o.items():
                if not merged[key].get(k):
                    merged[key][k] = v
        else:
            merged[key] = dict(o)
    return [Offer(**o) for o in merged.values()]


# --------------------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------------------


def _content(value: object) -> str:
    return str(value.get("content") or "") if isinstance(value, dict) else ""


async def load_business_facts(lookup: Lookup, client_profile: dict[str, str], *, asset_id: str) -> BusinessFacts:
    """Assemble the facts for checking a draft of `asset_id`.

    The offers stage is not checked against a stored ladder: its draft *is* the ladder being written,
    and the stored one is the version it is about to replace.
    """
    facts = BusinessFacts(
        service=(client_profile.get("sub_service") or client_profile.get("target_service") or "").strip(),
        industry=(client_profile.get("industry_label") or "").strip(),
    )

    industry = await lookup("industry_profile")
    if isinstance(industry, dict) and isinstance(industry.get("fields"), dict):
        facts.industry = str(industry["fields"].get("label") or facts.industry)
    if not facts.industry:
        facts.missing.append("client industry")
    if not facts.service:
        facts.missing.append("the service being marketed")

    settings = await lookup("cro_client_settings")
    if isinstance(settings, dict) and isinstance(settings.get("fields"), dict):
        facts.settings = {str(k): str(v) for k, v in settings["fields"].items() if v}
    if not facts.settings:
        facts.missing.append("CRO client settings (pricing, claim tier, testimonials, words to avoid)")

    if asset_id != "offers":
        ladder = _content(await lookup("offer_ladder")) or _content(await lookup("offers"))
        facts.offers = parse_offer_ladder(ladder)
        if not facts.offers:
            facts.missing.append("the offer ladder")

    facts.icp = _content(await lookup("icp"))
    if not facts.icp:
        facts.missing.append("the ICP")

    if asset_id != "funnel":
        facts.funnel = _content(await lookup("funnel_stages")) or _content(await lookup("funnel"))

    keywords = await lookup("keyword_clusters")
    if isinstance(keywords, dict) and isinstance(keywords.get("clean_keywords"), list):
        facts.keywords = [k for k in keywords["clean_keywords"] if isinstance(k, dict)]

    social = await lookup("social_post_sample")
    if isinstance(social, dict) and isinstance(social.get("stats"), list):
        facts.social = [s for s in social["stats"] if isinstance(s, dict)]

    return facts
