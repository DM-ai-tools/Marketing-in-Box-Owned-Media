"""What the Plan of Action reads about the work Phase 1 has already finished.

The Plan of Action is Phase 1's last stage. It does two jobs: summarise what was delivered for the
headline service, in enough detail that the summary is worth reading on its own, and suggest the
sub-services Phase 2 builds next (`sub_service_discovery.py`). This module is the first job.

The summary used to be "one line each", from a 1,200-character excerpt per deliverable. A reader of
the plan could see that a lead magnet existed and not what it was. So each deliverable now arrives
as **measured facts first** — its title, length, section outline, and the numbers the document
itself states (the CRO score, the ladder's offers and prices, the SMS and email counts) — then an
excerpt, and the run's keyword clusters close the document with their measured volumes. Parsed,
never summarised by a model: a model reading the documents back would be a second place for a fact
to be invented, and the plan's own model writes the prose from these facts anyway.

**Composed on read**, not stored: `GET /pipeline/runs/{id}/context/{key}` dispatches on
`COMPOSED_KEYS`, so a deliverable re-run after the plan was entered is still current. A Phase 2
run's documents are never read here: Phase 2 starts from the plan, it does not feed it.
"""

from __future__ import annotations

import re

from app.services.asset_check import draft_text
from app.services.business_facts import parse_offer_ladder

#: The context key a Phase 2 run files its own sub-service name under (`POST /runs/{id}/sub-service`).
#: Not an asset's output: nothing else on the server records which sub-service a run is for.
SUB_SERVICE_KEY = "sub_service"

ROOT_ICP_KEY = "plan_root_icp"
PHASE1_ASSETS_KEY = "plan_phase1_assets"

#: Keys that are composed on read rather than stored. Mirrored as `PLAN_COMPOSED_KEYS` in the
#: frontend's `data/assetCatalog.ts`; `tests/test_plan_context.py` parses that file to prove the two
#: agree, because a key declared on one side only is a plan field that silently goes back to asking.
COMPOSED_KEYS: frozenset[str] = frozenset({ROOT_ICP_KEY, PHASE1_ASSETS_KEY})

#: The stage names the operator sees (`label` in the frontend's `data/assetCatalog.ts`). The plan
#: prompt's Step 2 and Step 3 lists use the same names, so the plan, its inputs and the pipeline all
#: say "CRO — Page Rewrite" rather than three wordings for one asset.
LABELS: dict[str, str] = {
    "icp": "ICP — Ideal Customer Profile",
    "cro": "CRO — Page Rewrite",
    "pillar_page": "Pillar Page Design",
    "funnel": "Funnel",
    "funnel_hub_media": "Funnel Hub Media",
    "offers": "Offers / Value Ladder",
    "lead_magnet": "Lead Magnet",
    "blog": "Blog Post",
    "content_marketing_strategy": "Content Marketing Strategy",
    "social_content_strategy_audit": "Social Content Strategy Audit",
    "webinar": "Webinar",
    "book": "Book (from Webinar)",
    "podcast": "Podcast Episode",
    "sms_sequence": "SMS Sequence",
}

#: The headline service's deliverables, in the order the pipeline builds them. The ICP is its own key
#: (`plan_root_icp`), so it is not repeated here. The value ladder is filed under two keys.
PHASE1_ASSET_IDS: tuple[str, ...] = (
    "cro",
    "pillar_page",
    "funnel",
    "funnel_hub_media",
    "offers",
    "lead_magnet",
    "blog",
    "content_marketing_strategy",
    "social_content_strategy_audit",
    "webinar",
    "book",
    "podcast",
    "sms_sequence",
)

#: What a Phase 2 run builds per sub-service, in its running order. The plan's Step 3 briefs each
#: suggested sub-service under these names, so a suggestion can be run as-is.
PHASE2_ASSET_IDS: tuple[str, ...] = (
    "icp",
    "cro",
    "pillar_page",
    "funnel",
    "lead_magnet",
    "blog",
    "sms_sequence",
    "content_marketing_strategy",
    "funnel_hub_media",
)

#: Keys an asset's document may be filed under, where it is not just the asset id.
ALTERNATE_KEYS: dict[str, tuple[str, ...]] = {"offers": ("offers", "offer_ladder")}

PHASE1_EXCERPT_CHARS = 1500
#: Section headings listed per deliverable, and keyword rows from the run's keyword clusters.
_MAX_OUTLINE = 10
_MAX_KEYWORDS = 15

_EXCERPT_NOTE = "[Excerpt. The full document was approved in this engagement.]"

_HEADING = re.compile(r"^\s{0,3}(#{1,3})\s+(.+?)\s*#*\s*$", re.MULTILINE)
_BOLD_HEADING = re.compile(r"^\s{0,3}\*\*([^*\n]{3,90})\*\*\s*$", re.MULTILINE)
_SCORE_100 = re.compile(r"\b(\d{1,3})\s*/\s*100\b")
_SMS_ITEM = re.compile(
    r"^\s{0,3}(?:#{1,4}\s*|\*\*|\|\s*)?(?:SMS|Message|Text)\s*#?\s*(\d{1,2})\b", re.IGNORECASE | re.MULTILINE
)
_EMAIL_ITEM = re.compile(r"^\s{0,3}(?:#{1,4}\s*|\*\*|\|\s*)?Email\s*#?\s*(\d{1,2})\b", re.IGNORECASE | re.MULTILINE)


def keys_for(asset_id: str) -> tuple[str, ...]:
    return ALTERNATE_KEYS.get(asset_id, (asset_id,))


def excerpt(text: str, limit: int) -> str:
    """The opening of a document as readable words, cut at a sentence or line where one is near."""
    plain = draft_text(text or "")
    plain = re.sub(r"[ \t]+", " ", plain).strip()
    plain = re.sub(r"\n{3,}", "\n\n", plain)
    if len(plain) <= limit:
        return plain
    cut = plain[:limit]
    last = max(cut.rfind("\n"), cut.rfind(". "))
    body = cut[: last + 1] if last > limit * 0.5 else cut
    return f"{body.rstrip()}\n\n{_EXCERPT_NOTE}"


def render_root_icp(text: str) -> str:
    """The headline service's ICP, whole. Empty when the engagement has none."""
    return (text or "").strip()


def _clean(text: str) -> str:
    return re.sub(r"[*_`]+", "", text).strip(" :—-")


def outline(text: str) -> tuple[str, list[str]]:
    """The document's title and its section headings, in order, read off its Markdown.

    The title is the first `#` heading (else the first heading of any level); the outline is the
    `##` headings, or the `###` ones when there are none, or whole-line bold leads as a last resort.
    """
    plain = draft_text(text or "")
    headings = [(len(m.group(1)), _clean(m.group(2))) for m in _HEADING.finditer(plain)]
    headings = [(level, h) for level, h in headings if h]
    title = next((h for level, h in headings if level == 1), headings[0][1] if headings else "")
    sections = [h for level, h in headings if level == 2 and h != title]
    if not sections:
        sections = [h for level, h in headings if level == 3 and h != title]
    if not sections:
        sections = [_clean(m.group(1)) for m in _BOLD_HEADING.finditer(plain)]
    return title, list(dict.fromkeys(sections))


def asset_facts(asset_id: str, text: str) -> list[str]:
    """What a deliverable measurably is, one fact per line. Every number is one the document states."""
    plain = draft_text(text or "")
    title, sections = outline(text)
    facts: list[str] = []
    if title:
        facts.append(f"Title: {title}")
    facts.append(f"Length: about {len(plain.split()):,} words")
    if asset_id == "cro":
        scores = [int(n) for n in _SCORE_100.findall(plain) if int(n) <= 100]
        if scores:
            facts.append(f"Page score stated in the audit: {scores[0]}/100")
    if asset_id == "offers":
        offers = parse_offer_ladder(text)
        if offers:
            listed = "; ".join(f"{o.title}{f' ({o.price})' if o.price else ''}" for o in offers[:12])
            facts.append(f"Offers on the ladder ({len(offers)}): {listed}")
    if asset_id == "sms_sequence":
        numbers = {int(n) for n in _SMS_ITEM.findall(plain)}
        if numbers:
            facts.append(f"Messages in the sequence: {max(numbers)}")
    if asset_id in ("funnel", "lead_magnet", "funnel_hub_media"):
        emails = {int(n) for n in _EMAIL_ITEM.findall(plain)}
        if emails:
            facts.append(f"Nurture emails written: {max(emails)}")
    if sections:
        shown = sections[:_MAX_OUTLINE]
        more = f" (+{len(sections) - len(shown)} more)" if len(sections) > len(shown) else ""
        facts.append(f"Sections: {' | '.join(shown)}{more}")
    return facts


def render_keywords(rows: list[dict[str, object]]) -> str:
    """The headline service's top keywords by measured volume, from the run's keyword clusters."""
    ranked = sorted(
        (r for r in rows if isinstance(r, dict) and r.get("keyword") and isinstance(r.get("volume"), (int, float))),
        key=lambda r: -float(r["volume"]),  # type: ignore[arg-type]
    )[:_MAX_KEYWORDS]
    if not ranked:
        return ""
    lines = [f"- {r['keyword']} — {int(r['volume']):,}/mo" for r in ranked]  # type: ignore[arg-type]
    return "## Keyword Clusters (measured search volume)\n\n" + "\n".join(lines)


def render_phase1_assets(docs: dict[str, str], keywords: list[dict[str, object]] | None = None) -> str:
    """The headline service's approved deliverables, as the Plan's "Existing Phase 1 Assets":
    measured facts, then an excerpt, per deliverable. "" when nothing was built."""
    found = [(asset_id, docs[asset_id]) for asset_id in PHASE1_ASSET_IDS if (docs.get(asset_id) or "").strip()]
    if not found:
        return ""
    built = {a for a, _ in found}
    missing = [LABELS[a] for a in PHASE1_ASSET_IDS if a not in built]
    intro = (
        "Built and approved in Phase 1 for the headline service (treat as done, do not re-plan): "
        f"{', '.join(LABELS[a] for a, _ in found)}."
    )
    if missing:
        intro += f" Not built in Phase 1: {', '.join(missing)}."
    sections = []
    for asset_id, text in found:
        facts = "\n".join(f"- {fact}" for fact in asset_facts(asset_id, text))
        sections.append(f"## {LABELS[asset_id]}\n\n{facts}\n\nExcerpt:\n{excerpt(text, PHASE1_EXCERPT_CHARS)}")
    keyword_block = render_keywords(keywords or [])
    if keyword_block:
        sections.append(keyword_block)
    return intro + "\n\n" + "\n\n---\n\n".join(sections)
