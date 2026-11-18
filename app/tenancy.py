"""Tenants, their API keys and users, for the admin API and `scripts/tenants.py`.

These tables are not tenant data (no row-level security), so callers use a
system session and every query here filters on the tenant explicitly.
"""

import re
import uuid
from dataclasses import dataclass

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import generate_key, hash_key
from app.db.models import ApiKey, Role, Tenant, User

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


class EmailTakenError(Exception):
    """The email already has access (each person belongs to one tenant)."""


def normalize_email(email: str) -> str:
    email = email.strip().lower()
    if "@" not in email or len(email) > 320:
        raise ValueError("not an email address")
    return email


async def get_tenant_by_id(session: AsyncSession, tenant_id: uuid.UUID) -> Tenant | None:
    return await session.get(Tenant, tenant_id)


async def add_user(session: AsyncSession, tenant_id: uuid.UUID, email: str, role: Role) -> User:
    user = User(tenant_id=tenant_id, email=normalize_email(email), role=role)
    session.add(user)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise EmailTakenError(user.email) from exc
    await session.refresh(user)
    return user


async def list_users(session: AsyncSession, tenant_id: uuid.UUID) -> list[User]:
    return list(
        await session.scalars(select(User).where(User.tenant_id == tenant_id).order_by(User.email))
    )


async def get_user(session: AsyncSession, tenant_id: uuid.UUID, user_id: uuid.UUID) -> User | None:
    return await session.scalar(select(User).where(User.id == user_id, User.tenant_id == tenant_id))


async def set_user_role(
    session: AsyncSession, tenant_id: uuid.UUID, user_id: uuid.UUID, role: Role
) -> User | None:
    user = await get_user(session, tenant_id, user_id)
    if user is not None:
        user.role = role
        await session.commit()
    return user


async def remove_user(session: AsyncSession, tenant_id: uuid.UUID, user_id: uuid.UUID) -> bool:
    """Remove a user of this tenant; their next request is refused."""
    removed = await session.scalar(
        delete(User).where(User.id == user_id, User.tenant_id == tenant_id).returning(User.id)
    )
    await session.commit()
    return removed is not None
