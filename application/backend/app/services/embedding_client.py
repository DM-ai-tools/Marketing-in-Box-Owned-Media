"""The one place this backend turns text into embedding vectors.

OpenAI's `text-embedding-3-small`, over the same `OPENAI_API_KEY` the image client already uses.
Anthropic has no embeddings endpoint, and a second provider key for one call shape would be one
more secret to rotate for no gain.

Same shape as the other wrappers (`openai_image_client.py`, `firecrawl_client.py`): a cached client,
`is_configured()`, one exception type, and a log line per call so the spend is visible. It returns
plain lists of floats and knows nothing about chunks, runs or the database; `vector_context.py`
owns all of that.

The model and its dimension are constants, not settings. The `context_chunks.embedding` column is
`vector(1536)`, and every stored chunk records the model it was embedded with. Changing the model
is a migration plus a re-index, never an environment variable.

Testing
-------
Tests must not reach the live API. Patch `embed` (callers) or pass a transport to `_client`
through `reset_client` (this module's own mapping). See `tests/test_vector_context.py`.

Docs
----
* Embeddings guide   https://platform.openai.com/docs/guides/embeddings
* API reference      https://platform.openai.com/docs/api-reference/embeddings/create
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache

import httpx

logger = logging.getLogger(__name__)

API_KEY_ENV = "OPENAI_API_KEY"
#: Set to `off` to stop every vector feature without removing the key (the image client keeps
#: working). Read on every call, so it takes effect without a restart.
SWITCH_ENV = "VECTOR_CONTEXT"
BASE_URL = "https://api.openai.com/v1"
_TIMEOUT_SECONDS = 30.0

MODEL = "text-embedding-3-small"
DIMENSIONS = 1536

#: OpenAI accepts up to 2048 inputs per request; a stage's whole intake is far below that, and a
#: re-index of one run is a few hundred chunks. Batches keep one bad request from costing the rest.
_BATCH = 256
#: The model reads at most 8191 tokens per input. A chunk is a few thousand characters at most, so
#: this only guards against a caller handing over a whole document by mistake.
_MAX_INPUT_CHARS = 24_000


class EmbeddingError(Exception):
    """An embedding request failed or returned an unusable response."""


class EmbeddingNotConfigured(EmbeddingError):
    """No key, or `VECTOR_CONTEXT=off`. Callers treat this as "skip me", not a failure."""


def is_configured() -> bool:
    if os.environ.get(SWITCH_ENV, "").strip().lower() in {"0", "off", "false", "no"}:
        return False
    return bool(os.environ.get(API_KEY_ENV, "").strip())


@lru_cache(maxsize=1)
def _client() -> httpx.AsyncClient:
    """The shared async client. Cached so the connection pool is reused across requests."""
    key = os.environ.get(API_KEY_ENV, "").strip()
    if not key:
        raise EmbeddingNotConfigured(
            f"{API_KEY_ENV} is not set, so context search is unavailable. Add it to the backend .env."
        )
    return httpx.AsyncClient(
        base_url=BASE_URL,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        timeout=_TIMEOUT_SECONDS,
    )


def reset_client() -> None:
    """Drop the cached client. For tests and for a key rotated without a restart."""
    _client.cache_clear()


def _fail(exc: Exception) -> EmbeddingError:
    if isinstance(exc, httpx.TimeoutException):
        detail = f"OpenAI did not answer within {int(_TIMEOUT_SECONDS)}s."
    elif isinstance(exc, httpx.ConnectError):
        detail = "Could not reach OpenAI from this server."
    elif isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        try:
            body = exc.response.json()
            message = body.get("error", {}).get("message") if isinstance(body, dict) else None
        except ValueError:
            message = None
        message = str(message or exc.response.text)[:300]
        if status == 401:
            detail = f"OpenAI rejected the key ({API_KEY_ENV}): {message}"
        elif status == 429:
            detail = f"OpenAI rate-limited or out of quota: {message}"
        else:
            detail = f"OpenAI answered HTTP {status}: {message}"
    else:
        detail = str(exc) or exc.__class__.__name__
    return EmbeddingError(f"Embedding failed: {detail}")


async def embed(texts: list[str]) -> list[list[float]]:
    """One vector per input, in input order. Empty input, empty output, no request.

    Raises `EmbeddingNotConfigured` when there is no key (or the switch is off) and
    `EmbeddingError` for anything the API refused. Retries are not added here: an embedding that
    fails leaves the caller on the path it had before vectors existed, which is a working path.
    """
    if not texts:
        return []
    if not is_configured():
        raise EmbeddingNotConfigured(
            f"Context search is off: {API_KEY_ENV} is unset or {SWITCH_ENV} is off."
        )

    vectors: list[list[float]] = []
    tokens = 0
    for start in range(0, len(texts), _BATCH):
        batch = [(t or " ")[:_MAX_INPUT_CHARS] for t in texts[start : start + _BATCH]]
        try:
            response = await _client().post("/embeddings", json={"model": MODEL, "input": batch})
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise _fail(exc) from exc

        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, list) or len(data) != len(batch):
            raise EmbeddingError(
                f"Embedding failed: expected {len(batch)} vectors, got "
                f"{len(data) if isinstance(data, list) else 'none'}."
            )
        for item in sorted(data, key=lambda d: d.get("index", 0)):
            vector = item.get("embedding")
            if not isinstance(vector, list) or len(vector) != DIMENSIONS:
                raise EmbeddingError(f"Embedding failed: a vector was not {DIMENSIONS} numbers long.")
            vectors.append([float(x) for x in vector])
        usage = body.get("usage") if isinstance(body, dict) else None
        if isinstance(usage, dict):
            tokens += int(usage.get("total_tokens") or 0)

    logger.info("Embedded %d texts with %s (%d tokens)", len(texts), MODEL, tokens)
    return vectors
