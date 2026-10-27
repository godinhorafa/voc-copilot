from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from synthetic.generator import DEFAULT_SCENARIOS
from synthetic.schema import IndustryConfig, Scenarios


def _telecom_raw() -> dict[str, Any]:
    raw: dict[str, Any] = yaml.safe_load(DEFAULT_SCENARIOS.read_text())["industries"]["telecom"]
    return raw


def test_shipped_scenarios_are_valid(scenarios: Scenarios) -> None:
    assert set(scenarios.industries) == {"telecom", "banking", "ecommerce"}


def test_rejects_sentiment_not_summing_to_one() -> None:
    raw = _telecom_raw()
    raw["reasons"][0]["sentiment"] = [0.5, 0.5, 0.5]
    with pytest.raises(ValidationError, match="sum to 1"):
        IndustryConfig.model_validate(raw)


def test_rejects_event_with_unknown_reason() -> None:
    raw = _telecom_raw()
    raw["events"][0]["reason_multipliers"] = {"does_not_exist": 2.0}
    with pytest.raises(ValidationError, match="unknown reasons"):
        IndustryConfig.model_validate(raw)


def test_rejects_unknown_cancellation_reason() -> None:
    raw = _telecom_raw()
    raw["cancellation_reason"] = "nope"
    with pytest.raises(ValidationError, match="cancellation_reason"):
        IndustryConfig.model_validate(raw)


def test_rejects_event_ending_before_start() -> None:
    raw = _telecom_raw()
    raw["events"][0]["end"] = "2026-08-01"
    with pytest.raises(ValidationError, match="ends before"):
        IndustryConfig.model_validate(raw)


def test_rejects_unknown_fields() -> None:
    raw = _telecom_raw()
    raw["retention_rat"] = 0.5
    with pytest.raises(ValidationError, match="retention_rat"):
        IndustryConfig.model_validate(raw)
