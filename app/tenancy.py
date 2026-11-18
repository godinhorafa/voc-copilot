"""Tenants and their API keys, for the admin API and `scripts/tenants.py`.

These tables are not tenant data (no row-level security), so callers use a
system session and every query here filters on the tenant explicitly.
"""

import re
import uuid
from dataclasses import dataclass

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import generate_key, hash_key
from app.db.models import ApiKey, Role, Tenant

SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")


@dataclass(frozen=True)
class CreatedKey:
    record: ApiKey
    key: str  # shown once, never stored


async def create_tenant(session: AsyncSession, slug: str, name: str) -> Tenant:
    if not SLUG.match(slug):
        raise ValueError("slug must be 2-63 lowercase letters, digits or hyphens")
    tenant = Tenant(slug=slug, name=name)
    session.add(tenant)
    await session.commit()
    await session.refresh(tenant)  # load server defaults
    return tenant


async def get_tenant(session: AsyncSession, slug: str) -> Tenant | None:
    return await session.scalar(select(Tenant).where(Tenant.slug == slug))


async def list_tenants(session: AsyncSession) -> list[Tenant]:
    return list(await session.scalars(select(Tenant).order_by(Tenant.created_at)))


async def create_key(
    session: AsyncSession, tenant_id: uuid.UUID, *, name: str, role: Role
) -> CreatedKey:
    key, prefix = generate_key()
    record = ApiKey(
        tenant_id=tenant_id, name=name, role=role, prefix=prefix, key_hash=hash_key(key)
    )
    session.add(record)
    await session.commit()
    await session.refresh(record)
    return CreatedKey(record=record, key=key)


async def list_keys(session: AsyncSession, tenant_id: uuid.UUID) -> list[ApiKey]:
    return list(
        await session.scalars(
            select(ApiKey).where(ApiKey.tenant_id == tenant_id).order_by(ApiKey.created_at)
        )
    )


async def revoke_key(session: AsyncSession, tenant_id: uuid.UUID, key_id: uuid.UUID) -> bool:
    """Revoke a key of this tenant. False if there is no such active key."""
    revoked = await session.scalar(
        update(ApiKey)
        .where(ApiKey.id == key_id, ApiKey.tenant_id == tenant_id, ApiKey.revoked_at.is_(None))
        .values(revoked_at=func.now())
        .returning(ApiKey.id)
    )
    await session.commit()
    return revoked is not None
