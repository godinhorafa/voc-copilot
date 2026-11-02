import asyncio
from pathlib import Path

import pytest

from app.observability.usage import (
    DEFAULT_PRICES,
    Price,
    PriceTable,
    UsageMeter,
    load_prices,
    metered,
    record,
)

PRICES = PriceTable(
    models={"gpt-4o": Price(2.5, 10.0), "gpt-4o-mini": Price(0.15, 0.6)},
    free_providers=frozenset({"ollama"}),
)


def test_longest_prefix_wins() -> None:
    assert PRICES.lookup("openai", "gpt-4o-mini-2024-07-18") == Price(0.15, 0.6)
    assert PRICES.lookup("openai", "gpt-4o-2024-08-06") == Price(2.5, 10.0)


def test_free_providers_and_unknown_models() -> None:
    assert PRICES.lookup("ollama", "llama3.1") == Price(0.0, 0.0)
    assert PRICES.lookup("openai", "o9-preview") is None


def test_meter_aggregates_and_prices() -> None:
    meter = UsageMeter()
    for _ in range(2):
        meter.record(
            provider="openai",
            model="gpt-4o-mini",
            kind="chat",
            input_tokens=1000,
            output_tokens=200,
        )
    meter.record(
        provider="ollama", model="llama3.1", kind="chat", input_tokens=500, output_tokens=50
    )

    rows, total = meter.priced(PRICES)

    assert (meter.input_tokens, meter.output_tokens) == (2500, 450)
    mini = next(r for r in rows if r.model == "gpt-4o-mini")
    assert mini.calls == 2
    assert mini.cost_usd == pytest.approx((2000 * 0.15 + 400 * 0.6) / 1e6)
    assert total == pytest.approx(mini.cost_usd)


def test_total_is_unknown_when_any_model_is_unpriced() -> None:
    meter = UsageMeter()
    meter.record(provider="openai", model="gpt-4o", kind="chat", input_tokens=10, output_tokens=10)
    meter.record(provider="openai", model="mystery", kind="chat", input_tokens=10, output_tokens=10)
    rows, total = meter.priced(PRICES)
    assert total is None
    assert [r.cost_usd is None for r in rows] == [False, True]


def test_record_is_noop_outside_metered_and_scoped_inside() -> None:
    record(provider="x", model="m", kind="chat", input_tokens=1, output_tokens=1)
    with metered() as outer:
        record(provider="x", model="m", kind="chat", input_tokens=1, output_tokens=0)
        with metered() as inner:
            record(provider="x", model="m", kind="chat", input_tokens=5, output_tokens=0)
        record(provider="x", model="m", kind="embedding", input_tokens=2, output_tokens=0)
    assert (outer.input_tokens, inner.input_tokens) == (3, 5)


async def test_meter_propagates_into_tasks() -> None:
    async def work() -> None:
        record(provider="x", model="m", kind="chat", input_tokens=7, output_tokens=0)

    with metered() as meter:
        await asyncio.gather(asyncio.create_task(work()), asyncio.create_task(work()))
    assert meter.input_tokens == 14


def test_shipped_price_file_parses() -> None:
    table = load_prices(DEFAULT_PRICES)
    assert table.lookup("openai", "text-embedding-3-small-v1") is not None
    assert table.lookup("fake", "fake-llm") == Price(0.0, 0.0)


def test_custom_price_file(tmp_path: Path) -> None:
    path = tmp_path / "prices.yaml"
    path.write_text("free_providers: []\nmodels:\n  fake-llm: {input: 1.0, output: 2.0}\n")
    assert load_prices(path).lookup("fake", "fake-llm") == Price(1.0, 2.0)
