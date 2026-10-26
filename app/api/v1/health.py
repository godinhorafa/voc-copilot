import asyncio
from collections.abc import Awaitable
from typing import Literal

import structlog
from fastapi import APIRouter, Response, status
from pydantic import BaseModel
from sqlalchemy import text

from app.api.deps import EngineDep, RedisDep

router = APIRouter(tags=["health"])
logger = structlog.get_logger(__name__)

CHECK_TIMEOUT_S = 2.0

CheckStatus = Literal["ok", "error"]


class Liveness(BaseModel):
    status: Literal["ok"] = "ok"


class Readiness(BaseModel):
    status: CheckStatus
    checks: dict[str, CheckStatus]


async def _check(name: str, probe: Awaitable[object]) -> CheckStatus:
    try:
        await asyncio.wait_for(probe, timeout=CHECK_TIMEOUT_S)
    except Exception as exc:  # any failure means "not ready"; details go to logs only
        logger.warning("readiness_check_failed", check=name, error=repr(exc))
        return "error"
    return "ok"


async def _ping_db(engine: EngineDep) -> None:
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))


@router.get("/health", response_model=Liveness)
async def health() -> Liveness:
    """Liveness: the process is up. Never touches dependencies."""
    return Liveness()


@router.get("/ready", response_model=Readiness)
async def ready(engine: EngineDep, redis: RedisDep, response: Response) -> Readiness:
    """Readiness: dependencies are reachable, so traffic can be routed here."""
    db, cache = await asyncio.gather(
        _check("postgres", _ping_db(engine)),
        _check("redis", redis.ping()),
    )
    checks = {"postgres": db, "redis": cache}
    overall: CheckStatus = "ok" if all(v == "ok" for v in checks.values()) else "error"
    if overall != "ok":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return Readiness(status=overall, checks=checks)
