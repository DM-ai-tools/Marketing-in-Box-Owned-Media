"""What the Plan of Action reads from the whole engagement — the headline service *and* every
sub-service built under it.

The Plan of Action is the one deliverable that is about the engagement rather than about one service.
Its prompt asks for the Phase 1 work already done and for the sub-services to expand into, and every
sub-service Phase 2 has built is "already done" by the plan's own definition. It used to run as the
last stage of Phase 1, before any of that existed, so it scheduled work the operator had finished or
was about to finish.

It now runs at the end of Phase 2. Phase 2 stores one run per sub-service, each pointing at the Phase
1 run with `source_run_id`, and a run reads *down* that chain (a sub-service sees its parent) and never
sideways or up. This module is the one place that reads the other way: given any run in the
engagement it finds the root and every sibling, and renders three documents the Plan's inputs read.

They are **composed on read**, not stored. A stored copy would be stale the moment another
sub-service was approved, and "one plan covering all sub-services" is only true if the plan sees every
track as it stands now. `GET /pipeline/runs/{id}/context/{key}` dispatches on `COMPOSED_KEYS`, so the
frontend reads them through the same route it uses for every other document.

Excerpts, not documents. The question the Plan asks of each deliverable is *whether it exists and
what it is about*, and a pillar page alone is a full HTML file. The ICP is the exception: Step 0(C)
builds the whole plan from it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.services.asset_check import draft_text

#: The context key a sub-service's run files its own name under. Not an asset's output: the frontend
#: writes it when the track is created, because nothing else on the server records which sub-service
#: a run is for.
SUB_SERVICE_KEY = "sub_service"

ROOT_ICP_KEY = "plan_root_icp"
PHASE1_ASSETS_KEY = "plan_phase1_assets"
PHASE2_ASSETS_KEY = "plan_phase2_assets"

#: Keys that are composed on read rather than stored. Mirrored as `PLAN_COMPOSED_KEYS` in the
#: frontend's `data/assetCatalog.ts`; `tests/test_plan_context.py` parses that file to prove the two
#: agree, because a key declared on one side only is a plan field that silently goes back to asking.
COMPOSED_KEYS: frozenset[str] = frozenset({ROOT_ICP_KEY, PHASE1_ASSETS_KEY, PHASE2_ASSETS_KEY})

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

#: What one sub-service's Phase 2 run builds. `icp` leads: it is the sub-service's own buyer.
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

PHASE1_EXCERPT_CHARS = 1200
#: Smaller than Phase 1's because it is multiplied by every sub-service: ten sub-services at nine
#: documents each is the largest block in the plan's prompt.
PHASE2_EXCERPT_CHARS = 600
PHASE2_ICP_EXCERPT_CHARS = 900

_EXCERPT_NOTE = "[Excerpt. The full document was approved in this engagement.]"


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


def render_phase1_assets(docs: dict[str, str]) -> str:
    """The headline service's approved deliverables, as the Plan's "Existing Phase 1 Assets"."""
    found = [(asset_id, docs[asset_id]) for asset_id in PHASE1_ASSET_IDS if (docs.get(asset_id) or "").strip()]
    if not found:
        return ""
    intro = (
        "Built and approved earlier in this engagement for the headline service "
        f"(treat as done, do not re-plan): {', '.join(LABELS[a] for a, _ in found)}."
    )
    sections = [f"## {LABELS[a]}\n\n{excerpt(text, PHASE1_EXCERPT_CHARS)}" for a, text in found]
    return intro + "\n\n" + "\n\n---\n\n".join(sections)


@dataclass(frozen=True)
class SubServiceTrack:
    """One Phase 2 run: the sub-service it is for and the documents it has approved so far."""

    label: str
    docs: dict[str, str] = field(default_factory=dict)

    @property
    def built(self) -> tuple[str, ...]:
        return tuple(a for a in PHASE2_ASSET_IDS if (self.docs.get(a) or "").strip())

    @property
    def missing(self) -> tuple[str, ...]:
        return tuple(a for a in PHASE2_ASSET_IDS if a not in self.built)


def render_phase2_assets(tracks: list[SubServiceTrack]) -> str:
    """Every sub-service built under the headline service, as one document.

    A track that has approved nothing is left out: a sub-service the operator has only named is not
    "built", and listing it would tell the plan to skip work that has not started. It still reaches
    the plan through the typed "Sub-Services to Expand Into" input.
    """
    started = [t for t in tracks if t.built]
    if not started:
        return ""
    names = ", ".join(t.label for t in started)
    parts = [
        f"Phase 2 sub-services already built or in progress in this engagement ({len(started)}): {names}. "
        "Treat what each lists as built as done — do not re-brief it. What a sub-service lists as "
        "not yet built is still to plan."
    ]
    for track in started:
        lines = [f"## {track.label}", "", f"Built and approved: {', '.join(LABELS[a] for a in track.built)}."]
        if track.missing:
            lines.append(f"Not yet built: {', '.join(LABELS[a] for a in track.missing)}.")
        for asset_id in track.built:
            limit = PHASE2_ICP_EXCERPT_CHARS if asset_id == "icp" else PHASE2_EXCERPT_CHARS
            lines += ["", f"### {LABELS[asset_id]}", "", excerpt(track.docs[asset_id], limit)]
        parts.append("\n".join(lines))
    return "\n\n---\n\n".join(parts)
