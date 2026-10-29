from fastapi import APIRouter, HTTPException, status

from app.analytics.text_to_sql import MetricsFilters, TextToSQL, TextToSQLError
from app.api.deps import AnalyticsEngineDep, LLMDep, SettingsDep
from app.schemas.metrics import MetricsRequest, MetricsResponse, MetricsUsage

router = APIRouter(prefix="/metrics", tags=["metrics"])


@router.post(
    "",
    response_model=MetricsResponse,
    responses={422: {"description": "No valid query could be generated"}},
)
async def query_metrics(
    body: MetricsRequest, llm: LLMDep, analytics: AnalyticsEngineDep, settings: SettingsDep
) -> MetricsResponse:
    """Answer a quantitative question with a generated, guarded, read-only SQL query."""
    engine, set_role = analytics
    text_to_sql = TextToSQL(
        llm,
        engine,
        max_rows=settings.sql_max_rows,
        timeout_ms=settings.sql_timeout_ms,
        set_role=set_role,
    )
    filters = MetricsFilters(industry=body.industry, start=body.start, end=body.end)
    try:
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
        usage=MetricsUsage(
            input_tokens=answer.usage.input_tokens, output_tokens=answer.usage.output_tokens
        ),
    )
