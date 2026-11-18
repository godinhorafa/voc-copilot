"""arq worker entrypoint: `arq app.workers.settings.WorkerSettings`."""

from typing import Any, ClassVar

from arq.connections import RedisSettings

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.session import create_engine
from app.llm.factory import build_embedding_provider, build_llm_provider
from app.observability.tracing import setup_tracing
from app.workers.tasks import process_ingestion_job

_settings = get_settings()


async def startup(ctx: dict[str, Any]) -> None:
    configure_logging(_settings.log_level, json=_settings.log_json)
    setup_tracing(
        f"{_settings.otel_service_name}-worker",
        otlp_endpoint=_settings.otel_exporter_otlp_endpoint,
    )
    engine = create_engine(_settings)
    ctx["settings"] = _settings
    ctx["engine"] = engine  # tasks open sessions scoped to their job's tenant
    ctx["embeddings"] = build_embedding_provider(_settings)
    ctx["llm"] = build_llm_provider(_settings)


async def shutdown(ctx: dict[str, Any]) -> None:
    await ctx["engine"].dispose()


class WorkerSettings:
    functions: ClassVar = [process_ingestion_job]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(str(_settings.redis_url))
    max_tries = _settings.ingestion_max_tries
    job_timeout = 600
    keep_result = 3600
