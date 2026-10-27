"""Data models for the synthetic generator.

Two groups:

* Scenario config (`IndustryConfig` and friends) is loaded from `scenarios.yaml`
  and describes distributions: what customers contact about, how often, how it
  ends, and which dated events shift those distributions.
* Output records (`Customer`, `Conversation`, `GroundTruth`) are what the
  generator writes. `Conversation` is exactly what gets sent to the ingestion
  API; `GroundTruth` is kept apart so the system under test never sees labels.
"""

from datetime import date, datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

Channel = Literal["chat", "voice"]
Speaker = Literal["agent", "customer"]
Sentiment = Literal["negative", "neutral", "positive"]
ChurnRisk = Literal["low", "medium", "high"]

Probability = Field(ge=0.0, le=1.0)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# --------------------------------------------------------------------------- config


class Plan(_Strict):
    code: str
    name: str
    monthly_price: float = Field(gt=0)
    weight: float = Field(gt=0)


class ReasonTemplates(_Strict):
    """Phrase banks used by the template renderer. Slots: {plan}, {plan_code},
    {price}, {new_price}, {competitor}, {product}, {error_code}, {amount}."""

    openers: list[str] = Field(min_length=1)
    details: list[str] = Field(min_length=1)
    agent_probe: list[str] = Field(min_length=1)
    resolved: list[str] = Field(min_length=1)
    unresolved: list[str] = Field(min_length=1)


class ContactReason(_Strict):
    id: str
    description: str
    weight: float = Field(gt=0)
    resolution_rate: float = Probability
    # Base sentiment before resolution shifts it: P(negative), P(neutral), P(positive).
    sentiment: tuple[float, float, float]
    competitor_mention_rate: float = Probability
    templates: ReasonTemplates

    @model_validator(mode="after")
    def _sentiment_sums_to_one(self) -> Self:
        if abs(sum(self.sentiment) - 1.0) > 1e-6:
            raise ValueError(f"sentiment for {self.id!r} must sum to 1")
        return self


class Event(_Strict):
    """A dated incident that shifts contact volume, reasons, and churn."""

    id: str
    description: str
    start: date
    end: date
    volume_multiplier: float = Field(default=1.0, gt=0)
    reason_multipliers: dict[str, float] = Field(default_factory=dict)
    churn_multiplier: float = Field(default=1.0, gt=0)
    # Of the conversations whose reason the event affects, how many talk about it.
    mention_rate: float = Probability
    mention_phrases: list[str] = Field(min_length=1)
    slots: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _valid_window(self) -> Self:
        if self.end < self.start:
            raise ValueError(f"event {self.id!r} ends before it starts")
        return self

    def active_on(self, day: date) -> bool:
        return self.start <= day <= self.end


class IndustryConfig(_Strict):
    brand: str
    products: list[str] = Field(min_length=1)
    competitors: list[str] = Field(min_length=1)
    error_codes: list[str] = Field(min_length=1)
    plans: list[Plan] = Field(min_length=1)
    channels: dict[Channel, float]
    # Retention offer success when a customer asks to cancel.
    cancellation_reason: str
    retention_rate: float = Probability
    reasons: list[ContactReason] = Field(min_length=2)
    events: list[Event] = Field(default_factory=list)

    @model_validator(mode="after")
    def _references_resolve(self) -> Self:
        ids = {r.id for r in self.reasons}
        if len(ids) != len(self.reasons):
            raise ValueError("duplicate contact reason ids")
        if self.cancellation_reason not in ids:
            raise ValueError(f"cancellation_reason {self.cancellation_reason!r} is not a reason")
        for event in self.events:
            unknown = set(event.reason_multipliers) - ids
            if unknown:
                raise ValueError(f"event {event.id!r} references unknown reasons {unknown}")
        return self

    def reason(self, reason_id: str) -> ContactReason:
        return next(r for r in self.reasons if r.id == reason_id)


class CommonTemplates(_Strict):
    agent_greetings: list[str] = Field(min_length=1)
    agent_closings: list[str] = Field(min_length=1)
    customer_closings: dict[Sentiment, list[str]]
    competitor_lines: list[str] = Field(min_length=1)
    voice_fillers: list[str] = Field(min_length=1)


class Scenarios(_Strict):
    common: CommonTemplates
    industries: dict[str, IndustryConfig]


# --------------------------------------------------------------------------- output


class Turn(_Strict):
    speaker: Speaker
    text: str = Field(min_length=1)
    offset_s: int = Field(ge=0)


class Customer(_Strict):
    id: str
    industry: str
    plan_code: str
    signup_at: date
    churned_at: date | None = None
    churn_reason: str | None = None


class Conversation(_Strict):
    """Ingestion payload. Contains nothing the system is supposed to infer."""

    external_id: str
    customer_id: str
    industry: str
    channel: Channel
    started_at: datetime
    turns: list[Turn] = Field(min_length=2)


class GroundTruth(_Strict):
    """Labels decided before the text was written; the eval gold standard."""

    external_id: str
    contact_reason: str
    sentiment: Sentiment
    resolved: bool
    churn_risk: ChurnRisk
    churned: bool
    competitors: list[str]
    event_id: str | None = None


class Manifest(_Strict):
    industry: str
    seed: int
    start: date
    end: date
    n_conversations: int
    n_customers: int
    renderer: str
    scenario_sha256: str
    generator_version: str
