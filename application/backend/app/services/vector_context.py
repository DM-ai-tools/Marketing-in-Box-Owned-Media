"""Searching a run's approved documents by meaning: pgvector over `context_entries`.

The exact-key context lookups stay. They are correct and free, and nothing here replaces them. This
module covers the two places they fall short:

1. **Questions an earlier document already answers.** Matching is by key name only, so a field gets
   context only if someone wrote its key into the field definition, or the regex in
   `lib/contextSubKeys.ts` found a label. Everything else is asked of the operator, even when the
   approved ICP or CRO rewrite states it. `prefill` finds those answers and returns them as
   *suggestions* the operator confirms (`POST /pipeline/runs/{id}/prefill/{asset}`). Never a
   silent answer.
2. **Whole documents pasted where a few sections would do.** A matched document reaches the prompt
   whole: the 80KB CRO rewrite goes into nine stages as "brand voice" or "messaging framework".
   `expand_retrieval` sends the sections relevant to that input instead, for the fields listed in
   `RETRIEVAL_FIELDS` only.

Measure, then judge, as everywhere else in this pipeline:
- the embedding search *measures* which sections are near a question;
- one Haiku call *judges* whether they state the answer;
- `_verified` drops any answer whose quote is not in the excerpt, the same rule as
  `asset_check._verified`.

Scoping (`visible_entries`) is the rule `_latest_context_entry` applies, for every key at once:
- latest version only;
- up `source_run_id` at most `MAX_SOURCE_RUN_HOPS`;
- `PHASE_SCOPED_CONTEXT_KEYS` never leave their run.

Without that, a Phase 2 run would pull Phase 1's ICP back in by similarity, which is exactly the
leak the phase split exists to stop.

Indexing is lazy and also runs in the background after each save (`index_run`): any visible entry
with no chunks for the current model is chunked and embedded before a search. So a run approved
before vectors existed needs no backfill; its first search indexes it.

Every failure degrades to the behaviour before this module existed:
- no key, `VECTOR_CONTEXT=off`, no pgvector table, or an API error;
- the result: no suggestions, and documents sent whole.

The reason is put in `notes` and logged; nothing raises to a caller.
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from dataclasses import asdict, dataclass, field

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_sessionmaker
from app.db.models import ContextChunk, ContextEntry, Run
from app.services import context_chunks, embedding_client
from app.services.claude_client import get_client
from app.services.context_scope import MAX_SOURCE_RUN_HOPS, PHASE_SCOPED_CONTEXT_KEYS
from app.services.generation import HAIKU, OnUsage
from app.services.usage import CallUsage

logger = logging.getLogger(__name__)

PREFILL_MODEL = HAIKU
_PREFILL_MAX_TOKENS = 3000

#: Cosine similarity below which a section is not offered to the judge at all. Measured for
#: `text-embedding-3-small`: a question and the paragraph answering it score about 0.4-0.6, an
#: unrelated section of the same client's documents 0.1-0.25.
MIN_SCORE = 0.30
#: Sections shown to the judge per question.
HITS_PER_FIELD = 3
#: Everything shown to the judge in one prefill call, across all questions (about 10k tokens).
_PREFILL_EXCERPT_CHARS = 40_000
#: Questions per prefill call. A stage asks at most about 30.
MAX_PREFILL_FIELDS = 40
#: Chunks embedded per indexing pass, so one huge run cannot make one request slow.
_MAX_INDEX_CHUNKS = 800
_INSERT_BATCH = 100
_MAX_ANSWER_CHARS = 1200

#: Never suggested, whatever a document says. These are legal guardrails: a claim tier, whether
#: testimonials may run, how price may be shown, whether the client is regulated. A suggestion the
#: operator accepts with one click is still a decision nobody made. The same reasoning keeps
#: per-industry defaults for these fields out of CLAUDE.md's "Phase C".
NEVER_SUGGEST: frozenset[str] = frozenset(
    {
        "claim_substantiation_tier",
        "pricing_disclosure_mode",
        "testimonials_before_after_permitted",
        "regulated_field_flag",
        "regulation_name",
        "compliance_sensitivity",
        "locked_legal_compliance_text",
        "regulated_field_advertising_compliance_body",
        "regulated_field_advertising_compliance_body_optional",
    }
)

#: Never a source for a suggestion. A competitor listing states competitors' prices, offers and
#: guarantees, and "the client's price" read off one is the worst wrong answer this could give.
_PREFILL_EXCLUDED_PREFIXES = ("competitor_analysis_",)


# --------------------------------------------------------------------------------------
# Availability
# --------------------------------------------------------------------------------------

_table_seen = False


def enabled() -> bool:
    """A key is set and the switch is on. Says nothing about the database: see `available`."""
    return embedding_client.is_configured()


async def available(session: AsyncSession) -> bool:
    """The `context_chunks` table exists, so pgvector was installed when the migration ran. A yes
    is cached for the life of the process; a no is checked again next time, so installing pgvector
    and migrating takes effect without a restart."""
    global _table_seen
    if _table_seen:
        return True
    try:
        found = (await session.execute(text("SELECT to_regclass('public.context_chunks') IS NOT NULL"))).scalar()
    except Exception:  # noqa: BLE001 - any failure here means "not available", never a crash
        logger.exception("Could not check for the context_chunks table")
        return False
    _table_seen = bool(found)
    return _table_seen


# --------------------------------------------------------------------------------------
# Scope and index
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ScopedEntry:
    entry_id: uuid.UUID
    run_id: uuid.UUID
    context_key: str
    version: int
    text: str
    written_by_asset_id: str | None
    inherited: bool


def humanise_key(context_key: str) -> str:
    return context_key.replace("_", " ").strip().capitalize()


def scope_entries(levels: list[tuple[uuid.UUID, list[ContextEntry]]]) -> list[ScopedEntry]:
    """The scoping rule, given each run's rows nearest first (`levels[0]` is the run itself).

    - one entry per key, the latest version;
    - the nearest run wins;
    - a `PHASE_SCOPED_CONTEXT_KEYS` key is taken only from the run itself;
    - an entry with no text is skipped.
    """
    found: dict[str, ScopedEntry] = {}
    for depth, (run_id, rows) in enumerate(levels):
        for row in sorted(rows, key=lambda r: r.version, reverse=True):
            if row.context_key in found:
                continue
            if depth > 0 and row.context_key in PHASE_SCOPED_CONTEXT_KEYS:
                continue
            content = row.value.get("content") if isinstance(row.value, dict) else None
            if not isinstance(content, str) or not content.strip():
                continue
            found[row.context_key] = ScopedEntry(
                entry_id=row.id,
                run_id=run_id,
                context_key=row.context_key,
                version=row.version,
                text=content,
                written_by_asset_id=row.written_by_asset_id,
                inherited=depth > 0,
            )
    return list(found.values())


async def visible_entries(session: AsyncSession, run_uuid: uuid.UUID) -> list[ScopedEntry]:
    """Every document this run can read, one per key: the latest version, nearest run first."""
    levels: list[tuple[uuid.UUID, list[ContextEntry]]] = []
    seen: set[uuid.UUID] = set()
    current: uuid.UUID | None = run_uuid
    for _ in range(MAX_SOURCE_RUN_HOPS):
        if current is None or current in seen:
            break
        seen.add(current)
        rows = (
            await session.execute(
                select(ContextEntry)
                .where(ContextEntry.run_id == current)
                .order_by(ContextEntry.context_key, ContextEntry.version.desc())
                .distinct(ContextEntry.context_key)
            )
        ).scalars().all()
        levels.append((current, list(rows)))
        run = await session.get(Run, current)
        current = run.source_run_id if run is not None else None
    return scope_entries(levels)


def _embedding_input(entry: ScopedEntry, chunk: context_chunks.Chunk) -> str:
    """What is embedded: the document's name and the section's heading chain, then the text. The
    title carries most of what a section is about, and the stored `text` stays clean."""
    head = humanise_key(entry.context_key)
    if chunk.section_path:
        head = f"{head} — {chunk.section_path}"
    return f"{head}\n\n{chunk.text}"


async def ensure_indexed(session: AsyncSession, entries: list[ScopedEntry]) -> int:
    """Chunk and embed every indexable entry that has no chunks for the current model. Returns the
    number of chunks written. Raises `EmbeddingError` (callers degrade on it)."""
    candidates = [e for e in entries if context_chunks.indexable(e.context_key, e.text) is None]
    if not candidates:
        return 0
    have = set(
        (
            await session.execute(
                select(ContextChunk.context_entry_id)
                .where(
                    ContextChunk.context_entry_id.in_([e.entry_id for e in candidates]),
                    ContextChunk.embedding_model == embedding_client.MODEL,
                )
                .distinct()
            )
        ).scalars()
    )
    todo = [e for e in candidates if e.entry_id not in have]
    if not todo:
        return 0

    pieces: list[tuple[ScopedEntry, context_chunks.Chunk]] = []
    for entry in todo:
        chunks = context_chunks.chunk_document(entry.text)
        if len(pieces) + len(chunks) > _MAX_INDEX_CHUNKS:
            logger.warning("Indexing capped at %d chunks; the rest index on a later search", _MAX_INDEX_CHUNKS)
            break
        pieces.extend((entry, chunk) for chunk in chunks)
    if not pieces:
        return 0

    vectors = await embedding_client.embed([_embedding_input(e, c) for e, c in pieces])
    rows = [
        {
            "id": uuid.uuid4(),
            "context_entry_id": entry.entry_id,
            "run_id": entry.run_id,
            "context_key": entry.context_key,
            "chunk_index": chunk.index,
            "section_path": chunk.section_path,
            "text": chunk.text,
            "embedding_model": embedding_client.MODEL,
            "embedding": vector,
        }
        for (entry, chunk), vector in zip(pieces, vectors, strict=True)
    ]
    for start in range(0, len(rows), _INSERT_BATCH):
        await session.execute(
            pg_insert(ContextChunk)
            .values(rows[start : start + _INSERT_BATCH])
            .on_conflict_do_nothing(constraint="uq_context_chunks_entry_model_index")
        )
    await session.commit()
    logger.info("Indexed %d chunks across %d context entries", len(rows), len({e.entry_id for e, _ in pieces}))
    return len(rows)


async def index_run(run_id: str | uuid.UUID) -> None:
    """Index everything this run can see that is not indexed yet. For a background task after a
    save: opens its own session, never raises."""
    if not enabled():
        return
    try:
        run_uuid = run_id if isinstance(run_id, uuid.UUID) else uuid.UUID(str(run_id))
        async with get_sessionmaker()() as session:
            if not await available(session):
                return
            await ensure_indexed(session, await visible_entries(session, run_uuid))
    except Exception:  # noqa: BLE001 - indexing is an optimisation; a search indexes lazily anyway
        logger.exception("Background indexing failed for run %s", run_id)


@dataclass(frozen=True)
class Hit:
    context_entry_id: uuid.UUID
    context_key: str
    chunk_index: int
    section_path: str
    text: str
    score: float


async def search(
    session: AsyncSession, vector: list[float], entry_ids: list[uuid.UUID], k: int
) -> list[Hit]:
    """The `k` sections nearest `vector` among `entry_ids`' chunks, best first. Exact, not ANN:
    see `ContextChunk`'s docstring."""
    if not entry_ids or k <= 0:
        return []
    distance = ContextChunk.embedding.cosine_distance(vector)
    rows = (
        await session.execute(
            select(
                ContextChunk.context_entry_id,
                ContextChunk.context_key,
                ContextChunk.chunk_index,
                ContextChunk.section_path,
                ContextChunk.text,
                distance.label("distance"),
            )
            .where(
                ContextChunk.context_entry_id.in_(entry_ids),
                ContextChunk.embedding_model == embedding_client.MODEL,
            )
            .order_by(distance)
            .limit(k)
        )
    ).all()
    return [
        Hit(
            context_entry_id=row.context_entry_id,
            context_key=row.context_key,
            chunk_index=row.chunk_index,
            section_path=row.section_path,
            text=row.text,
            score=round(1.0 - float(row.distance), 4),
        )
        for row in rows
    ]


async def _chunk_count(session: AsyncSession, entry_id: uuid.UUID) -> int:
    return len(
        (
            await session.execute(
                select(ContextChunk.chunk_index).where(
                    ContextChunk.context_entry_id == entry_id,
                    ContextChunk.embedding_model == embedding_client.MODEL,
                )
            )
        ).all()
    )


# --------------------------------------------------------------------------------------
# B. Relevant sections instead of whole documents, at generation time
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class RetrievalSpec:
    """One input that may be sent as its relevant sections rather than whole.

    `keys` are looked up in order, as `hydrateContextFromDb` does: the field's own key (a document
    filed by hand) first, then the asset that produces it (an approved stage). `query` says what the
    receiving prompt uses this input *for*, which is what the sections are ranked against.
    """

    keys: tuple[str, ...]
    query: str
    budget_chars: int = 14_000


_CRO_KEYS = ("cro_rewritten_copy", "cro")

_BRAND_VOICE = (
    "Brand voice and tone; the words used for the buyer, the offer and the first step; the value "
    "proposition; proof points, results, case studies and testimonials; guarantees and risk "
    "reversal; offers and pricing; calls to action; objections and how they are answered."
)
_MESSAGING = (
    "Messaging framework: the headline promise and value proposition; who it is for and the problem "
    "it solves; the offer, pricing and what is included; proof points and results; objections and "
    "answers; calls to action; terminology for the buyer and the offer."
)

#: The opt-in list. Everything not named here is sent whole, as before.
#:
#: Only inputs where the CRO rewrite is read as *reference* (voice, proof, messaging) are here.
#: Where it is the copy itself — the Pillar Page's `improved_page_content`, the Funnel's
#: `cro_rewritten_copy` — every word is laid out, so it must stay whole and is deliberately absent.
#: `test_page_copy_inputs_are_never_retrieved` pins that.
RETRIEVAL_FIELDS: dict[str, dict[str, RetrievalSpec]] = {
    "offers": {"cro_messaging_framework": RetrievalSpec(_CRO_KEYS, _MESSAGING)},
    "lead_magnet": {"cro_messaging_framework": RetrievalSpec(_CRO_KEYS, _MESSAGING)},
    "blog": {"cro_rewritten_copy": RetrievalSpec(_CRO_KEYS, _BRAND_VOICE)},
    "content_marketing_strategy": {"cro_brand_voice_framework_optional": RetrievalSpec(_CRO_KEYS, _BRAND_VOICE)},
    "webinar": {"cro_rewritten_copy": RetrievalSpec(_CRO_KEYS, _BRAND_VOICE)},
    "book": {"brand_voice_tone_reference_optional": RetrievalSpec(_CRO_KEYS, _BRAND_VOICE)},
    "podcast": {"cro_rewritten_copy": RetrievalSpec(_CRO_KEYS, _BRAND_VOICE)},
    "sms_sequence": {"cro_rewritten_copy": RetrievalSpec(_CRO_KEYS, _BRAND_VOICE)},
    "plan_of_action": {"cro_messaging_framework_optional": RetrievalSpec(_CRO_KEYS, _MESSAGING)},
}

#: Inputs whose every word reaches the page. Never in `RETRIEVAL_FIELDS`, whatever their size.
FULL_TEXT_FIELDS: frozenset[tuple[str, str]] = frozenset(
    {("pillar_page", "improved_page_content"), ("funnel", "cro_rewritten_copy")}
)

_WS = re.compile(r"\s+")


def _norm(value: str) -> str:
    return _WS.sub(" ", (value or "").replace("’", "'").replace("‘", "'")).strip()


def same_document(answer: str, stored: str) -> bool:
    """Whether the answer the browser sent *is* the stored document, rather than one the operator
    supplied instead. Only then may it be cut down: a document the operator pasted is theirs, is not
    indexed under this key, and goes through whole. Compared on the opening, whitespace-normalised,
    because the browser's copy and the stored copy can differ in trailing whitespace."""
    a, b = _norm(answer), _norm(stored)
    if not a or not b:
        return False
    head = min(len(a), len(b), 2000)
    return a[:head] == b[:head] and abs(len(a) - len(b)) <= max(200, len(b) // 50)


def select_sections(hits: list[Hit], budget_chars: int) -> list[Hit]:
    """The best-scoring sections that fit in `budget_chars`, in document order. The opening section
    is always kept when it fits, because it is where a document says what it is."""
    chosen: dict[int, Hit] = {}
    used = 0
    opening = next((h for h in hits if h.chunk_index == 0), None)
    if opening is not None and len(opening.text) <= budget_chars:
        chosen[0] = opening
        used += len(opening.text)
    for hit in sorted(hits, key=lambda h: h.score, reverse=True):
        if hit.chunk_index in chosen:
            continue
        if used + len(hit.text) > budget_chars:
            continue
        chosen[hit.chunk_index] = hit
        used += len(hit.text)
    return [chosen[i] for i in sorted(chosen)]


def render_sections(label: str, chosen: list[Hit], total: int) -> str:
    head = (
        f"[Relevant sections of the approved {label}: {len(chosen)} of {total}, chosen for this input "
        "by meaning. The full document is approved in this run; the sections left out were judged "
        "unrelated to this input.]"
    )
    parts: list[str] = [head]
    previous = -1
    for hit in chosen:
        if previous >= 0 and hit.chunk_index != previous + 1:
            parts.append("[…]")
        parts.append(hit.text)
        previous = hit.chunk_index
    return "\n\n".join(parts)


async def expand_retrieval(
    run_id: str | None, asset_id: str, answers: dict[str, str]
) -> tuple[dict[str, str], dict | None]:
    """`answers` with each `RETRIEVAL_FIELDS` input that is a stored document over its budget
    replaced by its relevant sections. Returns the answers and a `context_retrieval` SSE event, or
    `(answers, None)` when nothing was eligible. Never raises: on any failure the input stays whole
    and the event says why."""
    specs = RETRIEVAL_FIELDS.get(asset_id)
    if not specs or not run_id:
        return answers, None
    due = {
        field_id: spec
        for field_id, spec in specs.items()
        if len(str(answers.get(field_id) or "")) > spec.budget_chars
        and (asset_id, field_id) not in FULL_TEXT_FIELDS
    }
    if not due:
        return answers, None
    if not enabled():
        return answers, None

    report: list[dict] = []
    notes: list[str] = []
    out = dict(answers)
    try:
        run_uuid = uuid.UUID(str(run_id))
        async with get_sessionmaker()() as session:
            if not await available(session):
                return answers, None
            entries = {e.context_key: e for e in await visible_entries(session, run_uuid)}
            for field_id, spec in due.items():
                answer = out[field_id]
                entry = next((entries[k] for k in spec.keys if k in entries), None)
                if entry is None or not same_document(answer, entry.text):
                    notes.append(f"{field_id}: sent whole (not the stored document)")
                    continue
                await ensure_indexed(session, [entry])
                total = await _chunk_count(session, entry.entry_id)
                if total == 0:
                    notes.append(f"{field_id}: sent whole (document not searchable)")
                    continue
                [vector] = await embedding_client.embed([spec.query])
                hits = await search(session, vector, [entry.entry_id], k=total)
                chosen = select_sections(hits, spec.budget_chars)
                if not chosen:
                    notes.append(f"{field_id}: sent whole (no section fitted)")
                    continue
                label = humanise_key(entry.context_key)
                out[field_id] = render_sections(label, chosen, total)
                report.append(
                    {
                        "field_id": field_id,
                        "context_key": entry.context_key,
                        "sections": len(chosen),
                        "total_sections": total,
                        "chars_before": len(answer),
                        "chars_after": len(out[field_id]),
                    }
                )
    except Exception as exc:  # noqa: BLE001 - the stage must run whether or not search works
        logger.exception("Context retrieval failed for %s; inputs sent whole", asset_id)
        return answers, {"type": "context_retrieval", "fields": [], "notes": [f"Sent whole: {exc}"]}

    if report:
        logger.info(
            "Retrieval for %s: %s",
            asset_id,
            ", ".join(f"{r['field_id']} {r['chars_before']}→{r['chars_after']} chars" for r in report),
        )
    return out, {"type": "context_retrieval", "fields": report, "notes": notes}


# --------------------------------------------------------------------------------------
# A + C. Suggested answers for intake questions
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class PrefillField:
    field_id: str
    label: str
    help: str = ""
    kind: str = "text"
    choices: tuple[str, ...] = ()
    #: Restrict the search to these context keys. Set for a field whose own key resolved but whose
    #: sub-key the regex extractor could not find (C): only that document should answer it.
    keys: tuple[str, ...] = ()

    def query(self) -> str:
        parts = [self.label]
        if self.help:
            parts.append(self.help)
        if self.choices:
            parts.append("Options: " + "; ".join(self.choices))
        return ". ".join(p.strip().rstrip(".") for p in parts if p.strip())


@dataclass(frozen=True)
class Suggestion:
    field_id: str
    answer: str
    quote: str
    context_key: str
    source_label: str
    section_path: str
    score: float


@dataclass
class PrefillResult:
    #: `ok` (searched; may still be empty), `off` (no key or switch off), `unavailable` (no
    #: pgvector table), `empty` (nothing approved to search yet), `error`.
    status: str
    suggestions: list[Suggestion] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    #: Answers the judge gave that failed verification, for the logs and the tests.
    dropped: int = 0

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "suggestions": [asdict(s) for s in self.suggestions],
            "notes": self.notes,
            "dropped": self.dropped,
        }


def _prefill_sources(entries: list[ScopedEntry], asset_id: str) -> list[ScopedEntry]:
    """What a suggestion may come from: not a competitor listing, and not this stage's own earlier
    output (an input answered from the output it produced is circular)."""
    return [
        e
        for e in entries
        if not e.context_key.startswith(_PREFILL_EXCLUDED_PREFIXES)
        and e.context_key != asset_id
        and e.written_by_asset_id != asset_id
    ]


def build_prefill_prompt(fields: list[PrefillField], excerpts: list[Hit], field_hits: dict[str, list[int]]) -> str:
    lines = [
        "You fill in intake questions for a marketing pipeline from documents already approved for "
        "this client. An operator confirms every answer you give before it is used.",
        "",
        "For each QUESTION, read only the EXCERPTS listed for it. Answer only when an excerpt states "
        "the answer about this client explicitly. Leave the question out otherwise. A guess, an "
        "inference, a generic best practice, or a fact about a competitor is worse than no answer: "
        "the operator is simply asked instead.",
        "",
        "Rules:",
        '- "quote": the exact words from the excerpt that state the answer, copied verbatim, 3-200 '
        "characters. Do not paraphrase, fix typos or join two passages.",
        '- "answer": the answer as the operator would type it, concise. For a question with CHOICES, '
        'exactly one of the choices, copied verbatim. For a yes/no question, "Yes" or "No".',
        '- "excerpt": the number of the excerpt the quote is from.',
        "",
        'Return only JSON: {"answers": [{"field_id": "...", "answer": "...", "quote": "...", "excerpt": 1}]}',
        "",
        "EXCERPTS",
    ]
    for number, hit in enumerate(excerpts, start=1):
        where = humanise_key(hit.context_key) + (f" › {hit.section_path}" if hit.section_path else "")
        lines += [f"[{number}] ({where})", hit.text, ""]
    lines.append("QUESTIONS")
    for f in fields:
        numbers = field_hits.get(f.field_id)
        if not numbers:
            continue
        row = f"- field_id: {f.field_id} | question: {f.label}"
        if f.help:
            row += f" | help: {f.help}"
        if f.choices:
            row += " | CHOICES: " + " ; ".join(f.choices)
        if f.kind == "boolean_flag":
            row += " | yes/no"
        row += " | excerpts: " + ", ".join(str(n) for n in numbers)
        lines.append(row)
    return "\n".join(lines)


def _verified(quote: str, excerpt: str) -> bool:
    q = _norm(quote).casefold().strip("\"'“”‘’ ")
    return len(q) >= 3 and q in _norm(excerpt).casefold()


def _normalise_answer(f: PrefillField, answer: str) -> str | None:
    answer = answer.strip()
    if not answer or len(answer) > _MAX_ANSWER_CHARS:
        return None
    if f.kind == "boolean_flag":
        if re.fullmatch(r"(?i)y(es)?|true", answer):
            return "Yes"
        if re.fullmatch(r"(?i)no?|false", answer):
            return "No"
        return None
    if f.kind == "enum_choice" and f.choices:
        return next((c for c in f.choices if c.casefold() == answer.casefold()), None)
    if f.kind == "number":
        return answer if re.fullmatch(r"-?\d+(\.\d+)?", answer.replace(",", "")) else None
    return answer


def parse_prefill_reply(
    raw: str, fields: list[PrefillField], excerpts: list[Hit], field_hits: dict[str, list[int]]
) -> tuple[list[Suggestion], int]:
    """The judge's answers that survive verification, and how many did not. An answer survives
    when its question was asked, its excerpt was one offered for that question, its quote is in that
    excerpt, and its value fits the question (a listed choice, a yes/no, a number)."""
    match = re.search(r"\{.*\}", raw or "", re.DOTALL)
    if not match:
        return [], 0
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return [], 0
    items = data.get("answers") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return [], 0

    by_id = {f.field_id: f for f in fields}
    suggestions: dict[str, Suggestion] = {}
    dropped = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        f = by_id.get(str(item.get("field_id") or ""))
        try:
            number = int(item.get("excerpt"))
        except (TypeError, ValueError):
            number = 0
        if f is None or f.field_id in suggestions or number not in field_hits.get(f.field_id, []):
            dropped += 1
            continue
        hit = excerpts[number - 1]
        quote = str(item.get("quote") or "")
        answer = _normalise_answer(f, str(item.get("answer") or ""))
        if answer is None or not _verified(quote, hit.text):
            dropped += 1
            continue
        suggestions[f.field_id] = Suggestion(
            field_id=f.field_id,
            answer=answer,
            quote=_norm(quote)[:300],
            context_key=hit.context_key,
            source_label=humanise_key(hit.context_key),
            section_path=hit.section_path,
            score=hit.score,
        )
    return [suggestions[f.field_id] for f in fields if f.field_id in suggestions], dropped


async def _judge(prompt: str, on_usage: OnUsage | None) -> str:
    started = time.monotonic()
    response = await get_client().messages.create(
        model=PREFILL_MODEL,
        max_tokens=_PREFILL_MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
    )
    if on_usage is not None:
        await on_usage(
            CallUsage.from_response(
                response, requested_model=PREFILL_MODEL, duration_ms=int((time.monotonic() - started) * 1000)
            )
        )
    return next((b.text for b in response.content if b.type == "text"), "")


async def prefill(
    session: AsyncSession,
    run_uuid: uuid.UUID,
    asset_id: str,
    fields: list[PrefillField],
    on_usage: OnUsage | None = None,
) -> PrefillResult:
    """Suggested answers for the questions a stage is about to ask, from this run's approved
    documents. One embedding request for every question, then one Haiku call for the stage, never
    one per question. Never raises."""
    fields = [f for f in fields if f.field_id not in NEVER_SUGGEST and f.label.strip()][:MAX_PREFILL_FIELDS]
    if not fields:
        return PrefillResult(status="ok")
    if not enabled():
        return PrefillResult(status="off", notes=["Context search is off (no OpenAI key, or VECTOR_CONTEXT=off)."])
    try:
        if not await available(session):
            return PrefillResult(status="unavailable", notes=["pgvector is not installed on this database."])
        entries = _prefill_sources(await visible_entries(session, run_uuid), asset_id)
        if not entries:
            return PrefillResult(status="empty")
        await ensure_indexed(session, entries)

        vectors = await embedding_client.embed([f.query() for f in fields])
        by_key = {e.context_key: e.entry_id for e in entries}
        all_ids = list(by_key.values())

        # Measure: the nearest sections per question, above the floor.
        excerpts: list[Hit] = []
        index_of: dict[tuple[uuid.UUID, int], int] = {}
        field_hits: dict[str, list[int]] = {}
        ranked: list[tuple[float, str, Hit]] = []
        for f, vector in zip(fields, vectors, strict=True):
            ids = [by_key[k] for k in f.keys if k in by_key] if f.keys else all_ids
            for hit in await search(session, vector, ids, HITS_PER_FIELD):
                if hit.score >= MIN_SCORE:
                    ranked.append((hit.score, f.field_id, hit))
        # Best matches claim the excerpt budget first, so a weak match for one question cannot
        # crowd out a strong one for another.
        used = 0
        for _score, field_id, hit in sorted(ranked, key=lambda r: r[0], reverse=True):
            slot = (hit.context_entry_id, hit.chunk_index)
            if slot not in index_of:
                if used + len(hit.text) > _PREFILL_EXCERPT_CHARS:
                    continue
                excerpts.append(hit)
                index_of[slot] = len(excerpts)
                used += len(hit.text)
            field_hits.setdefault(field_id, []).append(index_of[slot])
        if not field_hits:
            return PrefillResult(status="ok")

        # Judge: one call for every question that has something to read.
        prompt = build_prefill_prompt(fields, excerpts, field_hits)
        raw = await _judge(prompt, on_usage)
        suggestions, dropped = parse_prefill_reply(raw, fields, excerpts, field_hits)
    except Exception as exc:  # noqa: BLE001 - suggestions are advisory; the intake asks as before
        logger.exception("Prefill failed for %s on run %s", asset_id, run_uuid)
        return PrefillResult(status="error", notes=[f"Could not search earlier documents: {exc}"])

    logger.info(
        "Prefill %s: %d questions, %d with matches, %d suggested, %d dropped",
        asset_id,
        len(fields),
        len(field_hits),
        len(suggestions),
        dropped,
    )
    return PrefillResult(status="ok", suggestions=suggestions, dropped=dropped)
