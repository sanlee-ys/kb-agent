"""Integration test: the real index -> search round-trip against ChromaDB.

Unlike test_index.py (which covers the pure chunking functions) and test_tools.py
(which stubs the store), this test exercises the *real* boundary: it builds an
actual ChromaDB collection from a tiny temp corpus via scripts/index.main(), then
queries it through agent.tools.search_kb(). Write -> read -> assert, against the
engine we actually ship -- the kind of round-trip a mock can't vouch for
(embedding wiring, the metadata `kind` filter, the query contract).

It's the one test that isn't fully offline: ChromaDB's built-in embedding model
(all-MiniLM-L6-v2) is loaded here, and downloaded once (~80MB) the first time on a
given machine. That cost is why it carries the `integration` marker -- even though,
by choice, it still runs in the default suite (a test that never runs rots).

Seam: index.py and tools.py resolve their paths from module-level globals at call
time (KB_DIR/REPO_ROOT/CHROMA_DIR in index, CHROMA_DIR in tools), so we point both
at a temp store by monkeypatching those globals -- the same idiom test_index.py's
test_collect_documents already uses. No production code changes needed.
"""

from __future__ import annotations

import json

import pytest

import agent.tools as tools
import scripts.index as index


def _seed_kb(kb_dir) -> None:
    """Write a 2-doc corpus with deliberately distinct vocabulary.

    One ``projects`` doc and one ``libraries`` doc, so we can also exercise the
    metadata ``kind`` filter. The vocabularies don't overlap, which makes the top
    retrieval unambiguous and the test robust to embedding wobble.
    """
    projects = kb_dir / "projects"
    libraries = kb_dir / "libraries"
    projects.mkdir(parents=True)
    libraries.mkdir(parents=True)
    (projects / "alpha.md").write_text(
        "# Alpha\nDrone contract award: unmanned aircraft procurement and budget.",
        encoding="utf-8",
    )
    (libraries / "bravo.md").write_text(
        "# Bravo\nAircraft carrier strike group conducting naval operations at sea.",
        encoding="utf-8",
    )


def _assert_sys003(raw: str) -> dict:
    """Minimal SYS-003 observation shape check; returns the parsed dict.

    Mirrors test_tools._obs but is kept local on purpose: cross-importing a private
    helper between test modules is fragile, and hoisting it into a shared conftest
    is its own (shared-file) change. If a third caller ever needs it, promote it.
    """
    data = json.loads(raw)
    assert data["status"] in ("success", "warning", "error")
    assert isinstance(data["summary"], str) and data["summary"]
    if data["status"] == "success":
        assert "payload" in data and "source" in data
    else:
        assert isinstance(data["next_actions"], list) and data["next_actions"]
    return data


@pytest.fixture
def indexed_kb(tmp_path, monkeypatch):
    """Build a real ChromaDB index from a temp corpus and aim search_kb at it.

    Patches the path globals in both modules so index.main() writes to, and
    search_kb() reads from, the *same* temp store -- never the repo's real
    chroma_db/. REPO_ROOT must be an ancestor of the seeded files because
    collect_documents() records each source as ``relative_to(REPO_ROOT)``.
    """
    kb_dir = tmp_path / "kb"
    chroma_dir = tmp_path / "chroma_db"
    _seed_kb(kb_dir)

    monkeypatch.setattr(index, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(index, "KB_DIR", kb_dir)
    monkeypatch.setattr(index, "CHROMA_DIR", chroma_dir)
    index.main()  # real embeddings -> real collection on disk

    monkeypatch.setattr(tools, "CHROMA_DIR", chroma_dir)
    return chroma_dir


@pytest.mark.integration
def test_search_kb_round_trip_finds_indexed_doc(indexed_kb):
    """A query matching the alpha doc's vocabulary returns it as the top hit."""
    data = _assert_sys003(tools.search_kb("drone contract procurement"))
    assert data["status"] == "success"
    assert data["payload"], "expected at least one matching chunk"
    assert "alpha" in data["payload"][0]["source"]


@pytest.mark.integration
def test_search_kb_kind_filter_scopes_results(indexed_kb):
    """The metadata ``kind`` filter restricts results to the matching folder."""
    data = _assert_sys003(tools.search_kb("naval operations", kind="libraries"))
    assert data["status"] == "success"
    sources = [chunk["source"] for chunk in data["payload"]]
    assert sources and all("bravo" in src for src in sources)
    assert not any("alpha" in src for src in sources)
