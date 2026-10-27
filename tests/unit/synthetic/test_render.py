import json
from dataclasses import replace
from datetime import date

import pytest

from app.llm.fake import FakeLLMProvider
from synthetic.render import LLMRenderer, RenderError, TemplateRenderer, check_consistency
from synthetic.schema import Scenarios, Turn
from synthetic.simulation import ConversationSpec, simulate


def _specs(scenarios: Scenarios, industry: str, n: int = 400) -> list[ConversationSpec]:
    return simulate(
        scenarios.industries[industry],
        industry=industry,
        n=n,
        start=date(2026, 6, 1),
        end=date(2026, 11, 30),
        seed=11,
    ).specs


def _template(scenarios: Scenarios, industry: str) -> TemplateRenderer:
    return TemplateRenderer(scenarios.common, scenarios.industries[industry].competitors)


def _text(turns: list[Turn]) -> str:
    return "\n".join(t.text for t in turns)


@pytest.mark.parametrize("industry", ["telecom", "banking", "ecommerce"])
async def test_template_output_matches_labels(scenarios: Scenarios, industry: str) -> None:
    renderer = _template(scenarios, industry)
    for spec in _specs(scenarios, industry):
        turns = await renderer.render(spec)
        text = _text(turns)
        assert "{" not in text, f"unfilled slot in {spec.external_id}: {text}"
        assert check_consistency(spec, turns, scenarios.industries[industry].competitors) == []
        assert spec.brand in turns[0].text
        assert [t.offset_s for t in turns] == sorted(t.offset_s for t in turns)


async def test_template_mentions_event_when_attributed(scenarios: Scenarios) -> None:
    renderer = _template(scenarios, "telecom")
    with_event = [s for s in _specs(scenarios, "telecom") if s.event]
    assert with_event
    for spec in with_event:
        assert spec.event is not None
        phrases = [p.format_map(spec.slots) for p in spec.event.mention_phrases]
        text = _text(await renderer.render(spec))
        assert any(p in text for p in phrases)


async def test_template_is_deterministic_per_conversation(scenarios: Scenarios) -> None:
    renderer = _template(scenarios, "telecom")
    specs = _specs(scenarios, "telecom", n=50)
    forward = [await renderer.render(s) for s in specs]
    backward = [await renderer.render(s) for s in reversed(specs)]
    assert forward == list(reversed(backward))


def test_consistency_flags_missing_and_unlabeled_competitors(scenarios: Scenarios) -> None:
    competitors = scenarios.industries["telecom"].competitors
    spec = replace(_specs(scenarios, "telecom", n=5)[0], competitors=("NovaTel",))
    turns = [
        Turn(speaker="agent", text="Hello", offset_s=0),
        Turn(speaker="customer", text="SkyLink Mobile is cheaper", offset_s=5),
    ]
    problems = check_consistency(spec, turns, competitors)
    assert any("'NovaTel' is never mentioned" in p for p in problems)
    assert any("'SkyLink Mobile' is mentioned but not labeled" in p for p in problems)


def test_consistency_requires_both_speakers(scenarios: Scenarios) -> None:
    spec = _specs(scenarios, "telecom", n=5)[0]
    turns = [
        Turn(speaker="agent", text="a", offset_s=0),
        Turn(speaker="agent", text="b", offset_s=1),
    ]
    assert check_consistency(spec, turns, []) == [
        "conversation must contain both agent and customer turns"
    ]


def _llm_json(*texts: str) -> str:
    speakers = ["agent", "customer"]
    return json.dumps(
        {"turns": [{"speaker": speakers[i % 2], "text": t} for i, t in enumerate(texts)]}
    )


def _spec_without_competitors(scenarios: Scenarios) -> ConversationSpec:
    return next(s for s in _specs(scenarios, "telecom") if not s.competitors)


async def test_llm_renderer_retries_with_feedback(scenarios: Scenarios) -> None:
    spec = _spec_without_competitors(scenarios)
    bad = _llm_json("Hi", "NovaTel is cheaper", "I see", "Bye")
    good = _llm_json("Hi", "My bill is wrong", "Fixed", "Thanks")
    llm = FakeLLMProvider([bad, good])
    renderer = LLMRenderer(llm, scenarios.industries["telecom"].competitors)

    turns = await renderer.render(spec)

    assert _text(turns) == "Hi\nMy bill is wrong\nFixed\nThanks"
    assert len(llm.calls) == 2
    feedback = llm.calls[1][-1].content
    assert "'NovaTel' is mentioned but not labeled" in feedback


async def test_llm_renderer_gives_up_after_max_attempts(scenarios: Scenarios) -> None:
    spec = _spec_without_competitors(scenarios)
    bad = _llm_json("Hi", "NovaTel is cheaper", "I see", "Bye")
    llm = FakeLLMProvider([bad] * 3)
    renderer = LLMRenderer(llm, scenarios.industries["telecom"].competitors, max_attempts=3)
    with pytest.raises(RenderError, match="after 3 attempts"):
        await renderer.render(spec)
    assert len(llm.calls) == 3


async def test_llm_prompt_carries_labels(scenarios: Scenarios) -> None:
    spec = next(s for s in _specs(scenarios, "telecom") if s.competitors and s.event)
    renderer = LLMRenderer(FakeLLMProvider(), scenarios.industries["telecom"].competitors)
    system, user = renderer.build_messages(spec)
    assert system.role == "system"
    assert "ground truth" in system.content
    assert f"reason: {spec.reason.id}" in user.content
    assert f"sentiment: {spec.sentiment}" in user.content
    assert spec.event is not None
    assert spec.event.description in user.content
    for competitor in spec.competitors:
        assert competitor in user.content.split("forbidden_competitors")[0]
