import time

from fastapi import APIRouter, Depends, HTTPException, status

from app.analytics.text_to_sql import MetricsFilters, TextToSQL, TextToSQLError
from app.api.auth import PrincipalDep, require_role
from app.api.deps import AnalyticsEngineDep, LLMDep, PricesDep, SettingsDep
from app.core.rate_limit import rate_limit
from app.db.models import Role
from app.observability.tracing import current_trace_id
from app.observability.usage import metered
from app.schemas.metrics import MetricsRequest, MetricsResponse
from app.schemas.usage import usage_out

router = APIRouter(
    prefix="/metrics",
    tags=["metrics"],
    dependencies=[
        Depends(require_role(Role.ANALYST)),
        Depends(rate_limit("metrics", lambda s: s.rate_limit_metrics)),
    ],
)


@router.post(
    "",
    response_model=MetricsResponse,
    responses={422: {"description": "No valid query could be generated"}},
)
async def query_metrics(
    body: MetricsRequest,
    llm: LLMDep,
    analytics: AnalyticsEngineDep,
    settings: SettingsDep,
    prices: PricesDep,
    principal: PrincipalDep,
) -> MetricsResponse:
    """Answer a quantitative question with a generated, guarded, read-only SQL query."""
    engine, set_role = analytics
    text_to_sql = TextToSQL(
        llm,
        engine,
        max_rows=settings.sql_max_rows,
        timeout_ms=settings.sql_timeout_ms,
        set_role=set_role,
        tenant_id=principal.tenant_id,
    )
    filters = MetricsFilters(industry=body.industry, start=body.start, end=body.end)
    started = time.perf_counter()
    try:
        with metered() as meter:
            answer = await text_to_sql.answer(body.question, filters)
    except TextToSQLError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, {"message": str(exc), "errors": exc.errors}
        ) from exc
    return MetricsResponse(
        sql=answer.sql,
        explanation=answer.explanation,
        columns=answer.result.columns,
        rows=answer.result.rows,
        truncated=answer.result.truncated,
        attempts=answer.attempts,
        usage=usage_out(meter, prices, round((time.perf_counter() - started) * 1000)),
        trace_id=current_trace_id(),
    )
