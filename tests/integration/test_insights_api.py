from collections.abc import Sequence
from typing import Any

import pytest
from httpx import AsyncClient
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.llm.base import Message
from app.llm.fake import FakeLLMProvider
from tests.factories import conversation
from tests.helpers import ingest_payloads

pytestmark = pytest.mark.integration

# conversation id -> (started_at, reason, sentiment, resolved, competitors)
DATA: dict[str, tuple[str, str, str, bool, list[str]]] = {
    "a": ("2026-08-10T10:00:00", "billing_dispute", "negative", False, ["NovaTel"]),
    "b": ("2026-09-02T10:00:00", "billing_dispute", "negative", True, []),
    "c": ("2026-09-15T10:00:00", "cancellation", "neutral", False, ["NovaTel", "Orbit Wireless"]),
    "d": ("2026-09-20T10:00:00", "technical_issue", "positive", True, []),
}


def _extractor() -> FakeLLMProvider:
    def answer(schema: type[BaseModel], messages: Sequence[Message]) -> BaseModel:
        conv_id = next(cid for cid in DATA if f"conversation {cid} " in messages[-1].content)
        _, reason, sentiment, resolved, competitors = DATA[conv_id]
        return schema.model_validate(
            {
                "contact_reason": reason,
                "sentiment": sentiment,
                "resolved": resolved,
                "churn_risk": "high" if reason == "cancellation" else "low",
                "competitors": competitors,
                "summary": "s",
            }
        )

    return FakeLLMProvider(structured=answer)


async def _seed(client: AsyncClient, db: async_sessionmaker[AsyncSession]) -> None:
    payloads = [
        conversation(cid, texts=("Hi", f"This is conversation {cid} speaking", "Ok"), started_at=ts)
        for cid, (ts, *_) in DATA.items()
    ]
    await ingest_payloads(db, payloads, llm=_extractor())
    await client.put(
        "/v1/customers",
        json={
            "customers": [
                {"id": "c1", "industry": "telecom", "churned_at": "2026-09-16"},
                {"id": "c2", "industry": "telecom", "churned_at": "2026-08-01"},
                {"id": "c3", "industry": "telecom"},
            ]
        },
    )


async def test_insights_aggregates(
    client: AsyncClient, db: async_sessionmaker[AsyncSession]
) -> None:
    await _seed(client, db)
    body: dict[str, Any] = (await client.get("/v1/insights")).json()

    assert body["industries"] == ["telecom"]
    assert body["totals"] == {
        "conversations": 4,
        "with_insights": 4,
        "negative_share": 0.5,
        "unresolved_share": 0.5,
        "high_churn_risk_share": 0.25,
        "churned_customers": 2,
    }
    assert [
        (m["month"], m["conversations"], m["negative"], m["churned_customers"])
        for m in body["by_month"]
    ] == [
        ("2026-08-01", 1, 1, 1),
        ("2026-09-01", 3, 1, 1),
    ]
    assert body["by_reason"][0] == {
        "contact_reason": "billing_dispute",
        "conversations": 2,
        "negative_share": 1.0,
        "unresolved_share": 0.5,
    }
    assert body["competitors"] == [
        {"competitor": "NovaTel", "mentions": 2},
        {"competitor": "Orbit Wireless", "mentions": 1},
    ]


async def test_insights_filters(client: AsyncClient, db: async_sessionmaker[AsyncSession]) -> None:
    await _seed(client, db)
    september = (
        await client.get("/v1/insights", params={"start": "2026-09-01", "end": "2026-10-01"})
    ).json()
    assert september["totals"]["conversations"] == 3
    assert september["totals"]["churned_customers"] == 1
    assert [m["month"] for m in september["by_month"]] == ["2026-09-01"]

    other = (await client.get("/v1/insights", params={"industry": "banking"})).json()
    assert other["totals"]["conversations"] == 0
    assert other["totals"]["negative_share"] is None
    assert other["by_reason"] == []


async def test_insights_requires_api_key(client: AsyncClient) -> None:
    client.headers.pop("X-API-Key")
    assert (await client.get("/v1/insights")).status_code == 401
