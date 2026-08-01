"""Tests for scripts/eval_compare.py — the adapter over the paired-compare core.

All offline: runs are dicts shaped like eval_retrieval --json output, no
ChromaDB, no API key. Core behaviors (pairing, exclusion, duplicate flagging)
are exercised through the adapter path, which is the seam this repo owns.
"""

from __future__ import annotations

import json

import pytest

from scripts.eval_compare import (
    compare_runs,
    group_key_for,
    load_results,
    observations_from_results,
)
from scripts.paired_compare import (
    DiagnosticReason,
    Outcome,
    canonical_json,
    pair_observations,
    summarize_correctness,
    summarize_metric,
)


def _result(qid: str, rank: int | None, expected: list[str] | None = None) -> dict:
    return {
        "id": qid,
        "kind": "notes",
        "adversarial": False,
        "rank": rank,
        "returned": [],
        "expected": expected or [f"learning-notes/{qid}.md"],
    }


# ---- group keys ------------------------------------------------------------


def test_group_key_is_content_hash_not_bare_id():
    key = group_key_for(_result("q1", 1))
    assert key != "q1"  # the hand-assigned label alone is not identity
    assert len(key) == 64  # hex SHA-256
    # Deterministic, and independent of the graded outcome:
    assert key == group_key_for(_result("q1", None))


def test_group_key_changes_when_expected_sources_change():
    a = group_key_for(_result("q1", 1, expected=["a.md"]))
    b = group_key_for(_result("q1", 1, expected=["b.md"]))
    assert a != b


def test_group_key_ignores_expected_source_order():
    a = group_key_for(_result("q1", 1, expected=["a.md", "b.md"]))
    b = group_key_for(_result("q1", 1, expected=["b.md", "a.md"]))
    assert a == b


def test_canonical_json_sorts_keys():
    assert canonical_json({"b": 1, "a": [2, {"d": 3, "c": 4}]}) == canonical_json(
        {"a": [2, {"c": 4, "d": 3}], "b": 1}
    )


# ---- hit/miss thresholding -------------------------------------------------


def test_observation_thresholds_rank_against_k():
    results = [_result("q1", 1), _result("q2", 4), _result("q3", None)]

    at_5 = observations_from_results(results, "arm", k=5)
    assert [o.score for o in at_5] == [1.0, 1.0, 0.0]
    assert all(o.outcome is Outcome.SCORED for o in at_5)  # a miss is scored 0, not dropped

    at_3 = observations_from_results(results, "arm", k=3)
    assert [o.score for o in at_3] == [1.0, 0.0, 0.0]


def test_observation_metrics_carry_per_query_recall_and_reciprocal_rank():
    (obs,) = observations_from_results([_result("q1", 3)], "arm", k=5)
    assert obs.metrics == {
        "recall@1": 0.0,
        "recall@3": 1.0,
        "recall@5": 1.0,
        "reciprocal_rank": pytest.approx(1 / 3),
    }
    (miss,) = observations_from_results([_result("q2", None)], "arm", k=5)
    assert miss.metrics["reciprocal_rank"] == 0.0


def test_result_without_rank_field_is_unscored_not_a_miss():
    broken = {"id": "q1", "expected": ["a.md"]}  # no "rank" key at all
    (obs,) = observations_from_results([broken], "arm", k=5)
    assert obs.outcome is Outcome.UNSCORED
    assert obs.score is None


# ---- pairing bookkeeping through the adapter -------------------------------


def test_query_in_only_one_arm_is_excluded_and_flagged():
    baseline = observations_from_results([_result("q1", 1), _result("q2", 1)], "base", k=5)
    candidate = observations_from_results([_result("q1", 2)], "cand", k=5)

    result = pair_observations(baseline, candidate)
    assert len(result.pairs) == 1  # q2 does not pair
    assert result.total_groups == 2
    missing = [d for d in result.diagnostics if d.reason is DiagnosticReason.MISSING_OBSERVATION]
    assert len(missing) == 1
    assert missing[0].group_key == group_key_for(_result("q2", 1))

    # And the unpaired row leaves numerator AND denominator:
    lift = summarize_correctness(result.pairs)
    assert lift.eligible_pairs == 1
    assert lift.baseline_pass_rate == 1.0


def test_duplicate_query_is_flagged_and_dropped_from_both_arms():
    baseline = observations_from_results([_result("q1", 1), _result("q1", 3)], "base", k=5)
    candidate = observations_from_results([_result("q1", 2)], "cand", k=5)

    result = pair_observations(baseline, candidate)
    assert result.pairs == []  # no principled pick between the duplicates
    duplicates = [
        d for d in result.diagnostics if d.reason is DiagnosticReason.DUPLICATE_OBSERVATION
    ]
    assert len(duplicates) == 1
    assert duplicates[0].arm == "base"


def test_paired_metric_deltas_flow_through():
    baseline = observations_from_results([_result("q1", 3), _result("q2", None)], "base", k=5)
    candidate = observations_from_results([_result("q1", 1), _result("q2", 2)], "cand", k=5)

    result = pair_observations(baseline, candidate)
    mrr = summarize_metric(result.pairs, "reciprocal_rank")
    assert mrr.eligible_pairs == 2
    assert mrr.baseline_mean == pytest.approx((1 / 3 + 0) / 2)
    assert mrr.candidate_mean == pytest.approx((1 + 1 / 2) / 2)
    assert mrr.mean_delta == pytest.approx(mrr.candidate_mean - mrr.baseline_mean)


# ---- end to end over run files ---------------------------------------------


def _write_run(path, results: list[dict]) -> None:
    path.write_text(json.dumps({"summary": {}, "results": results}), encoding="utf-8")


def test_compare_runs_end_to_end(tmp_path):
    baseline = tmp_path / "baseline.json"
    candidate = tmp_path / "candidate.json"
    _write_run(baseline, [_result("q1", 1), _result("q2", None), _result("q3", 2)])
    _write_run(candidate, [_result("q1", 1), _result("q2", 4), _result("q4", 1)])

    report = compare_runs(baseline, candidate, baseline_name="base", candidate_name="cand")
    # q1/q2 pair; q3 and q4 are single-arm → 2 eligible pairs, 2 missing.
    assert "Eligible pairs     : 2" in report
    assert "missing-observation    : 2" in report
    assert "candidate 1, baseline 0, ties 1" in report
    assert "HARNESS HEALTH" in report


def test_load_results_refuses_a_file_without_results(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"summary": {}}), encoding="utf-8")
    with pytest.raises(ValueError, match="no 'results' list"):
        load_results(path)
