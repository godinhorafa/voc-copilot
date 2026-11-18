"""Request authentication, roles and tenant-scoped database access.

Every /v1 request resolves its API key to a principal. Database sessions for
the request are scoped to the principal's tenant, so row-level security
applies to everything the endpoint reads or writes (ADR 0012).

The bootstrap key (API_KEY) is an admin of the default tenant. It keeps
single-tenant deployments and the web UI's proxy working unchanged, and
creates the first real keys.
"""

import secrets
from collections.abc import AsyncIterator, Callable, Coroutine
from typing import Annotated, Any

import structlog
from fastapi import Depends, HTTPException, Security, status
from fastapi.security import APIKeyHeader
from opentelemetry import trace
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.deps import EngineDep, SettingsDep, SystemSessionmakerDep
from app.core.security import Principal, hash_key
from app.db.models import DEFAULT_TENANT_ID, ApiKey, Role
from app.db.session import tenant_sessionmaker

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

_UNAUTHORIZED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Invalid or missing API key",
    headers={"WWW-Authenticate": "APIKey"},
)


async def require_principal(
    settings: SettingsDep,
    sessionmaker: SystemSessionmakerDep,
    api_key: str | None = Security(api_key_header),
) -> Principal:
    if not api_key:
        raise _UNAUTHORIZED
    bootstrap = settings.api_key.get_secret_value().encode()
    # Constant-time comparison so response timing doesn't leak the key.
    if secrets.compare_digest(api_key.encode(), bootstrap):
        principal = Principal(tenant_id=DEFAULT_TENANT_ID, role=Role.ADMIN, key_id=None)
    else:
        # Looked up by hash, so timing reveals nothing about stored keys.
        async with sessionmaker() as session:
            row = await session.scalar(
                select(ApiKey).where(
                    ApiKey.key_hash == hash_key(api_key), ApiKey.revoked_at.is_(None)
                )
            )
        if row is None:
            raise _UNAUTHORIZED
        principal = Principal(tenant_id=row.tenant_id, role=row.role, key_id=row.id)

    structlog.contextvars.bind_contextvars(
        tenant_id=str(principal.tenant_id), role=principal.role.value
    )
    span = trace.get_current_span()
    span.set_attribute("voc.tenant_id", str(principal.tenant_id))
    span.set_attribute("voc.role", principal.role.value)
    return principal


PrincipalDep = Annotated[Principal, Depends(require_principal)]


def require_role(*roles: Role) -> Callable[[Principal], Coroutine[Any, Any, Principal]]:
    """Dependency: the caller's role must be one of `roles` (admin always is)."""
    allowed = {Role.ADMIN, *roles}

    async def check(principal: PrincipalDep) -> Principal:
        if principal.role not in allowed:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"The {principal.role} role cannot use this endpoint",
            )
        return principal

    return check


def get_tenant_sessionmaker(
    engine: EngineDep, principal: PrincipalDep
) -> async_sessionmaker[AsyncSession]:
    return tenant_sessionmaker(engine, principal.tenant_id)


SessionmakerDep = Annotated[async_sessionmaker[AsyncSession], Depends(get_tenant_sessionmaker)]


async def get_tenant_session(sessionmaker: SessionmakerDep) -> AsyncIterator[AsyncSession]:
    async with sessionmaker() as session:
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_tenant_session)]
