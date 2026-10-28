"""add context_chunks (pgvector)

Revision ID: d3a7c5e91f24
Revises: c8d914e6a5b3
Create Date: 2026-10-22 00:00:00

The searchable index over `context_entries`: each approved document cut into sections, each section
with an OpenAI embedding. See `ContextChunk` in `app/db/models.py` and
`app/services/vector_context.py`.

Conditional on the database having pgvector to install. Railway's Postgres ships it; a developer's
local Postgres (the Windows installer's, for one) often does not. Failing the migration there would
block every other migration behind it for a feature that is optional at runtime. So, without the
extension, this revision only logs a warning and creates nothing. The app checks for the table
(`vector_context.available`) and behaves exactly as it did before vectors existed. After installing
pgvector locally: `alembic downgrade c8d914e6a5b3 && alembic upgrade head`.

No ANN index on `embedding`: see `ContextChunk`'s docstring for why an exact scan is right here.

Hand-authored, matching every prior migration in this project.
"""

from __future__ import annotations

import logging
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d3a7c5e91f24"
down_revision: Union[str, None] = "c8d914e6a5b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger("alembic.runtime.migration")


def _pgvector_available() -> bool:
    bind = op.get_bind()
    return bool(
        bind.execute(sa.text("SELECT 1 FROM pg_available_extensions WHERE name = 'vector'")).scalar()
    )


def upgrade() -> None:
    if not _pgvector_available():
        logger.warning(
            "pgvector is not available on this Postgres, so context_chunks was not created. "
            "Context search stays off until it is; see this migration's docstring."
        )
        return

    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    # Plain SQL rather than `op.create_table`: SQLAlchemy has no built-in `vector` type, and a
    # migration must not import app code (`models.Vector`) that may change after it is written.
    op.execute(
        """
        CREATE TABLE context_chunks (
            id UUID PRIMARY KEY,
            context_entry_id UUID NOT NULL REFERENCES context_entries(id) ON DELETE CASCADE,
            run_id UUID NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
            context_key VARCHAR(200) NOT NULL,
            chunk_index INTEGER NOT NULL,
            section_path TEXT NOT NULL DEFAULT '',
            text TEXT NOT NULL,
            embedding_model VARCHAR(100) NOT NULL,
            embedding vector(1536) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_context_chunks_entry_model_index
                UNIQUE (context_entry_id, embedding_model, chunk_index)
        )
        """
    )
    op.create_index("ix_context_chunks_entry", "context_chunks", ["context_entry_id"])
    op.create_index("ix_context_chunks_run_key", "context_chunks", ["run_id", "context_key"])


def downgrade() -> None:
    # Without pgvector, upgrade created nothing, so there is nothing to drop.
    op.execute("DROP TABLE IF EXISTS context_chunks")
