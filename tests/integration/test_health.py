import pytest
from httpx import ASGITransport, AsyncClient

from app.main import create_app
from tests.conftest import make_settings

pytestmark = pytest.mark.integration


async def test_ready_when_dependencies_are_up(client: AsyncClient) -> None:
    response = await client.get("/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "checks": {"postgres": "ok", "redis": "ok"}}


async def test_not_ready_when_redis_is_down(migrated_db: str) -> None:
    settings = make_settings(database_url=migrated_db, redis_url="redis://127.0.0.1:1/0")
    app = create_app(settings)
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        response = await client.get("/ready")
    assert response.status_code == 503
    assert response.json()["checks"] == {"postgres": "ok", "redis": "error"}
