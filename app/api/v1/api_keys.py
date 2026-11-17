"""Admins manage their own tenant's API keys. Tenants themselves are created
by the operator (`python -m scripts.tenants`), not through the API."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status

from app.api.auth import PrincipalDep, require_role
from app.api.deps import SystemSessionmakerDep
from app.db.models import ApiKey
from app.schemas.api_key import ApiKeyCreate, ApiKeyCreated, ApiKeyOut
from app.tenancy import create_key, list_keys, revoke_key

router = APIRouter(prefix="/api-keys", tags=["api-keys"], dependencies=[Depends(require_role())])


@router.get("", response_model=list[ApiKeyOut])
async def get_keys(principal: PrincipalDep, sessionmaker: SystemSessionmakerDep) -> list[ApiKey]:
    """Keys of the caller's tenant, revoked ones included. Never the keys themselves."""
    async with sessionmaker() as session:
        return await list_keys(session, principal.tenant_id)


@router.post("", response_model=ApiKeyCreated, status_code=status.HTTP_201_CREATED)
async def post_key(
    body: ApiKeyCreate, principal: PrincipalDep, sessionmaker: SystemSessionmakerDep
) -> ApiKeyCreated:
    """Create a key for the caller's tenant. The response is the only time it is shown."""
    async with sessionmaker() as session:
        created = await create_key(session, principal.tenant_id, name=body.name, role=body.role)
    return ApiKeyCreated(**ApiKeyOut.model_validate(created.record).model_dump(), key=created.key)


@router.delete("/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_key(
    key_id: uuid.UUID, principal: PrincipalDep, sessionmaker: SystemSessionmakerDep
) -> Response:
    """Revoke a key. Requests with it fail from now on."""
    async with sessionmaker() as session:
        if not await revoke_key(session, principal.tenant_id, key_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "No active key with this id")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
