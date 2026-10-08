"""Continuing a draft the output cap cut off.

The join is the part that cannot be left to the prompt: a model told not to restate itself still
sometimes restates the line it was inside. These tests pin the trim, the streaming joiner, the
SSE `done` event the card branches on, and that Continue reuses already-paid prepass output.
"""
from __future__ import annotations

from typing import Any

import pytest

from app.routers import pipeline as pipeline_router
from app.services.generation import (
    CONTINUATION_MIN_OVERLAP,
    ContinuationJoiner,
    build_continuation_messages,
    join_continuation,
)

RUN_ID = "11111111-1111-1111-1111-111111111111"


def test_a_clean_append_is_returned_untouched() -> None:
    assert join_continuation("Hello world. ", "The next sentence.") == "The next sentence."


def test_a_restated_tail_is_trimmed() -> None:
    overlap = "the approval lands in two days.\n"
    assert len(overlap) >= CONTINUATION_MIN_OVERLAP
    partial = "Lead with the timeline: " + overlap
    continuation = overlap + "Then the CTA."
    assert join_continuation(partial, continuation) == "Then the CTA."


def test_the_longest_overlap_wins() -> None:
    # The short run "the next heading.\n" is also a suffix/prefix; taking it first would leave
    # the rest of the restated sentence in place. Longest-first is what makes the join invisible.
    tail = "Please finish the next heading.\n"
    assert len(tail) >= CONTINUATION_MIN_OVERLAP
    partial = "Now: " + tail
    continuation = tail + "After that, the proof."
    assert join_continuation(partial, continuation) == "After that, the proof."


def test_a_short_shared_run_is_ordinary_english_not_a_restatement() -> None:
    # "\n\n## " is how two markdown documents meet. Trimming it would eat a real heading.
    assert join_continuation("Intro ends here.\n\n## ", "## Proof\n") == "## Proof\n"


def test_a_preamble_line_is_dropped() -> None:
    partial = "The offer is $4,500.\n"
    continuation = "Continuing from where the document left off:\nThe next section is proof."
    assert join_continuation(partial, continuation) == "The next section is proof."


def test_a_heading_that_happens_to_say_continuing_is_kept() -> None:
    partial = "See also:\n"
    continuation = "## Continuing education\nA real section."
    assert join_continuation(partial, continuation) == continuation


def test_a_preamble_that_is_the_whole_reply_is_kept() -> None:
    assert join_continuation("Draft so far.", "Continuing from where I left off.") == (
        "Continuing from where I left off."
    )


def test_the_joiner_holds_the_head_until_it_can_decide() -> None:
    partial = "xxxx" * 20  # 80 chars, well under the 600-char window
    joiner = ContinuationJoiner(partial)
    assert joiner.feed("hello ") == ""
    assert joiner.feed("world") == ""
    assert joiner.flush() == "hello world"
    assert joiner.flush() == ""


def test_the_joiner_trims_once_the_window_fills() -> None:
    tail = "Z" * 80
    partial = "prefix-" + tail
    joiner = ContinuationJoiner(partial)
    # 600 chars of restated tail, then new text — enough to decide without waiting for the end.
    first = joiner.feed(tail + ("n" * 520) + "NEW")
    assert first.endswith("NEW")
    assert not first.startswith("Z")
    assert joiner.feed("+more") == "+more"


def test_continuation_messages_replay_the_draft_then_instruct() -> None:
    turns = build_continuation_messages("the draft so far", "cro")
    assert [t["role"] for t in turns] == ["assistant", "user"]
    assert turns[0]["content"] == "the draft so far"
    user = turns[1]["content"]
    assert "the draft so far" in user
    assert "cro" in user
    assert "Do not write a preamble" in user


def test_done_event_is_truncated_only_on_max_tokens() -> None:
    cut = pipeline_router._done_event("max_tokens")
    assert cut == {"type": "done", "stop_reason": "max_tokens", "truncated": True}
    finished = pipeline_router._done_event("end_turn")
    assert finished == {"type": "done", "stop_reason": "end_turn", "truncated": False}
    unknown = pipeline_router._done_event(None)
    assert unknown == {"type": "done", "stop_reason": None, "truncated": False}


def _sse_payloads(frames: list[str]) -> list[dict[str, Any]]:
    import json

    out: list[dict[str, Any]] = []
    for frame in frames:
        for line in frame.split("\n"):
            if line.startswith("data:"):
                out.append(json.loads(line[5:].strip()))
    return out


async def _patched_stream(
    monkeypatch: pytest.MonkeyPatch,
    *,
    stop_reason: str | None = "end_turn",
    continue_from: str | None = None,
    prefilled: dict[str, str] | None = None,
    answers: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    captured: dict[str, Any] = {}

    async def passthrough(_asset_id: str, incoming: dict[str, str], *_a: Any, **_k: Any) -> Any:
        captured.setdefault("answers_seen", []).append(dict(incoming))
        return incoming, None

    async def none(*_a: Any, **_k: Any) -> None:
        return None

    async def fake_generate(_asset_id: str, _answers: dict[str, str], _phase: str, **kwargs: Any):
        captured["generate"] = True
        on_complete = kwargs.get("on_complete")
        yield "hello"
        if on_complete:
            on_complete(stop_reason)

    async def fake_continue(_asset_id: str, _answers: dict[str, str], partial: str, _phase: str, **kwargs: Any):
        captured["continue_partial"] = partial
        on_complete = kwargs.get("on_complete")
        yield " more"
        if on_complete:
            on_complete(stop_reason)

    monkeypatch.setattr(pipeline_router, "_run_competitor_prepass", passthrough)
    monkeypatch.setattr(pipeline_router, "_run_social_data_prepass", passthrough)
    monkeypatch.setattr(pipeline_router, "_read_selected_service_pages", passthrough)
    monkeypatch.setattr(pipeline_router, "resolve_page_design", none)
    monkeypatch.setattr(pipeline_router, "resolve_page_template", none)
    monkeypatch.setattr(pipeline_router, "_run_industry", none)
    monkeypatch.setattr(pipeline_router, "generate_stage_stream", fake_generate)
    monkeypatch.setattr(pipeline_router, "generate_continuation_stream", fake_continue)

    frames = [
        e
        async for e in pipeline_router._generation_sse_stream(
            "icp",
            answers or {},
            {},
            "phase1",
            None,
            RUN_ID,
            continue_from=continue_from,
            prefilled=prefilled,
        )
    ]
    captured["frames"] = frames
    events = _sse_payloads(frames)
    events.append({"_captured": captured})  # type: ignore[dict-item]
    return events


@pytest.mark.asyncio
async def test_a_finished_stream_reports_not_truncated(monkeypatch: pytest.MonkeyPatch) -> None:
    events = await _patched_stream(monkeypatch, stop_reason="end_turn")
    done = [e for e in events if e.get("type") == "done"]
    assert done == [{"type": "done", "stop_reason": "end_turn", "truncated": False}]


@pytest.mark.asyncio
async def test_a_capped_stream_reports_truncated(monkeypatch: pytest.MonkeyPatch) -> None:
    events = await _patched_stream(monkeypatch, stop_reason="max_tokens")
    done = [e for e in events if e.get("type") == "done"]
    assert done == [{"type": "done", "stop_reason": "max_tokens", "truncated": True}]


@pytest.mark.asyncio
async def test_continue_from_calls_the_continuation_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    events = await _patched_stream(monkeypatch, continue_from="the draft so far")
    texts = [e["text"] for e in events if e.get("type") == "delta"]
    assert texts == [" more"]
    captured = events[-1]["_captured"]
    assert captured["continue_partial"] == "the draft so far"
    assert "generate" not in captured


@pytest.mark.asyncio
async def test_prefilled_answers_are_merged_before_the_prepasses_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events = await _patched_stream(
        monkeypatch,
        continue_from="partial",
        answers={"topic": "Meta Ads"},
        prefilled={"competitor_list": "Rival One"},
    )
    captured = events[-1]["_captured"]
    first_seen = captured["answers_seen"][0]
    assert first_seen["topic"] == "Meta Ads"
    assert first_seen["competitor_list"] == "Rival One"
