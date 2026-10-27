from datetime import date

from pydantic import BaseModel, ConfigDict, Field

MAX_CUSTOMERS_PER_REQUEST = 5_000


class CustomerIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=64)
    industry: str = Field(min_length=1, max_length=32)
    plan_code: str | None = Field(default=None, max_length=64)
    signup_at: date | None = None
    churned_at: date | None = None
    churn_reason: str | None = Field(default=None, max_length=128)


class CustomersUpsertRequest(BaseModel):
    customers: list[CustomerIn] = Field(min_length=1, max_length=MAX_CUSTOMERS_PER_REQUEST)


class CustomersUpsertResponse(BaseModel):
    upserted: int
