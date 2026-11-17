"""GET /v1/insights: aggregates for the dashboard.

Fixed, parameterized SQL written here (not model-generated). Shares are over
conversations whose extraction has run, so a backlog doesn't dilute them.
"""

from datetime import date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text

from app.api.auth import SessionDep, require_role
from app.db.models import Role
from app.schemas.insights import (
    CompetitorRow,
    InsightsResponse,
    InsightTotals,
    MonthPoint,
    ReasonRow,
)

router = APIRouter(
    prefix="/insights", tags=["insights"], dependencies=[Depends(require_role(Role.ANALYST))]
)

# Shared WHERE clause; every query binds :industry, :start and :end (NULL = no filter).
# Queries mark where it goes with __SCOPE__; values are always bound, never interpolated.
_SCOPE = """
    (CAST(:industry AS text) IS NULL OR c.industry = :industry)
    AND (CAST(:start AS timestamptz) IS NULL OR c.started_at >= :start)
    AND (CAST(:end AS timestamptz) IS NULL OR c.started_at < :end)
"""

_TOTALS = """
    SELECT count(*) AS conversations,
           count(i.conversation_id) AS with_insights,
           avg((i.sentiment = 'negative')::int) AS negative_share,
           avg((NOT i.resolved)::int) AS unresolved_share,
           avg((i.churn_risk = 'high')::int) AS high_churn_risk_share
    FROM conversations c LEFT JOIN insights i ON i.conversation_id = c.id
    WHERE __SCOPE__
"""

_CHURNED = """
    SELECT count(*) FROM customers c
    WHERE c.churned_at IS NOT NULL
      AND (CAST(:industry AS text) IS NULL OR c.industry = :industry)
      AND (CAST(:start AS timestamptz) IS NULL OR c.churned_at >= CAST(:start AS timestamptz))
      AND (CAST(:end AS timestamptz) IS NULL OR c.churned_at < CAST(:end AS timestamptz))
"""

_BY_MONTH = """
    WITH conv AS (
        SELECT date_trunc('month', c.started_at)::date AS month,
               count(*) AS conversations,
               count(*) FILTER (WHERE i.sentiment = 'negative') AS negative,
               count(*) FILTER (WHERE NOT i.resolved) AS unresolved
        FROM conversations c LEFT JOIN insights i ON i.conversation_id = c.id
        WHERE __SCOPE__
        GROUP BY 1
    ), churn AS (
        SELECT date_trunc('month', c.churned_at)::date AS month, count(*) AS churned
        FROM customers c
        WHERE c.churned_at IS NOT NULL
          AND (CAST(:industry AS text) IS NULL OR c.industry = :industry)
        GROUP BY 1
    )
    SELECT conv.*, coalesce(churn.churned, 0) AS churned_customers
    FROM conv LEFT JOIN churn USING (month)
    ORDER BY month
"""

_BY_REASON = """
    SELECT i.contact_reason,
           count(*) AS conversations,
           avg((i.sentiment = 'negative')::int) AS negative_share,
           avg((NOT i.resolved)::int) AS unresolved_share
    FROM conversations c JOIN insights i ON i.conversation_id = c.id
    WHERE __SCOPE__
    GROUP BY 1
    ORDER BY 2 DESC
"""

_COMPETITORS = """
    SELECT m.competitor, count(*) AS mentions
    FROM conversations c
    JOIN insights i ON i.conversation_id = c.id
    CROSS JOIN LATERAL unnest(i.competitors) AS m(competitor)
    WHERE __SCOPE__
    GROUP BY 1
    ORDER BY 2 DESC, 1
    LIMIT 10
"""


_TOTALS, _BY_MONTH, _BY_REASON, _COMPETITORS = (
    q.replace("__SCOPE__", _SCOPE) for q in (_TOTALS, _BY_MONTH, _BY_REASON, _COMPETITORS)
)


def _share(value: Any) -> float | None:
    return None if value is None else round(float(value), 4)


@router.get("", response_model=InsightsResponse)
async def get_insights(
    session: SessionDep,
    industry: Annotated[str | None, Query(max_length=32)] = None,
    start: Annotated[date | None, Query(description="Inclusive, on conversation start")] = None,
    end: Annotated[date | None, Query(description="Exclusive")] = None,
) -> InsightsResponse:
    """Totals, monthly trend, contact reasons and competitor mentions."""
    params = {
        "industry": industry,
        "start": datetime.combine(start, datetime.min.time()) if start else None,
        "end": datetime.combine(end, datetime.min.time()) if end else None,
    }
    industries = list(
        await session.scalars(text("SELECT DISTINCT industry FROM conversations ORDER BY 1"))
    )
    totals = (await session.execute(text(_TOTALS), params)).one()
    churned = await session.scalar(text(_CHURNED), params)
    months = (await session.execute(text(_BY_MONTH), params)).all()
    reasons = (await session.execute(text(_BY_REASON), params)).all()
    competitors = (await session.execute(text(_COMPETITORS), params)).all()

    return InsightsResponse(
        industries=industries,
        totals=InsightTotals(
            conversations=totals.conversations,
            with_insights=totals.with_insights,
            negative_share=_share(totals.negative_share),
            unresolved_share=_share(totals.unresolved_share),
            high_churn_risk_share=_share(totals.high_churn_risk_share),
            churned_customers=churned or 0,
        ),
        by_month=[
            MonthPoint(
                month=m.month,
                conversations=m.conversations,
                negative=m.negative,
                unresolved=m.unresolved,
                churned_customers=m.churned_customers,
            )
            for m in months
        ],
        by_reason=[
            ReasonRow(
                contact_reason=r.contact_reason,
                conversations=r.conversations,
                negative_share=_share(r.negative_share) or 0.0,
                unresolved_share=_share(r.unresolved_share) or 0.0,
            )
            for r in reasons
        ],
        competitors=[
            CompetitorRow(competitor=c.competitor, mentions=c.mentions) for c in competitors
        ],
    )
