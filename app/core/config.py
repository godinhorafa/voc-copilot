from functools import lru_cache
from typing import Literal, Self

from pydantic import Field, PostgresDsn, RedisDsn, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LLMProviderName = Literal["openai", "azure_openai", "ollama", "fake"]


class Settings(BaseSettings):
    # Empty values in .env (e.g. `OPENAI_API_KEY=`) count as unset.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_ignore_empty=True)

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
    llm_timeout_s: float = Field(default=60.0, gt=0)
    llm_max_retries: int = Field(default=3, ge=0)
    # Defaults to llm_provider; set it to mix, e.g. Ollama chat + OpenAI embeddings.
    embedding_provider: LLMProviderName | None = None
    embedding_model: str = "text-embedding-3-small"
    embedding_dim: int = Field(default=1536, gt=0)
    embedding_batch_size: int = Field(default=64, gt=0)

    # See ADR 0003 and the README results table. Hybrid is the default because
    # keyword-only search returns nothing when a question shares no terms with
    # the conversations, even though it scores higher on the lexical golden set.
    retrieval_strategy: Literal["vector", "keyword", "hybrid"] = "hybrid"
    retrieval_rerank: bool = False
    retrieval_candidates: int = Field(default=30, gt=0)

    # Agent loop budget: tool-calling turns before only final_answer is offered.
    agent_max_steps: int = Field(default=6, ge=1, le=20)
    # LLM-as-judge for evals; a different model than LLM_MODEL limits self-preference.
    judge_model: str = "gpt-4o"

    # Text-to-SQL. In production, connect as a login role that is only a member of
    # voc_analytics (see ADR 0007); unset, the main connection switches role per query.
    analytics_database_url: PostgresDsn | None = None
    # Limits (the database enforces the timeout).
    sql_max_rows: int = Field(default=200, gt=0, le=10_000)
    sql_timeout_ms: int = Field(default=5000, gt=0)

    chunk_max_words: int = Field(default=160, gt=0)
    chunk_overlap_turns: int = Field(default=1, ge=0)
    ingestion_max_tries: int = Field(default=4, ge=1)
    ingestion_retry_base_s: float = Field(default=5.0, ge=0)

    openai_api_key: SecretStr | None = None
    azure_openai_endpoint: str | None = None
    azure_openai_api_key: SecretStr | None = None
    azure_openai_api_version: str | None = None
    ollama_base_url: str = "http://localhost:11434"

    @model_validator(mode="after")
    def _check_provider_credentials(self) -> Self:
        # Fail at startup, not on the first request that happens to call the LLM.
        providers = {self.llm_provider, self.embedding_provider or self.llm_provider}
        if "openai" in providers and not self.openai_api_key:
            raise ValueError("OPENAI_API_KEY is required when using the openai provider")
        if "azure_openai" in providers:
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
                raise ValueError(f"Missing settings for the azure_openai provider: {missing}")
        if self.environment == "production" and self.api_key.get_secret_value() == "change-me":
            raise ValueError("API_KEY must be changed in production")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
