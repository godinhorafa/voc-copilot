"""Run a guarded query with database-level limits.

Even if the guard missed something, the database refuses it: the transaction
is READ ONLY, has a statement timeout, is always rolled back, and runs with
only the analytics role's privileges (SELECT on the analytics views).

How strong the last part is depends on the connection (ADR 0007):

* dedicated engine (ANALYTICS_DATABASE_URL), logged in as a user that is only
  a member of the analytics role: there is no other role to switch to.
* shared engine (dev default): `SET LOCAL ROLE` narrows privileges for the
  transaction. A query could call `set_config('role', ...)` to switch back,
  but privileges are checked when a statement starts, the driver accepts one
  statement per call, and the transaction is rolled back right after, so the
  switch never applies to a statement that reads data. The guard also blocks
  `set_config`. A dedicated engine removes the switch altogether.

The views only return the rows of the tenant in `app.tenant_id` (ADR 0012),
set here for the transaction. Without a tenant they return nothing.
"""

import uuid
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.analytics.catalog import ROLE
from app.analytics.sql_guard import GuardedQuery


@dataclass(frozen=True)
class QueryResult:
    columns: list[str]
    rows: list[list[Any]]
    truncated: bool  # more rows existed than max_rows


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    return value


async def execute(
    engine: AsyncEngine,
    query: GuardedQuery,
    *,
    max_rows: int = 200,
    timeout_ms: int = 5000,
    set_role: bool = True,
    tenant_id: uuid.UUID | None = None,
) -> QueryResult:
    async with engine.connect() as conn:
        tx = await conn.begin()
        try:
            await conn.execute(text("SET TRANSACTION READ ONLY"))
            await conn.execute(text(f"SET LOCAL statement_timeout = {int(timeout_ms)}"))
            if tenant_id is not None:
                await conn.execute(
                    text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(tenant_id)}
                )
            if set_role:
                await conn.execute(text(f"SET LOCAL ROLE {ROLE}"))
            await conn.execute(text("SET LOCAL search_path = analytics"))
            # Driver-level execution: text() would treat ':word' inside literals as a bind.
            result = await conn.exec_driver_sql(query.sql)
            columns = list(result.keys())
            rows = result.fetchmany(max_rows + 1)
        finally:
            await tx.rollback()
    return QueryResult(
        columns=columns,
        rows=[[_jsonable(v) for v in row] for row in rows[:max_rows]],
        truncated=len(rows) > max_rows,
    )
