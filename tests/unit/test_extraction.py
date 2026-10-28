import json
import re

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.ingestion.extraction import (
    InsightExtractor,
    extractor_version,
    load_taxonomy,
    output_schema,
)
from app.llm.factory import build_embedding_provider, build_llm_provider
from app.llm.fake import FakeEmbeddingProvider, FakeLLMProvider, placeholder_instance
from app.llm.openai_compatible import OpenAICompatibleEmbeddings, OpenAICompatibleLLM
from synthetic.generator import load_scenarios
from tests.conftest import make_settings

TURNS = [("agent", "Hello, how can I help?"), ("customer", "My bill went up, NovaTel is cheaper")]


def _answer(**overrides: object) -> str:
    base = {
        "contact_reason": "billing_dispute",
        "sentiment": "negative",
        "resolved": False,
        "churn_risk": "high",
        "competitors": ["NovaTel"],
        "summary": "Customer disputes a price increase and mentions a competitor.",
    }
    return json.dumps(base | overrides)


def test_taxonomy_matches_synthetic_scenarios() -> None:
    scenarios, _ = load_scenarios()
    taxonomy = load_taxonomy()
    assert set(taxonomy) == set(scenarios.industries)
    for industry, cfg in scenarios.industries.items():
        assert set(taxonomy[industry].reasons) == {r.id for r in cfg.reasons} | {"other"}
        assert taxonomy[industry].cancellation_reason == cfg.cancellation_reason


def test_schema_restricts_reason_to_industry_taxonomy() -> None:
    schema = output_schema("telecom")
    with pytest.raises(ValidationError):
        schema.model_validate_json(_answer(contact_reason="late_delivery"))
    assert schema.model_validate_json(_answer()).model_dump()["contact_reason"] == "billing_dispute"


def test_unknown_industry_accepts_free_reason() -> None:
    value = output_schema("aviation").model_validate_json(_answer(contact_reason="lost_bag"))
    assert value.model_dump()["contact_reason"] == "lost_bag"


def test_version_format_is_stable() -> None:
    assert re.fullmatch(r"1\.[0-9a-f]{8}", extractor_version())


def test_prompt_lists_reasons_and_cancellation_semantics() -> None:
    system, user = InsightExtractor(FakeLLMProvider()).build_messages("banking", "voice", TURNS)
    assert "`fee_dispute`: Questions or complaints about fees" in system.content
    assert "`account_closure`" in system.content
    assert "{" not in system.content
    assert user.content.startswith("Channel: voice")
    assert "customer: My bill went up" in user.content


async def test_extract_normalizes_competitors() -> None:
    llm = FakeLLMProvider([_answer(competitors=[" NovaTel ", "novatel", "SkyLink Mobile", ""])])
    result = await InsightExtractor(llm).extract("telecom", "chat", TURNS)
    assert result.fields.competitors == ["NovaTel", "SkyLink Mobile"]
    assert result.fields.contact_reason == "billing_dispute"
    assert result.version == extractor_version()
    assert result.usage.input_tokens > 0


def test_placeholder_instance_is_schema_valid() -> None:
    value = placeholder_instance(output_schema("telecom")).model_dump()
    assert value["contact_reason"] in load_taxonomy()["telecom"].reasons
    assert value["competitors"] == []


def _settings(**kw: object) -> Settings:
    return make_settings(**kw)


def test_factory_selects_providers() -> None:
    assert isinstance(build_llm_provider(_settings()), FakeLLMProvider)
    assert isinstance(build_embedding_provider(_settings()), FakeEmbeddingProvider)

    openai = _settings(llm_provider="openai", openai_api_key="sk-x")
    assert isinstance(build_llm_provider(openai), OpenAICompatibleLLM)
    assert isinstance(build_embedding_provider(openai), OpenAICompatibleEmbeddings)

    mixed = _settings(llm_provider="ollama", embedding_provider="fake")
    assert isinstance(build_llm_provider(mixed), OpenAICompatibleLLM)
    assert isinstance(build_embedding_provider(mixed), FakeEmbeddingProvider)


def test_embedding_provider_needs_its_own_credentials() -> None:
    with pytest.raises(ValidationError, match="OPENAI_API_KEY"):
        _settings(llm_provider="ollama", embedding_provider="openai")
