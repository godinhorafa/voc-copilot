import pytest
from pydantic import ValidationError

from tests.conftest import make_settings


def test_defaults_to_fake_provider() -> None:
    assert make_settings().llm_provider == "fake"


def test_openai_requires_api_key() -> None:
    with pytest.raises(ValidationError, match="OPENAI_API_KEY"):
        make_settings(llm_provider="openai")


def test_openai_with_key_is_valid() -> None:
    settings = make_settings(llm_provider="openai", openai_api_key="sk-test")
    assert settings.openai_api_key is not None
    assert "sk-test" not in repr(settings)


def test_azure_reports_all_missing_settings() -> None:
    with pytest.raises(ValidationError) as exc:
        make_settings(llm_provider="azure_openai", azure_openai_endpoint="https://x")
    message = str(exc.value)
    assert "AZURE_OPENAI_API_KEY" in message
    assert "AZURE_OPENAI_API_VERSION" in message
    assert "AZURE_OPENAI_ENDPOINT" not in message


def test_production_rejects_default_api_key() -> None:
    with pytest.raises(ValidationError, match="API_KEY"):
        make_settings(environment="production", api_key="change-me")
