"""Tenant isolation (row-level security), roles and API key management.

The default tenant ("A" here) is the one the bootstrap test key belongs to;
each test that needs a second tenant gets a fresh one ("B").
"""

import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.analytics.executor import execute
from app.analytics.sql_guard import check
from app.db.models import DEFAULT_TENANT_ID, Chunk, Conversation, Customer, Insight, Role, Turn
from app.db.session import create_sessionmaker, tenant_sessionmaker
from app.ingestion.service import upsert_customers
from app.schemas.customer import CustomerIn
from app.tenancy import create_key, create_tenant
from app.workers.tasks import process_ingestion_job
from scripts import tenants as tenants_cli
from tests.conftest import FakeQueue
from tests.factories import batch, conversation
from tests.helpers import ingest_payloads

pytestmark = pytest.mark.integration


@pytest.fixture
async def tenant_b(engine: AsyncEngine) -> uuid.UUID:
    async with create_sessionmaker(engine)() as session:
        return (await create_tenant(session, "tenant-b", "Tenant B")).id


@pytest.fixture
def db_b(engine: AsyncEngine, tenant_b: uuid.UUID) -> async_sessionmaker[AsyncSession]:
    return tenant_sessionmaker(engine, tenant_b)


async def _key(engine: AsyncEngine, tenant_id: uuid.UUID, role: Role) -> dict[str, str]:
    async with create_sessionmaker(engine)() as session:
        created = await create_key(session, tenant_id, name=f"test {role}", role=role)
    return {"X-API-Key": created.key}


async def _count(sessionmaker: async_sessionmaker[AsyncSession], model: Any) -> int:
    async with sessionmaker() as s:
        return await s.scalar(select(func.count()).select_from(model)) or 0


# --- the database enforces isolation -------------------------------------------------


async def test_each_tenant_sees_only_its_rows(
    db: async_sessionmaker[AsyncSession], db_b: async_sessionmaker[AsyncSession]
) -> None:
    await ingest_payloads(db, [conversation("a-1"), conversation("a-2")])
    # The same external id in another tenant is a different conversation.
    await ingest_payloads(db_b, [conversation("a-1")])

    for model in (Conversation, Turn, Chunk, Insight):
        assert await _count(db, model) == 2 * (await _count(db_b, model)) > 0, model
    async with db_b() as s:
        assert list(await s.scalars(select(Conversation.external_id))) == ["a-1"]


async def test_customer_ids_are_per_tenant(
    db: async_sessionmaker[AsyncSession], db_b: async_sessionmaker[AsyncSession]
) -> None:
    async with db() as s:
        await upsert_customers(s, [CustomerIn(id="c1", industry="telecom", plan_code="A")])
    async with db_b() as s:
        await upsert_customers(s, [CustomerIn(id="c1", industry="banking", plan_code="B")])
    async with db() as s:
        assert (await s.get(Customer, (DEFAULT_TENANT_ID, "c1"))) is not None
        assert list(await s.scalars(select(Customer.plan_code))) == ["A"]


async def test_cannot_write_into_another_tenant(
    db: async_sessionmaker[AsyncSession], tenant_b: uuid.UUID
) -> None:
    async with db() as s:
        with pytest.raises(ProgrammingError, match="row-level security"):
            await s.execute(
                text("INSERT INTO customers (tenant_id, id, industry) VALUES (:t, 'x', 'telecom')"),
                {"t": tenant_b},
            )


async def test_without_a_tenant_nothing_is_visible_or_writable(
    db: async_sessionmaker[AsyncSession], engine: AsyncEngine
) -> None:
    await ingest_payloads(db, [conversation("a-1")])
    async with engine.connect() as conn:
        # What a session that forgot its tenant gets: the app role, no app.tenant_id.
        await conn.execute(text("SET ROLE voc_app"))
        assert await conn.scalar(text("SELECT count(*) FROM conversations")) == 0
        # "unrecognized configuration parameter", or "" if this pooled connection set it before
        with pytest.raises(DBAPIError, match=r"app\.tenant_id|type uuid"):
            await conn.execute(text("INSERT INTO customers (id, industry) VALUES ('x', 't')"))
        await conn.rollback()


async def test_analytics_views_are_tenant_filtered(
    db: async_sessionmaker[AsyncSession],
    db_b: async_sessionmaker[AsyncSession],
    engine: AsyncEngine,
    tenant_b: uuid.UUID,
) -> None:
    await ingest_payloads(db, [conversation("a-1"), conversation("a-2")])
    await ingest_payloads(db_b, [conversation("b-1")])
    query = check("SELECT count(*) FROM conversations")

    async def count(tenant: uuid.UUID | None) -> Any:
        return (await execute(engine, query, tenant_id=tenant)).rows[0][0]

    assert await count(DEFAULT_TENANT_ID) == 2
    assert await count(tenant_b) == 1
    assert await count(None) == 0  # no tenant, no rows


# --- the API resolves keys to a tenant and a role ------------------------------------


async def test_api_requests_are_scoped_to_the_key_tenant(
    client: AsyncClient,
    db: async_sessionmaker[AsyncSession],
    engine: AsyncEngine,
    tenant_b: uuid.UUID,
) -> None:
    await ingest_payloads(db, [conversation("a-1")])
    analyst_b = await _key(engine, tenant_b, Role.ANALYST)

    assert (await client.get("/v1/conversations/a-1")).status_code == 200
    assert (await client.get("/v1/conversations/a-1", headers=analyst_b)).status_code == 404
    insights = (await client.get("/v1/insights", headers=analyst_b)).json()
    assert insights["totals"]["conversations"] == 0


async def test_uploads_and_jobs_belong_to_the_key_tenant(
    client: AsyncClient,
    queue: FakeQueue,
    engine: AsyncEngine,
    tenant_b: uuid.UUID,
    db: async_sessionmaker[AsyncSession],
    db_b: async_sessionmaker[AsyncSession],
    worker_ctx: dict[str, Any],
) -> None:
    ingest_b = await _key(engine, tenant_b, Role.INGEST)
    response = await client.post(
        "/v1/conversations", json=batch(conversation("b-1")), headers=ingest_b
    )
    assert response.status_code == 202
    job_id = response.json()["job_id"]
    assert queue.tenants == [tenant_b]

    # Other tenants can't see the job.
    assert (await client.get(f"/v1/jobs/{job_id}")).status_code == 404
    assert (await client.get(f"/v1/jobs/{job_id}", headers=ingest_b)).status_code == 200

    # The worker processes it inside tenant B.
    assert await process_ingestion_job(worker_ctx, job_id, str(tenant_b)) == "succeeded"
    assert await _count(db_b, Chunk) > 0
    assert await _count(db, Chunk) == 0


@pytest.mark.parametrize(
    ("role", "method", "path", "allowed"),
    [
        (Role.ANALYST, "GET", "/v1/insights", True),
        (Role.ANALYST, "POST", "/v1/conversations", False),
        (Role.ANALYST, "PUT", "/v1/customers", False),
        (Role.ANALYST, "GET", "/v1/api-keys", False),
        (Role.INGEST, "POST", "/v1/conversations", True),
        (Role.INGEST, "PUT", "/v1/customers", True),
        (Role.INGEST, "GET", "/v1/insights", False),
        (Role.INGEST, "POST", "/v1/ask", False),
        (Role.INGEST, "POST", "/v1/metrics", False),
        (Role.INGEST, "GET", "/v1/conversations/x", False),
        (Role.INGEST, "GET", "/v1/api-keys", False),
        (Role.ADMIN, "GET", "/v1/api-keys", True),
        (Role.ADMIN, "GET", "/v1/insights", True),
    ],
)
async def test_roles(
    client: AsyncClient, engine: AsyncEngine, role: Role, method: str, path: str, allowed: bool
) -> None:
    headers = await _key(engine, DEFAULT_TENANT_ID, role)
    bodies = {
        "/v1/conversations": batch(conversation("r-1")),
        "/v1/customers": {"customers": []},
        "/v1/ask": {"question": "Why?"},
        "/v1/metrics": {"question": "How many?"},
    }
    response = await client.request(method, path, json=bodies.get(path), headers=headers)
    if allowed:
        assert response.status_code not in (401, 403), response.text
    else:
        assert response.status_code == 403
        assert f"The {role} role cannot use this endpoint" in response.json()["detail"]


# --- admins manage their tenant's keys ----------------------------------------------


async def test_admin_creates_lists_and_revokes_keys(client: AsyncClient) -> None:
    created = await client.post("/v1/api-keys", json={"name": "dashboard", "role": "analyst"})
    assert created.status_code == 201
    body = created.json()
    key, key_id = body["key"], body["id"]
    assert key.startswith(f"voc_{body['prefix']}_")

    analyst = {"X-API-Key": key}
    assert (await client.get("/v1/insights", headers=analyst)).status_code == 200

    listed = (await client.get("/v1/api-keys")).json()
    assert [k["id"] for k in listed] == [key_id]
    assert "key" not in listed[0]
    assert "key_hash" not in listed[0]

    assert (await client.delete(f"/v1/api-keys/{key_id}")).status_code == 204
    assert (await client.get("/v1/insights", headers=analyst)).status_code == 401
    assert (await client.delete(f"/v1/api-keys/{key_id}")).status_code == 404
    assert (await client.get("/v1/api-keys")).json()[0]["revoked_at"] is not None


async def test_admin_cannot_touch_another_tenant_keys(
    client: AsyncClient, engine: AsyncEngine, tenant_b: uuid.UUID
) -> None:
    async with create_sessionmaker(engine)() as session:
        other = await create_key(session, tenant_b, name="b", role=Role.ANALYST)
    assert (await client.get("/v1/api-keys")).json() == []
    assert (await client.delete(f"/v1/api-keys/{other.record.id}")).status_code == 404
    still_valid = {"X-API-Key": other.key}
    assert (await client.get("/v1/insights", headers=still_valid)).status_code == 200


async def test_keys_are_stored_hashed(client: AsyncClient, engine: AsyncEngine) -> None:
    key = (await client.post("/v1/api-keys", json={"name": "x", "role": "ingest"})).json()["key"]
    async with engine.connect() as conn:
        dump = await conn.scalar(text("SELECT string_agg(api_keys::text, ' ') FROM api_keys"))
    assert key not in dump


# --- operator CLI ---------------------------------------------------------------------


def test_cli_creates_tenants_and_keys(
    migrated_db: str, engine: AsyncEngine, capsys: pytest.CaptureFixture[str]
) -> None:
    url = ["--database-url", migrated_db]
    assert tenants_cli.main([*url, "create", "acme", "--name", "Acme"]) == 0
    tenant_id = capsys.readouterr().out.strip()
    assert tenants_cli.main([*url, "create", "acme"]) == 1
    assert "already exists" in capsys.readouterr().err

    assert tenants_cli.main([*url, "create-key", "acme", "--role", "admin", "--name", "ops"]) == 0
    key = capsys.readouterr().out.strip()
    assert key.startswith("voc_")

    assert tenants_cli.main([*url, "keys", "acme"]) == 0
    listing = capsys.readouterr().out
    assert "admin" in listing
    assert "ops" in listing
    assert key not in listing
    key_id = listing.split()[0]

    assert tenants_cli.main([*url, "revoke-key", "acme", key_id]) == 0
    assert tenants_cli.main([*url, "revoke-key", "acme", key_id]) == 1
    assert tenants_cli.main([*url, "list"]) == 0
    assert tenant_id in capsys.readouterr().out
    assert tenants_cli.main([*url, "keys", "nope"]) == 1
