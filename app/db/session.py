"""Engines and sessions.

Two kinds of session (ADR 0012):

* tenant-scoped (`tenant_sessionmaker`): every transaction starts by setting
  `app.tenant_id` and switching to the `voc_app` role, so row-level security
  limits it to that tenant's rows. All work on customer data uses these.
* system (`create_sessionmaker`): no tenant and no role switch. Only for the
  tables that are not tenant data (tenants, api_keys), e.g. to authenticate
  a request before its tenant is known. On tenant tables it sees nothing
  (unless connected as a superuser) and cannot insert.
"""

import uuid
from typing import Any

from sqlalchemy import event, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session, SessionTransaction

from app.core.config import Settings

APP_ROLE = "voc_app"
TENANT_SETTING = "app.tenant_id"


def create_engine(settings: Settings, *, url: str | None = None) -> AsyncEngine:
    return create_async_engine(
        url or str(settings.database_url),
        pool_size=settings.db_pool_size,
        pool_pre_ping=True,
    )


def create_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """System sessions: not scoped to a tenant."""
    return async_sessionmaker(engine, expire_on_commit=False)


class TenantSession(Session):
    """Sync session class behind tenant-scoped AsyncSessions."""


@event.listens_for(TenantSession, "after_begin")
def _scope_transaction(
    session: Session, transaction: SessionTransaction, connection: Connection
) -> None:
    tenant_id: Any = session.info.get("tenant_id")
    if not isinstance(tenant_id, uuid.UUID):
        raise RuntimeError("tenant-scoped session without a tenant_id")
    # Both are transaction-local: the pooled connection returns to the pool clean.
    connection.execute(
        text("SELECT set_config(:name, :value, true)"),
        {"name": TENANT_SETTING, "value": str(tenant_id)},
    )
    connection.execute(text(f"SET LOCAL ROLE {APP_ROLE}"))


def tenant_sessionmaker(
    engine: AsyncEngine, tenant_id: uuid.UUID
) -> async_sessionmaker[AsyncSession]:
    """Sessions that can only read and write `tenant_id`'s rows."""
    return async_sessionmaker(
        engine,
        expire_on_commit=False,
        sync_session_class=TenantSession,
        info={"tenant_id": tenant_id},
    )
