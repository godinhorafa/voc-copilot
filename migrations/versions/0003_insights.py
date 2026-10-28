"""Insights table; track chunking separately from full processing.

Existing conversations keep their chunks (chunked_hash is backfilled) but are
marked unprocessed, so re-uploading them runs only the new extraction step.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "insights",
        sa.Column("conversation_id", sa.UUID(), nullable=False),
        sa.Column("contact_reason", sa.String(length=64), nullable=False),
        sa.Column("sentiment", sa.String(length=16), nullable=False),
        sa.Column("resolved", sa.Boolean(), nullable=False),
        sa.Column("churn_risk", sa.String(length=16), nullable=False),
        sa.Column("competitors", postgresql.ARRAY(sa.String(length=128)), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("extractor_version", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name=op.f("fk_insights_conversation_id_conversations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("conversation_id", name=op.f("pk_insights")),
    )
    op.create_index("ix_insights_contact_reason", "insights", ["contact_reason"], unique=False)
    op.add_column("conversations", sa.Column("chunked_hash", sa.String(length=64), nullable=True))
    op.execute("UPDATE conversations SET chunked_hash = processed_hash, processed_hash = NULL")


def downgrade() -> None:
    op.execute(
        "UPDATE conversations SET processed_hash = chunked_hash WHERE processed_hash IS NULL"
    )
    op.drop_column("conversations", "chunked_hash")
    op.drop_index("ix_insights_contact_reason", table_name="insights")
    op.drop_table("insights")
