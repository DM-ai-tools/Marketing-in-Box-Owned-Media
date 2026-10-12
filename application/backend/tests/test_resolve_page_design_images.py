"""Tests for the OpenAI image wiring inside `resolve_page_design`.

Nothing here touches Postgres — this backend's tests do not have it (see
`test_standalone_runs.py`), so `_ensure_generated_images` is tested at the seam it actually takes
a parameter for: `session_factory` is a plain callable, faked here rather than patched globally.

The bug this file exists to prevent: an earlier draft of this wiring called
`_ensure_generated_images` with a value dict containing only `image_briefs`, dropping `design_md`,
`theme_brief` and every other field the *same* run's stored context entry already carried. That
version would have persisted a "latest" `brand_design_tokens` entry with no design_md in it at
all, silently breaking every stage after the first for that run. `test_persisting_keeps_every_
pre_existing_field` is the regression guard.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

import pytest

from app.routers import pipeline as pipeline_router
from app.services import image_briefs, openai_image_client
from app.services.image_briefs import GeneratedImage, ImageBrief

RUN_UUID = uuid.UUID("11111111-1111-1111-1111-111111111111")


# ----------------------------------------------------------------------------------------------
# Reconstructing specs from a stored context entry
# ----------------------------------------------------------------------------------------------


def test_briefs_from_stored_reads_the_current_dict_shape():
    briefs = pipeline_router._briefs_from_stored(
        [{"role": "hero", "size": "1536x1024", "prompt": "a hero image"}]
    )
    assert briefs == (ImageBrief(role="hero", size="1536x1024", prompt="a hero image"),)


def test_briefs_from_stored_reads_the_legacy_bare_string_shape():
    """Entries written before this shape existed hold bare prompt strings. Read with OpenAI's
    default size rather than discarded — the same rule `_page_design_input` already applies to
    a pre-DESIGN.md `content`-only entry."""
    briefs = pipeline_router._briefs_from_stored(["a hero image, no size known"])
    assert briefs == (
        ImageBrief(role="", size=openai_image_client.DEFAULT_SIZE, prompt="a hero image, no size known"),
    )


def test_generated_images_from_stored_skips_an_entry_with_no_url():
    images = pipeline_router._generated_images_from_stored(
        [{"role": "hero", "size": "1536x1024", "prompt": "p", "url": "https://x/hero.png"}, {"role": "proof"}]
    )
    assert images == (GeneratedImage(role="hero", size="1536x1024", prompt="p", url="https://x/hero.png"),)


# ----------------------------------------------------------------------------------------------
# _ensure_generated_images
# ----------------------------------------------------------------------------------------------


@dataclass
class _FakeSession:
    run_exists: bool = True
    added: list[Any] = field(default_factory=list)
    committed: bool = False

    async def get(self, _model: Any, _ident: Any) -> Any:
        return object() if self.run_exists else None

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def commit(self) -> None:
        self.committed = True

    async def __aenter__(self) -> "_FakeSession":
        return self

    async def __aexit__(self, *_exc: Any) -> bool:
        return False


def _session_factory(session: _FakeSession):
    return lambda: session


async def _never_called(*_a, **_kw):
    raise AssertionError("generate_all should not have been called")


@pytest.mark.asyncio
async def test_skips_a_stage_that_is_not_page_replica(monkeypatch):
    monkeypatch.setattr(image_briefs, "generate_all", _never_called)
    session = _FakeSession()

    result = await pipeline_router._ensure_generated_images(
        _session_factory(session), RUN_UUID, {"image_briefs": [{"role": "hero", "size": "x", "prompt": "p"}]}, "icp"
    )

    assert result == {"image_briefs": [{"role": "hero", "size": "x", "prompt": "p"}]}
    assert session.added == []


@pytest.mark.asyncio
async def test_skips_when_images_already_generated(monkeypatch):
    monkeypatch.setattr(image_briefs, "generate_all", _never_called)
    session = _FakeSession()
    value = {"generated_images": [{"role": "hero", "size": "x", "prompt": "p", "url": "https://x/hero.png"}]}

    result = await pipeline_router._ensure_generated_images(_session_factory(session), RUN_UUID, value, "cro")

    assert result is value
    assert session.added == []


@pytest.mark.asyncio
async def test_skips_when_there_are_no_briefs(monkeypatch):
    monkeypatch.setattr(image_briefs, "generate_all", _never_called)
    session = _FakeSession()

    result = await pipeline_router._ensure_generated_images(_session_factory(session), RUN_UUID, {}, "cro")

    assert result == {}
    assert session.added == []


@pytest.mark.asyncio
async def test_persisting_keeps_every_pre_existing_field(monkeypatch):
    """The regression this file exists to prevent — see the module docstring."""
    async def fake_generate_all(briefs, _session):
        return (
            tuple(GeneratedImage(role=b.role, size=b.size, prompt=b.prompt, url=f"https://x/{b.role}.png") for b in briefs),
            (),
        )

    # Explicit, not relying on a real OPENAI_API_KEY being present in .env — `_ensure_generated_
    # images` skips generation entirely when this is False, which would make this test pass for
    # the wrong reason (never reaching `generate_all` at all) on a machine with no key configured.
    monkeypatch.setattr(openai_image_client, "is_configured", lambda: True)
    monkeypatch.setattr(image_briefs, "generate_all", fake_generate_all)
    monkeypatch.setattr(pipeline_router, "_next_version", lambda *_a, **_kw: _async(4))
    session = _FakeSession()

    stored_value = {
        "design_md": "# the real design system",
        "theme_brief": "# theme",
        "source_url": "https://client.example/",
        "capture_version": 3,
        "image_briefs": [{"role": "hero", "size": "1536x1024", "prompt": "a hero image"}],
    }

    result = await pipeline_router._ensure_generated_images(
        _session_factory(session), RUN_UUID, stored_value, "cro"
    )

    assert result["design_md"] == "# the real design system"
    assert result["theme_brief"] == "# theme"
    assert result["source_url"] == "https://client.example/"
    assert result["capture_version"] == 3
    assert result["generated_images"] == [
        {"role": "hero", "size": "1536x1024", "prompt": "a hero image", "url": "https://x/hero.png"}
    ]
    assert len(session.added) == 1
    assert session.added[0].value == result
    assert session.committed


@pytest.mark.asyncio
async def test_nothing_is_persisted_when_every_generation_fails(monkeypatch):
    async def fake_generate_all(_briefs, _session):
        return (), ("The hero image could not be generated: out of credits",)

    monkeypatch.setattr(openai_image_client, "is_configured", lambda: True)
    monkeypatch.setattr(image_briefs, "generate_all", fake_generate_all)
    session = _FakeSession()
    stored_value = {"design_md": "x", "image_briefs": [{"role": "hero", "size": "1536x1024", "prompt": "p"}]}

    result = await pipeline_router._ensure_generated_images(
        _session_factory(session), RUN_UUID, stored_value, "pillar_page"
    )

    assert result == stored_value
    assert session.added == []


async def _async(value):
    return value


# ----------------------------------------------------------------------------------------------
# Failures reach the operator: `image_notes`
# ----------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_total_failure_is_reported_and_still_not_cached(monkeypatch):
    async def fake_generate_all(_briefs, _session):
        return (), ("The hero image could not be generated: out of credits",)

    monkeypatch.setattr(openai_image_client, "is_configured", lambda: True)
    monkeypatch.setattr(image_briefs, "generate_all", fake_generate_all)
    session = _FakeSession()
    stored_value = {"design_md": "x", "image_briefs": [{"role": "hero", "size": "1536x1024", "prompt": "p"}]}
    notes: list[str] = []

    result = await pipeline_router._ensure_generated_images(
        _session_factory(session), RUN_UUID, stored_value, "pillar_page", notes
    )

    # Not cached, so the next qualifying stage retries...
    assert result == stored_value
    assert session.added == [] and not session.committed
    # ...and the operator is told, both what failed and that it will be tried again.
    assert notes[0] == "The hero image could not be generated: out of credits"
    assert any("tried again" in n for n in notes[1:])


@pytest.mark.asyncio
async def test_one_failed_brief_is_reported_and_the_others_are_kept(monkeypatch):
    async def fake_generate_all(_briefs, _session):
        return (
            (GeneratedImage(role="hero", size="1536x1024", prompt="p", url="https://api.example.com/media/a"),),
            ("The proof image could not be generated: content policy",),
        )

    monkeypatch.setattr(openai_image_client, "is_configured", lambda: True)
    monkeypatch.setattr(image_briefs, "generate_all", fake_generate_all)
    monkeypatch.setattr(pipeline_router, "_next_version", lambda *_a, **_kw: _async(2))
    session = _FakeSession()
    stored_value = {
        "design_md": "x",
        "image_briefs": [
            {"role": "hero", "size": "1536x1024", "prompt": "p"},
            {"role": "proof", "size": "1024x1024", "prompt": "q"},
        ],
    }
    notes: list[str] = []

    result = await pipeline_router._ensure_generated_images(
        _session_factory(session), RUN_UUID, stored_value, "pillar_page", notes
    )

    assert [g["role"] for g in result["generated_images"]] == ["hero"]
    assert session.committed
    assert notes == ["The proof image could not be generated: content policy"]
