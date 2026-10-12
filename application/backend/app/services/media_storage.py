"""Postgres-backed storage for files this backend generates and needs to hand out a URL for.

Why this exists
----------------
Every image this pipeline has referenced before now came pre-hosted: Context.dev's screenshots,
and originally Runway's generated images, both arrived as a URL on someone else's domain. OpenAI's
image models do not host anything — `openai_image_client.generate_image` returns raw bytes — so
something has to give those bytes a URL before a stage prompt can reference them the way
`design_md.py`'s logo section and `generation.py`'s `_generated_images_block` both already assume:
a short, reproducible link, not a value the model has to carry around itself.

**A base64 data URI is deliberately not that fix.** `design_md.py`'s logo section documents
exactly this failure already: a real image's data URI runs to thousands of characters the model
would have to echo byte-perfectly into its own output, which is what makes a generated page hit
its output cap mid-blob or draw a substitute instead. Handing the model a URL it copies once,
instead of a value it has to reproduce, is the whole fix.

Postgres, not local disk
-------------------------
The first version of this module wrote to local disk and served it via `StaticFiles`. That broke
on deployment: this app runs on Railway, where local disk is the *container's* disk — ephemeral
across every redeploy and restart by default. A Railway Volume would fix persistence, but Railway's
own docs are explicit that a Volume "cannot be used with replicas" and is capped at one per
service, so it would not survive this backend ever running as more than one instance either.

Postgres is already this app's durable, already-provisioned, already-multi-instance-safe system of
record, so the bytes go there instead — see `MediaAsset` in `app/db/models.py` for the table (the
one deliberate exception to this schema's usual "store a reference, not the bytes" rule — that
docstring explains why an OpenAI image specifically can't follow the `.pptx`
generate-on-demand-and-stream pattern the rest of this codebase uses for binary output) and
`app/routers/media.py` for the route that serves it back.
"""

from __future__ import annotations

import logging
import os
import re
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import MediaAsset

logger = logging.getLogger(__name__)

PUBLIC_BASE_URL_ENV = "BACKEND_PUBLIC_URL"
#: Matches `app/__main__.py`'s own dev defaults (`DEFAULT_HOST`/`DEFAULT_PORT`) — not imported
#: from there, since that module is the process entrypoint, not something a service should depend
#: on; the two are kept in step by comment rather than by code. On a real deployment this must be
#: set explicitly (e.g. on Railway: `https://${{RAILWAY_PUBLIC_DOMAIN}}`) — the loopback default
#: below is unreachable from outside the container and would silently hand out dead links.
_DEFAULT_PUBLIC_BASE_URL = "http://127.0.0.1:8001"

MEDIA_URL_PREFIX = "/media"


def _public_base_url() -> str:
    return os.environ.get(PUBLIC_BASE_URL_ENV, "").strip() or _DEFAULT_PUBLIC_BASE_URL


def media_url(asset_id: uuid.UUID | str) -> str:
    """The absolute URL `GET /media/{id}` serves this asset at, on the *current* public base."""
    return f"{_public_base_url().rstrip('/')}{MEDIA_URL_PREFIX}/{asset_id}"


#: A media URL written while `BACKEND_PUBLIC_URL` was unset: the loopback default, or the same
#: thing typed as `localhost`. Only these two hosts on the dev port — a real public host is left as
#: stored, because it was correct when written and rewriting it would move a working link.
_LOOPBACK_MEDIA_URL = re.compile(
    r"^https?://(?:127\.0\.0\.1|localhost):8001" + re.escape(MEDIA_URL_PREFIX) + r"/([0-9a-fA-F-]{32,36})/?$"
)


def current_media_url(url: str) -> str:
    """`url` with a loopback media base swapped for the current public base, else unchanged.

    Image URLs are stored absolute on the run and copied into generated HTML. One stored while the
    backend had no `BACKEND_PUBLIC_URL` points at `127.0.0.1:8001` — the container's own loopback,
    unreachable from anywhere else — and nothing would ever correct it. So every read that puts a
    stored URL in a prompt or returns it to the UI goes through here. The bytes never moved: they are
    in `media_assets` under the same id, served at `GET /media/{id}` on whatever host this is now.
    """
    match = _LOOPBACK_MEDIA_URL.match(url.strip()) if url else None
    return media_url(match.group(1)) if match else url


def public_base_url_problem() -> str | None:
    """Why generated image links would be unreachable on this deployment, or None.

    Only a production deployment with no `BACKEND_PUBLIC_URL` is a problem: there the loopback
    default is the container's own address. Locally the default is right. No host is guessed — the
    fix is a setting, and inventing one would hand out links to somebody else's domain.
    """
    env = (os.environ.get("APP_ENV") or "local").strip().lower()
    if env == "production" and not os.environ.get(PUBLIC_BASE_URL_ENV, "").strip():
        return (
            f"APP_ENV=production but {PUBLIC_BASE_URL_ENV} is not set: every generated image link will "
            f"point at {_DEFAULT_PUBLIC_BASE_URL}, which is unreachable outside this container. Set "
            f"{PUBLIC_BASE_URL_ENV} to this backend's public URL (on Railway: "
            "https://${{RAILWAY_PUBLIC_DOMAIN}})."
        )
    return None


def warn_if_public_base_url_missing() -> bool:
    """Log `public_base_url_problem` at error level. Returns whether it did. Called on startup."""
    problem = public_base_url_problem()
    if problem:
        logger.error(problem)
    return problem is not None


def save_generated_image(session: AsyncSession, data: bytes, *, output_format: str) -> str:
    """Stage a `MediaAsset` row on `session` and return the absolute URL it will be served at.

    Does not commit — the caller's transaction does (see `image_briefs.generate_all`, which saves
    every successfully-generated image on the same session that then writes the run's
    `generated_images` context entry, so the two either land together or not at all). The id is
    assigned here, client-side, rather than left to a post-flush default: the URL has to be known
    immediately, before any flush happens.
    """
    asset_id = uuid.uuid4()
    session.add(MediaAsset(id=asset_id, data=data, content_type=f"image/{output_format.lstrip('.')}"))
    url = media_url(asset_id)
    logger.info("Staged generated image bytes=%s url=%s", len(data), url)
    return url
