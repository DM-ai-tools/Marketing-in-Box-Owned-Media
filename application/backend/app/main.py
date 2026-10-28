"""FastAPI application entrypoint for the Marketing-in-a-Box orchestrator API.

Run with (Windows-safe, see the event-loop note below):
    python -m app --reload

This module intentionally stays thin: it wires up the FastAPI app, app-wide middleware/config,
and mounts routers. Business logic belongs in app/services/, and persistence belongs in
app/db/ (owned by a separate pass) — routers here should only ever call into the service layer.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import time
import warnings

from dotenv import load_dotenv

load_dotenv()

if sys.platform == "win32":
    # psycopg's async mode cannot run under Windows' ProactorEventLoop. The policy below is the
    # fix on uvicorn < 0.36; from 0.36 on, uvicorn hands `asyncio.run` an explicit `loop_factory`
    # (ProactorEventLoop on win32 unless it is spawning subprocesses), which ignores the policy
    # entirely — hence `app/__main__.py`, which builds the loop itself, and the fail-fast check in
    # `_verify_event_loop` below. Both the lookup and the call are deprecated as of Python 3.14, so
    # they sit inside the filter to keep that warning out of every startup log.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        _selector_policy = getattr(asyncio, "WindowsSelectorEventLoopPolicy", None)
        if _selector_policy is not None:
            asyncio.set_event_loop_policy(_selector_policy())

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.logging_config import configure_logging
from app.routers import anthropic, auth, buyer_language, chat_sessions, files, funnel_build, intel, media, page_variants, pipeline, summary, usage, vector_context

configure_logging()
logger = logging.getLogger(__name__)


def _verify_event_loop() -> None:
    """Refuse to serve on a loop psycopg can't use, instead of 500-ing every DB route.

    On Windows a ProactorEventLoop makes *every* database-backed endpoint fail at connect time
    (`psycopg.InterfaceError`) while `/health` and the docs keep answering 200 — which reads as
    "the backend is up, the feature is broken" (this is what silently emptied the chat-history
    sidebar). Fail at startup with the command that works instead.
    """
    if sys.platform != "win32":
        return
    loop_name = type(asyncio.get_running_loop()).__name__
    if "Proactor" not in loop_name:
        return
    raise RuntimeError(
        f"Started on {loop_name}, which psycopg's async mode cannot use, so every database route "
        "would fail with a 500. Launch the server with `python -m app --reload` (see "
        "app/__main__.py), or add --reload/--workers to your `uvicorn` command."
    )


SEED_TIMEOUT_SECONDS = 20


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    _verify_event_loop()
    # A production deployment with no BACKEND_PUBLIC_URL hands out generated image links nobody can
    # open. Logged, not fatal: everything else still works, and no host is invented to cover it.
    from app.services.media_storage import warn_if_public_base_url_missing

    warn_if_public_base_url_missing()
    await _seed_reference_data()
    yield


async def _seed_reference_data() -> None:
    """Upsert `asset_definitions` on boot, so a fresh database is usable without a manual step.

    Every stage's Save writes `run_stages` / `context_entries` / `approval_audit_log` rows that FK
    to `asset_definitions.asset_id`. On an unseeded database that surfaces only at the first Save,
    as `404 Unknown asset_id: 'icp'`, while `/health` and every chat-session route keep answering
    200 — which reads as a broken feature rather than a missing seed. The upsert is idempotent
    (see app/db/seed.py), so paying it once per boot is cheaper than the class of bug it removes.

    Deliberately non-fatal: if the database is unreachable at startup, the app should still come up
    and answer `/health` rather than crash-loop the deployment. The routes that need the DB will
    fail on their own terms, and the log line below says exactly how to apply the seed by hand.

    Also deliberately time-boxed. psycopg's own connect timeout is ~2 minutes, and nothing binds the
    port until this returns — long enough for a platform health check to fail the deploy over a
    database blip. `SEED_TIMEOUT_SECONDS` is far more than a reachable database needs and far less
    than a deploy window.
    """
    from app.db.seed import seed_asset_definitions

    try:
        count = await asyncio.wait_for(seed_asset_definitions(), timeout=SEED_TIMEOUT_SECONDS)
    except TimeoutError:
        logger.error(
            "asset_definitions seed timed out after %ss; the app will serve but every stage Save "
            "will 404 with 'Unknown asset_id' until it succeeds. Check DATABASE_URL, then apply it "
            "by hand with: python scripts/seed_asset_definitions.py",
            SEED_TIMEOUT_SECONDS,
        )
    except Exception:
        logger.exception(
            "asset_definitions seed failed at startup; the app will serve but every stage Save "
            "will 404 with 'Unknown asset_id' until it succeeds. Apply it by hand with: "
            "python scripts/seed_asset_definitions.py"
        )
    else:
        logger.info("asset_definitions seed applied (%d rows).", count)


app = FastAPI(
    title="Marketing-in-a-Box API",
    description=(
        "Orchestrator API for the Marketing-in-a-Box DAG pipeline: dependency-aware asset "
        "generation via the Anthropic Messages API, with a human-in-the-loop review gate."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

# Dev-only CORS: the Vite dev server proxies /api/* to this backend (no browser CORS
# involved in that path), but this is kept so the frontend can also be pointed at the API
# directly (e.g. `vite preview`, or a different dev port) without a proxy in front of it.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:4173",
        "http://127.0.0.1:4173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class HealthResponse(BaseModel):
    status: str
    service: str


@app.get("/health", response_model=HealthResponse, tags=["system"])
async def health_check() -> HealthResponse:
    """Liveness/readiness probe. Does not touch the database or Redis — those get their own
    dedicated checks once app/db/ and the Celery worker wiring land."""
    return HealthResponse(status="ok", service="marketing-in-a-box-backend")


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Log every API hit — method, path, status, and duration — for bug/traffic tracking."""
    start = time.perf_counter()
    response = await call_next(request)
    duration_ms = (time.perf_counter() - start) * 1000
    logger.info(
        "%s %s -> %s (%.1fms)",
        request.method,
        request.url.path,
        response.status_code,
        duration_ms,
    )
    return response


app.include_router(auth.router, prefix="/auth", tags=["auth"])
app.include_router(anthropic.router, prefix="/test/anthropic", tags=["anthropic-test"])
app.include_router(pipeline.router, prefix="/pipeline", tags=["pipeline"])
# The deployable funnel: its own module, same `/pipeline/runs/{run_id}/…` space.
app.include_router(funnel_build.router, prefix="/pipeline", tags=["funnel-build"])
# CRO's buyer-language questions, suggested from search data and the landing page.
app.include_router(buyer_language.router, prefix="/pipeline", tags=["buyer-language"])
# The reader's Summary tab: a deliverable's content as a few checked points.
app.include_router(summary.router, prefix="/pipeline", tags=["summary"])
# The clip button on file questions: an uploaded file's text, for the input box.
app.include_router(files.router, prefix="/pipeline", tags=["files"])
# Pillar Page variants: the comparison card's measured numbers.
app.include_router(page_variants.router, prefix="/pipeline", tags=["page-variants"])
# Context search (pgvector): suggested intake answers and the index behind them.
app.include_router(vector_context.router, prefix="/pipeline", tags=["context-search"])
app.include_router(chat_sessions.router, prefix="/chat-sessions", tags=["chat-sessions"])
app.include_router(usage.router, prefix="/usage", tags=["usage"])
# Market intelligence over Context.dev (brand records, web search). Every call in there goes
# through app/services/context_dev.py, the one module holding the API key.
app.include_router(intel.router, prefix="/intel", tags=["intel"])
# OpenAI-generated brand images, served from Postgres (`MediaAsset`) rather than local disk —
# this app deploys to Railway, where local disk is the container's own ephemeral disk. See
# app/services/media_storage.py.
app.include_router(media.router, prefix="/media", tags=["media"])

# Feature routers are mounted here as they land, e.g.:
#   from app.routers import runs
#   app.include_router(runs.router, prefix="/runs", tags=["runs"])
