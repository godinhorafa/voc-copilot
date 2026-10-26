from httpx import ASGITransport, AsyncClient

from app.main import create_app
from tests.conftest import make_settings


async def _client() -> AsyncClient:
    # /health has no dependencies, so the lifespan (DB, Redis) is not needed.
    app = create_app(make_settings())
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_generates_request_id() -> None:
    async with await _client() as client:
        response = await client.get("/health")
    assert response.status_code == 200
    assert len(response.headers["x-request-id"]) == 32


async def test_propagates_incoming_request_id() -> None:
    async with await _client() as client:
        response = await client.get("/health", headers={"X-Request-ID": "abc-123"})
    assert response.headers["x-request-id"] == "abc-123"
