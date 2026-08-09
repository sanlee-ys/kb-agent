"""Integrity tests for eval/compositional_set.yaml — the multi-hop corpus.

Static and offline: this reads the YAML and the files it names. No index, no
model calls, no network — the corpus is inert data and checking it spends
nothing.

PR #90 authored the corpus and hand-verified these invariants with a scratchpad
script that was never committed, so nothing stopped a later edit from quietly
dissolving the property the file exists to have. That property is the one in
`compositional_set.yaml:22-25`: **every query needs two or more source files
spanning two or more kinds.** Lose it and the corpus decays into another
single-hop set, scoring the same on a one-call design and a multi-node one —
which is the failure `eval/gold_set.yaml` already has and this file was built
to escape. These tests assert that instead of restating it in a comment.
"""

from __future__ import annotations

from collections import Counter

import pytest

from scripts.eval_retrieval import REPO_ROOT, load_gold_set
from scripts.index import notes_dirs

COMPOSITIONAL_SET = REPO_ROOT / "eval" / "compositional_set.yaml"

KINDS = ("projects", "libraries", "notes")

# Exactly one shape tag per query (compositional_set.yaml §5). `three-source`
# is a separate, orthogonal marker — see test_tags_agree_with_source_counts.
SHAPE_TAGS = frozenset(
    {"library-in-context", "concept-to-instance", "concept-to-library", "three-kind"}
)


@pytest.fixture(scope="module")
def queries() -> list[dict]:
    """The compositional corpus.

    `load_gold_set` is just a `queries:` YAML reader, and this file shares that
    top-level shape, so it loads the corpus without a second reader. The
    *entries* differ (`kinds` list vs `kind` scalar) — which is why
    eval_retrieval.py's scoring path cannot consume this file, by design.
    """
    return load_gold_set(COMPOSITIONAL_SET)


@pytest.fixture(scope="module")
def notes_clone():
    """The learning-notes checkout, or None when this machine has no clone.

    Resolved the way scripts/index.py resolves it (KB_AGENT_NOTES_DIRS, else
    projects.yaml `notes_dirs`) so this test agrees with what actually gets
    indexed rather than guessing a sibling path. A note's `source` is
    `<dir name>/<file>.md`, so the directory named `learning-notes` is the one
    these labels resolve against (ADR-012).
    """
    for directory in notes_dirs()[0]:
        if directory.name == "learning-notes" and directory.is_dir():
            return directory
    return None


def _kind_of(source: str) -> str:
    """The `kind` index.py will stamp on this source's chunks.

    kb/ sources take the kind from their parent directory; everything outside
    the repo is a note.
    """
    if source.startswith("kb/"):
        return source.split("/")[1]
    return "notes"


# ---- the corpus is the size and shape it says it is ------------------------


def test_corpus_size_and_ids_are_unique(queries):
    assert len(queries) == 61

    ids = [q["id"] for q in queries]
    assert len(ids) == len(set(ids)), "duplicate query ids"

    texts = [q["query"] for q in queries]
    assert len(texts) == len(set(texts)), "duplicate query strings"


def test_schema_uses_kinds_not_kind(queries):
    """The deliberate schema break from the gold set (§4, difference 1).

    A compositional query spans kinds, so a single `kind` scalar would be false
    on every entry. eval_retrieval.py reads `q["kind"]` and therefore fails
    loudly if pointed here — that is the intended guard, not a bug, so this
    asserts the scalar stays absent.
    """
    for q in queries:
        assert "kind" not in q, f"{q['id']}: has a scalar `kind`; this corpus uses `kinds`"
        assert isinstance(q["kinds"], list), f"{q['id']}: `kinds` must be a list"


# ---- the defining property: multi-source, multi-kind ------------------------


def test_every_query_is_multi_hop(queries):
    """The property the whole corpus exists to have (§1).

    If this fails the file has silently become as undiscriminating as the
    frozen 27, and any number scored against it means less than it appears to.
    """
    for q in queries:
        sources = q["expected_sources"]
        assert len(sources) >= 2, f"{q['id']}: single-source query does not belong here"
        assert len(set(sources)) == len(sources), f"{q['id']}: duplicate expected source"

        spanned = {_kind_of(s) for s in sources}
        assert len(spanned) >= 2, f"{q['id']}: all sources in one kind ({spanned})"


def test_kinds_list_matches_the_source_paths(queries):
    """The `kinds` label is derivable from the sources — so it must agree."""
    for q in queries:
        derived = {_kind_of(s) for s in q["expected_sources"]}
        assert derived <= set(KINDS), f"{q['id']}: odd kind in {derived}"
        assert derived == set(q["kinds"]), (
            f"{q['id']}: `kinds` says {sorted(set(q['kinds']))} "
            f"but the sources span {sorted(derived)}"
        )


# ---- tags stay consistent with what they label ------------------------------


def test_tags_agree_with_source_counts(queries):
    """Shape tag, `three-source`, and `three-kind` all mean what they say.

    `three-source` counts files; `three-kind` counts kind slices. They are
    different claims — 12 queries carry the first, 5 the second — so each is
    checked against the thing it actually describes.
    """
    for q in queries:
        tags = q.get("tags", [])
        sources = q["expected_sources"]

        shape = SHAPE_TAGS.intersection(tags)
        assert len(shape) == 1, f"{q['id']}: expected exactly one shape tag, got {sorted(shape)}"

        assert len(sources) in (2, 3), f"{q['id']}: {len(sources)} sources; corpus holds 2 or 3"
        assert ("three-source" in tags) == (len(sources) == 3), (
            f"{q['id']}: `three-source` tag disagrees with {len(sources)} sources"
        )
        assert ("three-kind" in tags) == (len(set(q["kinds"])) == 3), (
            f"{q['id']}: `three-kind` tag disagrees with kinds {sorted(set(q['kinds']))}"
        )


def test_composition_matches_the_documented_counts(queries):
    """§5's composition table, asserted rather than listed (SYS-019).

    These exact counts are what the header tells a reader the corpus is. If an
    edit moves one, the header is now wrong somewhere too, and the edit should
    say so deliberately.
    """
    by_shape = Counter(tag for q in queries for tag in q.get("tags", []) if tag in SHAPE_TAGS)
    assert by_shape == {
        "library-in-context": 19,
        "concept-to-instance": 20,
        "concept-to-library": 17,
        "three-kind": 5,
    }

    by_count = Counter(len(q["expected_sources"]) for q in queries)
    assert by_count == {2: 49, 3: 12}

    # 37 of the 44 indexed files appear as an expected source (§5).
    distinct = {s for q in queries for s in q["expected_sources"]}
    assert len(distinct) == 37


# ---- every labelled source is a real file -----------------------------------


def test_kb_sources_exist_and_notes_are_well_formed(queries):
    """kb/ sources are committed here, so they must exist.

    learning-notes/ sources live outside the repo — format-checked here, the
    same split tests/test_eval_retrieval.py makes for the gold set. Their
    existence is a separate test so a machine without the clone skips that
    check instead of failing this one.
    """
    for q in queries:
        assert q["expected_sources"], f"{q['id']} has no expected sources"
        for src in q["expected_sources"]:
            if src.startswith("kb/"):
                assert (REPO_ROOT / src).exists(), f"{q['id']}: {src} not found"
            else:
                assert src.startswith("learning-notes/"), f"{q['id']}: odd source {src}"


def test_notes_sources_exist_when_the_clone_is_present(queries, notes_clone):
    """The half the gold-set test cannot do — run only where it is runnable.

    CI clones learning-notes and points KB_AGENT_NOTES_DIRS at it, so this does
    run there. A workstation without the clone skips: a test that fails for
    lacking a sibling checkout is worse than no test.
    """
    if notes_clone is None:
        pytest.skip("no learning-notes clone configured; format-check only")

    for q in queries:
        for src in q["expected_sources"]:
            if src.startswith("learning-notes/"):
                # source is `<dir name>/<file>.md` — strip the dir, resolve the rest.
                relative = src.split("/", 1)[1]
                assert (notes_clone / relative).exists(), f"{q['id']}: {src} not found"
