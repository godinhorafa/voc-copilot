from app.core.config import Settings
from app.llm.base import EmbeddingProvider, LLMProvider
from app.llm.fake import FakeEmbeddingProvider, FakeLLMProvider


def build_llm_provider(settings: Settings) -> LLMProvider:
    match settings.llm_provider:
        case "fake":
            return FakeLLMProvider()
        case "openai" | "azure_openai" | "ollama":
            # Real providers land with the ingestion pipeline (phase 2).
            raise NotImplementedError(f"LLM provider {settings.llm_provider!r} not implemented yet")


def build_embedding_provider(settings: Settings) -> EmbeddingProvider:
    match settings.llm_provider:
        case "fake":
            return FakeEmbeddingProvider(dim=settings.embedding_dim)
        case "openai" | "azure_openai" | "ollama":
            raise NotImplementedError(
                f"Embedding provider {settings.llm_provider!r} not implemented yet"
            )
