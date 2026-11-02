from pydantic import BaseModel, Field

from app.observability.usage import PriceTable, UsageMeter


class ModelUsageOut(BaseModel):
    provider: str
    model: str
    kind: str = Field(description="chat | embedding")
    calls: int
    input_tokens: int
    output_tokens: int
    cost_usd: float | None = Field(description="Null when the model has no known price")


class UsageOut(BaseModel):
    input_tokens: int
    output_tokens: int
    latency_ms: int
    cost_usd: float | None = Field(
        description="Estimated from the price table; null if any model used is unpriced"
    )
    by_model: list[ModelUsageOut]


def usage_out(meter: UsageMeter, prices: PriceTable, latency_ms: int) -> UsageOut:
    rows, total = meter.priced(prices)
    return UsageOut(
        input_tokens=meter.input_tokens,
        output_tokens=meter.output_tokens,
        latency_ms=latency_ms,
        cost_usd=total,
        by_model=[ModelUsageOut(**vars(r)) for r in rows],
    )
