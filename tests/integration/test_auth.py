import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("headers", [{}, {"X-API-Key": "wrong"}])
async def test_v1_requires_api_key(client: AsyncClient, headers: dict[str, str]) -> None:
    client.headers.pop("X-API-Key")
    response = await client.get("/v1/jobs/00000000-0000-0000-0000-000000000000", headers=headers)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "APIKey"


async def test_health_is_public(client: AsyncClient) -> None:
    client.headers.pop("X-API-Key")
    assert (await client.get("/health")).status_code == 200
