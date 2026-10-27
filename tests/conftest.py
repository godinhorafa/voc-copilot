"""Shared fixtures.

Integration tests run against real Postgres (with pgvector) and Redis. By
default they start throwaway containers via Testcontainers; set
TEST_DATABASE_URL / TEST_REDIS_URL to point at existing instances instead
(useful when Docker is unavailable).
"""

import asyncio
import os
from collections.abc import AsyncIterator, Iterator

import pytest
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.main import create_app

PGVECTOR_IMAGE = "pgvector/pgvector:pg16"
REDIS_IMAGE = "redis:7-alpine"


def make_settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "environment": "test",
        "llm_provider": "fake",
        "log_json": False,
        "log_level": "WARNING",
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
def settings(migrated_db: str, redis_url: str) -> Settings:
    return make_settings(database_url=migrated_db, redis_url=redis_url)


@pytest.fixture
async def client(settings: Settings) -> AsyncIterator[AsyncClient]:
    app = create_app(settings)
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac,
    ):
        yield ac
