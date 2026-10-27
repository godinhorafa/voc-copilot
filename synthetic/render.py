"""Renderers turn a `ConversationSpec` (labels) into conversation turns (text).

* `TemplateRenderer` assembles turns from the phrase banks in `scenarios.yaml`.
  Offline, fast, deterministic; used for tests and the committed fixture.
* `LLMRenderer` asks an `LLMProvider` for a natural conversation that matches
  the labels, then validates the output and retries with feedback.

Both run the same `check_consistency` so that text never contradicts labels
(the most common failure: an LLM inventing a competitor the labels don't have,
which would silently corrupt the eval gold standard).
"""

import random
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, Field

from app.llm.base import LLMProvider, Message
from synthetic.schema import CommonTemplates, Speaker, Turn
from synthetic.simulation import ConversationSpec

PROMPT_PATH = Path(__file__).parent / "prompts" / "conversation.md"
AGENT_NAMES = ("Ana", "Bruno", "Carla", "Diego", "Elena", "Felipe", "Grace", "Hugo")


class RenderError(Exception):
    pass


class Renderer(Protocol):
    name: str

    async def render(self, spec: ConversationSpec) -> list[Turn]: ...


def _spec_rng(spec: ConversationSpec, salt: str) -> random.Random:
    # Per-conversation RNG: output doesn't depend on rendering order or concurrency.
    return random.Random(f"{salt}:{spec.external_id}")


def _with_offsets(
    rng: random.Random, spec: ConversationSpec, turns: list[tuple[Speaker, str]]
) -> list[Turn]:
    lo, hi = (3, 20) if spec.channel == "voice" else (5, 45)
    offset, out = 0, []
    for speaker, text in turns:
        out.append(Turn(speaker=speaker, text=text, offset_s=offset))
        offset += rng.randint(lo, hi)
    return out


def check_consistency(
    spec: ConversationSpec, turns: list[Turn], all_competitors: list[str]
) -> list[str]:
    """Return the ways `turns` contradict the labels in `spec` (empty if none)."""
    problems: list[str] = []
    full_text = "\n".join(t.text for t in turns).lower()
    speakers = {t.speaker for t in turns}
    if speakers != {"agent", "customer"}:
        problems.append("conversation must contain both agent and customer turns")
    for competitor in spec.competitors:
        if competitor.lower() not in full_text:
            problems.append(f"labeled competitor {competitor!r} is never mentioned")
    for competitor in set(all_competitors) - set(spec.competitors):
        if competitor.lower() in full_text:
            problems.append(f"competitor {competitor!r} is mentioned but not labeled")
    return problems


class TemplateRenderer:
    name = "template"

    def __init__(self, common: CommonTemplates, competitors: list[str]) -> None:
        self._common = common
        self._competitors = competitors

    async def render(self, spec: ConversationSpec) -> list[Turn]:
        rng = _spec_rng(spec, self.name)
        tpl = spec.reason.templates
        slots = {**spec.slots, "agent": rng.choice(AGENT_NAMES)}

        def fill(text: str, **extra: str) -> str:
            return text.format_map({**slots, **extra})

        def customer(text: str) -> tuple[Speaker, str]:
            if spec.channel == "voice" and rng.random() < 0.35:
                text = rng.choice(self._common.voice_fillers) + text[0].lower() + text[1:]
            return ("customer", text)

        opener = fill(rng.choice(tpl.openers))
        if spec.event:
            opener = f"{opener} {fill(rng.choice(spec.event.mention_phrases))}"

        details = [fill(rng.choice(tpl.details))]
        details += [
            fill(rng.choice(self._common.competitor_lines), competitor=c) for c in spec.competitors
        ]

        outcome = tpl.resolved if spec.resolved else tpl.unresolved
        turns: list[tuple[Speaker, str]] = [
            ("agent", fill(rng.choice(self._common.agent_greetings))),
            customer(opener),
            ("agent", fill(rng.choice(tpl.agent_probe))),
            customer(" ".join(details)),
            ("agent", fill(rng.choice(outcome))),
            ("agent", rng.choice(self._common.agent_closings)),
            customer(rng.choice(self._common.customer_closings[spec.sentiment])),
        ]
        rendered = _with_offsets(rng, spec, turns)
        if problems := check_consistency(spec, rendered, self._competitors):
            raise RenderError(f"{spec.external_id}: {problems}")
        return rendered


class _RenderedTurn(BaseModel):
    speaker: Speaker
    text: str = Field(min_length=1)


class RenderedConversation(BaseModel):
    turns: list[_RenderedTurn] = Field(min_length=4, max_length=24)


class LLMRenderer:
    name = "llm"

    def __init__(
        self,
        llm: LLMProvider,
        competitors: list[str],
        *,
        max_attempts: int = 3,
        temperature: float = 0.9,
    ) -> None:
        self._llm = llm
        self._competitors = competitors
        self._max_attempts = max_attempts
        self._temperature = temperature
        self._prompt = PROMPT_PATH.read_text()

    def build_messages(self, spec: ConversationSpec) -> list[Message]:
        event = (
            f"{spec.event.description}. Example of how a customer might put it: "
            f'"{spec.event.mention_phrases[0].format_map(spec.slots)}"'
            if spec.event
            else "none"
        )
        if spec.cancellation:
            outcome = (
                "the customer is retained by a retention offer"
                if spec.resolved
                else "the cancellation goes through"
            )
        else:
            outcome = "resolved" if spec.resolved else "not resolved"
        facts = {
            "brand": spec.brand,
            "industry": spec.industry,
            "channel": spec.channel,
            "plan": f"{spec.plan.name} ({spec.plan.code}), {spec.slots['price']}/month",
            "reason": f"{spec.reason.id}: {spec.reason.description}",
            "outcome": outcome,
            "sentiment": spec.sentiment,
            "competitors": ", ".join(spec.competitors) or "none",
            "forbidden_competitors": ", ".join(
                sorted(set(self._competitors) - set(spec.competitors))
            ),
            "event": event,
            "product": spec.slots["product"],
            "error_code": spec.slots["error_code"],
            "amount": spec.slots["amount"],
        }
        return [
            Message(role="system", content=self._prompt),
            Message(
                role="user",
                content="\n".join(f"- {k}: {v}" for k, v in facts.items()),
            ),
        ]

    async def render(self, spec: ConversationSpec) -> list[Turn]:
        messages = self.build_messages(spec)
        problems: list[str] = []
        for _ in range(self._max_attempts):
            result = await self._llm.complete_structured(
                messages, RenderedConversation, temperature=self._temperature
            )
            turns = _with_offsets(
                _spec_rng(spec, self.name),
                spec,
                [(t.speaker, t.text.strip()) for t in result.value.turns],
            )
            problems = check_consistency(spec, turns, self._competitors)
            if not problems:
                return turns
            messages = [
                *messages,
                Message(role="assistant", content=result.value.model_dump_json()),
                Message(
                    role="user",
                    content="The conversation breaks these rules, rewrite it: "
                    + "; ".join(problems),
                ),
            ]
        raise RenderError(
            f"{spec.external_id}: still inconsistent after {self._max_attempts} attempts: "
            f"{problems}"
        )
