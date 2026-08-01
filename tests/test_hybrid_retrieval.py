"""Tests for search_kb's hybrid dense+BM25 retrieval path (kb-agent/ADR-010).

All offline and all deterministic: the dense leg is a canned ranking from a fake
collection, so nothing here loads an embedding model or touches chroma_db/. What
is under test is the *fusion*, not ChromaDB — the pure RRF function, the lexical
leg's filtering and cutoff rules, the index cache's invalidation, and the fact
that the returned observation shape is byte-identical between the two modes.

The fake collection implements only the two methods search_kb calls: ``query``
(canned dense order, kind-filtered) and ``get`` (the real corpus, which the BM25
leg is built from).
"""

from __future__ import annotations

import json

import pytest

import agent.tools as tools
from agent.tools import reciprocal_rank_fusion

# A four-chunk corpus where dense and lexical deliberately disagree.
CORPUS = [
    (
        "projects/alpha#0",
        "The alpha project emits a SYS-003 observation from every tool it exposes.",
        {"source": "kb/projects/alpha.md", "kind": "projects", "name": "alpha"},
    ),
    (
        "notes/beta#0",
        "Tool results, and how an agent recovers when a call fails.",
        {"source": "notes/beta.md", "kind": "notes", "name": "beta"},
    ),
    (
        "libraries/gamma#0",
        "gamma ranks documents by BM25 term scoring.",
        {"source": "kb/libraries/gamma.md", "kind": "libraries", "name": "gamma"},
    ),
    (
        "notes/delta#0",
        "Unrelated content about database migrations and rollbacks.",
        {"source": "notes/delta.md", "kind": "notes", "name": "delta"},
    ),
]

# The dense leg's fixed opinion, chosen so it disagrees with BM25 on both of the
# queries used below — otherwise fusion would be untestable through search_kb.
DENSE_ORDER = ["notes/delta#0", "projects/alpha#0", "notes/beta#0", "libraries/gamma#0"]


class FakeCollection:
    """Stand-in for a ChromaDB collection with a canned dense ranking."""

    def __init__(self, corpus=CORPUS, dense_order=DENSE_ORDER):
        self.ids = [c[0] for c in corpus]
        self.documents = [c[1] for c in corpus]
        self.metadatas = [c[2] for c in corpus]
        self.dense_order = dense_order
        self.by_id = {c[0]: (c[1], c[2]) for c in corpus}

    def get(self, include=None):
        return {"ids": self.ids, "documents": self.documents, "metadatas": self.metadatas}

    def query(self, query_texts, n_results, where=None):
        picked = [
            cid
            for cid in self.dense_order
            if where is None or self.by_id[cid][1]["kind"] == where["kind"]
        ][:n_results]
        return {
            "ids": [picked],
            "documents": [[self.by_id[cid][0] for cid in picked]],
            "metadatas": [[self.by_id[cid][1] for cid in picked]],
        }


@pytest.fixture
def fake_kb(monkeypatch):
    """Aim search_kb at a fake collection and clear the BM25 cache around it."""
    collection = FakeCollection()
    monkeypatch.setattr(tools, "_get_collection", lambda: collection)
    monkeypatch.setattr(tools, "_LEXICAL_CACHE", None)
    yield collection
    tools._LEXICAL_CACHE = None


def _sources(raw: str) -> list[str]:
    """Ordered chunk sources from a successful search_kb observation."""
    data = json.loads(raw)
    assert data["status"] == "success", data
    return [chunk["source"] for chunk in data["payload"]]


# --- reciprocal_rank_fusion (pure) -------------------------------------------


def test_rrf_single_leg_is_a_passthrough():
    assert reciprocal_rank_fusion([["a", "b", "c"]]) == ["a", "b", "c"]


def test_rrf_no_legs_or_empty_legs_is_empty():
    assert reciprocal_rank_fusion([]) == []
    assert reciprocal_rank_fusion([[], []]) == []


def test_rrf_consistent_mid_ranks_beat_a_single_first_place():
    # The defining RRF property: agreement across legs outweighs one leg's top
    # pick. "b" is 2nd in both legs (1/62 + 1/62); "a" is 1st in one only (1/61).
    assert reciprocal_rank_fusion([["a", "b"], ["c", "b"]])[0] == "b"


def test_rrf_keeps_ids_seen_in_only_one_leg():
    fused = reciprocal_rank_fusion([["a"], ["b"]])
    assert sorted(fused) == ["a", "b"]


def test_rrf_ties_break_by_first_appearance():
    # "a" and "b" both score 1/61 + 1/62; order must follow the first leg.
    assert reciprocal_rank_fusion([["a", "b"], ["b", "a"]]) == ["a", "b"]


def test_rrf_k_damping_is_applied():
    # k sets how much a top rank outweighs agreement further down. "a" is 1st in
    # one leg, "b" is 4th in both. At the default k=60 ranks are compressed and
    # agreement wins (1/61 vs 2/64); at k=1 the rank-1 advantage wins (1/2 vs 2/5).
    legs = [["a", "p", "q", "b"], ["c", "r", "s", "b"]]
    assert reciprocal_rank_fusion(legs)[0] == "b"
    assert reciprocal_rank_fusion(legs, k=1)[0] == "a"


# --- the lexical leg ----------------------------------------------------------


def test_lexical_leg_ranks_the_term_match_first(fake_kb):
    index = tools._lexical_index(fake_kb)
    assert tools._lexical_ranking(index, "SYS-003 observation", None, 5) == [
        "projects/alpha#0"
    ]


def test_lexical_leg_drops_zero_score_chunks(fake_kb):
    # Only one chunk contains any of these terms, so the ranking must be length 1
    # rather than the whole corpus in arbitrary order.
    index = tools._lexical_index(fake_kb)
    assert len(tools._lexical_ranking(index, "migrations rollbacks", None, 5)) == 1


def test_lexical_leg_returns_nothing_when_no_term_matches(fake_kb):
    index = tools._lexical_index(fake_kb)
    assert tools._lexical_ranking(index, "zzzznonexistentterm", None, 5) == []


def test_lexical_leg_honors_the_kind_filter(fake_kb):
    index = tools._lexical_index(fake_kb)
    # The only lexical match for this query is a `projects` chunk.
    assert tools._lexical_ranking(index, "SYS-003 observation", "notes", 5) == []
    assert tools._lexical_ranking(index, "SYS-003 observation", "projects", 5) == [
        "projects/alpha#0"
    ]


def test_lexical_leg_respects_the_limit(fake_kb):
    index = tools._lexical_index(fake_kb)
    assert len(tools._lexical_ranking(index, "tool results agent", None, 1)) == 1


def test_lexical_index_is_cached_and_invalidated_by_content(fake_kb):
    first = tools._lexical_index(fake_kb)
    assert tools._lexical_index(fake_kb) is first  # same corpus -> same object

    # Same chunk count, different text: the fingerprint must still notice.
    fake_kb.documents[0] = "Completely different alpha text about naval logistics."
    rebuilt = tools._lexical_index(fake_kb)
    assert rebuilt is not first
    assert rebuilt.fingerprint != first.fingerprint


def test_lexical_index_is_none_for_an_empty_collection(monkeypatch):
    monkeypatch.setattr(tools, "_LEXICAL_CACHE", None)
    assert tools._lexical_index(FakeCollection(corpus=[], dense_order=[])) is None


# --- search_kb end to end -----------------------------------------------------


def test_hybrid_reorders_what_dense_alone_returns(fake_kb):
    # Dense puts delta first; BM25 puts alpha first; fusion must promote alpha.
    assert _sources(tools.search_kb("SYS-003 observation", hybrid=False))[0] == (
        "notes/delta.md"
    )
    assert _sources(tools.search_kb("SYS-003 observation", hybrid=True))[0] == (
        "kb/projects/alpha.md"
    )


def test_default_mode_follows_the_module_constant(fake_kb, monkeypatch):
    monkeypatch.setattr(tools, "HYBRID_RETRIEVAL", False)
    assert _sources(tools.search_kb("SYS-003 observation"))[0] == "notes/delta.md"
    monkeypatch.setattr(tools, "HYBRID_RETRIEVAL", True)
    assert _sources(tools.search_kb("SYS-003 observation"))[0] == "kb/projects/alpha.md"


def test_hybrid_kind_filter_excludes_other_kinds_from_both_legs(fake_kb):
    # alpha is the strongest lexical match but is a `projects` chunk: with
    # kind="notes" it must not leak in through the lexical leg.
    sources = _sources(tools.search_kb("SYS-003 observation", kind="notes", hybrid=True))
    assert sources
    assert "kb/projects/alpha.md" not in sources
    assert set(sources) <= {"notes/beta.md", "notes/delta.md"}


def test_hybrid_still_reorders_within_a_kind_filter(fake_kb):
    # Inside kind="notes", dense ranks delta above beta; BM25 matches beta only.
    assert _sources(tools.search_kb("recovers when a call fails", kind="notes"))[0] == (
        "notes/delta.md"
    )
    assert _sources(
        tools.search_kb("recovers when a call fails", kind="notes", hybrid=True)
    )[0] == "notes/beta.md"


def test_hybrid_respects_n_results(fake_kb):
    assert len(_sources(tools.search_kb("tool results", n_results=2, hybrid=True))) == 2


def test_hybrid_keeps_the_sys003_chunk_shape(fake_kb):
    data = json.loads(tools.search_kb("SYS-003 observation", hybrid=True))
    assert data["status"] == "success"
    assert data["source"] == [chunk["source"] for chunk in data["payload"]]
    for chunk in data["payload"]:
        assert set(chunk) == {"source", "text"}
        assert chunk["text"]


def test_hybrid_falls_back_to_dense_when_the_corpus_is_empty(monkeypatch):
    empty = FakeCollection(corpus=[], dense_order=[])
    monkeypatch.setattr(tools, "_get_collection", lambda: empty)
    monkeypatch.setattr(tools, "_LEXICAL_CACHE", None)
    data = json.loads(tools.search_kb("anything", hybrid=True))
    assert data["status"] == "warning"
    assert data["next_actions"]
