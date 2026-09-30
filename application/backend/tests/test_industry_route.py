"""The industry routes, and the one place a stored industry reaches a stage.

  - `POST /pipeline/industry/infer`     — a guess, or a mapped typed answer; stores nothing
  - `POST /pipeline/runs/{id}/industry` — file the confirmed answer
  - `GET  /pipeline/runs/{id}/industry` — read it back, inherited down `source_run_id`

Postgres is faked at the same seams `test_standalone_runs.py` uses (`get_sessionmaker`,
`_latest_context_entry`, `_next_version`), and the classifier is patched: nothing here calls Claude.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routers import pipeline as pipeline_router
from app.services import industry_voice
from app.services.industry_voice import IndustryProfile

RUN_ID = "11111111-1111-1111-1111-111111111111"
PARENT_RUN_ID = "22222222-2222-2222-2222-222222222222"


@dataclass
class _FakeEntry:
    value: dict[str, Any]
    version: int = 1


@dataclass
class _FakeSession:
    run_exists: bool = True
    added: list[Any] = field(default_factory=list)

    async def get(self, _model: Any, _ident: Any) -> Any:
        return object() if self.run_exists else None

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def commit(self) -> None:
        pass

    async def __aenter__(self) -> "_FakeSession":
        return self

    async def __aexit__(self, *_exc: Any) -> bool:
        return False


@dataclass
class _Store:
    session: _FakeSession
    on_run: dict[str, _FakeEntry] = field(default_factory=dict)
    on_parent: dict[str, _FakeEntry] = field(default_factory=dict)


@pytest.fixture(name="store")
def _store(monkeypatch: pytest.MonkeyPatch) -> _Store:
    store = _Store(session=_FakeSession())
    monkeypatch.setattr(pipeline_router, "get_sessionmaker", lambda: (lambda: store.session))

    async def fake_latest(_session: Any, run_uuid: uuid.UUID, context_key: str) -> Any:
        if context_key in store.on_run:
            return store.on_run[context_key], run_uuid
        if context_key in store.on_parent:
            return store.on_parent[context_key], uuid.UUID(PARENT_RUN_ID)
        return None

    async def fake_next_version(_session: Any, _run: uuid.UUID, context_key: str) -> int:
        existing = store.on_run.get(context_key)
        return (existing.version if existing else 0) + 1

    monkeypatch.setattr(pipeline_router, "_latest_context_entry", fake_latest)
    monkeypatch.setattr(pipeline_router, "_next_version", fake_next_version)
    return store


@pytest.fixture(name="client")
def _client() -> TestClient:
    return TestClient(app)


FINANCE = IndustryProfile("regulated_finance", "Mortgage broking", "operator_typed", 0.8, "Brokers home loans.")


# --------------------------------------------------------------------------------------
# infer
# --------------------------------------------------------------------------------------


def test_infer_returns_the_guess_and_stores_nothing(client: TestClient, store: _Store, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    async def fake_infer(answers: dict[str, str], *, typed: str | None = None) -> IndustryProfile:
        seen.update(answers=answers, typed=typed)
        return FINANCE

    monkeypatch.setattr(industry_voice, "infer_industry", fake_infer)
    res = client.post("/pipeline/industry/infer", json={"answers": {"company_name": "Arg"}, "typed": "Mortgage broking"})
    assert res.status_code == 200, res.text
    assert res.json()["profile"]["bucket"] == "regulated_finance"
    assert res.json()["profile"]["bucket_label"] == "Regulated Finance"
    assert seen == {"answers": {"company_name": "Arg"}, "typed": "Mortgage broking"}
    assert store.session.added == []


def test_infer_with_no_guess_returns_null(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def no_guess(_answers: dict[str, str], *, typed: str | None = None) -> None:
        return None

    monkeypatch.setattr(industry_voice, "infer_industry", no_guess)
    res = client.post("/pipeline/industry/infer", json={"answers": {}})
    assert res.status_code == 200
    assert res.json() == {"profile": None}


# --------------------------------------------------------------------------------------
# save / read
# --------------------------------------------------------------------------------------


def test_save_files_a_versioned_context_entry(client: TestClient, store: _Store) -> None:
    res = client.post(
        f"/pipeline/runs/{RUN_ID}/industry",
        json={"bucket": "healthcare", "label": "  Dental   clinic ", "source": "operator_picked"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["version"] == 1
    (entry,) = store.session.added
    assert entry.context_key == industry_voice.CONTEXT_KEY
    assert entry.written_by_asset_id is None
    assert industry_voice.from_entry(entry.value) == IndustryProfile("healthcare", "Dental clinic", "operator_picked")


def test_unknown_bucket_is_a_422_naming_the_valid_ones(client: TestClient, store: _Store) -> None:
    res = client.post(f"/pipeline/runs/{RUN_ID}/industry", json={"bucket": "crypto", "label": "x", "source": "operator_picked"})
    assert res.status_code == 422
    assert "regulated_finance" in res.json()["detail"]
    assert store.session.added == []


def test_save_on_a_missing_run_is_a_404(client: TestClient, store: _Store) -> None:
    store.session.run_exists = False
    res = client.post(f"/pipeline/runs/{RUN_ID}/industry", json={"bucket": "healthcare", "label": "x", "source": "operator_picked"})
    assert res.status_code == 404


def test_read_is_404_before_anything_is_filed(client: TestClient, store: _Store) -> None:
    assert client.get(f"/pipeline/runs/{RUN_ID}/industry").status_code == 404


def test_a_phase2_run_inherits_its_parents_industry(client: TestClient, store: _Store) -> None:
    store.on_parent[industry_voice.CONTEXT_KEY] = _FakeEntry(value=industry_voice.render(FINANCE), version=3)
    res = client.get(f"/pipeline/runs/{RUN_ID}/industry")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["profile"]["label"] == "Mortgage broking"
    assert body["inherited_from_run_id"] == PARENT_RUN_ID
    assert body["version"] == 3


# --------------------------------------------------------------------------------------
# into the stage
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_industry_resolves_the_stored_profile(store: _Store) -> None:
    assert await pipeline_router._run_industry(None) is None
    assert await pipeline_router._run_industry(RUN_ID) is None
    store.on_run[industry_voice.CONTEXT_KEY] = _FakeEntry(value=industry_voice.render(FINANCE))
    assert await pipeline_router._run_industry(RUN_ID) == FINANCE


@pytest.mark.asyncio
async def test_run_industry_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken() -> Any:
        raise RuntimeError("database down")

    monkeypatch.setattr(pipeline_router, "get_sessionmaker", broken)
    assert await pipeline_router._run_industry(RUN_ID) is None
    assert await pipeline_router._run_industry("not-a-uuid") is None


@pytest.mark.asyncio
async def test_generation_stream_writes_the_stage_in_the_runs_voice(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    async def passthrough(_asset_id: str, answers: dict[str, str], *_a: Any, **_k: Any) -> Any:
        return answers, None

    async def no_design(*_a: Any, **_k: Any) -> None:
        return None

    async def the_voice(run_id: str | None) -> IndustryProfile | None:
        captured["run_id"] = run_id
        return FINANCE

    async def fake_stream(asset_id: str, answers: dict[str, str], phase: str, **kwargs: Any):
        captured.update(kwargs)
        yield "hello"

    monkeypatch.setattr(pipeline_router, "_run_competitor_prepass", passthrough)
    monkeypatch.setattr(pipeline_router, "_run_social_data_prepass", passthrough)
    monkeypatch.setattr(pipeline_router, "resolve_page_design", no_design)
    monkeypatch.setattr(pipeline_router, "_run_industry", the_voice)
    monkeypatch.setattr(pipeline_router, "generate_stage_stream", fake_stream)

    events = [e async for e in pipeline_router._generation_sse_stream("icp", {}, {}, "phase1", None, RUN_ID)]
    assert any('"delta"' in e for e in events)
    assert captured["run_id"] == RUN_ID
    assert captured["voice"] == FINANCE
