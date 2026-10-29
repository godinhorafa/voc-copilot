"""Database-level defenses for text-to-SQL, exercised without the guard.

Queries here are wrapped in GuardedQuery directly, as if the guard had let them
through, to show what the database itself refuses.
"""

import json
from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.analytics.catalog import ROLE, SCHEMA, VIEWS
from app.analytics.executor import execute
from app.analytics.sql_guard import GuardedQuery, check
from app.analytics.text_to_sql import MetricsFilters, TextToSQL, TextToSQLError
from app.llm.fake import FakeLLMProvider
from tests.factories import conversation
from tests.helpers import ingest_payloads

pytestmark = pytest.mark.integration

LOGIN_USER = "voc_analytics_login"
LOGIN_PASSWORD = "test-only-password"


def raw(sql: str) -> GuardedQuery:
    return GuardedQuery(sql=sql, tables=frozenset())


@pytest.fixture
async def engine(migrated_db: str) -> AsyncIterator[AsyncEngine]:
    eng = create_async_engine(migrated_db)
    yield eng
    await eng.dispose()


@pytest.fixture
async def login_engine(migrated_db: str, engine: AsyncEngine) -> AsyncIterator[AsyncEngine]:
    """Connection as a login user that is only a member of the analytics role."""
    async with engine.begin() as conn:
        await conn.execute(
            text(
                f"DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{LOGIN_USER}') "
                f"THEN CREATE ROLE {LOGIN_USER} LOGIN PASSWORD '{LOGIN_PASSWORD}' IN ROLE {ROLE}; "
                "END IF; END $$"
            )
        )
    url = make_url(migrated_db).set(username=LOGIN_USER, password=LOGIN_PASSWORD)
    eng = create_async_engine(url)
    yield eng
    await eng.dispose()


async def test_views_match_catalog(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT table_name, column_name FROM information_schema.columns "
                    "WHERE table_schema = :s ORDER BY table_name, ordinal_position"
                ),
                {"s": SCHEMA},
            )
        ).all()
    actual: dict[str, list[str]] = {}
    for table, column in rows:
        actual.setdefault(table, []).append(column)
    assert actual == {v.name: [c.name for c in v.columns] for v in VIEWS}


@pytest.mark.parametrize("set_role", [True, False])
async def test_role_cannot_read_base_tables(
    engine: AsyncEngine, login_engine: AsyncEngine, set_role: bool
) -> None:
    target = engine if set_role else login_engine
    with pytest.raises(DBAPIError, match="permission denied for table conversations"):
        await execute(target, raw("SELECT * FROM public.conversations"), set_role=set_role)


async def test_transaction_is_read_only(engine: AsyncEngine) -> None:
    with pytest.raises(DBAPIError, match="read-only transaction"):
        await execute(engine, raw("CREATE TEMP TABLE x (id int)"))


async def test_statement_timeout(engine: AsyncEngine) -> None:
    with pytest.raises(DBAPIError, match="statement timeout"):
        await execute(engine, raw("SELECT pg_sleep(2)"), timeout_ms=100)


async def test_shared_connection_role_switch_does_not_help_within_one_statement(
    engine: AsyncEngine,
) -> None:
    """On the shared connection, set_config('role') succeeds, but privileges are
    checked when the statement starts, so the same statement still can't read
    base tables; the switch would only apply to later statements, and the
    executor runs one statement and rolls back."""
    switched = await execute(engine, raw("SELECT set_config('role', session_user, true)"))
    assert switched.rows[0][0]  # the switch itself is allowed in this mode
    with pytest.raises(DBAPIError, match="permission denied for table conversations"):
        await execute(
            engine,
            raw(
                "SELECT set_config('role', session_user, true), "
                "(SELECT count(*) FROM public.conversations)"
            ),
        )


async def test_driver_refuses_multiple_statements(engine: AsyncEngine) -> None:
    with pytest.raises(DBAPIError, match="multiple commands"):
        await execute(
            engine,
            raw("SELECT set_config('role', session_user, true); SELECT * FROM public.jobs"),
        )


async def test_dedicated_login_cannot_switch_role(login_engine: AsyncEngine) -> None:
    escape = raw("SELECT set_config('role', 'postgres', true)")
    with pytest.raises(DBAPIError, match="permission denied to set role"):
        await execute(login_engine, escape, set_role=False)


async def test_dedicated_login_reads_views(login_engine: AsyncEngine) -> None:
    result = await execute(
        login_engine, check("SELECT count(*) AS n FROM conversations"), set_role=False
    )
    assert result.columns == ["n"]


async def _seed(db: async_sessionmaker[AsyncSession]) -> None:
    await ingest_payloads(
        db,
        [
            conversation("a", channel="chat", started_at="2026-09-03T10:00:00"),
            conversation("b", channel="voice", started_at="2026-09-04T10:00:00"),
            conversation("c", channel="voice", started_at="2026-10-04T10:00:00"),
        ],
    )


def _sql(sql: str, explanation: str = "counts") -> str:
    return json.dumps({"sql": sql, "explanation": explanation})


async def test_text_to_sql_runs_guarded_query(
    db: async_sessionmaker[AsyncSession], engine: AsyncEngine
) -> None:
    await _seed(db)
    llm = FakeLLMProvider(
        [_sql("SELECT channel, count(*) AS n FROM conversations GROUP BY channel ORDER BY channel")]
    )
    answer = await TextToSQL(llm, engine).answer(
        "Conversations by channel?", MetricsFilters(industry="telecom")
    )
    assert answer.result.columns == ["channel", "n"]
    assert answer.result.rows == [["chat", 1], ["voice", 2]]
    assert answer.sql.startswith("SELECT channel, COUNT(*) AS n FROM analytics.conversations")
    assert answer.attempts == 1
    system = llm.calls[0][0].content
    assert "analytics.competitor_mentions" in system
    assert "industry = 'telecom'" in system


async def test_text_to_sql_repairs_after_guard_and_db_errors(
    db: async_sessionmaker[AsyncSession], engine: AsyncEngine
) -> None:
    await _seed(db)
    llm = FakeLLMProvider(
        [
            _sql("SELECT count(*) FROM public.conversations"),
            _sql("SELECT count(*) FROM conversations WHERE nope = 1"),
            _sql("SELECT count(*) AS n FROM conversations"),
        ]
    )
    answer = await TextToSQL(llm, engine, max_attempts=3).answer("How many?")
    assert answer.result.rows == [[3]]
    assert answer.attempts == 3
    assert "rejected by the SQL guard" in answer.errors[0]
    assert 'column "nope" does not exist' in answer.errors[1]
    assert "Fix it." in llm.calls[1][-1].content


async def test_text_to_sql_gives_up(engine: AsyncEngine) -> None:
    llm = FakeLLMProvider([_sql("DELETE FROM conversations")] * 2)
    with pytest.raises(TextToSQLError) as exc:
        await TextToSQL(llm, engine).answer("Delete everything")
    assert len(exc.value.errors) == 2


async def test_metrics_endpoint(
    app: FastAPI, client: AsyncClient, db: async_sessionmaker[AsyncSession]
) -> None:
    await _seed(db)
    app.state.llm = FakeLLMProvider(
        [
            _sql(
                "SELECT date_trunc('month', started_at) AS month, count(*) AS n "
                "FROM conversations GROUP BY 1 ORDER BY 1"
            )
        ]
    )
    response = await client.post("/v1/metrics", json={"question": "Conversations per month?"})
    body = response.json()
    assert response.status_code == 200
    assert body["columns"] == ["month", "n"]
    assert [row[1] for row in body["rows"]] == [2, 1]
    assert body["rows"][0][0].startswith("2026-09-01")
    assert body["truncated"] is False

    app.state.llm = FakeLLMProvider([_sql("DROP TABLE conversations")] * 2)
    failed = await client.post("/v1/metrics", json={"question": "Drop it"})
    assert failed.status_code == 422
    assert len(failed.json()["detail"]["errors"]) == 2
