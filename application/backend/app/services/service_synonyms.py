"""Alternate names for a service: the different words people use when they search for the same thing.

"SEO" and "search engine optimisation" are one service, but DataForSEO treats them as two queries
and returns two different keyword sets. Seeded with the service's name alone, a run never sees the
demand behind its other names. So every service is given 4-6 genuine alternate names before its
keywords are fetched, and each is seeded too.

- **One Haiku call per service** (`synonyms_for`), with the client's industry so "Conveyancing"
  gets property-law names and not legal-marketing ones. Plan discovery asks for them inside its own
  existing call instead (`GUIDANCE`), so ten candidates do not cost ten more calls.
- **Measure, then judge, then check.** The model proposes; `clean_synonyms` drops, in code:
  - the service name itself, and near-duplicates of it or of each other;
  - a name that is the service plus a qualifier (agency, company, near me, best, cheap, a place)
    rather than a different name;
  - anything over five words.
  The prompt says the same, and the code holds it to that.
- **Never fatal.** No key or a failed call means no synonyms, and the service is seeded as before.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass

from app.services import keyword_rank
from app.services.claude_client import get_client
from app.services.generation import HAIKU, OnUsage
from app.services.usage import CallUsage

logger = logging.getLogger(__name__)

MODEL = HAIKU
MIN_SYNONYMS = 4
MAX_SYNONYMS = 6
_MAX_WORDS = 5
_MAX_TOKENS = 600

#: Words that make a name a *qualified* form of the service rather than another name for it.
QUALIFIERS: frozenset[str] = frozenset(
    {
        "agency", "agencies", "company", "companies", "firm", "firms", "near", "me", "nearby", "best",
        "top", "cheap", "cheapest", "affordable", "local", "consultant", "consultants", "expert",
        "experts", "specialist", "specialists", "provider", "providers", "freelancer", "freelancers",
        "contractor", "contractors", "cost", "costs", "price", "prices", "pricing", "quote", "quotes",
        "australia", "melbourne", "sydney", "brisbane", "perth", "adelaide", "uk", "usa", "london", "nz",
    }
)
#: Words that carry no meaning of their own in a service name ("services", "and").
_FILLER = frozenset({"service", "services", "and", "&", "the", "for", "of", "in", "a", "an"})

#: The rules and the worked examples. Shared with `sub_service_discovery`, so both calls are told the
#: same thing.
GUIDANCE = """Alternate names are different words people use to describe the SAME service, worded differently enough
that a search engine treats them as distinct queries.

They are NOT:
- qualifiers: the service plus agency, company, firm, near me, best, cheap, cost, a city or a country;
- sub-services or a narrower specialty of it (for "SEO", not "local SEO" or "technical SEO");
- a related but different service (for "SEO", not "content marketing").

Examples of good alternate names:
Digital marketing
- "SEO" -> search engine optimisation, organic search, google ranking, search visibility
- "Google Ads" -> PPC advertising, pay per click, paid search, adwords
- "Social Media Marketing" -> social media management, social media advertising, social media strategy
- "Web Development" -> website design, web design, website development, site development
- "Email Marketing" -> email campaigns, email automation, email newsletters, EDM marketing
Legal
- "Family Law" -> divorce law, matrimonial law, family legal services, separation law
- "Criminal Law" -> criminal defence, criminal litigation, criminal legal representation
- "Conveyancing" -> property transfer, property settlement, property law, settlement services
- "Immigration Law" -> visa law, migration law, immigration legal services, residency law
Finance
- "Tax Accounting" -> taxation services, tax advisory, tax services, tax preparation
- "Mortgage Broking" -> home loan broking, mortgage lending, home loan advice, mortgage advisory
- "Financial Planning" -> wealth management, financial advisory, investment planning, financial advice
- "Bookkeeping" -> accounts management, financial record keeping, accounting services, business accounting
Healthcare
- "Physiotherapy" -> physical therapy, physio treatment, movement therapy, rehabilitation therapy
- "Dentistry" -> dental care, oral health, dental treatment, teeth care
- "Psychology" -> mental health therapy, counselling, psychotherapy, talking therapy
- "Chiropractic" -> spinal care, chiropractic treatment, spinal adjustment, joint therapy
Trades
- "Plumbing" -> pipe work, plumbing repairs, drain services, water system services
- "Electrical" -> electrical services, wiring services, power installation, electrical repairs
- "Landscaping" -> garden design, outdoor landscaping, garden services, yard maintenance
- "Building & Renovation" -> home renovation, building services, construction services, property renovation
Education
- "Tutoring" -> private tuition, academic coaching, private lessons, learning support
- "Personal Training" -> fitness coaching, personal coaching, fitness training, exercise coaching
- "Corporate Training" -> workplace training, staff development, professional training, employee development
Real estate
- "Property Management" -> rental management, landlord services, tenancy management, investment property management
- "Buyers Agent" -> buyers advocate, property advocate, home buying service, property acquisition service
Hospitality
- "Catering" -> food catering, event catering, catering services, catering solutions
- "Event Management" -> event planning, event coordination, event organisation, event services"""


@dataclass(frozen=True)
class ServiceSynonyms:
    service: str
    synonyms: tuple[str, ...]
    #: `claude`, or `none` when no call was made or it failed. `note` says why.
    source: str
    note: str = ""


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def _core(text: str) -> frozenset[str]:
    return frozenset(keyword_rank.singular(w) for w in _words(text) if w not in _FILLER)


def clean_synonyms(service: str, proposed: list[str]) -> list[str]:
    """The model's names that are genuinely other names for `service`, lowercased, at most
    `MAX_SYNONYMS`, in the order given.

    Compared on content words, singularised, ignoring "services": so "electrical services" is the
    same name as "Electrical" here. That form is seeded anyway (`keywords.seeds_for_service`)."""
    base = _core(service)
    seen = {base}
    out: list[str] = []
    for raw in proposed:
        name = " ".join(_words(str(raw)))
        if not name or len(name.split()) > _MAX_WORDS:
            continue
        # A qualified form ("seo agency", "best seo near me", "plumbing melbourne"): not another name.
        if any(w in QUALIFIERS for w in name.split()):
            continue
        core = _core(name)
        if not core or core in seen:
            continue
        seen.add(core)
        out.append(name)
        if len(out) >= MAX_SYNONYMS:
            break
    return out


def _prompt(service: str, industry: str, market: str) -> str:
    return (
        f"{GUIDANCE}\n\n"
        f"Service: {service}\n"
        f"Client's industry: {industry or 'not stated'}\n"
        f"Market: {market or 'not stated'}\n\n"
        f"Give {MIN_SYNONYMS} to {MAX_SYNONYMS} alternate names for this service, as people in that "
        "industry's market would search for it, in that market's own spelling and terms (Australian "
        "and British English write optimisation, not optimization). Lowercase, no quotes, no "
        "explanation.\n"
        'Return only JSON: {"synonyms": ["...", "..."]}'
    )


def parse_reply(raw: str) -> list[str]:
    match = re.search(r"\{.*\}", raw or "", re.DOTALL)
    if not match:
        return []
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []
    items = data.get("synonyms") if isinstance(data, dict) else None
    return [str(i) for i in items if isinstance(i, str)] if isinstance(items, list) else []


async def synonyms_for(
    service: str, industry: str = "", on_usage: OnUsage | None = None, *, market: str = ""
) -> ServiceSynonyms:
    """Alternate names for one service. Never raises."""
    service = " ".join((service or "").split())
    if not service:
        return ServiceSynonyms(service, (), "none", "No service name.")
    started = time.monotonic()
    try:
        response = await get_client().messages.create(
            model=MODEL,
            max_tokens=_MAX_TOKENS,
            messages=[{"role": "user", "content": _prompt(service, industry.strip(), market.strip())}],
        )
        if on_usage is not None:
            await on_usage(
                CallUsage.from_response(response, requested_model=MODEL, duration_ms=int((time.monotonic() - started) * 1000))
            )
        raw = next((b.text for b in response.content if b.type == "text"), "")
    except Exception as exc:  # noqa: BLE001 — without synonyms the service is seeded as before
        logger.warning("Synonyms for %r failed: %s", service, exc)
        return ServiceSynonyms(service, (), "none", f"Alternate names could not be generated: {exc}")

    names = clean_synonyms(service, parse_reply(raw))
    logger.info("Synonyms for %r: %s", service, ", ".join(names) or "none kept")
    return ServiceSynonyms(service, tuple(names), "claude", "" if names else "No usable alternate names came back.")
