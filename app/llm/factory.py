from openai import AsyncAzureOpenAI, AsyncOpenAI

from app.core.config import LLMProviderName, Settings
from app.llm.base import EmbeddingProvider, LLMProvider
from app.llm.fake import FakeEmbeddingProvider, FakeLLMProvider
from app.llm.openai_compatible import OpenAICompatibleEmbeddings, OpenAICompatibleLLM


def build_client(settings: Settings, provider: LLMProviderName) -> AsyncOpenAI:
    timeout, max_retries = settings.llm_timeout_s, settings.llm_max_retries
    match provider:
        case "openai":
            assert settings.openai_api_key is not None  # enforced by Settings
            return AsyncOpenAI(
                api_key=settings.openai_api_key.get_secret_value(),
                timeout=timeout,
                max_retries=max_retries,
            )
        case "azure_openai":
            assert settings.azure_openai_api_key is not None
            return AsyncAzureOpenAI(
                azure_endpoint=settings.azure_openai_endpoint or "",
                api_key=settings.azure_openai_api_key.get_secret_value(),
                api_version=settings.azure_openai_api_version,
                timeout=timeout,
                max_retries=max_retries,
            )
        case "ollama":
            # Ollama ignores the key, but the client requires one.
            return AsyncOpenAI(
                base_url=f"{settings.ollama_base_url.rstrip('/')}/v1",
                api_key="ollama",
                timeout=timeout,
                max_retries=max_retries,
            )
        case "fake":
            raise ValueError("the fake provider has no client")


def build_llm_provider(settings: Settings) -> LLMProvider:
    if settings.llm_provider == "fake":
        return FakeLLMProvider.permissive()
    return OpenAICompatibleLLM(
        build_client(settings, settings.llm_provider),
        settings.llm_model,
        provider=settings.llm_provider,
    )


def build_embedding_provider(settings: Settings) -> EmbeddingProvider:
    provider = settings.embedding_provider or settings.llm_provider
    if provider == "fake":
        return FakeEmbeddingProvider(dim=settings.embedding_dim)
    return OpenAICompatibleEmbeddings(
        build_client(settings, provider),
        settings.embedding_model,
        settings.embedding_dim,
        send_dimensions=settings.embedding_model.startswith("text-embedding-3"),
        provider=provider,
    )
