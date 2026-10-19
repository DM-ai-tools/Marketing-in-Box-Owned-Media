"""The `asset_definitions` seed — the 25-stage DAG registry, and the idempotent upsert that
applies it.

This module owns the *data*. Two callers apply it:

- `app.main`'s lifespan, on every boot (see `seed_asset_definitions`). `asset_definitions` is
  slow-changing reference data that `run_stages`, `context_entries`, `prompt_recipes` and
  `field_schema_registry` all FK to, so an unseeded database fails at the first Save with
  `404 Unknown asset_id: 'icp'` while every other route still answers 200 — a failure that reads
  as a broken feature rather than a missing seed. Seeding at startup means a fresh database (a new
  Railway environment, a rebuilt volume, a teammate's local Postgres) is correct without anyone
  remembering a manual step.
- `scripts/seed_asset_definitions.py`, for running it by hand against an arbitrary DSN, and for
  its `--check` mode which validates this table with no database at all.

Why this is not an Alembic data migration
-----------------------------------------
`asset_definitions` is described in app/db/SCHEMA.md as "slow-changing reference/config data," and
the doc explicitly expects the asset vocabulary to keep growing (new prompts, new stages). Baking a
fixed INSERT into a revision would freeze that revision's seed data in history — every future
change to a stage's `depends_on`/`is_gated`/`model_tier` would need a new migration purely to
correct config data, not schema. The upsert below is keyed on `asset_id` and re-runnable, so every
row converges to the definition here without a migration.

Idempotency
-----------
Postgres `INSERT ... ON CONFLICT (asset_id) DO UPDATE`, so applying this twice (or on every boot)
leaves the table in the same end state as applying it once. `reads_context_keys` and
`writes_context_keys` are deliberately left unset — they fall back to the column's `'[]'::jsonb`
server default, and are not part of this seed's scope.

A note on the row count
-----------------------
26 rows: 3 gated foundation stages, 12 generation stages, 11 competitor-analysis stages
(`competitor_analysis_funnel` was added after the merge, for the funnel's Step 2).
`seo_pillar_page` was merged into `pillar_page` (see that row's comment below), which is why this
was 25 rather than 26 until the funnel's competitor stage was added.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------------------
# DAG data. (asset_id, depends_on, is_gated, model_tier)
# --------------------------------------------------------------------------------------
# `sequence_order` below is simply this list's position (a stable topological order the rows
# happen to already be given in -- every `depends_on` edge points strictly earlier in the list,
# verified by `validate_dag()`). `phase` is 1 for all rows: nothing in this list is a Phase 2
# remarketing asset per docs/Marketing_in_a_Box_Session_Context.md Sec. 6 ("Phase 2 -- Remarketing
# sub-service" is a distinct, separately-tagged track not represented in this task's DAG).
# `display_name` is a humanized form of asset_id (acronyms upper-cased) -- cosmetic only, not
# read by any orchestrator logic.

# `model_tier` note: no row declares `opus` any more. The three that did — `icp`, `cro`,
# `pillar_page` — moved to `sonnet` when the pipeline dropped Opus entirely; see
# `app/services/generation.py`'s STAGE_CONFIGS, which is what actually selects the model. The
# `opus` member survives on the enum rather than being migrated away, because rows written by runs
# made before the move still carry it and a monitoring read of those rows must still resolve.
DAG_ROWS: list[dict] = [
    {"asset_id": "icp", "depends_on": [], "is_gated": True, "model_tier": "sonnet"},
    {"asset_id": "competitor_analysis_cro", "depends_on": [], "is_gated": False, "model_tier": "sonnet"},
    {"asset_id": "cro", "depends_on": ["icp", "competitor_analysis_cro"], "is_gated": True, "model_tier": "sonnet"},
    {"asset_id": "competitor_analysis_seo_pillar_page", "depends_on": [], "is_gated": False, "model_tier": "sonnet"},
    {
        # One merged Pillar Page stage. The former `seo_pillar_page` row (same prompt file, run a
        # second time as an "SEO variant pass") is gone: that prompt is now v2.0 and carries the
        # SEO + competitor-benchmark pass itself, so `competitor_analysis_seo_pillar_page` — which
        # used to pair with the variant — is a hard edge on `pillar_page` instead.
        #
        # Applying this script does not delete the retired `seo_pillar_page` row from a database
        # that already has it (the upsert only writes the rows listed here). It is left in place on
        # purpose: `run_stages` / `context_entries` / `approval_audit_log` rows from runs made
        # before the merge still FK to it.
        "asset_id": "pillar_page",
        "depends_on": ["cro", "competitor_analysis_seo_pillar_page"],
        "is_gated": True,
        "model_tier": "sonnet",
    },
    {"asset_id": "competitor_analysis_offers", "depends_on": [], "is_gated": False, "model_tier": "sonnet"},
    {"asset_id": "offers", "depends_on": ["icp", "cro", "competitor_analysis_offers"], "is_gated": False, "model_tier": "sonnet"},
    {"asset_id": "competitor_analysis_funnel", "depends_on": [], "is_gated": False, "model_tier": "sonnet"},
    {"asset_id": "funnel", "depends_on": ["icp", "cro", "pillar_page", "competitor_analysis_funnel"], "is_gated": False, "model_tier": "sonnet"},
    {"asset_id": "funnel_hub_media", "depends_on": ["icp", "cro", "pillar_page"], "is_gated": False, "model_tier": "sonnet"},
    {"asset_id": "competitor_analysis_lead_magnet", "depends_on": [], "is_gated": False, "model_tier": "sonnet"},
    {
        "asset_id": "lead_magnet",
        "depends_on": ["icp", "cro", "pillar_page", "funnel", "offers", "competitor_analysis_lead_magnet"],
        "is_gated": False,
        "model_tier": "sonnet",
    },
    {
        # cro / funnel / lead_magnet / offers are optional soft-reads (read from context if
        # present, per the Plan-of-Action synthesis nature of this stage) -- NOT hard DAG edges,
        # per the task's explicit instruction not to add them to depends_on.
        "asset_id": "plan_of_action",
        "depends_on": ["icp"],
        "is_gated": False,
        "model_tier": "sonnet",
    },
    {"asset_id": "competitor_analysis_blog", "depends_on": [], "is_gated": False, "model_tier": "sonnet"},
    {"asset_id": "blog", "depends_on": ["icp", "cro", "pillar_page", "competitor_analysis_blog"], "is_gated": False, "model_tier": "sonnet"},
    {"asset_id": "competitor_analysis_content_marketing", "depends_on": [], "is_gated": False, "model_tier": "sonnet"},
    {
        "asset_id": "content_marketing_strategy",
        "depends_on": ["icp", "cro", "competitor_analysis_content_marketing"],
        "is_gated": False,
        "model_tier": "sonnet",
    },
    {"asset_id": "competitor_analysis_social_content_strategy", "depends_on": [], "is_gated": False, "model_tier": "sonnet"},
    {
        "asset_id": "social_content_strategy_audit",
        "depends_on": ["competitor_analysis_social_content_strategy"],
        "is_gated": False,
        "model_tier": "sonnet",
    },
    {"asset_id": "sms_sequence", "depends_on": ["funnel"], "is_gated": False, "model_tier": "haiku"},
    {"asset_id": "competitor_analysis_webinars", "depends_on": [], "is_gated": False, "model_tier": "sonnet"},
    {"asset_id": "webinar", "depends_on": ["icp", "cro", "competitor_analysis_webinars"], "is_gated": False, "model_tier": "sonnet"},
    {"asset_id": "competitor_analysis_book", "depends_on": [], "is_gated": False, "model_tier": "sonnet"},
    {
        # webinar / icp remain book's only hard depends_on -- competitor_analysis_book is a soft,
        # optional read (book.json's competitor_book_positioning field, fallback=ask_user_if_missing)
        # per the user's explicit decision: wire the schema field, but don't make it a blocking
        # DAG edge, since Webinar-to-Book-Architect-Prompt.md's own text never references it.
        "asset_id": "book",
        "depends_on": ["webinar", "icp"],
        "is_gated": False,
        "model_tier": "sonnet",
    },
    {"asset_id": "competitor_analysis_podcast", "depends_on": [], "is_gated": False, "model_tier": "sonnet"},
    {
        # `webinar` is included as a real FK dependency (the safe default per the task) even
        # though it is optional/soft context for this stage in the intended design -- not a hard
        # blocking prerequisite. Left here rather than dropped so the orchestrator can still
        # resolve it as an upstream node if a future revision tightens this to a hard gate.
        "asset_id": "podcast",
        "depends_on": ["icp", "competitor_analysis_podcast", "webinar"],
        "is_gated": False,
        "model_tier": "sonnet",
    },
]

_ACRONYMS = {"icp": "ICP", "cro": "CRO", "seo": "SEO", "sms": "SMS"}


def humanize(asset_id: str) -> str:
    words = asset_id.split("_")
    return " ".join(_ACRONYMS.get(w, w.capitalize()) for w in words)


def build_rows() -> list[dict]:
    """Attach `phase`, `sequence_order`, `display_name` to each DAG_ROWS entry."""
    rows = []
    for i, row in enumerate(DAG_ROWS, start=1):
        rows.append(
            {
                "asset_id": row["asset_id"],
                "display_name": humanize(row["asset_id"]),
                "phase": 1,
                "sequence_order": i,
                "is_gated": row["is_gated"],
                "depends_on": row["depends_on"],
                "model_tier": row["model_tier"],
            }
        )
    return rows


def validate_dag(rows: list[dict]) -> list[str]:
    """Pure-Python structural validation -- no DB connection needed. Returns a list of problems
    (empty list == valid)."""
    problems: list[str] = []
    known_ids = {r["asset_id"] for r in rows}

    if len(known_ids) != len(rows):
        problems.append("duplicate asset_id values in DAG_ROWS")

    valid_tiers = {"opus", "sonnet", "haiku"}
    seen_order: dict[tuple[int, int], str] = {}
    id_to_order = {r["asset_id"]: r["sequence_order"] for r in rows}

    for row in rows:
        aid = row["asset_id"]
        if row["model_tier"] not in valid_tiers:
            problems.append(f"{aid}: model_tier {row['model_tier']!r} not in {valid_tiers}")
        if row["phase"] not in (1, 2):
            problems.append(f"{aid}: phase {row['phase']!r} must be 1 or 2")

        key = (row["phase"], row["sequence_order"])
        if key in seen_order:
            problems.append(f"{aid}: (phase, sequence_order)={key} collides with {seen_order[key]}")
        seen_order[key] = aid

        for dep in row["depends_on"]:
            if dep not in known_ids:
                problems.append(f"{aid}: depends_on references unknown asset_id {dep!r}")
            elif id_to_order.get(dep, 0) >= id_to_order[aid]:
                problems.append(
                    f"{aid}: depends_on {dep!r} does not precede it in sequence_order "
                    "(list is not a valid topological order / possible cycle)"
                )

    return problems


# --------------------------------------------------------------------------------------
# Applying the seed
# --------------------------------------------------------------------------------------

_UPDATABLE_COLUMNS = (
    "display_name",
    "phase",
    "sequence_order",
    "is_gated",
    "depends_on",
    "model_tier",
)


def build_upsert(row: dict):
    """The `INSERT ... ON CONFLICT (asset_id) DO UPDATE` statement for one row.

    Kept separate so `scripts/seed_asset_definitions.py --check` can compile it without a
    connection and prove it produces valid SQL against the real column types.
    """
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from app.db.models import AssetDefinition

    stmt = pg_insert(AssetDefinition.__table__).values(**row)
    return stmt.on_conflict_do_update(
        index_elements=[AssetDefinition.__table__.c.asset_id],
        set_={col: getattr(stmt.excluded, col) for col in _UPDATABLE_COLUMNS},
    )


# Retired stages are parked at or above this `sequence_order` rather than deleted — see
# `_park_retired_stages`. Chosen to sit far above any plausible live DAG size while staying well
# inside SMALLINT.
PARK_BASE = 1000


async def _park_retired_stages(session, live_ids: list[str]) -> list[str]:
    """Move rows this seed no longer defines out of the live `sequence_order` range.

    A stage that is dropped from `DAG_ROWS` is deliberately *not* deleted — `run_stages`,
    `context_entries` and `approval_audit_log` rows from runs made before it was retired still FK to
    its `asset_id` (this is why the `pillar_page` row's comment says the merge leaves the old
    `seo_pillar_page` row in place). But leaving it at its old `sequence_order` keeps that slot
    occupied forever, and the stage that inherits the slot then collides with it on
    `uq_asset_definitions_phase_order` — which is exactly what a deferred constraint cannot rescue,
    because the collision is in the *committed* end state, not an intermediate one.

    Parking slots are assigned by `asset_id` order, so the same set of retired stages always lands
    on the same slots: re-running this is a no-op rather than a walk further up the range.
    """
    from sqlalchemy import bindparam, text

    result = await session.execute(
        text(
            """
            WITH retired AS (
                SELECT asset_id,
                       :park_base + (row_number() OVER (ORDER BY asset_id))::int AS slot
                FROM asset_definitions
                WHERE phase = 1 AND asset_id <> ALL(:live_ids)
            )
            UPDATE asset_definitions a
            SET sequence_order = retired.slot
            FROM retired
            WHERE a.asset_id = retired.asset_id
              AND a.sequence_order IS DISTINCT FROM retired.slot
            RETURNING a.asset_id
            """
        ).bindparams(bindparam("live_ids", expanding=False), park_base=PARK_BASE),
        {"live_ids": live_ids},
    )
    return [row[0] for row in result]


async def apply_asset_definitions(session) -> int:
    """Bring `asset_definitions` in line with `DAG_ROWS`. Returns the number of rows upserted.

    Does not commit — the caller owns the transaction boundary, and the boundary matters here:
    `uq_asset_definitions_phase_order` is DEFERRABLE INITIALLY DEFERRED so that the parking pass and
    the upserts below are judged together, at COMMIT, rather than statement by statement.

    Raises ValueError if `DAG_ROWS` is malformed. That is a programming error in this file, not an
    environment problem, so it is not swallowed: a database seeded from a broken DAG is worse than
    one that refuses to seed.
    """
    rows = build_rows()
    problems = validate_dag(rows)
    if problems:
        raise ValueError("DAG_ROWS failed validation: " + "; ".join(problems))

    parked = await _park_retired_stages(session, [row["asset_id"] for row in rows])
    if parked:
        logger.info("Parked %d retired asset_definitions row(s): %s", len(parked), ", ".join(parked))

    for row in rows:
        await session.execute(build_upsert(row))
    return len(rows)


async def seed_asset_definitions() -> int:
    """Apply the seed against the app's own engine. Returns the number of rows written.

    Called from `app.main`'s lifespan on every boot. Errors propagate — the caller decides whether
    an unreachable database should be fatal.
    """
    from app.db.base import get_sessionmaker

    session_factory = get_sessionmaker()
    async with session_factory() as session:
        count = await apply_asset_definitions(session)
        await session.commit()
    return count
