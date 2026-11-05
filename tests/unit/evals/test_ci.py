import json
from pathlib import Path

from evals.ci import Suite, compare, finish


def test_compare_flags_drops_beyond_tolerance_only() -> None:
    rows = compare(
        {"a": 0.80, "b": 0.795, "c": 0.9, "new": 0.5}, {"a": 0.81, "b": 0.81, "c": 0.8}, 0.01
    )
    status = {r.name: r.regressed for r in rows}
    assert status == {"a": False, "b": True, "c": False, "new": False}
    assert next(r for r in rows if r.name == "new").baseline is None


def test_finish_fails_on_regression_and_reports(tmp_path: Path) -> None:
    baselines = tmp_path / "baselines.json"
    baselines.write_text(json.dumps({"tolerance": 0.01, "metrics": {"m": 0.9}}))
    report = tmp_path / "report.md"
    suite = Suite(metrics={"m": 0.85}, skipped={"agent": "no cache"})

    assert finish(suite, update_baselines=False, report=report, baselines_path=baselines) is False
    text = report.read_text()
    assert "❌ regression" in text
    assert "| m | 0.850 | 0.900 | -0.050 | ❌ |" in text
    assert "- `agent`: no cache" in text


def test_finish_fails_on_failed_eval_even_without_regression(tmp_path: Path) -> None:
    suite = Suite(metrics={}, failed={"sql": "cache miss"})
    assert (
        finish(
            suite,
            update_baselines=False,
            report=tmp_path / "r.md",
            baselines_path=tmp_path / "b.json",
        )
        is False
    )


def test_update_baselines_accepts_current_numbers(tmp_path: Path) -> None:
    baselines = tmp_path / "baselines.json"
    baselines.write_text(json.dumps({"tolerance": 0.02, "metrics": {"m": 0.9}}))
    suite = Suite(metrics={"m": 0.8, "n": 0.123456})
    assert finish(suite, update_baselines=True, report=tmp_path / "r.md", baselines_path=baselines)
    assert json.loads(baselines.read_text()) == {
        "tolerance": 0.02,
        "metrics": {"m": 0.8, "n": 0.1235},
    }


def test_improvement_passes_and_is_marked(tmp_path: Path) -> None:
    baselines = tmp_path / "b.json"
    baselines.write_text(json.dumps({"tolerance": 0.01, "metrics": {"m": 0.5}}))
    report = tmp_path / "r.md"
    assert finish(
        Suite(metrics={"m": 0.6}), update_baselines=False, report=report, baselines_path=baselines
    )
    assert "⬆️" in report.read_text()


def test_committed_baselines_cover_retrieval() -> None:
    data = json.loads((Path(__file__).parents[3] / "evals" / "baselines.json").read_text())
    names = set(data["metrics"])
    for strategy in ("vector", "keyword", "hybrid"):
        assert f"retrieval.{strategy}.fake-embedding.mrr@10" in names
