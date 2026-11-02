from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from arq.connections import ArqRedis
from fastapi import FastAPI
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from redis.asyncio import ConnectionPool

from app.api import v1
from app.api.v1 import health
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.core.middleware import RequestContextMiddleware
from app.db.models import EMBEDDING_DIM
from app.db.session import create_engine, create_sessionmaker
from app.llm.factory import build_embedding_provider, build_llm_provider
from app.observability.tracing import setup_tracing
from app.workers.queue import ArqJobQueue

logger = structlog.get_logger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, json=settings.log_json)
    if settings.embedding_dim != EMBEDDING_DIM:
        raise ValueError(
            f"EMBEDDING_DIM={settings.embedding_dim} but the schema stores {EMBEDDING_DIM}-d "
            "vectors; changing it needs a migration"
        )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(settings)
        # No connection attempt here: the API starts (and reports not-ready) without Redis.
        redis = ArqRedis(ConnectionPool.from_url(str(settings.redis_url)))
        app.state.settings = settings
        app.state.engine = engine
        # Text-to-SQL uses a least-privilege connection when one is configured.
        analytics_engine = (
            create_engine(settings, url=str(settings.analytics_database_url))
            if settings.analytics_database_url
            else None
        )
        app.state.analytics_engine = analytics_engine
        app.state.sessionmaker = create_sessionmaker(engine)
        app.state.redis = redis
        app.state.queue = ArqJobQueue(redis)
        app.state.llm = build_llm_provider(settings)
        app.state.embeddings = build_embedding_provider(settings)
        logger.info("startup", environment=settings.environment, llm=settings.llm_provider)
        try:
            yield
        finally:
            await redis.aclose()
            if analytics_engine is not None:
                await analytics_engine.dispose()
            await engine.dispose()
            logger.info("shutdown")

    app = FastAPI(title="VOC Copilot", version="0.1.0", lifespan=lifespan)
    setup_tracing(settings.otel_service_name, otlp_endpoint=settings.otel_exporter_otlp_endpoint)
    # Added before the request-context middleware so request logs carry the trace id.
    FastAPIInstrumentor.instrument_app(app, excluded_urls="health,ready")
    app.add_middleware(RequestContextMiddleware)
    app.include_router(health.router)
    app.include_router(v1.router)
    return app
