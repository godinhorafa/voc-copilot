"""Fixed-window rate limiting in Redis for the expensive endpoints.

Each request increments a counter keyed by (scope, client, window number); the
key expires with its window. Fixed windows allow a burst of up to 2x the limit
across a window boundary, which is acceptable for protecting LLM spend and
much simpler than a sliding log.

The client is identified by a hash of its API key plus its IP. Behind a proxy
that sets X-Forwarded-For (the web UI's nginx), enable
RATE_LIMIT_TRUST_FORWARDED_FOR so each user's IP counts, not the proxy's.

If Redis is unavailable the request is allowed and a warning is logged: the
limiter protects cost, and failing closed would turn a cache outage into an
API outage.
"""

import hashlib
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import structlog
from fastapi import HTTPException, Request, Response, status
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.config import Settings

logger = structlog.get_logger(__name__)

_UNITS = {"second": 1, "minute": 60, "hour": 3600, "day": 86400}
_RATE = re.compile(r"^\s*(\d+)\s*/\s*(second|minute|hour|day)\s*$")


@dataclass(frozen=True)
class Rate:
    limit: int
    window_s: int


def parse_rate(value: str) -> Rate | None:
    """ "30/minute" -> Rate(30, 60); "" or "0/minute" -> None (no limit)."""
    if not value.strip():
        return None
    match = _RATE.match(value)
    if not match:
        raise ValueError(f"rate must look like '30/minute', got {value!r}")
    limit = int(match.group(1))
    return Rate(limit, _UNITS[match.group(2)]) if limit else None


@dataclass(frozen=True)
class Decision:
    allowed: bool
    limit: int
    remaining: int
    reset_s: int  # seconds until the window resets


class RateLimiter:
    def __init__(self, redis: Redis, clock: Callable[[], float] = time.time) -> None:
        self._redis = redis
        self._clock = clock

    async def hit(self, key: str, rate: Rate) -> Decision:
        now = self._clock()
        window = int(now // rate.window_s)
        reset_s = max(1, int((window + 1) * rate.window_s - now))
        redis_key = f"ratelimit:{key}:{window}"
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.incr(redis_key)
            pipe.expire(redis_key, rate.window_s + 1)
            count, _ = await pipe.execute()
        remaining = max(0, rate.limit - int(count))
        return Decision(int(count) <= rate.limit, rate.limit, remaining, reset_s)


def client_identity(request: Request, settings: Settings) -> str:
    api_key = request.headers.get("x-api-key", "")
    ip = request.client.host if request.client else "unknown"
    if settings.rate_limit_trust_forwarded_for:
        forwarded = request.headers.get("x-forwarded-for", "")
        ip = forwarded.split(",")[0].strip() or ip
    return hashlib.sha256(f"{api_key}|{ip}".encode()).hexdigest()[:24]


def rate_limit(
    scope: str, setting: Callable[[Settings], str]
) -> Callable[[Request, Response], Awaitable[None]]:
    """FastAPI dependency limiting one endpoint group, configured by a setting."""

    async def dependency(request: Request, response: Response) -> None:
        settings: Settings = request.app.state.settings
        rate = parse_rate(setting(settings))
        if rate is None:
            return
        limiter = RateLimiter(request.app.state.redis)
        try:
            decision = await limiter.hit(f"{scope}:{client_identity(request, settings)}", rate)
        except RedisError as exc:
            logger.warning("rate_limit_unavailable", scope=scope, error=repr(exc))
            return
        headers = {
            "RateLimit-Limit": str(decision.limit),
            "RateLimit-Remaining": str(decision.remaining),
            "RateLimit-Reset": str(decision.reset_s),
        }
        if not decision.allowed:
            logger.info("rate_limited", scope=scope)
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                f"Rate limit of {rate.limit} requests per {rate.window_s}s exceeded",
                headers=headers | {"Retry-After": str(decision.reset_s)},
            )
        response.headers.update(headers)

    return dependency
