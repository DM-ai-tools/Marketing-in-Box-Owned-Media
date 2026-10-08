"""One generation per stage at a time.

Nothing used to stop the same stage being generated twice at once. Two clicks on Retry, a
double-submitted Refine, a reopened tab whose store still held a streaming card — each opened a
second `messages.stream` against the same asset, and both were billed. The second one also won:
deltas from two streams interleave into the same card, so what the operator reviewed was two
documents spliced together at whatever rate the two connections happened to arrive.

The client guards this too (`generationRequests` in `pipeline/pipelineStore.ts` refuses to start a
second stream for a card that already has one). That guard covers the common case and this one
covers the rest: a second browser window, a stale client, a reload that left the first request
running server-side. A guard on only one side of the wire is a guard that the other side's bugs
walk straight through.

**A dict in module state, deliberately.** This backend runs as a single instance, which is the
whole reason this can be so small — `ANTHROPIC` calls are billed per process, there is no second
worker to coordinate with, and asyncio runs one task at a time between awaits, so `acquire` and
`release` (both synchronous, with no await inside) cannot interleave. If this service is ever
replicated, this module is the one place that has to move to Redis, and `active_count` is what a
test would then assert against.

Keys carry the run (or the chat session) as well as the asset, so two different clients working on
two different runs of the same stage do not block each other — the thing being protected is one
document, not one prompt file.
"""

from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)

#: How long a held key survives without being released.
#:
#: Release happens in a `finally` inside the SSE generator, which covers a normal finish, a failure,
#: and the client disconnecting (Starlette cancels the generator). What it does not cover is a
#: response that is never consumed at all — the generator never starts, so its `finally` never runs.
#: Without an expiry that leaks a key and wedges one stage of one run until the process restarts,
#: which is a far worse failure than the duplicate request this module exists to prevent. The
#: window is generous because the thing being bounded is a real generation: the 128k stages stream
#: for several minutes.
_STALE_AFTER_SECONDS = 45 * 60

#: key -> monotonic time it was acquired.
_active: dict[str, float] = {}


class StreamBusy(Exception):
    """Raised by `acquire` when `key` is already generating. Reported as a 409, not a 500: the
    request is well-formed and the operator's answer is "the one you already started is still
    running", which is information rather than a fault."""

    def __init__(self, key: str, held_for_seconds: float) -> None:
        self.key = key
        self.held_for_seconds = held_for_seconds
        super().__init__(f"{key} has been generating for {held_for_seconds:.0f}s")


def key_for(kind: str, asset_id: str, phase: str, run_id: str | None, chat_session_id: str | None) -> str | None:
    """The lock key for one stream, or None when there is nothing safe to key on.

    `run_id` first, because it is what identifies the document being written; the chat session is
    the fallback for a brand-new chat whose first stage runs before any run row exists. With
    *neither* there is no way to tell one anonymous client's first call from another's, and a key of
    `"anon"` would make two unrelated operators' first stages block each other — so such a call is
    deliberately left unguarded rather than guarded wrongly.
    """
    owner = run_id or chat_session_id
    if not owner:
        return None
    return f"{kind}:{phase}:{owner}:{asset_id}"


def acquire(key: str | None) -> None:
    """Claim `key`, or raise `StreamBusy`. A None key is unguardable — see `key_for` — and is a
    no-op rather than an error."""
    if key is None:
        return
    held_since = _active.get(key)
    if held_since is not None:
        held_for = time.monotonic() - held_since
        if held_for < _STALE_AFTER_SECONDS:
            raise StreamBusy(key, held_for)
        # Past the window the holder is assumed gone (see `_STALE_AFTER_SECONDS`). Logged at
        # warning, because a real one of these means a generator's `finally` did not run.
        logger.warning("Stream lock %s was held for %.0fs with no release; taking it over", key, held_for)
    _active[key] = time.monotonic()


def release(key: str | None) -> None:
    """Give `key` back. Safe to call for a key that was never acquired, which is what lets every
    caller put this in a `finally` without first checking whether its `acquire` succeeded."""
    if key is None:
        return
    _active.pop(key, None)


def is_active(key: str | None) -> bool:
    return key is not None and key in _active


def active_count() -> int:
    """How many streams are held. For tests and for a future health probe — nothing in the request
    path reads it."""
    return len(_active)
