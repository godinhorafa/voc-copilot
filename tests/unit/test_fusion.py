import pytest

from app.retrieval.fusion import rrf


def test_rrf_scores_and_order() -> None:
    fused = rrf([["a", "b", "c"], ["b", "d"]], k=60)
    scores = dict(fused)
    assert scores["b"] == pytest.approx(1 / 62 + 1 / 61)
    assert scores["a"] == pytest.approx(1 / 61)
    assert [item for item, _ in fused] == ["b", "a", "d", "c"]


def test_item_in_both_lists_beats_top_of_one() -> None:
    fused = rrf([["x", "shared"], ["y", "shared"]])
    assert fused[0][0] == "shared"


def test_ties_keep_first_appearance() -> None:
    assert [i for i, _ in rrf([["a"], ["b"]])] == ["a", "b"]


def test_empty_and_invalid() -> None:
    assert rrf([]) == []
    assert rrf([[], []]) == []
    with pytest.raises(ValueError, match="k must be"):
        rrf([["a"]], k=-1)
