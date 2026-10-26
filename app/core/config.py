from functools import lru_cache
from typing import Literal, Self

from pydantic import Field, PostgresDsn, RedisDsn, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LLMProviderName = Literal["openai", "azure_openai", "ollama", "fake"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "voc-copilot"
    environment: Literal["local", "test", "production"] = "local"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_json: bool = True

    database_url: PostgresDsn = PostgresDsn("postgresql+asyncpg://voc:voc@localhost:5432/voc")
    db_pool_size: int = Field(default=5, ge=1)
    redis_url: RedisDsn = RedisDsn("redis://localhost:6379/0")

    api_key: SecretStr = SecretStr("change-me")

    llm_provider: LLMProviderName = "fake"
    llm_model: str = "gpt-4o-mini"
    embedding_model: str = "text-embedding-3-small"
    embedding_dim: int = Field(default=1536, gt=0)

    openai_api_key: SecretStr | None = None
    azure_openai_endpoint: str | None = None
    azure_openai_api_key: SecretStr | None = None
    azure_openai_api_version: str | None = None
    ollama_base_url: str = "http://localhost:11434"

    @model_validator(mode="after")
    def _check_provider_credentials(self) -> Self:
        # Fail at startup, not on the first request that happens to call the LLM.
        if self.llm_provider == "openai" and not self.openai_api_key:
            raise ValueError("OPENAI_API_KEY is required when LLM_PROVIDER=openai")
        if self.llm_provider == "azure_openai":
            missing = [
                name
                for name, value in {
                    "AZURE_OPENAI_ENDPOINT": self.azure_openai_endpoint,
                    "AZURE_OPENAI_API_KEY": self.azure_openai_api_key,
                    "AZURE_OPENAI_API_VERSION": self.azure_openai_api_version,
                }.items()
                if not value
            ]
            if missing:
                raise ValueError(f"Missing settings for LLM_PROVIDER=azure_openai: {missing}")
        if self.environment == "production" and self.api_key.get_secret_value() == "change-me":
            raise ValueError("API_KEY must be changed in production")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
