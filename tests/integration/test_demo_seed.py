import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from scripts import demo_seed

pytestmark = pytest.mark.integration


async def test_demo_seed_uses_ground_truth_and_marks_it(
    client: AsyncClient, migrated_db: str, db: async_sessionmaker[AsyncSession]
) -> None:
    # `db` empties the tables after the test.
    processed = await demo_seed.seed(migrated_db, demo_seed.DATASET)
    assert processed == 500

    insights = (await client.get("/v1/insights")).json()
    reasons = {r["contact_reason"]: r["conversations"] for r in insights["by_reason"]}
    # Real label distribution, not one placeholder reason.
    assert len(reasons) >= 5
    assert insights["totals"]["churned_customers"] == 27

    conversation = (await client.get("/v1/conversations/conv_telecom_000239")).json()
    assert conversation["insight"]["model"] == demo_seed.ORACLE_MODEL
