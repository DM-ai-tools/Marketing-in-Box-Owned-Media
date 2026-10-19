"""Serves the images `app/services/image_briefs.py` generates through OpenAI.

  - GET /media/{id}   the raw bytes, with the Content-Type they were saved under

One route. Public, unauthenticated: these URLs are meant to be pasted straight into generated
HTML — a client-facing landing page an anonymous visitor's browser will load `<img src>` from —
so there is nothing to authenticate against. See `app/services/media_storage.py` for why the bytes
live in Postgres (`MediaAsset`) rather than on local disk or an object store.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, HTTPException, Response

from app.db.base import get_sessionmaker
from app.db.models import MediaAsset

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/{asset_id}")
async def serve_media(asset_id: str) -> Response:
    try:
        asset_uuid = uuid.UUID(asset_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Not a media id.") from exc

    session_factory = get_sessionmaker()
    async with session_factory() as session:
        asset = await session.get(MediaAsset, asset_uuid)

    if asset is None:
        raise HTTPException(status_code=404, detail="No media found for this id.")

    # A copied logo can be SVG, which a browser runs script in when opened directly. These headers
    # keep it an image on this origin; the bytes never change for an id, so it caches forever.
    return Response(
        content=asset.data,
        media_type=asset.content_type,
        headers={
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; img-src data:; sandbox",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "public, max-age=31536000, immutable",
        },
    )
