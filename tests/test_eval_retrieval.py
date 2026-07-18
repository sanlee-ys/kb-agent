"""Tests for scripts/eval_retrieval.py — metric math and gold-set integrity.

All offline: the retrieval calls are faked, so no ChromaDB, no API key.
"""

from __future__ import annotations

import json

import pytest

from scripts.eval_retrieval import (
    K_VALUES,
    evaluate,
    load_gold_set,
    rank_of_first_hit,
    returned_sources,
    summarize,
)


def _observation(sources: list[str], status: str = "success") -> str:
    if status == "success":
        return json.dumps(
            {"status": status, "payload": [{"source": s, "text": "…"} for s in sources]}
        )
    return json.dumps({"status": status, "summary": "kb not indexed"})


# ---- unit pieces -----------------------------------------------------------


def test_returned_sources_normalizes_windows_separators():
    obs = _observation(["kb\\projects\\notes-api.md", "learning-notes/06-rag.md"])
    assert returned_sources(obs) == [
        "kb/projects/notes-api.md",
        "learning-notes/06-rag.md",
    ]


def test_returned_sources_refuses_non_success():
    with pytest.raises(RuntimeError, match="kb not indexed"):
        returned_sources(_observation([], status="error"))


def test_rank_of_first_hit():
    sources = ["a.md", "b.md", "b.md", "c.md"]
    assert rank_of_first_hit(sources, {"a.md"}) == 1
    assert rank_of_first_hit(sources, {"c.md"}) == 4  # chunk position, not file position
    assert rank_of_first_hit(sources, {"z.md"}) is None
    assert rank_of_first_hit(sources, {"z.md", "b.md"}) == 2


# ---- evaluate + summarize --------------------------------------------------

_FAKE_QUERIES = [
    {"id": "q1", "kind": "projects", "query": "one", "expected_sources": ["kb/projects/a.md"]},
    {"id": "q2", "kind": "notes", "query": "two", "expected_sources": ["learning-notes/n.md"]},
    {
        "id": "q3",
        "kind": "notes",
        "query": "three",
        "tags": ["adversarial"],
        "expected_sources": ["learning-notes/n.md"],
    },
]

_FAKE_RETURNS = {
    "one": ["kb\\projects\\a.md", "x", "y", "z", "w"],  # rank 1 (and OS separators)
    "two": ["x", "y", "learning-notes/n.md", "z", "w"],  # rank 3
    "three": ["x", "y", "z", "w", "v"],  # miss
}


def _fake_search(query: str, kind: str | None, n_results: int) -> str:
    assert n_results == max(K_VALUES)
    return _observation(_FAKE_RETURNS[query])


def test_evaluate_and_summarize_metric_math():
    results = evaluate(_FAKE_QUERIES, search_fn=_fake_search)
    assert [r["rank"] for r in results] == [1, 3, None]

    summary = summarize(results)
    overall = summary["overall"]
    assert overall["n"] == 3
    assert overall["recall@1"] == pytest.approx(1 / 3)
    assert overall["recall@3"] == pytest.approx(2 / 3)
    assert overall["recall@5"] == pytest.approx(2 / 3)
    assert overall["mrr"] == pytest.approx((1 + 1 / 3 + 0) / 3)

    assert summary["projects"]["recall@1"] == 1.0
    assert summary["adversarial"]["n"] == 1
    assert summary["adversarial"]["mrr"] == 0.0
    # No libraries queries in the fake set → slice omitted, not divided by zero.
    assert "libraries" not in summary


def test_evaluate_kind_filter_passthrough():
    seen: list[str | None] = []

    def spy(query: str, kind: str | None, n_results: int) -> str:
        seen.append(kind)
        return _observation(_FAKE_RETURNS[query])

    evaluate(_FAKE_QUERIES, search_fn=spy, kind_filter=False)
    assert seen == [None, None, None]
    seen.clear()
    evaluate(_FAKE_QUERIES, search_fn=spy, kind_filter=True)
    assert seen == ["projects", "notes", "notes"]


# ---- the real gold set stays well-formed -----------------------------------


def test_gold_set_matches_settled_scope():
    queries = load_gold_set()
    assert len(queries) == 27

    ids = [q["id"] for q in queries]
    assert len(ids) == len(set(ids)), "duplicate query ids"

    adversarial = [q for q in queries if "adversarial" in q.get("tags", [])]
    assert len(adversarial) == 4
    plain = [q for q in queries if q not in adversarial]
    kinds = ("projects", "libraries", "notes")
    by_kind = {k: sum(1 for q in plain if q["kind"] == k) for k in kinds}
    assert by_kind == {"projects": 8, "libraries": 5, "notes": 10}

    from scripts.eval_retrieval import REPO_ROOT

    for q in queries:
        assert q["kind"] in ("projects", "libraries", "notes")
        assert q["expected_sources"], f"{q['id']} has no expected sources"
        for src in q["expected_sources"]:
            # kb/ sources live in this repo — they must exist (CI checks this).
            # learning-notes/ sources live outside the repo; format-check only.
            if src.startswith("kb/"):
                assert (REPO_ROOT / src).exists(), f"{q['id']}: {src} not found"
            else:
                assert src.startswith("learning-notes/"), f"{q['id']}: odd source {src}"
