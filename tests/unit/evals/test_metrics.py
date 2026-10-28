import pytest

from evals.metrics import (
    RankingScores,
    classification_report,
    mean_ranking,
    ranking_scores,
    set_scores,
)


def test_perfect_predictions() -> None:
    report = classification_report(["a", "b", "a"], ["a", "b", "a"])
    assert (report.accuracy, report.macro_f1, report.confusions) == (1.0, 1.0, [])


def test_per_class_scores_and_confusions() -> None:
    expected = ["a", "a", "a", "b", "b", "c"]
    predicted = ["a", "a", "b", "b", "a", "c"]
    report = classification_report(expected, predicted)

    assert report.accuracy == pytest.approx(4 / 6)
    a = report.per_class["a"]
    assert a.precision == pytest.approx(2 / 3)
    assert a.recall == pytest.approx(2 / 3)
    assert a.support == 3
    b = report.per_class["b"]
    assert (b.precision, b.recall) == (0.5, 0.5)
    assert report.per_class["c"].f1 == 1.0
    assert report.macro_f1 == pytest.approx((2 / 3 + 0.5 + 1.0) / 3)
    assert sorted(report.confusions) == [("a", "b", 1), ("b", "a", 1)]


def test_predicting_unseen_class_hurts_recall_only_of_gold_class() -> None:
    report = classification_report(["a", "a"], ["a", "zzz"])
    assert set(report.per_class) == {"a"}
    assert report.per_class["a"].recall == 0.5
    assert report.per_class["a"].precision == 1.0


def test_booleans_work_as_labels() -> None:
    report = classification_report([True, False, True], [True, True, True])
    assert report.per_class["True"].recall == 1.0
    assert report.per_class["False"].recall == 0.0


def test_length_mismatch() -> None:
    with pytest.raises(ValueError, match="same length"):
        classification_report(["a"], [])


def test_set_scores_are_micro_and_case_insensitive() -> None:
    scores = set_scores(
        [{"NovaTel"}, set(), {"Kartly", "ShopNest"}],
        [{"novatel"}, {"Orbit"}, {"Kartly"}],
    )
    # tp=2 (novatel, kartly), fp=1 (orbit), fn=1 (shopnest)
    assert scores.precision == pytest.approx(2 / 3)
    assert scores.recall == pytest.approx(2 / 3)
    assert scores.exact_match == pytest.approx(1 / 3)


def test_set_scores_all_empty_is_perfect() -> None:
    scores = set_scores([set(), set()], [set(), set()])
    assert (scores.precision, scores.recall, scores.exact_match) == (1.0, 1.0, 1.0)


def test_ranking_scores() -> None:
    scores = ranking_scores(["x", "a", "y", "b"], {"a", "b", "c"}, k=3)
    assert scores.mrr == 0.5
    assert scores.precision == pytest.approx(1 / 3)
    assert scores.recall == pytest.approx(1 / 3)


def test_ranking_recall_is_normalized_by_k() -> None:
    relevant = {f"r{i}" for i in range(40)}
    scores = ranking_scores(["r1", "r2", "r3", "r4", "r5"], relevant, k=5)
    assert (scores.mrr, scores.precision, scores.recall) == (1.0, 1.0, 1.0)


def test_ranking_no_hits_and_short_lists() -> None:
    assert ranking_scores([], {"a"}, k=5) == RankingScores(0.0, 0.0, 0.0)
    assert ranking_scores(["a"], {"a"}, k=5).precision == pytest.approx(1 / 5)


def test_ranking_rejects_bad_input() -> None:
    with pytest.raises(ValueError, match="relevant"):
        ranking_scores(["a"], set(), k=1)
    with pytest.raises(ValueError, match="positive"):
        ranking_scores(["a"], {"a"}, k=0)


def test_mean_ranking() -> None:
    mean = mean_ranking([RankingScores(1.0, 0.5, 1.0), RankingScores(0.0, 0.0, 0.0)])
    assert mean == RankingScores(0.5, 0.25, 0.5)
    assert mean_ranking([]) == RankingScores(0.0, 0.0, 0.0)
