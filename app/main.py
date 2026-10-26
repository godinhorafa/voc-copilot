from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from redis.asyncio import Redis

from app.api.v1 import health
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.core.middleware import RequestContextMiddleware
from app.db.session import create_engine, create_sessionmaker
from app.llm.factory import build_embedding_provider, build_llm_provider

logger = structlog.get_logger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, json=settings.log_json)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(settings)
        redis = Redis.from_url(str(settings.redis_url))
        app.state.settings = settings
        app.state.engine = engine
        app.state.sessionmaker = create_sessionmaker(engine)
        app.state.redis = redis
        app.state.llm = build_llm_provider(settings)
        app.state.embeddings = build_embedding_provider(settings)
        logger.info("startup", environment=settings.environment, llm=settings.llm_provider)
        try:
            yield
        finally:
            await redis.aclose()
            await engine.dispose()
            logger.info("shutdown")

    app = FastAPI(title="VOC Copilot", version="0.1.0", lifespan=lifespan)
    app.add_middleware(RequestContextMiddleware)
    app.include_router(health.router)
    return app
