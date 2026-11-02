"""Per-request token usage and cost.

Providers call `record()` after every LLM or embedding call. Inside a
`metered()` block (one per API request or ingestion job) the calls accumulate
in a `UsageMeter`; outside, recording is a no-op. Context variables are copied
into tasks created inside the block, so streamed requests are metered too.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from functools import cache
from pathlib import Path
from typing import Literal

import yaml

DEFAULT_PRICES = Path(__file__).parent / "prices.yaml"
Kind = Literal["chat", "embedding"]


@dataclass(frozen=True)
class Price:
    input: float  # USD per 1M tokens
    output: float


@dataclass(frozen=True)
class PriceTable:
    models: dict[str, Price]
    free_providers: frozenset[str]

    def lookup(self, provider: str, model: str) -> Price | None:
        if provider in self.free_providers:
            return Price(0.0, 0.0)
        matches = [key for key in self.models if model.startswith(key)]
        return self.models[max(matches, key=len)] if matches else None


@cache
def load_prices(path: Path = DEFAULT_PRICES) -> PriceTable:
    raw = yaml.safe_load(path.read_text())
    return PriceTable(
        models={name: Price(**p) for name, p in raw.get("models", {}).items()},
        free_providers=frozenset(raw.get("free_providers", [])),
    )


@dataclass
class ModelUsage:
    provider: str
    model: str
    kind: Kind
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None


@dataclass
class UsageMeter:
    by_model: dict[tuple[str, str, Kind], ModelUsage] = field(default_factory=dict)

    def record(
        self, *, provider: str, model: str, kind: Kind, input_tokens: int, output_tokens: int
    ) -> None:
        usage = self.by_model.setdefault((provider, model, kind), ModelUsage(provider, model, kind))
        usage.calls += 1
        usage.input_tokens += input_tokens
        usage.output_tokens += output_tokens

    @property
    def input_tokens(self) -> int:
        return sum(u.input_tokens for u in self.by_model.values())

    @property
    def output_tokens(self) -> int:
        return sum(u.output_tokens for u in self.by_model.values())

    def priced(self, prices: PriceTable) -> tuple[list[ModelUsage], float | None]:
        """Per-model usage with cost, and the total (None if any model is unpriced)."""
        rows = []
        total: float | None = 0.0
        for usage in self.by_model.values():
            price = prices.lookup(usage.provider, usage.model)
            cost = (
                None
                if price is None
                else (usage.input_tokens * price.input + usage.output_tokens * price.output) / 1e6
            )
            rows.append(replace(usage, cost_usd=None if cost is None else round(cost, 6)))
            total = None if total is None or cost is None else total + cost
        return rows, None if total is None else round(total, 6)


_current: ContextVar[UsageMeter | None] = ContextVar("usage_meter", default=None)


@contextmanager
def metered() -> Iterator[UsageMeter]:
    meter = UsageMeter()
    token = _current.set(meter)
    try:
        yield meter
    finally:
        _current.reset(token)


def record(*, provider: str, model: str, kind: Kind, input_tokens: int, output_tokens: int) -> None:
    meter = _current.get()
    if meter is not None:
        meter.record(
            provider=provider,
            model=model,
            kind=kind,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
