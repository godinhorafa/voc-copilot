import json
from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from redis.asyncio import Redis

from app.core.config import Settings
from app.core.rate_limit import Rate, RateLimiter
from app.llm.fake import FakeLLMProvider

pytestmark = pytest.mark.integration


@pytest.fixture
async def redis(redis_url: str) -> AsyncIterator[Redis]:
    client = Redis.from_url(redis_url)
    await client.flushdb()
    yield client
    await client.flushdb()
    await client.aclose()


async def test_fixed_window(redis: Redis) -> None:
    now = [1000.0]
    limiter = RateLimiter(redis, clock=lambda: now[0])
    rate = Rate(limit=2, window_s=60)

    first = await limiter.hit("k", rate)
    second = await limiter.hit("k", rate)
    third = await limiter.hit("k", rate)
    assert [d.allowed for d in (first, second, third)] == [True, True, False]
    assert (first.remaining, second.remaining, third.remaining) == (1, 0, 0)
    assert third.reset_s == 20  # window [960, 1020)

    assert (await limiter.hit("other", rate)).allowed  # keys are independent
    now[0] = 1021.0  # next window
    assert (await limiter.hit("k", rate)).allowed
    assert await redis.ttl("ratelimit:k:17") <= 61  # keys expire with their window


async def test_ask_returns_429_with_headers(
    app: FastAPI, client: AsyncClient, settings: Settings, redis: Redis
) -> None:
    app.state.settings = settings.model_copy(update={"rate_limit_ask": "2/minute"})
    app.state.llm = FakeLLMProvider.permissive()

    codes = []
    for _ in range(3):
        response = await client.post("/v1/ask", json={"question": "Why?"})
        codes.append(response.status_code)
    assert codes == [200, 200, 429]
    assert response.headers["RateLimit-Limit"] == "2"
    assert response.headers["RateLimit-Remaining"] == "0"
    assert int(response.headers["Retry-After"]) >= 1
    assert "2 requests per 60s" in response.json()["detail"]


async def test_limits_are_per_endpoint_group(
    app: FastAPI, client: AsyncClient, settings: Settings, redis: Redis
) -> None:
    app.state.settings = settings.model_copy(
        update={"rate_limit_ask": "1/minute", "rate_limit_metrics": "5/minute"}
    )
    llm = FakeLLMProvider.permissive()
    llm.queue(json.dumps({"sql": "SELECT count(*) FROM conversations", "explanation": "n"}))
    app.state.llm = llm
    assert (await client.post("/v1/ask", json={"question": "Why?"})).status_code == 200
    assert (await client.post("/v1/ask", json={"question": "Why?"})).status_code == 429
    metrics = await client.post("/v1/metrics", json={"question": "How many?"})
    assert metrics.status_code == 200
    assert metrics.headers["RateLimit-Remaining"] == "4"


async def test_redis_outage_fails_open(
    app: FastAPI, client: AsyncClient, settings: Settings
) -> None:
    app.state.settings = settings.model_copy(update={"rate_limit_ask": "1/minute"})
    app.state.llm = FakeLLMProvider.permissive()
    await app.state.redis.aclose()
    app.state.redis = Redis.from_url("redis://127.0.0.1:1/0")
    for _ in range(3):
        assert (await client.post("/v1/ask", json={"question": "Why?"})).status_code == 200
