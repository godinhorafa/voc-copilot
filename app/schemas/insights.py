from datetime import date

from pydantic import BaseModel, Field


class InsightTotals(BaseModel):
    conversations: int
    with_insights: int = Field(description="Conversations whose extraction has run")
    negative_share: float | None
    unresolved_share: float | None
    high_churn_risk_share: float | None
    churned_customers: int = Field(description="Customers whose churn date is in the window")


class MonthPoint(BaseModel):
    month: date
    conversations: int
    negative: int
    unresolved: int
    churned_customers: int


class ReasonRow(BaseModel):
    contact_reason: str
    conversations: int
    negative_share: float
    unresolved_share: float


class CompetitorRow(BaseModel):
    competitor: str
    mentions: int


class InsightsResponse(BaseModel):
    industries: list[str] = Field(description="Industries with data, for filters")
    totals: InsightTotals
    by_month: list[MonthPoint]
    by_reason: list[ReasonRow]
    competitors: list[CompetitorRow]
