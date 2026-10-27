import asyncio

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.conftest import alembic_config

pytestmark = pytest.mark.integration


async def test_pgvector_extension_installed(migrated_db: str) -> None:
    engine = create_async_engine(migrated_db)
    async with engine.connect() as conn:
        version = await conn.scalar(
            text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
        )
    await engine.dispose()
    assert version is not None


async def test_migrations_round_trip(migrated_db: str) -> None:
    """Every migration must downgrade cleanly and re-apply."""
    cfg = alembic_config(migrated_db)
    await asyncio.to_thread(command.downgrade, cfg, "base")
    await asyncio.to_thread(command.upgrade, cfg, "head")


async def test_models_match_migrations(migrated_db: str) -> None:
    """Fails when a model changed without a matching Alembic migration."""
    await asyncio.to_thread(command.check, alembic_config(migrated_db))
