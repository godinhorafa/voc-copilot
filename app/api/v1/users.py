"""The caller's identity, and admins managing who can sign in to their tenant.

Users sign in through the identity provider; this list decides which tenant
and role their email maps to (ADR 0013).
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status

from app.api.auth import PrincipalDep, require_role
from app.api.deps import SystemSessionmakerDep
from app.core.security import Principal
from app.db.models import User
from app.schemas.user import MeOut, TenantOut, UserCreate, UserOut, UserUpdate
from app.tenancy import (
    EmailTakenError,
    add_user,
    get_tenant_by_id,
    list_users,
    remove_user,
    set_user_role,
)

me_router = APIRouter(prefix="/me", tags=["users"])
router = APIRouter(prefix="/users", tags=["users"], dependencies=[Depends(require_role())])


@me_router.get("", response_model=MeOut)
async def get_me(principal: PrincipalDep, sessionmaker: SystemSessionmakerDep) -> MeOut:
    """Who is calling: sign-in method, role and tenant. Open to every role."""
    async with sessionmaker() as session:
        tenant = await get_tenant_by_id(session, principal.tenant_id)
    if tenant is None:  # deleted while the request was in flight
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Tenant no longer exists")
    return MeOut(
        auth="oidc" if principal.method == "oidc" else "api_key",
        role=principal.role,
        email=principal.email,
        tenant=TenantOut.model_validate(tenant),
    )


def _not_yourself(principal: Principal, user_id: uuid.UUID) -> None:
    # Keeps the last admin from locking everyone out by accident.
    if principal.user_id == user_id:
        raise HTTPException(status.HTTP_409_CONFLICT, "You can't change or remove yourself")


@router.get("", response_model=list[UserOut])
async def get_users(principal: PrincipalDep, sessionmaker: SystemSessionmakerDep) -> list[User]:
    async with sessionmaker() as session:
        return await list_users(session, principal.tenant_id)


@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def post_user(
    body: UserCreate, principal: PrincipalDep, sessionmaker: SystemSessionmakerDep
) -> User:
    """Give an email access to this tenant. They sign in with the identity provider."""
    async with sessionmaker() as session:
        try:
            return await add_user(session, principal.tenant_id, body.email, body.role)
        except ValueError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
        except EmailTakenError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, "This email already has access") from exc


@router.patch("/{user_id}", response_model=UserOut)
async def patch_user(
    user_id: uuid.UUID,
    body: UserUpdate,
    principal: PrincipalDep,
    sessionmaker: SystemSessionmakerDep,
) -> User:
    _not_yourself(principal, user_id)
    async with sessionmaker() as session:
        user = await set_user_role(session, principal.tenant_id, user_id, body.role)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such user")
    return user


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: uuid.UUID, principal: PrincipalDep, sessionmaker: SystemSessionmakerDep
) -> Response:
    """Remove access. Their next request is refused, even with a valid token."""
    _not_yourself(principal, user_id)
    async with sessionmaker() as session:
        if not await remove_user(session, principal.tenant_id, user_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "No such user")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
