"""Tests for scripts/eval_kind_usage.py — tool_use parsing and rate math.

All offline: the model call is faked, so no network and no API key. The parsing
helpers are exercised through both shapes they see in the wild — plain dicts and
attribute-style blocks like the Anthropic SDK returns.
"""

from __future__ import annotations

import pytest

from agent.tools import TOOLS
from scripts.eval_kind_usage import KINDS, evaluate, first_tool_use, grade_call, summarize


class _Block:
    """Attribute-style stand-in for an SDK content block."""

    def __init__(self, type: str, name: str | None = None, input: dict | None = None):
        self.type = type
        self.name = name
        self.input = input


def _tool_use(name: str, **kwargs) -> dict:
    return {"type": "tool_use", "name": name, "input": kwargs}


_TEXT = {"type": "text", "text": "let me look that up"}


# ---- first_tool_use --------------------------------------------------------


def test_first_tool_use_skips_leading_text_blocks():
    blocks = [_TEXT, _tool_use("search_kb", query="q", kind="notes")]
    assert first_tool_use(blocks) == {
        "name": "search_kb",
        "input": {"query": "q", "kind": "notes"},
    }


def test_first_tool_use_takes_only_the_first_call():
    blocks = [_tool_use("list_projects"), _tool_use("search_kb", query="q", kind="notes")]
    assert first_tool_use(blocks)["name"] == "list_projects"


def test_first_tool_use_reads_sdk_style_attribute_blocks():
    blocks = [
        _Block("text"),
        _Block("tool_use", name="search_kb", input={"query": "q", "kind": "libraries"}),
    ]
    assert first_tool_use(blocks) == {
        "name": "search_kb",
        "input": {"query": "q", "kind": "libraries"},
    }


def test_first_tool_use_none_when_no_tool_called():
    assert first_tool_use([_TEXT]) is None
    assert first_tool_use([]) is None
    assert first_tool_use(None) is None


def test_first_tool_use_tolerates_a_missing_input():
    assert first_tool_use([_Block("tool_use", name="list_projects")]) == {
        "name": "list_projects",
        "input": {},
    }


# ---- grade_call ------------------------------------------------------------


def test_grade_call_correct_kind():
    graded = grade_call(first_tool_use([_tool_use("search_kb", query="q", kind="notes")]), "notes")
    assert graded == {
        "tool": "search_kb",
        "passed_kind": "notes",
        "kind_passed": True,
        "kind_correct": True,
        "other_tool": False,
    }


def test_grade_call_wrong_kind_still_counts_as_passed():
    graded = grade_call(
        first_tool_use([_tool_use("search_kb", query="q", kind="libraries")]), "notes"
    )
    assert graded["kind_passed"] is True
    assert graded["kind_correct"] is False
    assert graded["other_tool"] is False


def test_grade_call_search_without_kind():
    graded = grade_call(first_tool_use([_tool_use("search_kb", query="q")]), "notes")
    assert graded["passed_kind"] is None
    assert graded["kind_passed"] is False
    assert graded["kind_correct"] is False
    assert graded["other_tool"] is False


def test_grade_call_other_tool_is_not_a_kind_pass_but_is_flagged():
    graded = grade_call(first_tool_use([_tool_use("list_projects")]), "projects")
    assert graded["tool"] == "list_projects"
    assert graded["kind_passed"] is False
    assert graded["other_tool"] is True


def test_grade_call_no_tool_call_at_all():
    graded = grade_call(None, "projects")
    assert graded["tool"] is None
    assert graded["kind_passed"] is False
    assert graded["other_tool"] is True


def test_grade_call_rejects_a_kind_outside_the_enum():
    graded = grade_call(first_tool_use([_tool_use("search_kb", query="q", kind="stuff")]), "notes")
    assert graded["passed_kind"] == "stuff"
    assert graded["kind_passed"] is False
    assert graded["kind_correct"] is False


# ---- evaluate + summarize --------------------------------------------------

_FAKE_QUERIES = [
    {"id": "q1", "kind": "projects", "query": "one"},
    {"id": "q2", "kind": "libraries", "query": "two"},
    {"id": "q3", "kind": "notes", "query": "three"},
    {"id": "q4", "kind": "notes", "query": "four", "tags": ["adversarial"]},
]

_FAKE_RESPONSES = {
    "one": [_tool_use("search_kb", query="one", kind="projects")],  # correct
    "two": [_tool_use("search_kb", query="two", kind="notes")],  # passed, wrong
    "three": [_tool_use("search_kb", query="three")],  # no kind
    "four": [_tool_use("list_projects")],  # other tool
}


def _fake_call(query: str):
    return _FAKE_RESPONSES[query]


def test_evaluate_records_per_query_grades_and_query_metadata():
    results = evaluate(_FAKE_QUERIES, _fake_call)
    assert [r["id"] for r in results] == ["q1", "q2", "q3", "q4"]
    assert [r["kind_passed"] for r in results] == [True, True, False, False]
    assert [r["kind_correct"] for r in results] == [True, False, False, False]
    assert [r["other_tool"] for r in results] == [False, False, False, True]
    assert [r["adversarial"] for r in results] == [False, False, False, True]
    assert results[0]["expected_kind"] == "projects"
    assert results[0]["query"] == "one"


def test_summarize_rate_math_and_slices():
    summary = summarize(evaluate(_FAKE_QUERIES, _fake_call))

    overall = summary["overall"]
    assert overall["n"] == 4
    assert overall["kind_pass_rate"] == pytest.approx(2 / 4)
    assert overall["kind_correct_rate"] == pytest.approx(1 / 4)
    assert overall["search_first_rate"] == pytest.approx(3 / 4)
    assert overall["other_tool"] == 1

    assert summary["projects"]["kind_correct_rate"] == 1.0
    # Wrong kind counts toward pass but not correct.
    assert summary["libraries"]["kind_pass_rate"] == 1.0
    assert summary["libraries"]["kind_correct_rate"] == 0.0
    assert summary["notes"]["n"] == 2
    assert summary["notes"]["kind_pass_rate"] == 0.0
    assert summary["adversarial"]["n"] == 1
    assert summary["adversarial"]["other_tool"] == 1
    assert summary["adversarial"]["search_first_rate"] == 0.0


def test_search_first_rate_is_the_complement_of_other_tool():
    """The tool-selection rate and the other-tool count must never disagree."""
    for m in summarize(evaluate(_FAKE_QUERIES, _fake_call)).values():
        assert m["search_first_rate"] == pytest.approx((m["n"] - m["other_tool"]) / m["n"])


def test_search_first_rate_is_an_upper_bound_on_kind_pass():
    """A kind can only be passed on a search_kb call, so kind-pass can't exceed it."""
    for m in summarize(evaluate(_FAKE_QUERIES, _fake_call)).values():
        assert m["kind_pass_rate"] <= m["search_first_rate"]
        assert m["kind_correct_rate"] <= m["kind_pass_rate"]


def test_summarize_omits_an_empty_slice():
    summary = summarize(evaluate(_FAKE_QUERIES[:1], _fake_call))
    assert "libraries" not in summary
    assert "adversarial" not in summary
    assert summary["overall"]["n"] == 1


# ---- the steering text stays wired to the schema ----------------------------


def test_search_kb_schema_still_enumerates_the_three_kinds():
    """The steering text names three kinds; the schema must still accept exactly those."""
    search = next(t for t in TOOLS if t["name"] == "search_kb")
    assert tuple(search["input_schema"]["properties"]["kind"]["enum"]) == KINDS


def test_list_projects_description_points_at_search_kb_and_claims_only_the_roster():
    """The measured fix for proj-08: list_projects must not over-claim.

    It returns name+description only, so its description has to hand off questions
    about a project's contents to search_kb. Dropping that cross-pointer regressed
    search_kb-first from 1.000 to 0.963 in the A/B, so it is worth a guard.
    """
    listing = next(t for t in TOOLS if t["name"] == "list_projects")
    description = listing["description"]
    assert "search_kb" in description
    assert "projects.yaml" in description
