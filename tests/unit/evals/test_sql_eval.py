from pathlib import Path

import pytest

from app.analytics.sql_guard import check
from evals.sql_eval import load_questions, results_match

GOLDEN = Path(__file__).parents[3] / "evals" / "datasets" / "sql_golden.jsonl"


def test_golden_reference_queries_pass_the_guard() -> None:
    questions = load_questions(GOLDEN)
    assert len(questions) >= 15
    assert len({q.id for q in questions}) == len(questions)
    for q in questions:
        check(q.sql)


def test_unordered_match_ignores_row_and_column_order_and_names() -> None:
    expected = [["chat", 3], ["voice", 2]]
    assert results_match(expected, [[2, "voice"], [3, "chat"]], ordered=False)


def test_ordered_match_requires_row_order() -> None:
    expected = [["2026-09", 1], ["2026-10", 2]]
    assert results_match(expected, [["2026-09", 1], ["2026-10", 2]], ordered=True)
    assert not results_match(expected, [["2026-10", 2], ["2026-09", 1]], ordered=True)


def test_numeric_tolerance_and_int_float_equivalence() -> None:
    assert results_match([[0.3333]], [[0.33333333]], ordered=False)
    assert results_match([[3]], [[3.0]], ordered=False)
    assert not results_match([[0.33]], [[0.34]], ordered=False)


@pytest.mark.parametrize(
    "actual",
    [
        [["chat", 3]],  # missing row
        [["chat", 3], ["voice", 2], ["sms", 1]],  # extra row
        [["chat", 3], ["chat", 3]],  # duplicate instead of another row
        [["chat", 3, "x"], ["voice", 2, "y"]],  # extra column
    ],
)
def test_mismatches(actual: list[list[object]]) -> None:
    assert not results_match([["chat", 3], ["voice", 2]], actual, ordered=False)


def test_null_and_bool_cells() -> None:
    assert results_match([[None, True]], [[True, None]], ordered=False)
    assert not results_match([[None]], [[0]], ordered=False)
