import enum
import uuid
from datetime import date, datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    Computed,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# Deterministic constraint names keep Alembic autogenerate diffs stable.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

# Fixed by the schema: changing the embedding model's dimension needs a migration.
EMBEDDING_DIM = 1536

# Created by migration 0006; existing data and the bootstrap API_KEY belong to it.
DEFAULT_TENANT_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def tenant_column() -> Mapped[uuid.UUID]:
    """Owning tenant of a row, filled from the transaction's tenant (ADR 0012).

    The default reads `app.tenant_id`, set by tenant-scoped sessions; with no
    tenant set it raises instead of guessing. Row-level security policies on
    every table with this column only expose and accept the current tenant's rows.
    """
    return mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        server_default=text("current_setting('app.tenant_id')::uuid"),
        index=True,
    )


class Tenant(Base):
    """An isolated organization: its data is invisible to every other tenant."""

    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Role(enum.StrEnum):
    ADMIN = "admin"  # everything, including managing the tenant's API keys
    ANALYST = "analyst"  # ask, metrics, insights, read conversations
    INGEST = "ingest"  # upload conversations and customers, read jobs


# One Postgres type for the roles of API keys and users.
ROLE_TYPE = Enum(Role, name="member_role", values_callable=lambda e: [m.value for m in e])


class ApiKey(Base):
    """Only a hash is stored; the key itself is shown once, when it is created."""

    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(128))
    role: Mapped[Role] = mapped_column(ROLE_TYPE)
    # Public part of the key, to recognize it in listings and logs.
    prefix: Mapped[str] = mapped_column(String(16))
    key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class User(Base):
    """A person who signs in through the identity provider (ADR 0013).

    Matched on the verified email claim of their ID token. An email belongs to
    one tenant; removing the row revokes access at the next request.
    """

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    email: Mapped[str] = mapped_column(String(320), unique=True)  # lowercased
    role: Mapped[Role] = mapped_column(ROLE_TYPE)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Customer(Base):
    """Customer records come from the CRM / billing system, not from transcripts."""

    __tablename__ = "customers"
    # CRM ids are only unique within a tenant.
    __table_args__ = (PrimaryKeyConstraint("tenant_id", "id"),)

    tenant_id: Mapped[uuid.UUID] = tenant_column()
    id: Mapped[str] = mapped_column(String(64))
    industry: Mapped[str] = mapped_column(String(32))
    plan_code: Mapped[str | None] = mapped_column(String(64))
    signup_at: Mapped[date | None] = mapped_column(Date)
    churned_at: Mapped[date | None] = mapped_column(Date, index=True)
    churn_reason: Mapped[str | None] = mapped_column(String(128))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (
        Index("ix_conversations_industry_started_at", "industry", "started_at"),
        UniqueConstraint("tenant_id", "external_id", name="uq_conversations_tenant_external_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = tenant_column()
    external_id: Mapped[str] = mapped_column(String(128))
    # No FK: transcripts and CRM data arrive independently and in any order.
    customer_id: Mapped[str | None] = mapped_column(String(64), index=True)
    industry: Mapped[str] = mapped_column(String(32))
    channel: Mapped[str] = mapped_column(String(16))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    content_hash: Mapped[str] = mapped_column(String(64))
    # Each pipeline step records the content hash it last completed for.
    chunked_hash: Mapped[str | None] = mapped_column(String(64))
    processed_hash: Mapped[str | None] = mapped_column(String(64))  # all steps done
    processing_error: Mapped[str | None] = mapped_column(Text)
    # PII found and replaced at ingestion, by kind (e.g. {"EMAIL": 2}); never the values.
    redactions: Mapped[dict[str, int]] = mapped_column(JSONB, default=dict, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    turns: Mapped[list["Turn"]] = relationship(
        back_populates="conversation",
        order_by="Turn.idx",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    chunks: Mapped[list["Chunk"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan", passive_deletes=True
    )
    insight: Mapped["Insight | None"] = relationship(
        back_populates="conversation", cascade="all, delete-orphan", passive_deletes=True
    )

    @property
    def is_processed(self) -> bool:
        return self.processed_hash == self.content_hash


class Turn(Base):
    __tablename__ = "turns"

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), primary_key=True
    )
    idx: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[uuid.UUID] = tenant_column()
    speaker: Mapped[str] = mapped_column(String(16))
    text: Mapped[str] = mapped_column(Text)
    offset_s: Mapped[int | None] = mapped_column(Integer)

    conversation: Mapped[Conversation] = relationship(back_populates="turns")


class Chunk(Base):
    __tablename__ = "chunks"
    __table_args__ = (
        Index(
            "ix_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        Index("ix_chunks_tsv", "tsv", postgresql_using="gin"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    tenant_id: Mapped[uuid.UUID] = tenant_column()
    turn_start: Mapped[int] = mapped_column(Integer)
    turn_end: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM))
    embedding_model: Mapped[str] = mapped_column(String(128))
    tsv: Mapped[str] = mapped_column(
        TSVECTOR, Computed("to_tsvector('english', text)", persisted=True)
    )

    conversation: Mapped[Conversation] = relationship(back_populates="chunks")


class Insight(Base):
    """LLM-extracted structured view of one conversation."""

    __tablename__ = "insights"
    __table_args__ = (Index("ix_insights_contact_reason", "contact_reason"),)

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), primary_key=True
    )
    tenant_id: Mapped[uuid.UUID] = tenant_column()
    contact_reason: Mapped[str] = mapped_column(String(64))
    sentiment: Mapped[str] = mapped_column(String(16))
    resolved: Mapped[bool] = mapped_column(Boolean)
    churn_risk: Mapped[str] = mapped_column(String(16))
    competitors: Mapped[list[str]] = mapped_column(ARRAY(String(128)))
    summary: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    extractor_version: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(128))
    input_tokens: Mapped[int] = mapped_column(Integer)
    output_tokens: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    conversation: Mapped[Conversation] = relationship(back_populates="insight")


class JobStatus(enum.StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"  # finished, some conversations failed
    FAILED = "failed"


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = tenant_column()
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, name="job_status", values_callable=lambda e: [m.value for m in e]),
        default=JobStatus.QUEUED,
    )
    conversation_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(UUID(as_uuid=True)))
    total: Mapped[int] = mapped_column(Integer)
    processed: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
