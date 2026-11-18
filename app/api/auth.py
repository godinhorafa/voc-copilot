"""Request authentication, roles and tenant-scoped database access.

Every /v1 request resolves to a principal, from one of:

* `Authorization: Bearer <ID token>`: a person signed in through OIDC
  (ADR 0013). When present it is the only credential considered, so a bad
  token never falls back to an API key added by a proxy.
* `X-API-Key`: a machine client (ADR 0012).

Database sessions for the request are scoped to the principal's tenant, so
row-level security applies to everything the endpoint reads or writes.

The bootstrap key (API_KEY) is an admin of the default tenant. It keeps
single-tenant deployments and the web UI's proxy working unchanged, and
creates the first real keys.
"""

import secrets
from collections.abc import AsyncIterator, Callable, Coroutine
from typing import Annotated, Any

import structlog
from fastapi import Depends, HTTPException, Security, status
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer
from opentelemetry import trace
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.deps import EngineDep, OIDCDep, SettingsDep, SystemSessionmakerDep
from app.core.oidc import OIDCError, OIDCVerifier
from app.core.security import Principal, hash_key
from app.db.models import DEFAULT_TENANT_ID, ApiKey, Role, User
from app.db.session import tenant_sessionmaker

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
bearer = HTTPBearer(auto_error=False)

_UNAUTHORIZED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Invalid or missing API key",
    headers={"WWW-Authenticate": "APIKey"},
)


def _bearer_error(message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=message,
        headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
    )


async def _user_principal(
    token: str,
    oidc: OIDCVerifier | None,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> Principal:
    if oidc is None:
        raise _bearer_error("Sign-in is not enabled on this server (OIDC_ISSUER is unset)")
    try:
        identity = await oidc.verify(token)
    except OIDCError as exc:
        raise _bearer_error(str(exc)) from exc
    async with sessionmaker() as session:
        user = await session.scalar(select(User).where(User.email == identity.email))
    if user is None:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"{identity.email} has no access to VOC Copilot; ask an admin to add you",
        )
    return Principal(tenant_id=user.tenant_id, role=user.role, user_id=user.id, email=user.email)


async def require_principal(
    settings: SettingsDep,
    sessionmaker: SystemSessionmakerDep,
    oidc: OIDCDep,
    api_key: Annotated[str | None, Security(api_key_header)] = None,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Security(bearer)] = None,
) -> Principal:
    if credentials is not None:
        principal = await _user_principal(credentials.credentials, oidc, sessionmaker)
        _annotate(principal)
        return principal
    if not api_key:
        raise _UNAUTHORIZED
    bootstrap = settings.api_key.get_secret_value().encode()
    # Constant-time comparison so response timing doesn't leak the key.
    if secrets.compare_digest(api_key.encode(), bootstrap):
        principal = Principal(tenant_id=DEFAULT_TENANT_ID, role=Role.ADMIN)
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
    _annotate(principal)
    return principal


def _annotate(principal: Principal) -> None:
    """Who is calling, on logs and traces. Never the email: it is personal data."""
    context = {
        "tenant_id": str(principal.tenant_id),
        "role": principal.role.value,
        "auth": principal.method,
    }
    if principal.user_id:
        context["user_id"] = str(principal.user_id)
    structlog.contextvars.bind_contextvars(**context)
    span = trace.get_current_span()
    for name, value in context.items():
        span.set_attribute(f"voc.{name}", value)


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
