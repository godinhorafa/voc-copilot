"""Classification metrics, dependency-free so they are easy to audit."""

from collections import Counter
from collections.abc import Hashable, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class ClassScores:
    precision: float
    recall: float
    f1: float
    support: int


@dataclass(frozen=True)
class FieldReport:
    accuracy: float
    macro_f1: float
    per_class: dict[str, ClassScores]
    confusions: list[tuple[str, str, int]]  # (expected, predicted, count), most common first
    n: int


def _f1(p: float, r: float) -> float:
    return 2 * p * r / (p + r) if p + r else 0.0


def classification_report(
    expected: Sequence[Hashable], predicted: Sequence[Hashable], *, top_confusions: int = 5
) -> FieldReport:
    if len(expected) != len(predicted):
        raise ValueError("expected and predicted must have the same length")
    n = len(expected)
    if n == 0:
        return FieldReport(0.0, 0.0, {}, [], 0)

    pairs = list(zip(expected, predicted, strict=True))
    tp: Counter[Hashable] = Counter(e for e, p in pairs if e == p)
    gold = Counter(expected)
    pred = Counter(predicted)

    # Classes that appear in the gold labels; predictions of unseen classes
    # still count as false positives against precision of nothing, and as
    # misses for the gold class, which is what macro-F1 over gold classes needs.
    per_class: dict[str, ClassScores] = {}
    for label in sorted(gold, key=str):
        precision = tp[label] / pred[label] if pred[label] else 0.0
        recall = tp[label] / gold[label]
        per_class[str(label)] = ClassScores(precision, recall, _f1(precision, recall), gold[label])

    confusions = Counter((str(e), str(p)) for e, p in pairs if e != p).most_common(top_confusions)
    return FieldReport(
        accuracy=sum(tp.values()) / n,
        macro_f1=sum(c.f1 for c in per_class.values()) / len(per_class),
        per_class=per_class,
        confusions=[(e, p, c) for (e, p), c in confusions],
        n=n,
    )


@dataclass(frozen=True)
class SetScores:
    precision: float
    recall: float
    f1: float
    exact_match: float  # share of items whose predicted set equals the gold set


def set_scores(expected: Sequence[set[str]], predicted: Sequence[set[str]]) -> SetScores:
    """Micro precision/recall over (item, element) pairs; elements compared case-insensitively."""
    if len(expected) != len(predicted):
        raise ValueError("expected and predicted must have the same length")
    tp = fp = fn = exact = 0
    for gold, pred in zip(expected, predicted, strict=True):
        g = {x.casefold() for x in gold}
        p = {x.casefold() for x in pred}
        tp += len(g & p)
        fp += len(p - g)
        fn += len(g - p)
        exact += g == p
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    return SetScores(
        precision, recall, _f1(precision, recall), exact / len(expected) if expected else 0.0
    )


@dataclass(frozen=True)
class RankingScores:
    mrr: float  # 1 / rank of the first relevant item, 0 if none in the list
    precision: float  # relevant share of the top k
    recall: float  # relevant found in top k / min(k, |relevant|)


def ranking_scores(ranked: Sequence[str], relevant: set[str], k: int) -> RankingScores:
    """Scores for one query.

    Recall is normalized by min(k, |relevant|): a question with 40 relevant
    conversations can't have more than k of them in the top k, so plain recall
    would cap far below 1 and mostly measure the size of the relevant set.
    """
    if k <= 0:
        raise ValueError("k must be positive")
    if not relevant:
        raise ValueError("a query needs at least one relevant item")
    top = list(ranked[:k])
    first = next((i for i, item in enumerate(top, start=1) if item in relevant), None)
    hits = sum(item in relevant for item in top)
    return RankingScores(
        mrr=1 / first if first else 0.0,
        precision=hits / k,
        recall=hits / min(k, len(relevant)),
    )


def mean_ranking(scores: Sequence[RankingScores]) -> RankingScores:
    if not scores:
        return RankingScores(0.0, 0.0, 0.0)
    n = len(scores)
    return RankingScores(
        mrr=sum(s.mrr for s in scores) / n,
        precision=sum(s.precision for s in scores) / n,
        recall=sum(s.recall for s in scores) / n,
    )
