"""Manage tenants and API keys (operator tool; connects to DATABASE_URL).

    uv run python -m scripts.tenants create acme --name "Acme Telecom"
    uv run python -m scripts.tenants list
    uv run python -m scripts.tenants create-key acme --role admin --name "acme admin"
    uv run python -m scripts.tenants keys acme
    uv run python -m scripts.tenants revoke-key acme <key-id>
    uv run python -m scripts.tenants add-user acme ana@acme.com --role admin
    uv run python -m scripts.tenants users acme
    uv run python -m scripts.tenants remove-user acme ana@acme.com

A new key is printed once, on stdout; only its hash is stored. Tenant admins
can then create and revoke their own keys through /v1/api-keys, and give
people access through /v1/users (they sign in via OIDC, ADR 0013).
"""

import argparse
import asyncio
import sys
import uuid
from collections.abc import Awaitable, Callable

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.config import get_settings
from app.db.models import Role, Tenant
from app.db.session import create_sessionmaker
from app.tenancy import (
    EmailTakenError,
    add_user,
    create_key,
    create_tenant,
    get_tenant,
    list_keys,
    list_tenants,
    list_users,
    normalize_email,
    remove_user,
    revoke_key,
)


class CommandError(Exception):
    pass


async def _tenant(session: AsyncSession, slug: str) -> Tenant:
    tenant = await get_tenant(session, slug)
    if tenant is None:
        raise CommandError(f"no tenant {slug!r}")
    return tenant


async def cmd_create(session: AsyncSession, args: argparse.Namespace) -> None:
    try:
        tenant = await create_tenant(session, args.slug, args.name or args.slug)
    except IntegrityError as exc:
        raise CommandError(f"tenant {args.slug!r} already exists") from exc
    except ValueError as exc:
        raise CommandError(str(exc)) from exc
    print(tenant.id)


async def cmd_list(session: AsyncSession, args: argparse.Namespace) -> None:
    for tenant in await list_tenants(session):
        print(f"{tenant.id}  {tenant.slug:<24} {tenant.name}")


async def cmd_create_key(session: AsyncSession, args: argparse.Namespace) -> None:
    tenant = await _tenant(session, args.slug)
    created = await create_key(session, tenant.id, name=args.name, role=Role(args.role))
    print(f"created key {created.record.id} ({args.role}); it is not shown again:", file=sys.stderr)
    print(created.key)


async def cmd_keys(session: AsyncSession, args: argparse.Namespace) -> None:
    tenant = await _tenant(session, args.slug)
    for key in await list_keys(session, tenant.id):
        state = f"revoked {key.revoked_at:%Y-%m-%d}" if key.revoked_at else "active"
        print(f"{key.id}  voc_{key.prefix}_…  {key.role.value:<8} {state:<18} {key.name}")


async def cmd_revoke_key(session: AsyncSession, args: argparse.Namespace) -> None:
    tenant = await _tenant(session, args.slug)
    if not await revoke_key(session, tenant.id, args.key_id):
        raise CommandError(f"no active key {args.key_id} in {args.slug!r}")
    print(f"revoked {args.key_id}", file=sys.stderr)


async def cmd_add_user(session: AsyncSession, args: argparse.Namespace) -> None:
    tenant = await _tenant(session, args.slug)
    try:
        user = await add_user(session, tenant.id, args.email, Role(args.role))
    except ValueError as exc:
        raise CommandError(str(exc)) from exc
    except EmailTakenError as exc:
        raise CommandError(f"{args.email} already has access") from exc
    print(user.id)


async def cmd_users(session: AsyncSession, args: argparse.Namespace) -> None:
    tenant = await _tenant(session, args.slug)
    for user in await list_users(session, tenant.id):
        print(f"{user.id}  {user.role.value:<8} {user.email}")


async def cmd_remove_user(session: AsyncSession, args: argparse.Namespace) -> None:
    tenant = await _tenant(session, args.slug)
    email = normalize_email(args.email)
    match = [u for u in await list_users(session, tenant.id) if u.email == email]
    if not match or not await remove_user(session, tenant.id, match[0].id):
        raise CommandError(f"{email} is not a user of {args.slug!r}")
    print(f"removed {email}", file=sys.stderr)


Command = Callable[[AsyncSession, argparse.Namespace], Awaitable[None]]


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Manage tenants and API keys.")
    p.add_argument("--database-url", default=None, help="defaults to DATABASE_URL")
    sub = p.add_subparsers(required=True)

    create = sub.add_parser("create", help="create a tenant")
    create.add_argument("slug")
    create.add_argument("--name")
    create.set_defaults(func=cmd_create)

    sub.add_parser("list", help="list tenants").set_defaults(func=cmd_list)

    create_k = sub.add_parser("create-key", help="create an API key for a tenant")
    create_k.add_argument("slug")
    create_k.add_argument("--role", choices=[r.value for r in Role], required=True)
    create_k.add_argument("--name", required=True, help="what the key is for")
    create_k.set_defaults(func=cmd_create_key)

    keys = sub.add_parser("keys", help="list a tenant's API keys")
    keys.add_argument("slug")
    keys.set_defaults(func=cmd_keys)

    revoke = sub.add_parser("revoke-key", help="revoke an API key")
    revoke.add_argument("slug")
    revoke.add_argument("key_id", type=uuid.UUID)
    revoke.set_defaults(func=cmd_revoke_key)

    add = sub.add_parser("add-user", help="let an email sign in to a tenant")
    add.add_argument("slug")
    add.add_argument("email")
    add.add_argument("--role", choices=[r.value for r in Role], required=True)
    add.set_defaults(func=cmd_add_user)

    users = sub.add_parser("users", help="list a tenant's users")
    users.add_argument("slug")
    users.set_defaults(func=cmd_users)

    remove = sub.add_parser("remove-user", help="remove a user's access")
    remove.add_argument("slug")
    remove.add_argument("email")
    remove.set_defaults(func=cmd_remove_user)
    return p


async def run(database_url: str, func: Command, args: argparse.Namespace) -> None:
    engine = create_async_engine(database_url)
    try:
        async with create_sessionmaker(engine)() as session:
            await func(session, args)
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    url = args.database_url or str(get_settings().database_url)
    try:
        asyncio.run(run(url, args.func, args))
    except CommandError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
