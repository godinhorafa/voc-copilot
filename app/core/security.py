import secrets

from fastapi import HTTPException, Security, status
from fastapi.security import APIKeyHeader

from app.api.deps import SettingsDep

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def require_api_key(
    settings: SettingsDep, api_key: str | None = Security(api_key_header)
) -> None:
    expected = settings.api_key.get_secret_value().encode()
    # Constant-time comparison so response timing doesn't leak the key.
    if api_key is None or not secrets.compare_digest(api_key.encode(), expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
            headers={"WWW-Authenticate": "APIKey"},
        )
