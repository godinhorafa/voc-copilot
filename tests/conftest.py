"""Shared fixtures.

Integration tests run against real Postgres (with pgvector) and Redis. By
default they start throwaway containers via Testcontainers; set
TEST_DATABASE_URL / TEST_REDIS_URL to point at existing instances instead
(useful when Docker is unavailable).
"""

import asyncio
import os
import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import Settings
from app.db.models import DEFAULT_TENANT_ID
from app.db.session import tenant_sessionmaker
from app.llm.fake import FakeEmbeddingProvider, FakeLLMProvider
from app.main import create_app

PGVECTOR_IMAGE = "pgvector/pgvector:pg16"
REDIS_IMAGE = "redis:7-alpine"
TEST_API_KEY = "test-api-key"


def make_settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "environment": "test",
        "llm_provider": "fake",
        "log_json": False,
        "log_level": "WARNING",
        "api_key": TEST_API_KEY,
        # Off by default: tests share one Redis and one client identity, so
        # counts would leak between tests. test_rate_limit.py turns them on.
        "rate_limit_ask": "",
        "rate_limit_metrics": "",
        "rate_limit_ingest": "",
    }
    # model_validate skips env vars and .env, so tests never pick up local config.
    return Settings.model_validate(base | overrides)


@pytest.fixture(scope="session")
def postgres_url() -> Iterator[str]:
    if url := os.environ.get("TEST_DATABASE_URL"):
        yield url
        return
    from testcontainers.community.postgres import PostgresContainer

    with PostgresContainer(PGVECTOR_IMAGE, driver="asyncpg") as pg:
        yield pg.get_connection_url()


@pytest.fixture(scope="session")
def redis_url() -> Iterator[str]:
    if url := os.environ.get("TEST_REDIS_URL"):
        yield url
        return
    from testcontainers.community.redis import RedisContainer

    with RedisContainer(REDIS_IMAGE) as rc:
        yield f"redis://{rc.get_container_host_ip()}:{rc.get_exposed_port(6379)}/0"


def alembic_config(database_url: str) -> Config:
    cfg = Config("alembic.ini")
    cfg.attributes["database_url"] = database_url
    cfg.attributes["configure_logger"] = False
    return cfg


@pytest.fixture(scope="session")
async def migrated_db(postgres_url: str) -> str:
    # Alembic's env.py calls asyncio.run(), so it must run outside this loop.
    await asyncio.to_thread(command.upgrade, alembic_config(postgres_url), "head")
    return postgres_url


@pytest.fixture
async def engine(migrated_db: str) -> AsyncIterator[AsyncEngine]:
    """Engine on the test database; all data is removed after each test."""
    eng = create_async_engine(migrated_db)
    yield eng
    async with eng.begin() as conn:
        # TRUNCATE is not subject to row-level security: it empties every tenant.
        await conn.execute(text("TRUNCATE customers, conversations, jobs, api_keys, users CASCADE"))
        await conn.execute(
            text("DELETE FROM tenants WHERE id <> :default"), {"default": DEFAULT_TENANT_ID}
        )
    await eng.dispose()


@pytest.fixture
def db(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Sessions scoped to the default tenant, the one the test API key belongs to."""
    return tenant_sessionmaker(engine, DEFAULT_TENANT_ID)


@pytest.fixture
def settings(migrated_db: str, redis_url: str) -> Settings:
    return make_settings(database_url=migrated_db, redis_url=redis_url, ingestion_retry_base_s=0.0)


class FakeQueue:
    def __init__(self) -> None:
        self.enqueued: list[uuid.UUID] = []
        self.tenants: list[uuid.UUID] = []

    async def enqueue_ingestion(self, job_id: uuid.UUID, tenant_id: uuid.UUID) -> None:
        self.enqueued.append(job_id)
        self.tenants.append(tenant_id)


@pytest.fixture
def queue() -> FakeQueue:
    return FakeQueue()


@pytest.fixture
async def app(
    settings: Settings, queue: FakeQueue, db: async_sessionmaker[AsyncSession]
) -> AsyncIterator[FastAPI]:
    """Started app; tests may swap `app.state.llm` / `app.state.embeddings`."""
    application = create_app(settings)
    async with application.router.lifespan_context(application):
        application.state.queue = queue
        yield application


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-API-Key": TEST_API_KEY},
    ) as ac:
        yield ac


@pytest.fixture
def worker_ctx(
    settings: Settings, engine: AsyncEngine, db: async_sessionmaker[AsyncSession]
) -> dict[str, Any]:
    """The context arq passes to tasks, built from test fixtures."""
    return {
        "settings": settings,
        "engine": engine,
        "embeddings": FakeEmbeddingProvider(dim=settings.embedding_dim),
        "llm": FakeLLMProvider.permissive(),
        "job_try": 1,
    }
