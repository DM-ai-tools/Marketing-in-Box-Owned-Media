"""Shared test setup.

The backend loads `.env` on import, so a developer's real keys are in the environment while tests
run. Manus is the one integration a test could trigger just by streaming a stage (the Pillar Page
goes to Manus whenever a key is set), and a Manus task is minutes long and costs credits. So the key
is removed for every test; a test that wants Manus sets it with `monkeypatch.setenv`.
"""
from __future__ import annotations

import pytest

from app.services import embedding_client, manus_client, service_synonyms


@pytest.fixture(autouse=True)
def _no_live_manus(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in manus_client.API_KEY_ENVS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def _no_live_context_search(monkeypatch: pytest.MonkeyPatch) -> None:
    """The OpenAI key in `.env` also turns on context search, which would embed text and query the
    database on every save and stage stream. Off for every test; `tests/test_vector_context.py`
    turns it on and fakes the API and the database."""
    monkeypatch.setenv(embedding_client.SWITCH_ENV, "off")


@pytest.fixture(autouse=True)
def _no_live_synonyms(monkeypatch: pytest.MonkeyPatch) -> None:
    """Alternate service names are one Haiku call per service, made whenever keywords are fetched.
    The Anthropic key in `.env` would make that call live from any keyword test, so it returns no
    names unless a test says otherwise (`tests/test_keyword_rank.py`)."""

    async def none(service: str, industry: str = "", on_usage=None, **_kw) -> service_synonyms.ServiceSynonyms:
        return service_synonyms.ServiceSynonyms(service, (), "none", "stubbed in tests")

    monkeypatch.setattr(service_synonyms, "synonyms_for", none)
