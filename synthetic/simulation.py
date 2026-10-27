"""Label simulation: decides *what happened* before any text is written.

The simulator walks a date range, places conversations on days (busier while
events are active), and for each one samples the customer, contact reason,
outcome, sentiment, competitor mentions, and the event behind it. Customers who
churn stop contacting. The result is a list of `ConversationSpec`s whose labels
are the ground truth; renderers only turn them into text.

Everything is driven by one seeded `random.Random`, so the same config and seed
always produce the same specs.
"""

import random
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from synthetic.schema import (
    Channel,
    ChurnRisk,
    ContactReason,
    Customer,
    Event,
    GroundTruth,
    IndustryConfig,
    Plan,
    Sentiment,
)

SENTIMENTS: tuple[Sentiment, Sentiment, Sentiment] = ("negative", "neutral", "positive")
PRICE_INCREASE = 1.15
CUSTOMERS_PER_CONVERSATION = 0.4
BUSINESS_HOURS = (8, 21)


@dataclass
class _CustomerState:
    id: str
    plan: Plan
    signup_at: date
    churned_at: date | None = None
    churn_reason: str | None = None

    def to_model(self, industry: str) -> Customer:
        return Customer(
            id=self.id,
            industry=industry,
            plan_code=self.plan.code,
            signup_at=self.signup_at,
            churned_at=self.churned_at,
            churn_reason=self.churn_reason,
        )


@dataclass(frozen=True)
class ConversationSpec:
    external_id: str
    customer_id: str
    industry: str
    brand: str
    plan: Plan
    channel: Channel
    started_at: datetime
    reason: ContactReason
    sentiment: Sentiment
    resolved: bool
    cancellation: bool
    churned: bool
    churn_risk: ChurnRisk
    competitors: tuple[str, ...]
    event: Event | None
    slots: dict[str, str] = field(default_factory=dict)

    def ground_truth(self) -> GroundTruth:
        return GroundTruth(
            external_id=self.external_id,
            contact_reason=self.reason.id,
            sentiment=self.sentiment,
            resolved=self.resolved,
            churn_risk=self.churn_risk,
            churned=self.churned,
            competitors=list(self.competitors),
            event_id=self.event.id if self.event else None,
        )


@dataclass(frozen=True)
class Simulation:
    customers: list[Customer]
    specs: list[ConversationSpec]


def _daterange(start: date, end: date) -> list[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def _active_events(config: IndustryConfig, day: date) -> list[Event]:
    return [e for e in config.events if e.active_on(day)]


def _day_weight(config: IndustryConfig, day: date) -> float:
    weight = 1.0
    for event in _active_events(config, day):
        weight *= event.volume_multiplier
    return weight


def _reason_weights(config: IndustryConfig, events: Sequence[Event]) -> list[float]:
    weights = []
    for reason in config.reasons:
        w = reason.weight
        for event in events:
            w *= event.reason_multipliers.get(reason.id, 1.0)
        weights.append(w)
    return weights


def _sample_sentiment(rng: random.Random, reason: ContactReason, resolved: bool) -> Sentiment:
    neg, neu, pos = reason.sentiment
    # A good outcome moves half of the negative mass to positive; a bad one the reverse.
    weights = [neg * 0.5, neu, pos + neg * 0.5] if resolved else [neg + pos * 0.7, neu, pos * 0.3]
    return rng.choices(SENTIMENTS, weights=weights)[0]


def _churn_risk(
    *, cancellation: bool, churned: bool, sentiment: Sentiment, resolved: bool, competitors: bool
) -> ChurnRisk:
    if churned or cancellation or (competitors and sentiment == "negative"):
        return "high"
    if competitors or (sentiment == "negative" and not resolved):
        return "medium"
    return "low"


def _money(value: float) -> str:
    return f"${value:,.2f}"


def simulate(
    config: IndustryConfig,
    *,
    industry: str,
    n: int,
    start: date,
    end: date,
    seed: int,
) -> Simulation:
    if n <= 0:
        raise ValueError("n must be positive")
    if end < start:
        raise ValueError("end must not be before start")

    rng = random.Random(seed)

    # 1. Customers. Enough of them that churn does not exhaust the pool.
    n_customers = max(10, int(n * CUSTOMERS_PER_CONVERSATION))
    customers = [
        _CustomerState(
            id=f"cust_{industry}_{i:05d}",
            plan=rng.choices(config.plans, weights=[p.weight for p in config.plans])[0],
            signup_at=start - timedelta(days=rng.randint(30, 1500)),
        )
        for i in range(n_customers)
    ]

    # 2. When conversations happen: days weighted by active events, then a time.
    days = _daterange(start, end)
    picked_days = sorted(rng.choices(days, weights=[_day_weight(config, d) for d in days], k=n))
    open_h, close_h = BUSINESS_HOURS
    timestamps = sorted(
        datetime.combine(d, time(rng.randint(open_h, close_h - 1), rng.randint(0, 59)))
        for d in picked_days
    )

    channels = list(config.channels)
    channel_weights = list(config.channels.values())
    specs: list[ConversationSpec] = []

    # 3. Each conversation, in chronological order so churn is respected.
    for i, started_at in enumerate(timestamps):
        day = started_at.date()
        active = [c for c in customers if c.churned_at is None]
        if not active:
            break
        customer = rng.choice(active)
        events = _active_events(config, day)
        reason = rng.choices(config.reasons, weights=_reason_weights(config, events))[0]

        # Attribute the conversation to an event only if the event affects this reason.
        event = next(
            (
                e
                for e in events
                if reason.id in e.reason_multipliers and rng.random() < e.mention_rate
            ),
            None,
        )

        cancellation = reason.id == config.cancellation_reason
        churned = False
        if cancellation:
            churn_boost = 1.0
            for e in events:
                churn_boost *= e.churn_multiplier
            retained = rng.random() < config.retention_rate / churn_boost
            resolved, churned = retained, not retained
        else:
            resolved = rng.random() < reason.resolution_rate

        sentiment = _sample_sentiment(rng, reason, resolved)

        mention_rate = reason.competitor_mention_rate * (1.5 if event else 1.0)
        competitors: tuple[str, ...] = ()
        if rng.random() < min(mention_rate, 1.0):
            k = 2 if rng.random() < 0.15 else 1
            competitors = tuple(rng.sample(config.competitors, k=k))

        if churned:
            customer.churned_at = day
            customer.churn_reason = (
                event.id if event else ("competitor" if competitors else reason.id)
            )

        price = customer.plan.monthly_price
        slots = {
            "brand": config.brand,
            "plan": customer.plan.name,
            "plan_code": customer.plan.code,
            "price": _money(price),
            "new_price": _money(price * PRICE_INCREASE),
            "amount": _money(rng.choice([4.99, 12.50, 19.90, 27.30, 45.00, 89.90, 120.00])),
            "product": rng.choice(config.products),
            "error_code": rng.choice(config.error_codes),
        }
        if event:
            slots.update(event.slots)

        specs.append(
            ConversationSpec(
                external_id=f"conv_{industry}_{i:06d}",
                customer_id=customer.id,
                industry=industry,
                brand=config.brand,
                plan=customer.plan,
                channel=rng.choices(channels, weights=channel_weights)[0],
                started_at=started_at,
                reason=reason,
                sentiment=sentiment,
                resolved=resolved,
                cancellation=cancellation,
                churned=churned,
                churn_risk=_churn_risk(
                    cancellation=cancellation,
                    churned=churned,
                    sentiment=sentiment,
                    resolved=resolved,
                    competitors=bool(competitors),
                ),
                competitors=competitors,
                event=event,
                slots=slots,
            )
        )

    return Simulation(customers=[c.to_model(industry) for c in customers], specs=specs)
