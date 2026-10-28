"""`POST /pipeline/pillar/variants/compare`: the comparison card's numbers. Thin; see
`app/services/page_compare.py`.

Takes the drafts in the body (they are not approved, so there is nothing on the run to read) plus
what the variants already measured (the hosted page's outline, the beat sheet's topics), and counts
each draft the same way. Stores nothing, no model.

The client's live page is a column too. The reference variant's outline of it is reused when the
browser has one; otherwise the page is read here (`current_url`, else the drafts' answers):
free first, then one rendered read (1 credit) when the site walls the free read off. A page that
still cannot be read comes back as a row carrying why, so the card says so rather than dropping it.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from fastapi import APIRouter

from app.services import page_compare, page_variants, scraper

router = APIRouter()


class VariantDraft(BaseModel):
    variant: str
    label: str = ""
    text: str


class CompareRequest(BaseModel):
    drafts: list[VariantDraft] = Field(default_factory=list)
    #: The beat sheet's topics, from the competitor variant's `page_variant` event.
    topics: list[str] = Field(default_factory=list)
    #: The run's target keywords, checked on every draft alike.
    keywords: list[str] = Field(default_factory=list)
    #: The live page as the reference variant already measured it, when it could (saves a read).
    current: dict | None = None
    #: The live page to read when no measured outline is sent; else read from `answers`.
    current_url: str | None = None
    #: One draft's intake answers, for the page the reference variant beats.
    answers: dict[str, str] = Field(default_factory=dict)


def _row(page: page_compare.PageOutline, variant: str, topics: list[str], has_page: bool) -> dict:
    coverage = page_compare.topic_coverage(page, topics)
    row = page.to_dict()
    row["variant"] = variant
    row["has_page"] = has_page
    row["topics_covered"] = list(coverage) if coverage else None
    return row


async def _current_row(payload: "CompareRequest", keywords: list[str]) -> dict | None:
    if payload.current and not payload.current.get("error"):
        fields = {k: v for k, v in payload.current.items() if k in page_compare.PageOutline.__dataclass_fields__}
        page = page_compare.PageOutline(**{**fields, "label": "Current page"})
        return _row(page, "current", payload.topics, True)
    from app.routers.pipeline import _as_url, _variant_reference_url

    url = _as_url(payload.current_url or "") if payload.current_url else _variant_reference_url(payload.answers)
    if not url:
        return None
    page = await page_variants.read_outline("Current page", url, scraper._fetch_html, keywords, rendered=True)
    return _row(page, "current", payload.topics, page.error is None)


@router.post("/pillar/variants/compare")
async def compare_variants(payload: CompareRequest) -> dict:
    keywords = [k for k in payload.keywords if k.strip()][:30]
    pages = []
    for draft in payload.drafts[:6]:
        page = page_compare.outline(draft.label or draft.variant, draft.text, None, keywords)
        pages.append(_row(page, draft.variant, payload.topics, page_compare.page_html(draft.text) is not None))
    return {"pages": pages, "current": await _current_row(payload, keywords)}
