"""People signing in with OIDC ID tokens (ADR 0013), and admins managing them.

The test client also sends the bootstrap API key on every request: a bearer
token, when present, must be the only credential considered.
"""

import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.oidc import OIDCVerifier
from app.db.models import DEFAULT_TENANT_ID, Role
from app.db.session import create_sessionmaker
from app.tenancy import add_user, create_tenant
from scripts import tenants as tenants_cli
from tests.factories import batch, conversation
from tests.helpers import ingest_payloads
from tests.idp import AUDIENCE, ISSUER, FakeIdP

pytestmark = pytest.mark.integration


@pytest.fixture
def idp(app: FastAPI) -> FakeIdP:
    provider = FakeIdP()
    app.state.oidc = OIDCVerifier(ISSUER, AUDIENCE, client=provider.client())
    return provider


async def _user(engine: AsyncEngine, email: str, role: Role, tenant: uuid.UUID) -> uuid.UUID:
    async with create_sessionmaker(engine)() as session:
        return (await add_user(session, tenant, email, role)).id


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_signed_in_user_gets_their_tenant_and_role(
    client: AsyncClient, idp: FakeIdP, engine: AsyncEngine
) -> None:
    await _user(engine, "ana@acme.test", Role.ANALYST, DEFAULT_TENANT_ID)
    me = await client.get("/v1/me", headers=bearer(idp.token("ANA@acme.test")))
    assert me.status_code == 200
    assert me.json() == {
        "auth": "oidc",
        "role": "analyst",
        "email": "ana@acme.test",
        "tenant": {"id": str(DEFAULT_TENANT_ID), "slug": "default", "name": "Default"},
    }
    analyst = bearer(idp.token("ana@acme.test"))
    assert (await client.get("/v1/insights", headers=analyst)).status_code == 200
    upload = await client.post("/v1/conversations", json=batch(conversation()), headers=analyst)
    assert upload.status_code == 403


async def test_api_key_callers_see_themselves_too(client: AsyncClient) -> None:
    me = (await client.get("/v1/me")).json()
    assert me["auth"] == "api_key"
    assert me["role"] == "admin"
    assert me["email"] is None


async def test_users_only_see_their_tenant(
    client: AsyncClient,
    idp: FakeIdP,
    engine: AsyncEngine,
    db: async_sessionmaker[AsyncSession],
) -> None:
    await ingest_payloads(db, [conversation("a-1")])
    async with create_sessionmaker(engine)() as session:
        other = (await create_tenant(session, "other", "Other")).id
    await _user(engine, "bo@other.test", Role.ANALYST, other)
    bo = bearer(idp.token("bo@other.test"))
    assert (await client.get("/v1/conversations/a-1", headers=bo)).status_code == 404
    assert (await client.get("/v1/me", headers=bo)).json()["tenant"]["slug"] == "other"


@pytest.mark.parametrize(
    ("token_kwargs", "status"),
    [
        ({"aud": "other-app"}, 401),
        ({"email_verified": False}, 401),
        ({"email": "stranger@acme.test"}, 403),  # valid token, but not a user
    ],
)
async def test_bad_tokens_never_fall_back_to_the_api_key(
    client: AsyncClient,
    idp: FakeIdP,
    engine: AsyncEngine,
    token_kwargs: dict[str, Any],
    status: int,
) -> None:
    await _user(engine, "ana@acme.test", Role.ADMIN, DEFAULT_TENANT_ID)
    response = await client.get(
        "/v1/insights", headers=bearer(idp.token(**{"email": "ana@acme.test"} | token_kwargs))
    )
    assert response.status_code == status
    if status == 401:
        assert response.headers["www-authenticate"].startswith("Bearer")


async def test_bearer_without_oidc_configured(client: AsyncClient, app: FastAPI) -> None:
    assert app.state.oidc is None
    response = await client.get("/v1/insights", headers=bearer("whatever"))
    assert response.status_code == 401
    assert "not enabled" in response.json()["detail"]


async def test_admin_manages_users(client: AsyncClient, idp: FakeIdP, engine: AsyncEngine) -> None:
    created = await client.post("/v1/users", json={"email": " Bea@Acme.test ", "role": "ingest"})
    assert created.status_code == 201
    user_id = created.json()["id"]
    assert created.json()["email"] == "bea@acme.test"
    bea = bearer(idp.token("bea@acme.test"))
    assert (await client.get("/v1/me", headers=bea)).json()["role"] == "ingest"

    duplicate = await client.post("/v1/users", json={"email": "bea@acme.test", "role": "admin"})
    assert duplicate.status_code == 409
    invalid = await client.post("/v1/users", json={"email": "nope", "role": "admin"})
    assert invalid.status_code == 422

    assert (await client.patch(f"/v1/users/{user_id}", json={"role": "analyst"})).status_code == 200
    assert (await client.get("/v1/me", headers=bea)).json()["role"] == "analyst"
    assert [u["email"] for u in (await client.get("/v1/users")).json()] == ["bea@acme.test"]

    assert (await client.delete(f"/v1/users/{user_id}")).status_code == 204
    assert (await client.get("/v1/me", headers=bea)).status_code == 403  # same token, no access
    assert (await client.delete(f"/v1/users/{user_id}")).status_code == 404


async def test_admins_cannot_lock_themselves_out_or_reach_other_tenants(
    client: AsyncClient, idp: FakeIdP, engine: AsyncEngine
) -> None:
    me = await _user(engine, "root@acme.test", Role.ADMIN, DEFAULT_TENANT_ID)
    root = bearer(idp.token("root@acme.test"))
    assert (await client.delete(f"/v1/users/{me}", headers=root)).status_code == 409
    demote = await client.patch(f"/v1/users/{me}", json={"role": "ingest"}, headers=root)
    assert demote.status_code == 409

    async with create_sessionmaker(engine)() as session:
        other = (await create_tenant(session, "other", "Other")).id
    theirs = await _user(engine, "x@other.test", Role.ADMIN, other)
    assert (await client.delete(f"/v1/users/{theirs}", headers=root)).status_code == 404
    assert [u["email"] for u in (await client.get("/v1/users", headers=root)).json()] == [
        "root@acme.test"
    ]


async def test_non_admins_cannot_manage_users(
    client: AsyncClient, idp: FakeIdP, engine: AsyncEngine
) -> None:
    await _user(engine, "ana@acme.test", Role.ANALYST, DEFAULT_TENANT_ID)
    response = await client.get("/v1/users", headers=bearer(idp.token("ana@acme.test")))
    assert response.status_code == 403


def test_cli_manages_users(
    migrated_db: str, engine: AsyncEngine, capsys: pytest.CaptureFixture[str]
) -> None:
    url = ["--database-url", migrated_db]
    add = [*url, "add-user", "default"]
    assert tenants_cli.main([*add, "Cai@Acme.test", "--role", "analyst"]) == 0
    assert tenants_cli.main([*add, "cai@acme.test", "--role", "admin"]) == 1
    assert "already has access" in capsys.readouterr().err
    assert tenants_cli.main([*url, "users", "default"]) == 0
    assert "analyst  cai@acme.test" in capsys.readouterr().out
    assert tenants_cli.main([*url, "remove-user", "default", "CAI@acme.test"]) == 0
    assert tenants_cli.main([*url, "remove-user", "default", "cai@acme.test"]) == 1
