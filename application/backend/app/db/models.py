"""SQLAlchemy 2.0 declarative models for Marketing-in-a-Box.

Two families of tables, per the task that produced this file:

1. Core pipeline tables (docs/Marketing_in_a_Box_Session_Context.md, Sec. 4 "Data Storage" and
   Sec. 6 "Asset Wiring" — the "Permanent" tier only):
   - Client                -> clients
   - Context store         -> context_entries          (additive-only, versioned)
   - Execution graph/state -> asset_definitions, runs, run_stages
   - Approval/edit audit   -> approval_audit_log
   - Prompt recipe library -> prompt_recipes

2. Conversational Intake Engine tables (docs/Conversational_Intake_Engine_Design.md, Sec. 5 —
   exact column lists as specified there):
   - field_schema_registry
   - field_sessions
   - attachments

See app/db/SCHEMA.md for the full rationale behind each table, constraint, and index.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

# --------------------------------------------------------------------------------------
# Enums
#
# Reconciliation note (per task instructions): the two source docs name pipeline-stage
# states slightly differently. The Intake design (Sec. 3) defines the FieldSession's own
# state machine as NOT_STARTED -> COLLECTING -> READY_FOR_GENERATION -> CONFIRMED. The PRD
# (referenced from Intake design Sec. 5) separately mentions an `INTAKE_IN_PROGRESS`
# stage sub-state preceding DRAFTING/AWAITING_REVIEW. These are the same moment in the
# pipeline described from two altitudes: FieldSessionStatus is the fine-grained state of
# one intake conversation; StageStatus is the coarse-grained state of an entire DAG stage
# (intake -> generation -> review -> approval) shown on the strategist's stage timeline.
# StageStatus reuses the literal name COLLECTING (rather than introducing a second synonym
# INTAKE_IN_PROGRESS) for the states the two machines share, so a FieldSession's status can
# be copied onto its parent RunStage without a translation table.
# --------------------------------------------------------------------------------------


class RunStatus(str, enum.Enum):
    """Overall status of one client pipeline run (all stages, both phases)."""

    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    PAUSED = "PAUSED"
    CANCELLED = "CANCELLED"


class StageStatus(str, enum.Enum):
    """Per-(run, asset) stage lifecycle. Superset of FieldSessionStatus — see note above."""

    NOT_STARTED = "NOT_STARTED"
    COLLECTING = "COLLECTING"  # intake in progress (== PRD's INTAKE_IN_PROGRESS)
    READY_FOR_GENERATION = "READY_FOR_GENERATION"
    CONFIRMED = "CONFIRMED"
    DRAFTING = "DRAFTING"
    AWAITING_REVIEW = "AWAITING_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"  # HITL "sent back" loop; app layer re-drives to DRAFTING


class FieldSessionStatus(str, enum.Enum):
    """Exact state machine from Conversational_Intake_Engine_Design.md Sec. 3."""

    NOT_STARTED = "NOT_STARTED"
    COLLECTING = "COLLECTING"
    READY_FOR_GENERATION = "READY_FOR_GENERATION"
    CONFIRMED = "CONFIRMED"


class AuditAction(str, enum.Enum):
    """What a strategist did to a stage's draft output, per the audit trail requirement."""

    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EDITED = "EDITED"


class ModelTier(str, enum.Enum):
    """Which Claude cost tier a DAG stage (or the Interviewer Agent) should call.

    Added by the `add_model_tier_to_asset_definitions` migration, after the initial schema.
    See app/db/SCHEMA.md, "LLM model tiers" for the full rationale; in short:
    - OPUS: no longer used by any seeded stage. The 3 gated foundation stages (icp, cro,
      pillar_page) were on this tier — errors there cascade to every downstream stage — and are
      now on SONNET: the cascade is answered by the human review gate that already sits on those
      three stages, which made Opus's 2.5x input/output premium hard to justify on output a
      strategist signs off on regardless. Kept on the enum because rows from earlier runs carry
      it; a read of those rows must still resolve. Do not reuse for new stages.
    - SONNET: every generation stage bar the Haiku three, and all competitor-analysis stages, plus
      the 3 foundation stages described above — competitor
      analysis specifically needs real verification reasoning (confirming a competitor is
      genuine, not a directory/aggregator) rather than cheap pattern-matching, to avoid
      hallucinating fake competitors.
    - HAIKU: narrow/short-output or high-frequency work — `sms_sequence` (short, templated
      output) and the Interviewer Agent (runs once per field per turn; not itself a row in
      this table — see SCHEMA.md).
    """

    OPUS = "opus"
    SONNET = "sonnet"
    HAIKU = "haiku"


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


# --------------------------------------------------------------------------------------
# Core tables
# --------------------------------------------------------------------------------------


class Client(Base):
    """A client engagement. Satisfies Session Context Sec. 4 "Client profile object".

    Deliberately holds only the operator-entered intake fields named explicitly in Sec. 6's
    ICP row ("company, industry, region, service focus, known competitors") — everything the
    pipeline itself derives (ICP personas, CRO copy, etc.) lives in `context_entries`, keyed
    by run, never as columns here. This keeps the client row stable while the 22-asset
    context vocabulary is free to grow without a migration.
    """

    __tablename__ = "clients"

    id: Mapped[uuid.UUID] = _uuid_pk()
    company_name: Mapped[str] = mapped_column(String(300), nullable=False)
    industry: Mapped[str | None] = mapped_column(String(200))
    region: Mapped[str | None] = mapped_column(String(200))
    service_focus: Mapped[str | None] = mapped_column(String(500))
    known_competitors: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    runs: Mapped[list["Run"]] = relationship(back_populates="client")


class AssetDefinition(Base):
    """Static DAG registry row: one per Phase-1/Phase-2 asset (16 + 6, per Session Context Sec. 6).

    This is the machine-readable form of the asset wiring table: which stage this is, whether
    it is HITL-gated, its position in the DAG, and its declared `depends_on` edges plus the
    exact context keys it reads/writes. `run_stages`, `prompt_recipes`, and
    `field_schema_registry` all foreign-key to `asset_id` here for referential integrity, so
    this table must be seeded from the finalized asset registry before those tables can hold
    rows for a given asset (see app/db/SCHEMA.md, "Seeding order").

    `asset_id` is a free-form string (e.g. "icp", "lead_magnet", "remarketing_pillar_page"),
    not a fixed-size enum: the Day-1 schema-migration script already produced draft asset_ids
    (see application/backend/schemas/drafts/*.json) that don't all match the Session Context
    table's display names 1:1, and Phase 2 / future assets will keep adding new ids. A rigid
    Postgres ENUM here would require a migration for every new asset; TEXT does not.
    """

    __tablename__ = "asset_definitions"
    __table_args__ = (
        CheckConstraint("phase IN (1, 2)", name="ck_asset_definitions_phase"),
        # Deferred so the boot-time reseed (app/db/seed.py) can reorder stages within one
        # transaction — moving stage N into a slot another stage has not vacated yet is a
        # transient state, not an invalid one. See the `defer_asset_order_unique` migration.
        UniqueConstraint(
            "phase",
            "sequence_order",
            name="uq_asset_definitions_phase_order",
            deferrable=True,
            initially="DEFERRED",
        ),
    )

    asset_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    phase: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    sequence_order: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    is_gated: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    depends_on: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )  # list[str] of upstream asset_id values (DAG edges)
    reads_context_keys: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )  # list[str], e.g. ["icp_*", "cro_rewritten_copy"]
    writes_context_keys: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )  # list[str], e.g. ["cro_audit_findings", "cro_rewritten_copy"]
    model_tier: Mapped[ModelTier] = mapped_column(
        SAEnum(
            ModelTier,
            name="model_tier_enum",
            native_enum=True,
            # ModelTier members are lowercase-valued ("opus") with uppercase names ("OPUS"), unlike
            # every other enum in this file — without values_callable, SQLAlchemy binds by .name and
            # collides with the lowercase labels the migration actually created in Postgres.
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        server_default=ModelTier.SONNET.value,
    )  # which Claude cost tier this stage's generation call should use — see ModelTier docstring
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Run(Base):
    """One client pipeline run — the execution-graph root. Session Context Sec. 4.

    Models Phase 1 and Phase 2 assets of one client engagement as stages (`run_stages`) of
    the SAME run, rather than as two separate runs: Phase 2's first stage reads Phase 1's
    `icp_*`/`cro_rewritten_copy`/`design_tokens` context (Sec. 6, Phase 2 table), and since
    `context_entries` are scoped by `run_id`, keeping both phases on one run lets that read
    happen with a plain same-run context lookup instead of a cross-run join. "Runs as its own
    parallel track" (Sec. 6) is read here as "can be scheduled concurrently once its
    dependencies are satisfied," not "is a different run."

    `source_run_id` exists for the edge case this default doesn't cover: a client who buys
    the Phase 2 remarketing track later, as a fresh engagement, against an already-completed
    Phase 1 run. In that case a new `Run` is created with `source_run_id` pointing at the
    original, and the context resolver falls back to the source run's `context_entries` when
    a key isn't found on the new run. See app/db/SCHEMA.md's open question on this.
    """

    __tablename__ = "runs"

    id: Mapped[uuid.UUID] = _uuid_pk()
    client_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("clients.id", ondelete="RESTRICT"), nullable=False
    )
    source_run_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("runs.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[RunStatus] = mapped_column(
        SAEnum(RunStatus, name="run_status", native_enum=True),
        nullable=False,
        server_default=RunStatus.IN_PROGRESS.value,
    )
    current_stage_id: Mapped[str | None] = mapped_column(
        String(100),
        ForeignKey("asset_definitions.asset_id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    client: Mapped["Client"] = relationship(back_populates="runs")
    stages: Mapped[list["RunStage"]] = relationship(
        back_populates="run", cascade="all, delete-orphan", foreign_keys="RunStage.run_id"
    )

    __table_args__ = (Index("ix_runs_client_id", "client_id"),)


class RunStage(Base):
    """Per-(run, asset) execution state. The DAG "current stage" ledger. Session Context Sec. 4/6.

    One row is created per asset for a run (lazily, when the orchestrator first reaches that
    stage) and its `status` walks the StageStatus lifecycle. `depends_on`/gating itself is NOT
    duplicated here — it lives once, statically, on `asset_definitions.depends_on` /
    `is_gated`; the orchestrator computes "is this stage unblocked yet" by joining that against
    sibling rows here, rather than the DB enforcing DAG order via constraints (topological
    dependency evaluation is inherently dynamic/app-layer logic, not a fixed CHECK constraint).
    """

    __tablename__ = "run_stages"
    __table_args__ = (
        UniqueConstraint("run_id", "asset_id", name="uq_run_stages_run_asset"),
        Index("ix_run_stages_asset_status", "asset_id", "status"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False
    )
    asset_id: Mapped[str] = mapped_column(
        String(100), ForeignKey("asset_definitions.asset_id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[StageStatus] = mapped_column(
        SAEnum(StageStatus, name="stage_status", native_enum=True),
        nullable=False,
        server_default=StageStatus.NOT_STARTED.value,
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    run: Mapped["Run"] = relationship(back_populates="stages", foreign_keys=[run_id])
    asset: Mapped["AssetDefinition"] = relationship()


class ContextEntry(Base):
    """The context store: approved stage outputs, keyed by (run, context_key), versioned.

    Session Context Sec. 4 ("Approved stage outputs (structured JSON)") and Sec. 6's
    cross-cutting rule: "Context keys are additive, never overwritten. Once a field like
    `icp_pain_points` is written, later stages only read it. Revising the ICP means
    re-running Stage 1..." — i.e. a "revision" is a NEW row with `version` incremented, never
    an UPDATE of the old row.

    Enforcement is two-layered:
    - Schema level: no `updated_at` column, and no natural single-row-per-key uniqueness —
      `UniqueConstraint(run_id, context_key, version)` allows many versions per key but
      forbids two rows from claiming the same version number (no silent duplicate/overwrite
      races on `INSERT ... version = max+1`).
    - Database level: the initial migration adds a trigger that raises on any UPDATE or DELETE
      against this table, so "additive-only" is a real constraint the database enforces, not
      just an ORM convention (see the migration / app/db/SCHEMA.md for the trigger body).

    "Current" value for a key is `SELECT ... ORDER BY version DESC LIMIT 1` — deliberately not
    a separate `is_current` boolean, which would itself have to be updated (violating the
    append-only rule) every time a new version lands.
    """

    __tablename__ = "context_entries"
    __table_args__ = (
        UniqueConstraint("run_id", "context_key", "version", name="uq_context_entries_run_key_version"),
        Index("ix_context_entries_run_key", "run_id", "context_key"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False
    )
    context_key: Mapped[str] = mapped_column(String(200), nullable=False)
    version: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("1"))
    value: Mapped[dict] = mapped_column(JSONB, nullable=False)
    written_by_asset_id: Mapped[str | None] = mapped_column(
        String(100), ForeignKey("asset_definitions.asset_id", ondelete="RESTRICT"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ApprovalAuditLog(Base):
    """Who approved/rejected/edited what stage output, when, with what notes.

    Session Context Sec. 4 ("Approval & edit audit trail"). `notes` is the same free-text
    surface the Intake design's PRD amendment scopes rejection notes to (Sec. 2, PRD
    amendment: "free text is scoped to (a) rejection notes on drafts, and (b) answers within
    an active field-intake session") — this table is where (a) lands. `diff_snapshot` carries
    a before/after payload for EDITED actions so an inline edit is reconstructable without
    re-deriving it from `context_entries` versions (which only capture approved output, not
    the rejected/edited intermediate).
    """

    __tablename__ = "approval_audit_log"
    __table_args__ = (
        Index("ix_approval_audit_log_run_asset", "run_id", "asset_id"),
        Index("ix_approval_audit_log_created_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False
    )
    asset_id: Mapped[str] = mapped_column(
        String(100), ForeignKey("asset_definitions.asset_id", ondelete="RESTRICT"), nullable=False
    )
    action: Mapped[AuditAction] = mapped_column(
        SAEnum(AuditAction, name="audit_action", native_enum=True), nullable=False
    )
    actor: Mapped[str] = mapped_column(String(300), nullable=False)  # strategist email/username
    notes: Mapped[str | None] = mapped_column(Text)
    diff_snapshot: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class PromptRecipe(Base):
    """Versioned prompt recipe library. Session Context Sec. 4.

    `recipe_body` is genuinely free-form template text (the master prompt markdown, e.g. the
    contents of `manual_execution/Lead-Magnet-Architect-Prompt.md`) — TEXT is the correct type
    here, not a TEXT-as-catch-all misuse. `is_active` plus a partial unique index guarantees at
    most one active recipe version per asset at a time, matching how the Prompt Assembler
    (Intake design Sec. 3) should resolve "the current recipe" for a stage without ambiguity.
    """

    __tablename__ = "prompt_recipes"
    __table_args__ = (
        UniqueConstraint("asset_id", "version", name="uq_prompt_recipes_asset_version"),
        Index(
            "uq_prompt_recipes_one_active_per_asset",
            "asset_id",
            unique=True,
            postgresql_where=text("is_active"),
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    asset_id: Mapped[str] = mapped_column(
        String(100), ForeignKey("asset_definitions.asset_id", ondelete="RESTRICT"), nullable=False
    )
    version: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    recipe_body: Mapped[str] = mapped_column(Text, nullable=False)
    source_file: Mapped[str | None] = mapped_column(String(500))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


# --------------------------------------------------------------------------------------
# Conversational Intake Engine tables
# (Conversational_Intake_Engine_Design.md Sec. 5 "Data model (Postgres, additive)")
# --------------------------------------------------------------------------------------


class FieldSchemaRegistry(Base):
    """`field_schema_registry(asset_id, version, schema_json, created_at)` — versioned, append-only.

    Exact column list from Intake design Sec. 5. Append-only / immutable is enforced the same
    way as `context_entries`: no `updated_at`, a `(asset_id, version)` uniqueness constraint
    (which also serves as the FK target for `field_sessions.(stage_id, schema_version)`), and
    a DB trigger blocking UPDATE/DELETE (see migration). Immutability matters here specifically
    because a `FieldSession` pins a `schema_version` for reproducibility (FR-I8): the exact
    schema a run's intake was conducted against must never change under it.
    """

    __tablename__ = "field_schema_registry"
    __table_args__ = (
        UniqueConstraint("asset_id", "version", name="uq_field_schema_registry_asset_version"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    asset_id: Mapped[str] = mapped_column(
        String(100), ForeignKey("asset_definitions.asset_id", ondelete="RESTRICT"), nullable=False
    )
    version: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    schema_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class FieldSession(Base):
    """`field_sessions(id, run_id, stage_id, schema_version, status, resolved_fields JSONB,
    pending_field_ids JSONB, transcript JSONB, created_at, updated_at)` — exact column list
    from Intake design Sec. 5.

    Column is named `stage_id` (not `asset_id`) to match the design doc verbatim, but it is
    wired with a composite FK to `field_schema_registry(asset_id, version)` via
    `(stage_id, schema_version)` — this simultaneously (a) guarantees `stage_id` is a real,
    known asset, since `field_schema_registry.asset_id` itself FKs to `asset_definitions`, and
    (b) guarantees the pinned `schema_version` actually exists for that stage, which a bare FK
    to `asset_definitions.asset_id` alone could not do.

    A stage can be revisited (Session Context Sec. 6: revising an upstream stage cascades a
    re-approval requirement down the chain, which can reopen intake), so `(run_id, stage_id)`
    is deliberately NOT a hard uniqueness constraint — only indexed for the lookup pattern.
    Instead, a partial unique index allows at most one session that is still open (not yet
    `CONFIRMED`) per `(run_id, stage_id)` at a time, preventing two concurrent intake
    conversations for the same stage.
    """

    __tablename__ = "field_sessions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["stage_id", "schema_version"],
            ["field_schema_registry.asset_id", "field_schema_registry.version"],
            ondelete="RESTRICT",
            name="fk_field_sessions_schema_registry",
        ),
        Index("ix_field_sessions_run_stage", "run_id", "stage_id"),
        Index(
            "uq_field_sessions_one_open_per_stage",
            "run_id",
            "stage_id",
            unique=True,
            postgresql_where=text("status != 'CONFIRMED'"),
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False
    )
    stage_id: Mapped[str] = mapped_column(String(100), nullable=False)
    schema_version: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    status: Mapped[FieldSessionStatus] = mapped_column(
        SAEnum(FieldSessionStatus, name="field_session_status", native_enum=True),
        nullable=False,
        server_default=FieldSessionStatus.NOT_STARTED.value,
    )
    resolved_fields: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    pending_field_ids: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    transcript: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    attachments: Mapped[list["Attachment"]] = relationship(
        back_populates="field_session", cascade="all, delete-orphan"
    )


class VerificationConfidence(str, enum.Enum):
    """How firmly a competitor's offering was confirmed, per the competitor-analysis prompts'
    shared requirement to "Mark verification_confidence for each entry ... based on whether the
    offering was directly confirmed on the page"."""

    VERIFIED = "Verified"
    PARTIALLY_VERIFIED = "Partially verified"
    UNVERIFIED = "Unverified"


class CompetitorAnalysis(Base):
    """One run of a `competitor_analysis_*` stage — the parent row for its competitor rows.

    Stored as first-class relational rows rather than as a `context_entries` JSON blob because
    this output is *read by the UI*, not just replayed into a downstream prompt: the operator
    reviews a competitor listing (name, page URL, verification confidence, offering summary) and
    the notes explaining any gap below the requested 10. Querying that out of JSONB on every
    render, with no column types or FK integrity, is the wrong shape for data the product
    actually displays and will later want to filter and de-duplicate across runs.

    The approved output is *also* still written to `context_entries` on save (rendered as prose),
    because that is the channel the paired main asset's prompt reads from — these tables are the
    structured record, not a replacement for the context store.

    `raw_output` keeps the model's exact response so a parse can be re-run or audited without
    paying for another web-search call.
    """

    __tablename__ = "competitor_analyses"
    __table_args__ = (
        Index("ix_competitor_analyses_run_asset", "run_id", "asset_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False
    )
    asset_id: Mapped[str] = mapped_column(
        String(100), ForeignKey("asset_definitions.asset_id", ondelete="RESTRICT"), nullable=False
    )
    # The resolved placeholder values this run was executed with, kept so a listing can be
    # explained ("benchmarked against X, in Y, for Z") without re-deriving them.
    target_url: Mapped[str] = mapped_column(Text, nullable=False)
    service: Mapped[str | None] = mapped_column(String(300))
    niche: Mapped[str | None] = mapped_column(String(300))
    location: Mapped[str | None] = mapped_column(String(300))

    requested_count: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("10"))
    returned_count: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("0"))
    # Prose explanation of any gap below `requested_count`, plus excluded near-misses.
    notes: Mapped[str | None] = mapped_column(Text)
    raw_output: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    competitors: Mapped[list["Competitor"]] = relationship(
        back_populates="analysis", cascade="all, delete-orphan", order_by="Competitor.rank"
    )


class Competitor(Base):
    """One competitor within a `CompetitorAnalysis`.

    `rank` preserves the model's own ordering (it ranks by `similarity_score`), so the listing
    renders in the order the analysis intended rather than by insertion or primary key.
    """

    __tablename__ = "competitors"
    __table_args__ = (
        UniqueConstraint("analysis_id", "domain", name="uq_competitors_analysis_domain"),
        Index("ix_competitors_analysis_rank", "analysis_id", "rank"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    analysis_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("competitor_analyses.id", ondelete="CASCADE"), nullable=False
    )
    rank: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    domain: Mapped[str] = mapped_column(String(300), nullable=False)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    page_url: Mapped[str | None] = mapped_column(Text)
    verification_confidence: Mapped[VerificationConfidence] = mapped_column(
        SAEnum(
            VerificationConfidence,
            name="verification_confidence",
            native_enum=True,
            # Values carry spaces and mixed case ("Partially verified") because they are quoted
            # verbatim from the prompts' own output contract — bind by value, not by .name.
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        server_default=VerificationConfidence.UNVERIFIED.value,
    )
    offering_summary: Mapped[str | None] = mapped_column(Text)
    # Text, not numeric, and deliberately so. What competitors publish is "From $1,500/mo",
    # "$990 setup + $2,400/mo", "$4,500 one-off" — the unit, the qualifier and the "from" are the
    # informative parts, and a Numeric column would force the parser to throw them away (or to
    # invent a figure where a band was published). Only `competitor_analysis_offers` populates it
    # today; the other competitor stages have no price to report.
    starting_price: Mapped[str | None] = mapped_column(String(120))
    # One short, stage-specific classifier, rendered in the listing as a pill next to the name. Each
    # competitor prompt that has such a thing writes it here rather than each getting a column of
    # its own: `03_Lead_Magnet.md` puts the lead-magnet type ("Gated ebook"), `04_Blog.md` the blog's
    # content focus, `10_Podcast.md` the episode's topical focus. Null for the stages that classify
    # nothing.
    category: Mapped[str | None] = mapped_column(String(160))
    similarity_score: Mapped[float | None] = mapped_column(Float)
    avg_position: Mapped[float | None] = mapped_column(Float)
    intersections: Mapped[int | None] = mapped_column(Integer)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    analysis: Mapped["CompetitorAnalysis"] = relationship(back_populates="competitors")


class ChatSession(Base):
    """One saved pipeline conversation, listed in the UI's chat-history sidebar.

    Deliberately decoupled from `runs`/`context_entries`: those model *approved* DAG state
    (Session Context Sec. 4/6), while this table is a raw autosave of the in-progress chat
    transcript itself (every message bubble, question, streamed draft, and the pipeline
    diagram's position) so a click in the history sidebar can restore the exact UI a strategist
    left off at — state no other table captures. `state` mirrors the frontend's
    `pipelineStore` shape wholesale (JSONB) rather than being normalized into rows, matching
    the precedent set by `FieldSession.transcript` elsewhere in this schema.

    `run_id` links to the pipeline Run once the first stage is actually saved (see
    `pipeline.py`'s `create_run`), but stays NULL until then since a chat can exist — and be
    listed in history — before any stage has been approved.

    `user_id` is the owner, and every route in `app/routers/chat_sessions.py` filters on it. It is
    what makes the history sidebar per-account rather than per-installation: without it `GET
    /chat-sessions` returned the whole table, so anybody who signed in read — and could open,
    overwrite or delete — every other operator's conversations.

    It is **nullable, and a NULL is invisible to everyone**. That is deliberate rather than an
    unfinished constraint. Rows predating this column have no owner that can be inferred from the
    data (`state` carries no identity), so the choice is between a default owner and a default of
    nobody, and only one of those fails closed. The migration backfills to the oldest account so an
    existing single-operator install keeps its history; on an install where that guess is wrong, or
    where there was no account at all, the rows stay NULL and simply stop being listed. They are
    still in the table and can be reassigned by hand — which is recoverable, whereas having shown
    one operator's client work to another is not.
    """

    __tablename__ = "chat_sessions"
    __table_args__ = (
        Index("ix_chat_sessions_updated_at", "updated_at"),
        # The history sidebar's only query is "my sessions, newest first", so the index carries the
        # sort column too and it is answered without a per-user sort.
        Index("ix_chat_sessions_user_updated", "user_id", "updated_at"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("runs.id", ondelete="SET NULL"), nullable=True, unique=True
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False, server_default=text("'New chat'"))
    state: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class Attachment(Base):
    """`attachments(id, field_session_id, field_id, storage_url, mime_type, uploaded_at)`.

    Exact column list from Intake design Sec. 5 / FR-I7 ("File/spreadsheet attachments ...
    are uploaded as first-class attachments referenced by ID, never inlined as chat text").
    `field_id` is a plain string, not an FK: it addresses one field inside the parent
    session's `field_schema_registry.schema_json` document, and fields are JSON array
    elements, not rows a foreign key can target. Per the intake sequence (Sec. 5, `POST
    /attachments`), a row here is only durable once promoted from its short-TTL Redis pointer
    on confirm — pre-confirm attachments are out of scope for this permanent-tier table.
    """

    __tablename__ = "attachments"
    __table_args__ = (Index("ix_attachments_session_field", "field_session_id", "field_id"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    field_session_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("field_sessions.id", ondelete="CASCADE"), nullable=False
    )
    field_id: Mapped[str] = mapped_column(String(200), nullable=False)
    storage_url: Mapped[str] = mapped_column(Text, nullable=False)
    mime_type: Mapped[str] = mapped_column(String(150), nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    field_session: Mapped["FieldSession"] = relationship(back_populates="attachments")


class ApiUsage(Base):
    """One row per Anthropic API call — what it cost, and which chat spent it.

    Written from the `usage` block the API returns on every response, so these are measured token
    counts rather than an estimate from character counts. Nothing else in the schema records this:
    `context_entries` holds what a stage produced, not what producing it consumed, and a stage that
    was generated three times and saved once leaves one context row and three calls.

    Scoped to a chat, not to a run. A run only exists once the first stage is approved, and the
    spend an operator wants explained includes the drafts they rejected before that — so
    `chat_session_id` is the attribution key and `run_id` is carried alongside it for reports that
    group by client engagement. Both are nullable and both `SET NULL` on delete: usage is a
    financial record and must survive the deletion of the chat that incurred it, otherwise a tidied
    history quietly reduces last month's total.

    `cost_usd` is stored rather than derived on read. See the module docstring of
    `app/services/pricing.py` — list prices change, and a monitoring panel whose historical totals
    move when a rate changes cannot be reconciled against an invoice. `rates_version` records which
    price table produced the figure.
    """

    __tablename__ = "api_usage"
    __table_args__ = (
        Index("ix_api_usage_chat_session_id", "chat_session_id"),
        Index("ix_api_usage_created_at", "created_at"),
        Index("ix_api_usage_run_id", "run_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    chat_session_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("chat_sessions.id", ondelete="SET NULL"), nullable=True
    )
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("runs.id", ondelete="SET NULL"), nullable=True
    )
    # Free strings, not FKs. `asset_id` is usually an `asset_definitions` row but not always — the
    # competitor briefing is a real call attributable to a stage that is not itself a stage — and a
    # usage row must never fail to write because the thing it describes has no catalog entry.
    asset_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Which pipeline: "phase1" | "phase2".
    phase: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # What kind of call: "generation" | "continuation" | "revision" | "competitor" | "briefing" |
    # "check". A free string rather than an enum for the same reason `asset_id` is (see above) — a
    # new kind of call must not need a migration before its spend can be recorded.
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    # As the response reported it, which may be a dated snapshot id rather than the alias requested.
    model: Mapped[str] = mapped_column(String(80), nullable=False)

    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    cache_creation_input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    cache_read_input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    # Server-side tool calls billed per request. Only web search costs money today; the column is
    # named for it rather than generically so a second billed tool gets its own column and its own
    # rate instead of being averaged into this one.
    web_search_requests: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))

    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, server_default=text("0"))
    rates_version: Mapped[str] = mapped_column(String(20), nullable=False)

    # `end_turn`, `max_tokens`, `refusal`, … — a truncated deliverable is a cost question as much as
    # a quality one, since everything up to the cut was paid for.
    stop_reason: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # Wall-clock of the call, for "why did that stage take four minutes" next to what it cost.
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


# --------------------------------------------------------------------------------------
# Authentication tables
#
# Email and password only — there is no federated/OAuth identity in this schema by design.
#
# Three tables rather than one wide `users` row, because the three things they hold have three
# different lifetimes: an account is permanent, a login session is revocable and expires, and a
# reset token is single-use. Folding either token into `users` makes "log this device out" or
# "that reset link was already used" unrepresentable.
#
# Credentials are never stored recoverably: `users.password_hash` is scrypt (stdlib `hashlib`,
# see app/services/auth.py) and both token tables store a SHA-256 of the token, never the token
# itself — a dump of this schema cannot be replayed as a login.
# --------------------------------------------------------------------------------------


class User(Base):
    """An account. Email is the identity key, which is what makes the "have I been here before?"
    question answerable from the first field in the sign-in UI.

    `password_hash` is NOT NULL: a password is the only way into this system, so an account
    without one could never be signed in to and has no reason to exist. (It was nullable while
    Google sign-in was on the table — an OAuth-only account genuinely has no password — and was
    tightened when that was dropped, so the column now states the real invariant instead of
    leaving a row shape the application can never produce.)

    Email is stored already-lowercased (see `normalize_email` in app/services/auth.py) behind a
    unique constraint on the column, rather than a functional `lower(email)` index. Normalization
    happens in exactly one place in the application, and a case-sensitive unique index would
    cheerfully create a second account for the same person typing the same address.
    """

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = _uuid_pk()
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # True once a reset link sent to the address has been redeemed — reaching a link in the inbox
    # is the only proof of ownership this system can obtain. A fresh signup therefore starts
    # False; the account still works, and the flag exists so a later "verify your email" gate has
    # something to read.
    email_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    sessions: Mapped[list["UserSession"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class UserSession(Base):
    """A signed-in browser. The cookie carries a random token; this row carries its SHA-256.

    Opaque server-side sessions rather than a self-contained JWT, because the two operations this
    product actually needs — "sign out" and "sign out everywhere" — are a DELETE here and are
    simply not expressible against a stateless token that stays valid until it expires. The cost
    is one indexed lookup per request, which is nothing beside a Claude call.
    """

    __tablename__ = "user_sessions"
    __table_args__ = (
        Index("ix_user_sessions_user_id", "user_id"),
        Index("ix_user_sessions_expires_at", "expires_at"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # SHA-256 hex of the cookie value, never the value itself — see the section docstring.
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Truncated on write; for a future "your active devices" list, never for an auth decision.
    user_agent: Mapped[str | None] = mapped_column(String(400), nullable=True)

    user: Mapped["User"] = relationship(back_populates="sessions")


class PasswordResetToken(Base):
    """A single-use, time-limited password reset grant.

    A row rather than a signed stateless token, for the same reason as `UserSession`: "this link
    has already been used" is a fact about the world that has to be *recorded* somewhere. A signed
    token carries its own expiry but nothing can revoke it, so a reset link forwarded out of an
    inbox stays live for its whole window even after the password has already been changed.
    """

    __tablename__ = "password_reset_tokens"
    __table_args__ = (Index("ix_password_reset_tokens_user_id", "user_id"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Stamped the moment the token is redeemed, which is what makes it single-use.
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class MediaAsset(Base):
    """One generated image's bytes, served back at `GET /media/{id}` (`app/routers/media.py`).

    The one deliberate break from this schema's own convention: every other binary-shaped output
    in this codebase (the `.pptx` slide deck) is generated on demand and streamed, never stored —
    see `download_slide_deck`. An OpenAI-generated image cannot follow that pattern because it
    cannot be cheaply regenerated identically (it is not deterministic, and a re-generation costs
    real money), and it has to keep answering at the same URL for as long as the HTML it is
    embedded in exists.

    It also deliberately does not live on local disk (`app/services/media_storage.py`'s first
    version did, briefly). This backend runs on Railway, where local disk is the *container's*
    disk: ephemeral across redeploys/restarts by default, and — confirmed against Railway's own
    volume docs before this table was added — even a persistent Volume "cannot be used with
    replicas" and is capped at one per service, so it would not survive this backend ever scaling
    beyond one instance either. Postgres is already this app's durable, already-provisioned,
    already-multi-instance-safe system of record, so the bytes go here instead of introducing a
    second storage dependency (S3 or similar) for what is, per run, three small files.
    """

    __tablename__ = "media_assets"
    __table_args__ = (Index("ix_media_assets_created_at", "created_at"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    #: e.g. "image/png" — served back verbatim as the response's Content-Type.
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
