from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.llm.base import EmbeddingProvider, LLMProvider
from app.observability.usage import DEFAULT_PRICES, PriceTable, load_prices
from app.workers.queue import JobQueue


def get_app_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_engine(request: Request) -> AsyncEngine:
    engine: AsyncEngine = request.app.state.engine
    return engine


def get_analytics_engine(request: Request) -> tuple[AsyncEngine, bool]:
    """Engine for text-to-SQL and whether it must switch role per query."""
    dedicated: AsyncEngine | None = request.app.state.analytics_engine
    if dedicated is not None:
        return dedicated, False
    engine: AsyncEngine = request.app.state.engine
    return engine, True


def get_redis(request: Request) -> Redis:
    redis: Redis = request.app.state.redis
    return redis


def get_sessionmaker(request: Request) -> async_sessionmaker[AsyncSession]:
    sessionmaker: async_sessionmaker[AsyncSession] = request.app.state.sessionmaker
    return sessionmaker


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.sessionmaker() as session:
        yield session


def get_llm(request: Request) -> LLMProvider:
    llm: LLMProvider = request.app.state.llm
    return llm


def get_embeddings(request: Request) -> EmbeddingProvider:
    embeddings: EmbeddingProvider = request.app.state.embeddings
    return embeddings


def get_prices(request: Request) -> PriceTable:
    settings: Settings = request.app.state.settings
    return load_prices(settings.prices_file or DEFAULT_PRICES)


SettingsDep = Annotated[Settings, Depends(get_app_settings)]
PricesDep = Annotated[PriceTable, Depends(get_prices)]
EngineDep = Annotated[AsyncEngine, Depends(get_engine)]
AnalyticsEngineDep = Annotated[tuple[AsyncEngine, bool], Depends(get_analytics_engine)]
RedisDep = Annotated[Redis, Depends(get_redis)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]
SessionmakerDep = Annotated[async_sessionmaker[AsyncSession], Depends(get_sessionmaker)]
LLMDep = Annotated[LLMProvider, Depends(get_llm)]
EmbeddingsDep = Annotated[EmbeddingProvider, Depends(get_embeddings)]


def get_queue(request: Request) -> JobQueue:
    queue: JobQueue = request.app.state.queue
    return queue


QueueDep = Annotated[JobQueue, Depends(get_queue)]
