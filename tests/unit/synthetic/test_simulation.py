from collections import Counter
from datetime import date

import pytest

from synthetic.schema import Scenarios
from synthetic.simulation import ConversationSpec, Simulation, simulate

START, END = date(2026, 6, 1), date(2026, 11, 30)


def _run(
    scenarios: Scenarios, industry: str = "telecom", n: int = 1500, seed: int = 7
) -> Simulation:
    return simulate(
        scenarios.industries[industry], industry=industry, n=n, start=START, end=END, seed=seed
    )


def _count(specs: list[ConversationSpec], month: int, reason: str) -> int:
    return sum(s.started_at.month == month and s.reason.id == reason for s in specs)


def test_same_seed_same_output(scenarios: Scenarios) -> None:
    a, b = _run(scenarios, seed=1), _run(scenarios, seed=1)
    assert [s.ground_truth() for s in a.specs] == [s.ground_truth() for s in b.specs]
    assert [s.started_at for s in a.specs] == [s.started_at for s in b.specs]
    assert a.customers == b.customers


def test_different_seed_different_output(scenarios: Scenarios) -> None:
    a, b = _run(scenarios, seed=1), _run(scenarios, seed=2)
    assert [s.ground_truth() for s in a.specs] != [s.ground_truth() for s in b.specs]


def test_conversations_are_chronological_within_period(scenarios: Scenarios) -> None:
    specs = _run(scenarios).specs
    times = [s.started_at for s in specs]
    assert times == sorted(times)
    assert times[0].date() >= START
    assert times[-1].date() <= END
    assert [s.external_id for s in specs] == [f"conv_telecom_{i:06d}" for i in range(len(specs))]


def test_churned_customers_stop_contacting(scenarios: Scenarios) -> None:
    sim = _run(scenarios)
    churned_at = {c.id: c.churned_at for c in sim.customers if c.churned_at}
    assert churned_at, "expected some churn"
    churn_convs = Counter(s.customer_id for s in sim.specs if s.churned)
    assert set(churn_convs) == set(churned_at)
    assert all(count == 1 for count in churn_convs.values())
    for spec in sim.specs:
        cutoff = churned_at.get(spec.customer_id)
        if cutoff and not spec.churned:
            assert spec.started_at.date() <= cutoff


def test_churn_only_through_failed_cancellation(scenarios: Scenarios) -> None:
    config = scenarios.industries["telecom"]
    for spec in _run(scenarios).specs:
        if spec.churned:
            assert spec.reason.id == config.cancellation_reason
            assert spec.cancellation
            assert not spec.resolved
            assert spec.churn_risk == "high"


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_price_increase_drives_billing_and_cancellations(scenarios: Scenarios, seed: int) -> None:
    # Config implies ~2.1x billing and ~2.5x cancellation contacts in September.
    # Compare against the Jun-Aug average (cancellations are rare, one month is noisy).
    sim = _run(scenarios, n=3000, seed=seed)
    for reason in ("billing_dispute", "cancellation"):
        baseline = sum(_count(sim.specs, m, reason) for m in (6, 7, 8)) / 3
        assert _count(sim.specs, 9, reason) > 1.6 * baseline
    churn_by_month = Counter(s.started_at.month for s in sim.specs if s.churned)
    churn_baseline = sum(churn_by_month[m] for m in (6, 7, 8)) / 3
    assert churn_by_month[9] > 1.8 * churn_baseline


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_outage_spikes_technical_issues(scenarios: Scenarios, seed: int) -> None:
    specs = _run(scenarios, seed=seed).specs
    outage = {date(2026, 10, d) for d in (14, 15, 16)}
    tech = Counter(s.started_at.date() for s in specs if s.reason.id == "technical_issue")
    per_day_outage = sum(tech[d] for d in outage) / len(outage)
    other_days = (END - START).days + 1 - len(outage)
    per_day_normal = sum(v for d, v in tech.items() if d not in outage) / other_days
    # Config implies ~5x (2.2x volume, technical share 0.29 -> 0.65); assert with margin.
    assert per_day_outage > 3 * per_day_normal


def test_events_attributed_only_inside_window_and_to_affected_reasons(
    scenarios: Scenarios,
) -> None:
    attributed = [s for s in _run(scenarios).specs if s.event]
    assert {s.event.id for s in attributed if s.event} == {
        "price_increase_2026_09",
        "network_outage_2026_10",
    }
    for spec in attributed:
        assert spec.event is not None
        assert spec.event.active_on(spec.started_at.date())
        assert spec.reason.id in spec.event.reason_multipliers
        assert spec.slots.items() >= spec.event.slots.items()


@pytest.mark.parametrize("industry", ["telecom", "banking", "ecommerce"])
def test_competitor_labels_are_known_and_unique(scenarios: Scenarios, industry: str) -> None:
    known = set(scenarios.industries[industry].competitors)
    specs = _run(scenarios, industry=industry).specs
    assert any(s.competitors for s in specs)
    for spec in specs:
        assert set(spec.competitors) <= known
        assert len(set(spec.competitors)) == len(spec.competitors)


def test_rejects_invalid_arguments(scenarios: Scenarios) -> None:
    config = scenarios.industries["telecom"]
    with pytest.raises(ValueError, match="positive"):
        simulate(config, industry="telecom", n=0, start=START, end=END, seed=1)
    with pytest.raises(ValueError, match="before start"):
        simulate(config, industry="telecom", n=10, start=END, end=START, seed=1)
