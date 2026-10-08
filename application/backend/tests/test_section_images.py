"""Section images — up to two extra gpt-image-2 images for a pillar page's own body sections.

Same seam as `test_resolve_page_design_images.py`: `session_factory` is a plain callable, faked
here, so nothing touches Postgres or the OpenAI API.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

import pytest

from app.routers import pipeline as pipeline_router
from app.services import image_briefs, openai_image_client
from app.services.image_briefs import (
    MAX_SECTION_IMAGES,
    SECTION_ROLE_PREFIX,
    GeneratedImage,
    build_section_briefs,
    content_section_headings,
)

RUN_UUID = uuid.UUID("22222222-2222-2222-2222-222222222222")

_CONTENT = """# Commercial Loans in Melbourne

## Fund the next stage of your business
Hero lead-in.

## Contents
- a
- b

## How our commercial lending process works
Steps.

## Equipment and asset finance
Detail.

## Property development finance
Detail.

## Frequently asked questions
Q and A.

## Ready to talk? Book a call
CTA.
"""


def test_headings_skip_the_hero_and_sections_that_never_carry_a_photo() -> None:
    assert content_section_headings(_CONTENT) == (
        "How our commercial lending process works",
        "Equipment and asset finance",
    )


def test_headings_fall_back_to_h3_and_handle_empty_copy() -> None:
    assert content_section_headings("### Hero\n### Our team approach\n") == ("Our team approach",)
    assert content_section_headings("") == ()
    assert content_section_headings("no headings at all") == ()


def test_section_briefs_are_capped_named_and_sized_validly() -> None:
    briefs = build_section_briefs("", ("A", "B", "C"), subject_context="commercial lending")
    assert len(briefs) == MAX_SECTION_IMAGES
    assert [b.role for b in briefs] == [f"{SECTION_ROLE_PREFIX}A", f"{SECTION_ROLE_PREFIX}B"]
    assert all(b.size in openai_image_client.VALID_SIZES for b in briefs)
    assert "commercial lending" in briefs[0].prompt


@dataclass
class _FakeSession:
    added: list[Any] = field(default_factory=list)
    committed: bool = False

    async def get(self, _model: Any, _ident: Any) -> Any:
        return object()

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def commit(self) -> None:
        self.committed = True

    async def __aenter__(self) -> "_FakeSession":
        return self

    async def __aexit__(self, *_exc: Any) -> bool:
        return False


async def _async(value):
    return value


async def _fake_generate_all(briefs, _session):
    return (
        tuple(GeneratedImage(role=b.role, size=b.size, prompt=b.prompt, url=f"https://x/{i}.png") for i, b in enumerate(briefs)),
        (),
    )


async def _never_called(*_a, **_kw):
    raise AssertionError("generate_all should not have been called")


def _configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(openai_image_client, "is_configured", lambda: True)
    monkeypatch.setattr(pipeline_router, "_next_version", lambda *_a, **_kw: _async(7))


@pytest.mark.asyncio
async def test_generates_and_stores_apart_from_the_page_level_images(monkeypatch: pytest.MonkeyPatch) -> None:
    _configured(monkeypatch)
    monkeypatch.setattr(image_briefs, "generate_all", _fake_generate_all)
    session = _FakeSession()
    value = {"design_md": "# d", "generated_images": [{"role": "hero", "size": "1536x1024", "prompt": "p", "url": "https://x/h.png"}]}

    result = await pipeline_router._ensure_section_images(
        lambda: session, RUN_UUID, value, "pillar_page", {"improved_page_content": _CONTENT}, {}
    )

    assert result["generated_images"] == value["generated_images"]
    assert [item["role"] for item in result["section_images"]] == [
        f"{SECTION_ROLE_PREFIX}How our commercial lending process works",
        f"{SECTION_ROLE_PREFIX}Equipment and asset finance",
    ]
    assert session.committed and len(session.added) == 1
    roles = [image.role for image in pipeline_router._all_generated_images(result)]
    assert roles[0] == "hero" and len(roles) == 3


@pytest.mark.asyncio
async def test_only_the_design_language_stage_pays_for_section_images(monkeypatch: pytest.MonkeyPatch) -> None:
    _configured(monkeypatch)
    monkeypatch.setattr(image_briefs, "generate_all", _never_called)
    value = {"design_md": "# d"}
    result = await pipeline_router._ensure_section_images(
        lambda: _FakeSession(), RUN_UUID, value, "cro", {"improved_page_content": _CONTENT}, {}
    )
    assert result is value


@pytest.mark.asyncio
async def test_a_rerun_with_the_same_sections_reuses_what_it_paid_for(monkeypatch: pytest.MonkeyPatch) -> None:
    _configured(monkeypatch)
    monkeypatch.setattr(image_briefs, "generate_all", _never_called)
    value = {
        "section_images": [
            {"role": f"{SECTION_ROLE_PREFIX}Equipment and asset finance", "size": "1024x1024", "prompt": "p", "url": "https://x/1.png"}
        ]
    }
    result = await pipeline_router._ensure_section_images(
        lambda: _FakeSession(), RUN_UUID, value, "pillar_page", {"improved_page_content": _CONTENT}, {}
    )
    assert result is value


@pytest.mark.asyncio
async def test_new_sections_regenerate(monkeypatch: pytest.MonkeyPatch) -> None:
    _configured(monkeypatch)
    monkeypatch.setattr(image_briefs, "generate_all", _fake_generate_all)
    value = {
        "section_images": [
            {"role": f"{SECTION_ROLE_PREFIX}A section that is gone", "size": "1024x1024", "prompt": "p", "url": "https://x/old.png"}
        ]
    }
    result = await pipeline_router._ensure_section_images(
        lambda: _FakeSession(), RUN_UUID, value, "pillar_page", {"improved_page_content": _CONTENT}, {}
    )
    assert all("gone" not in item["role"] for item in result["section_images"])


@pytest.mark.asyncio
async def test_unconfigured_or_no_sections_is_a_clean_no_op(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(openai_image_client, "is_configured", lambda: False)
    monkeypatch.setattr(pipeline_router.runway_image_client, "is_configured", lambda: False)
    monkeypatch.setattr(image_briefs, "generate_all", _never_called)
    value: dict = {}
    assert await pipeline_router._ensure_section_images(
        lambda: _FakeSession(), RUN_UUID, value, "pillar_page", {"improved_page_content": _CONTENT}, {}
    ) is value
    _configured(monkeypatch)
    assert await pipeline_router._ensure_section_images(
        lambda: _FakeSession(), RUN_UUID, value, "pillar_page", {"improved_page_content": "plain text"}, {}
    ) is value
