"""Tests for `app/services/media_storage.py` — Postgres-backed storage for OpenAI's generated
bytes.

See that module's docstring for why this exists at all: OpenAI's image models return base64, not
a hosted URL the way Runway's did, so something has to turn the bytes into a link a prompt can
reference without echoing thousands of characters of base64 into the model's own output — and why
that something is Postgres rather than local disk (this app deploys to Railway, where local disk
is the container's own ephemeral disk).

This backend's tests do not have Postgres (see `test_standalone_runs.py`), so `save_generated_image`
is tested against a minimal fake session that only implements the one method it actually calls —
`add()` — the same seam every other DB-touching test in this suite uses.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.db.models import MediaAsset
from app.services import media_storage


@dataclass
class _FakeSession:
    added: list[Any] = field(default_factory=list)

    def add(self, obj: Any) -> None:
        self.added.append(obj)


def test_save_generated_image_stages_a_media_asset_and_returns_its_url(monkeypatch):
    monkeypatch.delenv(media_storage.PUBLIC_BASE_URL_ENV, raising=False)
    session = _FakeSession()

    url = media_storage.save_generated_image(session, b"fake-png-bytes", output_format="png")

    assert url.startswith("http://127.0.0.1:8001/media/")
    assert len(session.added) == 1
    asset = session.added[0]
    assert isinstance(asset, MediaAsset)
    assert asset.data == b"fake-png-bytes"
    assert asset.content_type == "image/png"
    assert str(asset.id) in url


def test_save_generated_image_respects_a_configured_public_base_url(monkeypatch):
    monkeypatch.setenv(media_storage.PUBLIC_BASE_URL_ENV, "https://api.example.com/")
    session = _FakeSession()

    url = media_storage.save_generated_image(session, b"x", output_format="webp")

    assert url.startswith("https://api.example.com/media/")
    assert session.added[0].content_type == "image/webp"


def test_two_saves_never_collide_on_id():
    session = _FakeSession()

    first = media_storage.save_generated_image(session, b"a", output_format="png")
    second = media_storage.save_generated_image(session, b"b", output_format="png")

    assert first != second
    assert session.added[0].id != session.added[1].id


# ---------- loopback URLs written before BACKEND_PUBLIC_URL was set ----------

_ID = "3f2b8c1e-9a4d-4e7b-8c1a-2d5e6f708192"


def test_a_stored_loopback_url_is_rewritten_onto_the_current_public_base(monkeypatch):
    monkeypatch.setenv(media_storage.PUBLIC_BASE_URL_ENV, "https://api.example.com/")
    for stored in (f"http://127.0.0.1:8001/media/{_ID}", f"http://localhost:8001/media/{_ID}"):
        assert media_storage.current_media_url(stored) == f"https://api.example.com/media/{_ID}"


def test_a_real_public_host_is_left_as_stored(monkeypatch):
    monkeypatch.setenv(media_storage.PUBLIC_BASE_URL_ENV, "https://api.example.com")
    stored = f"https://old-deploy.up.railway.app/media/{_ID}"
    assert media_storage.current_media_url(stored) == stored
    # Not a media URL at all, or a loopback on some other port: not this backend's link, untouched.
    assert media_storage.current_media_url("http://127.0.0.1:5173/logo.png") == "http://127.0.0.1:5173/logo.png"
    assert media_storage.current_media_url("") == ""


def test_with_no_public_base_a_loopback_url_stays_loopback(monkeypatch):
    monkeypatch.delenv(media_storage.PUBLIC_BASE_URL_ENV, raising=False)
    assert media_storage.current_media_url(f"http://localhost:8001/media/{_ID}") == f"http://127.0.0.1:8001/media/{_ID}"


def test_a_new_url_is_written_on_the_current_public_base(monkeypatch):
    monkeypatch.setenv(media_storage.PUBLIC_BASE_URL_ENV, "https://api.example.com")
    url = media_storage.save_generated_image(_FakeSession(), b"x", output_format="png")
    assert url.startswith("https://api.example.com/media/")


def test_stored_images_reach_the_prompt_on_the_public_base(monkeypatch):
    from app.routers.pipeline import _generated_images_from_stored

    monkeypatch.setenv(media_storage.PUBLIC_BASE_URL_ENV, "https://api.example.com")
    (image,) = _generated_images_from_stored(
        [{"role": "hero", "size": "1536x1024", "prompt": "p", "url": f"http://127.0.0.1:8001/media/{_ID}"}]
    )
    assert image.url == f"https://api.example.com/media/{_ID}"


# ---------- the startup warning ----------


def test_production_without_a_public_base_logs_an_error(monkeypatch, caplog):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv(media_storage.PUBLIC_BASE_URL_ENV, raising=False)
    with caplog.at_level("ERROR", logger=media_storage.logger.name):
        assert media_storage.warn_if_public_base_url_missing() is True
    assert any("BACKEND_PUBLIC_URL" in r.getMessage() and "unreachable" in r.getMessage() for r in caplog.records)


def test_no_warning_locally_or_when_the_base_is_set(monkeypatch, caplog):
    monkeypatch.delenv(media_storage.PUBLIC_BASE_URL_ENV, raising=False)
    monkeypatch.setenv("APP_ENV", "local")
    with caplog.at_level("ERROR", logger=media_storage.logger.name):
        assert media_storage.warn_if_public_base_url_missing() is False
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv(media_storage.PUBLIC_BASE_URL_ENV, "https://api.example.com")
    with caplog.at_level("ERROR", logger=media_storage.logger.name):
        assert media_storage.warn_if_public_base_url_missing() is False
    assert not caplog.records
