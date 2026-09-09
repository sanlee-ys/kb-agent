"""Tests for scripts/eval_gate.py — the SYS-017 tier 2 retrieval gate.

Offline only: the gate reads fixture JSON and the committed floors file. It
never opens ChromaDB and never calls search_kb. A gate that cannot fail is
theater, so these tests cover pass, floor breach, n shrink, and a missing file.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.eval_gate import (
    DEFAULT_THRESHOLDS,
    GATED_METRICS,
    GateError,
    grade_arm,
    load_run,
    load_thresholds,
    main,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "eval_gate"
PASS_UNFILTERED = FIXTURES / "pass_unfiltered.json"
PASS_KIND_FILTER = FIXTURES / "pass_kind_filter.json"
BREACH_UNFILTERED = FIXTURES / "breach_unfiltered.json"
N_SHRINK_UNFILTERED = FIXTURES / "n_shrink_unfiltered.json"

FLOORS = {
    "n": 27,
    "recall@1": 0.88,
    "recall@5": 0.92,
    "mrr": 0.90,
}


def _overall(**overrides):
    data = {
        "n": 27,
        "recall@1": 0.963,
        "recall@5": 1.0,
        "mrr": 0.981,
    }
    data.update(overrides)
    return data


def test_committed_floors_match_gold_set_size_and_proposed_values():
    floors = load_thresholds(DEFAULT_THRESHOLDS)
    for arm in ("unfiltered", "kind_filter"):
        assert floors[arm]["n"] == 27
        assert floors[arm]["recall@1"] == 0.88
        assert floors[arm]["recall@5"] == 0.92
        assert floors[arm]["mrr"] == 0.90


def test_grade_arm_pass_at_operating_point():
    assert grade_arm("unfiltered", _overall(), FLOORS) == []


def test_grade_arm_pass_exactly_on_each_floor():
    exact = {"n": 27, "recall@1": 0.88, "recall@5": 0.92, "mrr": 0.90}
    assert grade_arm("kind_filter", exact, FLOORS) == []


def test_grade_arm_floor_breach():
    problems = grade_arm("unfiltered", _overall(**{"recall@1": 0.70}), FLOORS)
    assert problems
    assert any("recall@1" in p and "0.700" in p for p in problems)


def test_grade_arm_n_shrink_refuses_to_grade_even_when_metrics_are_perfect():
    problems = grade_arm(
        "unfiltered",
        {"n": 26, "recall@1": 1.0, "recall@5": 1.0, "mrr": 1.0},
        FLOORS,
    )
    assert len(problems) == 1
    assert "refuse to grade" in problems[0]
    assert "n=26" in problems[0]
    # A truncated snapshot must not be reported as a metric pass.
    assert not any(key in problems[0] for key in GATED_METRICS)


def test_grade_arm_n_grow_also_refuses():
    problems = grade_arm("kind_filter", _overall(n=28), FLOORS)
    assert any("n=28" in p for p in problems)


def test_load_run_reads_summary_overall():
    overall = load_run(PASS_UNFILTERED)
    assert overall["n"] == 27
    assert overall["recall@1"] == 0.963
    assert overall["mrr"] == 0.981


def test_load_run_missing_file(tmp_path):
    missing = tmp_path / "absent.json"
    with pytest.raises(GateError, match="missing JSON file"):
        load_run(missing)


def test_load_run_malformed_json(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(GateError, match="malformed JSON"):
        load_run(bad)


def test_load_run_missing_overall(tmp_path):
    path = tmp_path / "no_overall.json"
    path.write_text(json.dumps({"summary": {"projects": {"n": 8}}}), encoding="utf-8")
    with pytest.raises(GateError, match="summary.overall"):
        load_run(path)


def test_main_pass_on_fixture_files():
    assert (
        main(
            [
                "--unfiltered",
                str(PASS_UNFILTERED),
                "--kind-filter",
                str(PASS_KIND_FILTER),
            ]
        )
        == 0
    )


def test_main_floor_breach_exits_1():
    assert (
        main(
            [
                "--unfiltered",
                str(BREACH_UNFILTERED),
                "--kind-filter",
                str(PASS_KIND_FILTER),
            ]
        )
        == 1
    )


def test_main_n_shrink_exits_1():
    assert (
        main(
            [
                "--unfiltered",
                str(N_SHRINK_UNFILTERED),
                "--kind-filter",
                str(PASS_KIND_FILTER),
            ]
        )
        == 1
    )


def test_main_missing_file_exits_1(tmp_path):
    missing = tmp_path / "nope.json"
    assert (
        main(
            [
                "--unfiltered",
                str(missing),
                "--kind-filter",
                str(PASS_KIND_FILTER),
            ]
        )
        == 1
    )


def test_main_malformed_file_exits_1(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("]", encoding="utf-8")
    assert (
        main(
            [
                "--unfiltered",
                str(bad),
                "--kind-filter",
                str(PASS_KIND_FILTER),
            ]
        )
        == 1
    )
