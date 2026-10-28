"""Context search: chunking, scoping, retrieval at generation time, and suggested intake answers.

Nothing here reaches OpenAI, Anthropic or Postgres. The embedding API is an `httpx.MockTransport`
or a patched `embedding_client.embed`; the database is patched at the module-level functions
`vector_context` itself calls (`available`, `visible_entries`, `ensure_indexed`, `search`). What is
tested is the reasoning an operator would act on:

- the Phase 2 scoping rule (a sub-service run must never retrieve its parent's ICP),
- that a document the operator supplied is never cut down,
- that page copy is never retrieved,
- that a suggestion survives only with a verbatim quote from the section it names,
- that legal guardrail fields and competitor listings are never a suggestion,
- and that every failure leaves the pipeline exactly as it was before vectors existed.
"""

from __future__ import annotations

import json
import re
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.models import ContextChunk
from app.services import context_chunks, embedding_client, vector_context
from app.services.vector_context import Hit, PrefillField, ScopedEntry

BACKEND = Path(__file__).resolve().parents[1]
CATALOG = BACKEND.parent / "frontend" / "src" / "data" / "assetCatalog.ts"
RUN = uuid.UUID("11111111-1111-1111-1111-111111111111")
PARENT = uuid.UUID("22222222-2222-2222-2222-222222222222")


@pytest.fixture
def search_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(embedding_client.SWITCH_ENV, "on")
    monkeypatch.setenv(embedding_client.API_KEY_ENV, "sk-test")


# --------------------------------------------------------------------------------------
# Chunking
# --------------------------------------------------------------------------------------

CRO_DOC = """# PART 0 — Audit summary

The page scores 58/100. Proof is thin above the fold.

## Layer 2 — Oxytocin: 4/10

No named client results.

# PART 2 — Rewritten page

## Hero

Grow qualified leads from Meta Ads in 90 days, or we keep working for free.

## Pricing

| Plan | Price | Includes |
| --- | --- | --- |
| Starter | $1,500/mo | 1 campaign |
| Growth | $3,000/mo | 3 campaigns |

## Proof

"Our cost per lead fell 41% in two months." — Sarah, Coastal Physio

```html
<div class="hero"><p>Old markup</p><span>x</span><a href="#">y</a><img src="z"><section></section></div>
```
"""


def _words(text: str) -> list[str]:
    return re.findall(r"[A-Za-z0-9$%,./]+", text)


def test_chunks_follow_headings_and_keep_every_word_outside_html() -> None:
    chunks = context_chunks.chunk_document(CRO_DOC * 3)
    joined = "\n".join(c.text for c in chunks)
    source = CRO_DOC.split("```html")[0] * 3
    marker = _words("[HTML page omitted from search]")
    rest = _words(joined)
    for word in marker * 3:
        rest.remove(word)
    assert sorted(rest) == sorted(_words(source))
    assert "Old markup" not in joined
    assert "[HTML page omitted from search]" in joined
    assert [c.index for c in chunks] == list(range(len(chunks)))
    assert any("PART 2" in c.section_path for c in chunks)


def test_a_table_is_never_cut_in_half() -> None:
    for chunk in context_chunks.chunk_document(CRO_DOC * 3):
        if "| Starter" in chunk.text:
            assert "| Growth" in chunk.text


def test_a_long_block_is_split_under_the_cap() -> None:
    doc = "# Big\n\n" + "\n".join(f"- point {i} " + "word " * 40 for i in range(200))
    chunks = context_chunks.chunk_document(doc)
    assert len(chunks) > 5
    assert all(len(c.text) <= context_chunks.MAX_CHARS + 200 for c in chunks)


@pytest.mark.parametrize(
    ("key", "text", "indexed"),
    [
        ("cro", CRO_DOC, True),
        ("brand_design_tokens", CRO_DOC, False),
        ("icp", "too short", False),
        ("pillar_page", "<!doctype html><html><body>" + "<div>x</div>" * 100 + "</body></html>", False),
    ],
)
def test_what_is_indexed(key: str, text: str, indexed: bool) -> None:
    assert (context_chunks.indexable(key, text) is None) is indexed


# --------------------------------------------------------------------------------------
# Scoping: the rule `_latest_context_entry` applies, for every key at once
# --------------------------------------------------------------------------------------


def _row(key: str, content: str, version: int = 1, by: str | None = None) -> Any:
    return SimpleNamespace(
        id=uuid.uuid4(), context_key=key, version=version, value={"content": content}, written_by_asset_id=by
    )


def test_a_sub_service_run_never_sees_its_parents_icp() -> None:
    scoped = vector_context.scope_entries(
        [
            (RUN, [_row("cro", "sub-service CRO")]),
            (PARENT, [_row("icp", "parent ICP"), _row("cro", "parent CRO"), _row("cro_client_settings", "settings")]),
        ]
    )
    by_key = {e.context_key: e for e in scoped}
    assert "icp" not in by_key
    assert by_key["cro"].text == "sub-service CRO" and not by_key["cro"].inherited
    assert by_key["cro_client_settings"].inherited and by_key["cro_client_settings"].run_id == PARENT


def test_only_the_latest_version_of_a_key_is_searched() -> None:
    scoped = vector_context.scope_entries([(RUN, [_row("icp", "v1", 1), _row("icp", "v3", 3), _row("icp", "v2", 2)])])
    assert [e.text for e in scoped] == ["v3"]


def test_the_scoped_keys_are_the_routers_own() -> None:
    from app.routers import pipeline as pipeline_router

    assert pipeline_router.PHASE_SCOPED_CONTEXT_KEYS is vector_context.PHASE_SCOPED_CONTEXT_KEYS


# --------------------------------------------------------------------------------------
# B. Relevant sections at generation time
# --------------------------------------------------------------------------------------


def test_page_copy_inputs_are_never_retrieved() -> None:
    for asset_id, field_id in vector_context.FULL_TEXT_FIELDS:
        assert field_id not in vector_context.RETRIEVAL_FIELDS.get(asset_id, {})


def test_every_retrieval_field_reads_the_cro_rewrite_in_both_schema_and_catalog() -> None:
    """Three places have to agree: this list, the backend schema (what the INPUTS block reads) and
    the frontend catalog (what fills the answer). A field id that drifts in any one is a field the
    list silently stops covering."""
    catalog = CATALOG.read_text(encoding="utf-8")
    for asset_id, specs in vector_context.RETRIEVAL_FIELDS.items():
        for field_id, spec in specs.items():
            assert spec.keys[0] == "cro_rewritten_copy"
            schema_fields = [
                field
                for path in BACKEND.glob(f"schemas/drafts/{asset_id}*.json")
                for field in json.loads(path.read_text(encoding="utf-8"))["fields"]
                if field.get("field_id") == field_id
            ]
            assert schema_fields, f"{asset_id}.{field_id} is not in its schema"
            assert all(f.get("context_key") == "cro_rewritten_copy" for f in schema_fields)
            assert re.search(rf'ctx\("{field_id}",[^\n]*"cro_rewritten_copy"', catalog), f"{field_id} not in the catalog"


def _hit(index: int, chars: int, score: float, entry: uuid.UUID | None = None) -> Hit:
    return Hit(
        context_entry_id=entry or RUN,
        context_key="cro",
        chunk_index=index,
        section_path=f"S{index}",
        text=f"section {index} " + "x" * (chars - 12),
        score=score,
    )


def test_sections_fit_the_budget_keep_the_opening_and_read_in_order() -> None:
    hits = [_hit(5, 4000, 0.9), _hit(2, 4000, 0.8), _hit(0, 2000, 0.1), _hit(7, 4000, 0.7), _hit(3, 500, 0.6)]
    chosen = vector_context.select_sections(hits, 11_000)
    assert [h.chunk_index for h in chosen] == [0, 2, 3, 5]
    assert sum(len(h.text) for h in chosen) <= 11_000
    rendered = vector_context.render_sections("Cro", chosen, 8)
    assert "4 of 8" in rendered and rendered.count("[…]") == 2


def test_same_document_tells_the_stored_copy_from_the_operators_own() -> None:
    stored = CRO_DOC * 10
    assert vector_context.same_document(stored + "\n\n", stored)
    assert vector_context.same_document(stored.replace("\n", "\r\n"), stored)
    assert not vector_context.same_document("Our own brand voice guide.\n" + stored, stored)
    assert not vector_context.same_document(stored[:2500], stored)


def _patch_db(monkeypatch: pytest.MonkeyPatch, entries: list[ScopedEntry], hits: list[Hit], chunks: int = 20) -> None:
    @asynccontextmanager
    async def fake_session():  # type: ignore[no-untyped-def]
        yield object()

    async def yes(_session: Any) -> bool:
        return True

    async def visible(_session: Any, _run: uuid.UUID) -> list[ScopedEntry]:
        return entries

    async def indexed(_session: Any, _entries: list[ScopedEntry]) -> int:
        return 0

    async def found(_session: Any, _vector: list[float], ids: list[uuid.UUID], k: int) -> list[Hit]:
        return [h for h in hits if h.context_entry_id in ids][:k]

    async def count(_session: Any, _entry: uuid.UUID) -> int:
        return chunks

    async def embed(texts: list[str]) -> list[list[float]]:
        return [[0.0] * embedding_client.DIMENSIONS for _ in texts]

    monkeypatch.setattr(vector_context, "get_sessionmaker", lambda: fake_session)
    monkeypatch.setattr(vector_context, "available", yes)
    monkeypatch.setattr(vector_context, "visible_entries", visible)
    monkeypatch.setattr(vector_context, "ensure_indexed", indexed)
    monkeypatch.setattr(vector_context, "search", found)
    monkeypatch.setattr(vector_context, "_chunk_count", count)
    monkeypatch.setattr(vector_context.embedding_client, "embed", embed)


def _entry(key: str, text: str, by: str | None = None) -> ScopedEntry:
    return ScopedEntry(
        entry_id=uuid.uuid4(), run_id=RUN, context_key=key, version=1, text=text, written_by_asset_id=by, inherited=False
    )


@pytest.mark.asyncio
async def test_the_stored_rewrite_is_sent_as_its_relevant_sections(
    search_on: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = CRO_DOC * 40
    cro = _entry("cro", doc, by="cro")
    _patch_db(monkeypatch, [cro], [_hit(i, 1500, 0.9 - i / 100, cro.entry_id) for i in range(20)])
    answers = {"cro_rewritten_copy": doc, "topic": "Meta Ads"}
    out, event = await vector_context.expand_retrieval(str(RUN), "sms_sequence", answers)
    assert out["topic"] == "Meta Ads"
    assert len(out["cro_rewritten_copy"]) <= vector_context.RETRIEVAL_FIELDS["sms_sequence"]["cro_rewritten_copy"].budget_chars + 400
    assert out["cro_rewritten_copy"].startswith("[Relevant sections of the approved Cro")
    assert event and event["fields"][0]["chars_before"] == len(doc)


@pytest.mark.asyncio
async def test_a_document_the_operator_supplied_goes_through_whole(
    search_on: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    cro = _entry("cro", CRO_DOC * 40)
    _patch_db(monkeypatch, [cro], [_hit(0, 1500, 0.9, cro.entry_id)])
    own = "Our brand book, written by the client.\n" + "Voice rules. " * 3000
    out, event = await vector_context.expand_retrieval(str(RUN), "sms_sequence", {"cro_rewritten_copy": own})
    assert out["cro_rewritten_copy"] == own
    assert event and not event["fields"]


@pytest.mark.asyncio
async def test_retrieval_off_changes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    answers = {"cro_rewritten_copy": CRO_DOC * 40}
    out, event = await vector_context.expand_retrieval(str(RUN), "sms_sequence", answers)
    assert out == answers and event is None


@pytest.mark.asyncio
async def test_a_retrieval_failure_sends_the_input_whole(search_on: None, monkeypatch: pytest.MonkeyPatch) -> None:
    doc = CRO_DOC * 40
    _patch_db(monkeypatch, [_entry("cro", doc)], [])

    async def broken(_texts: list[str]) -> list[list[float]]:
        raise embedding_client.EmbeddingError("Embedding failed: OpenAI answered HTTP 500")

    monkeypatch.setattr(vector_context.embedding_client, "embed", broken)
    out, event = await vector_context.expand_retrieval(str(RUN), "sms_sequence", {"cro_rewritten_copy": doc})
    assert out["cro_rewritten_copy"] == doc
    assert event and "Sent whole" in event["notes"][0]


# --------------------------------------------------------------------------------------
# A + C. Suggested answers
# --------------------------------------------------------------------------------------

ICP_TEXT = "The buyer is a clinic owner. Average deal size is $4,500 per month. They buy within 30 days."


def _icp_hit(entry: uuid.UUID, score: float = 0.6) -> Hit:
    return Hit(
        context_entry_id=entry, context_key="icp", chunk_index=0, section_path="Buyer", text=ICP_TEXT, score=score
    )


def test_an_answer_survives_only_with_a_verbatim_quote_from_its_excerpt() -> None:
    fields = [
        PrefillField("deal_size", "Average deal size"),
        PrefillField("buyer", "Who buys"),
        PrefillField("cycle", "Sales cycle"),
        PrefillField("motion", "Sales motion", kind="enum_choice", choices=("Self-serve", "Sales-led")),
    ]
    excerpts = [_icp_hit(RUN)]
    hits = {"deal_size": [1], "buyer": [1], "cycle": [1], "motion": [1]}
    reply = json.dumps(
        {
            "answers": [
                {"field_id": "deal_size", "answer": "$4,500/mo", "quote": "Average deal size is  $4,500 per month", "excerpt": 1},
                {"field_id": "buyer", "answer": "Clinic owners", "quote": "The buyer is a dentist", "excerpt": 1},
                {"field_id": "cycle", "answer": "30 days", "quote": "They buy within 30 days", "excerpt": 2},
                {"field_id": "motion", "answer": "sales-led", "quote": "clinic owner", "excerpt": 1},
                {"field_id": "unasked", "answer": "x", "quote": "clinic owner", "excerpt": 1},
            ]
        }
    )
    suggestions, dropped = vector_context.parse_prefill_reply("Here you go:\n" + reply, fields, excerpts, hits)
    assert {s.field_id: s.answer for s in suggestions} == {"deal_size": "$4,500/mo", "motion": "Sales-led"}
    assert dropped == 3


def test_a_choice_answer_must_be_one_of_the_choices() -> None:
    f = PrefillField("motion", "Sales motion", kind="enum_choice", choices=("Self-serve", "Sales-led"))
    reply = json.dumps({"answers": [{"field_id": "motion", "answer": "Partner-led", "quote": "clinic owner", "excerpt": 1}]})
    suggestions, dropped = vector_context.parse_prefill_reply(reply, [f], [_icp_hit(RUN)], {"motion": [1]})
    assert suggestions == [] and dropped == 1


@pytest.mark.asyncio
async def test_prefill_measures_then_judges_once(search_on: None, monkeypatch: pytest.MonkeyPatch) -> None:
    icp = _entry("icp", ICP_TEXT * 5, by="icp")
    rival = _entry("competitor_analysis_offers", "Rival pricing: $99/mo", by="competitor_analysis_offers")
    own = _entry("offers", "Previous offers draft", by="offers")
    _patch_db(
        monkeypatch,
        [icp, rival, own],
        [_icp_hit(icp.entry_id), _icp_hit(rival.entry_id, 0.95), _icp_hit(own.entry_id, 0.95)],
    )
    prompts: list[str] = []

    async def judge(prompt: str, _on_usage: Any) -> str:
        prompts.append(prompt)
        return json.dumps({"answers": [{"field_id": "deal_size", "answer": "$4,500 per month", "quote": "$4,500 per month", "excerpt": 1}]})

    monkeypatch.setattr(vector_context, "_judge", judge)
    fields = [
        PrefillField("deal_size", "Average deal size"),
        PrefillField("claim_substantiation_tier", "Claim substantiation tier"),
        PrefillField("faint", "Something unrelated"),
    ]
    result = await vector_context.prefill(object(), RUN, "offers", fields)  # type: ignore[arg-type]

    assert result.status == "ok"
    assert [(s.field_id, s.context_key) for s in result.suggestions] == [("deal_size", "icp")]
    assert len(prompts) == 1
    assert "claim_substantiation_tier" not in prompts[0]
    assert "Rival pricing" not in prompts[0] and "Previous offers draft" not in prompts[0]


@pytest.mark.asyncio
async def test_a_sub_key_field_is_searched_only_in_its_own_document(
    search_on: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    icp = _entry("icp", ICP_TEXT * 5)
    cro = _entry("cro", CRO_DOC * 3)
    _patch_db(monkeypatch, [icp, cro], [_icp_hit(icp.entry_id)])
    judged: list[str] = []

    async def judge(prompt: str, _on_usage: Any) -> str:
        judged.append(prompt)
        return "{}"

    monkeypatch.setattr(vector_context, "_judge", judge)
    field = PrefillField("word_for_the_reader", "Word for the reader", keys=("cro_terminology_map", "cro"))
    result = await vector_context.prefill(object(), RUN, "pillar_page", [field])  # type: ignore[arg-type]
    # The only good match is in the ICP, which this field may not read, so the judge is never asked.
    assert result.status == "ok" and not result.suggestions and not judged


@pytest.mark.asyncio
async def test_prefill_is_off_without_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    result = await vector_context.prefill(object(), RUN, "offers", [PrefillField("x", "X")])  # type: ignore[arg-type]
    assert result.status == "off" and not result.suggestions


# --------------------------------------------------------------------------------------
# The wrapper, the column type, and the routes
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_embed_returns_vectors_in_input_order(search_on: None, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body)
        data = [{"index": i, "embedding": [float(i)] * embedding_client.DIMENSIONS} for i in range(len(body["input"]))]
        return httpx.Response(200, json={"data": list(reversed(data)), "usage": {"total_tokens": 7}})

    client = httpx.AsyncClient(base_url=embedding_client.BASE_URL, transport=httpx.MockTransport(handler))
    monkeypatch.setattr(embedding_client, "_client", lambda: client)
    vectors = await embedding_client.embed(["a", "b", "c"])
    assert [v[0] for v in vectors] == [0.0, 1.0, 2.0]
    assert seen[0]["model"] == embedding_client.MODEL


@pytest.mark.asyncio
async def test_embed_says_which_failure_it_was(search_on: None, monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "Incorrect API key"}})

    client = httpx.AsyncClient(base_url=embedding_client.BASE_URL, transport=httpx.MockTransport(handler))
    monkeypatch.setattr(embedding_client, "_client", lambda: client)
    with pytest.raises(embedding_client.EmbeddingError, match="rejected the key"):
        await embedding_client.embed(["a"])


@pytest.mark.asyncio
async def test_embed_refuses_when_switched_off(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(embedding_client.EmbeddingNotConfigured):
        await embedding_client.embed(["a"])


def test_vectors_are_cast_explicitly_on_insert_and_search() -> None:
    insert = str(
        pg_insert(ContextChunk)
        .values(
            [
                {
                    "id": uuid.uuid4(),
                    "context_entry_id": RUN,
                    "run_id": RUN,
                    "context_key": "icp",
                    "chunk_index": 0,
                    "section_path": "",
                    "text": "t",
                    "embedding_model": embedding_client.MODEL,
                    "embedding": [0.5] * 3,
                }
            ]
        )
        .compile(dialect=postgresql.psycopg.dialect())
    )
    assert "AS VECTOR(1536)" in insert
    search = str(ContextChunk.embedding.cosine_distance([0.1, 0.2]).compile(dialect=postgresql.psycopg.dialect()))
    assert "<=>" in search and "AS VECTOR(1536)" in search
    process = ContextChunk.__table__.c.embedding.type.bind_processor(postgresql.psycopg.dialect())
    assert process([1, 0.5]) == "[1.0,0.5]"


def test_prefill_route_returns_the_result_and_404s_an_unknown_run(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.main import app
    from app.routers import vector_context as route

    class _Session:
        def __init__(self, exists: bool) -> None:
            self.exists = exists

        async def get(self, _model: Any, _ident: Any) -> Any:
            return object() if self.exists else None

        async def __aenter__(self) -> "_Session":
            return self

        async def __aexit__(self, *_exc: Any) -> bool:
            return False

    calls: list[list[PrefillField]] = []

    async def fake_prefill(_session: Any, run: uuid.UUID, asset: str, fields: list[PrefillField], on_usage: Any = None):
        calls.append(fields)
        return vector_context.PrefillResult(status="ok")

    monkeypatch.setattr(route.vector_context, "prefill", fake_prefill)
    client = TestClient(app)
    body = {"fields": [{"field_id": "deal_size", "label": "Average deal size", "choices": [], "keys": []}], "phase": "phase1"}

    monkeypatch.setattr(route, "get_sessionmaker", lambda: lambda: _Session(True))
    ok = client.post(f"/pipeline/runs/{RUN}/prefill/offers", json=body)
    assert ok.status_code == 200 and ok.json()["status"] == "ok"
    assert calls[0][0].field_id == "deal_size"

    monkeypatch.setattr(route, "get_sessionmaker", lambda: lambda: _Session(False))
    assert client.post(f"/pipeline/runs/{RUN}/prefill/offers", json=body).status_code == 404
