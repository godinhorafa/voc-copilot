"""Structured insight extraction.

The output schema is built per industry so that `contact_reason` is an enum of
that industry's taxonomy: with strict structured output the model cannot invent
a reason, and every insight aggregates cleanly. The extractor version combines
a manual number with a hash of the prompt and taxonomy, so any change to either
is visible on the stored insights.
"""

import hashlib
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, create_model

from app.llm.base import LLMProvider, Message, Usage

PROMPT_PATH = Path(__file__).parent / "prompts" / "extract_insights.md"
TAXONOMY_PATH = Path(__file__).parent / "taxonomy.yaml"
EXTRACTOR_MAJOR = 1

Sentiment = Literal["negative", "neutral", "positive"]
ChurnRisk = Literal["low", "medium", "high"]


class InsightFields(BaseModel):
    """Industry-independent view of an extraction result."""

    contact_reason: str
    sentiment: Sentiment
    resolved: bool
    churn_risk: ChurnRisk
    competitors: list[str]
    summary: str


@dataclass(frozen=True)
class IndustryTaxonomy:
    reasons: dict[str, str]
    cancellation_reason: str


@cache
def load_taxonomy() -> dict[str, IndustryTaxonomy]:
    raw = yaml.safe_load(TAXONOMY_PATH.read_text())["industries"]
    return {
        name: IndustryTaxonomy(
            reasons=dict(cfg["reasons"]), cancellation_reason=cfg["cancellation_reason"]
        )
        for name, cfg in raw.items()
    }


@cache
def extractor_version() -> str:
    digest = hashlib.sha256(PROMPT_PATH.read_bytes() + TAXONOMY_PATH.read_bytes()).hexdigest()
    return f"{EXTRACTOR_MAJOR}.{digest[:8]}"


@cache
def output_schema(industry: str) -> type[BaseModel]:
    taxonomy = load_taxonomy().get(industry)
    reason_type: Any = Literal[tuple(taxonomy.reasons)] if taxonomy else str
    model: type[BaseModel] = create_model(
        f"Insight_{industry}",
        contact_reason=(reason_type, Field(description="Main contact reason id")),
        sentiment=(Sentiment, Field(description="Customer sentiment at the end")),
        resolved=(bool, Field(description="Issue solved in this contact")),
        churn_risk=(ChurnRisk, Field(description="Risk that the customer leaves")),
        competitors=(list[str], Field(description="Competitor names the customer mentions")),
        summary=(str, Field(description="One sentence for an analyst")),
    )
    return model


def _system_prompt(industry: str) -> str:
    taxonomy = load_taxonomy().get(industry)
    if taxonomy:
        reasons = "\n".join(f"- `{rid}`: {desc}" for rid, desc in taxonomy.reasons.items())
        cancellation = f"`{taxonomy.cancellation_reason}`"
    else:
        reasons = "- (no taxonomy for this industry: use a short snake_case label)"
        cancellation = "cancel"
    return PROMPT_PATH.read_text().format(
        industry=industry,
        reasons=reasons,
        cancellation_label=cancellation,
        brand_hint="the company the agent works for",
    )


def format_transcript(turns: list[tuple[str, str]]) -> str:
    return "\n".join(f"{speaker}: {text}" for speaker, text in turns)


@dataclass(frozen=True)
class Extraction:
    fields: InsightFields
    model: str
    usage: Usage
    version: str


class InsightExtractor:
    def __init__(self, llm: LLMProvider) -> None:
        self._llm = llm

    def build_messages(
        self, industry: str, channel: str, turns: list[tuple[str, str]]
    ) -> list[Message]:
        return [
            Message(role="system", content=_system_prompt(industry)),
            Message(
                role="user",
                content=f"Channel: {channel}\n\nConversation:\n{format_transcript(turns)}",
            ),
        ]

    async def extract(
        self, industry: str, channel: str, turns: list[tuple[str, str]]
    ) -> Extraction:
        result = await self._llm.complete_structured(
            self.build_messages(industry, channel, turns), output_schema(industry)
        )
        fields = InsightFields.model_validate(result.value.model_dump())
        # Normalize competitor names: trimmed, deduplicated case-insensitively, order kept.
        seen: set[str] = set()
        competitors = []
        for name in (c.strip() for c in fields.competitors):
            if name and name.lower() not in seen:
                seen.add(name.lower())
                competitors.append(name)
        fields = fields.model_copy(update={"competitors": competitors})
        return Extraction(
            fields=fields, model=result.model, usage=result.usage, version=extractor_version()
        )
