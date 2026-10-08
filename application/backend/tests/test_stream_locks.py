"""The in-process generation lock — one stream per (run, stage, kind).

A second generate/continue against the same document used to open a second billed stream and
interleave two drafts. The client store now refuses that too; these tests pin the server half,
which is what a second tab or a stale client still has to be told.
"""
from __future__ import annotations

import time

import pytest
from fastapi import HTTPException

from app.routers import pipeline as pipeline_router
from app.services import stream_locks


@pytest.fixture(autouse=True)
def _empty_lock_table():
    stream_locks._active.clear()
    yield
    stream_locks._active.clear()


def test_acquire_then_release_lets_the_next_caller_in() -> None:
    key = stream_locks.key_for("generate", "cro", "phase1", "run-1", None)
    stream_locks.acquire(key)
    assert stream_locks.is_active(key)
    stream_locks.release(key)
    stream_locks.acquire(key)
    stream_locks.release(key)


def test_a_second_acquire_is_busy() -> None:
    key = stream_locks.key_for("generate", "cro", "phase1", "run-1", None)
    stream_locks.acquire(key)
    with pytest.raises(stream_locks.StreamBusy) as raised:
        stream_locks.acquire(key)
    assert raised.value.key == key
    assert raised.value.held_for_seconds >= 0


def test_two_runs_of_the_same_stage_do_not_block_each_other() -> None:
    a = stream_locks.key_for("generate", "cro", "phase1", "run-a", None)
    b = stream_locks.key_for("generate", "cro", "phase1", "run-b", None)
    assert a != b
    stream_locks.acquire(a)
    stream_locks.acquire(b)
    assert stream_locks.active_count() == 2


def test_refine_and_generate_are_separate_keys() -> None:
    gen = stream_locks.key_for("generate", "cro", "phase1", "run-1", None)
    ref = stream_locks.key_for("refine", "cro", "phase1", "run-1", None)
    assert gen != ref
    stream_locks.acquire(gen)
    stream_locks.acquire(ref)


def test_no_owner_is_left_unguarded_rather_than_keyed_as_anon() -> None:
    assert stream_locks.key_for("generate", "cro", "phase1", None, None) is None
    stream_locks.acquire(None)
    stream_locks.acquire(None)
    assert stream_locks.active_count() == 0


def test_release_of_an_unknown_key_is_a_no_op() -> None:
    stream_locks.release("generate:phase1:run-1:missing")
    stream_locks.release(None)


def test_a_stale_lock_is_taken_over(monkeypatch: pytest.MonkeyPatch) -> None:
    key = stream_locks.key_for("generate", "cro", "phase1", "run-1", None)
    stream_locks.acquire(key)
    stream_locks._active[key] = time.monotonic() - stream_locks._STALE_AFTER_SECONDS - 1
    stream_locks.acquire(key)
    assert stream_locks.is_active(key)


def test_claim_stream_is_a_409_not_a_500() -> None:
    payload = pipeline_router.GenerateStageRequest(answers={}, phase="phase1", run_id="run-1")
    key = pipeline_router._claim_stream("generate", "icp", payload)
    with pytest.raises(HTTPException) as raised:
        pipeline_router._claim_stream("generate", "icp", payload)
    assert raised.value.status_code == 409
    assert "already generating" in str(raised.value.detail)
    stream_locks.release(key)


@pytest.mark.asyncio
async def test_the_lock_is_released_when_the_stream_ends() -> None:
    payload = pipeline_router.GenerateStageRequest(answers={}, phase="phase1", run_id="run-2")
    key = pipeline_router._claim_stream("generate", "icp", payload)
    assert stream_locks.is_active(key)

    async def frames():
        yield "data: {\"type\": \"delta\"}\n\n"

    out = [frame async for frame in pipeline_router._released_on_finish(key, frames())]
    assert out
    assert not stream_locks.is_active(key)


@pytest.mark.asyncio
async def test_the_lock_is_released_when_the_stream_raises() -> None:
    payload = pipeline_router.GenerateStageRequest(answers={}, phase="phase1", run_id="run-3")
    key = pipeline_router._claim_stream("generate", "icp", payload)

    async def boom():
        yield "data: {\"type\": \"delta\"}\n\n"
        raise RuntimeError("disconnected")

    with pytest.raises(RuntimeError):
        _ = [frame async for frame in pipeline_router._released_on_finish(key, boom())]
    assert not stream_locks.is_active(key)
