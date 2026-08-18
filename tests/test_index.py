"""Tests for the chunking/collection logic in scripts/index.py.

These cover the pure functions; main() (which writes to ChromaDB) is left to the
manual smoke run.
"""

from __future__ import annotations

import os
from pathlib import Path

import httpx
import pytest

import scripts.index as index
from scripts.index import (
    MAX_CHUNK_CHARS,
    NOTES_DIRS_ENV,
    chunk_markdown,
    plan_index_update,
)


@pytest.fixture(autouse=True)
def _no_ambient_notes_override(monkeypatch):
    """Never let a real KB_AGENT_NOTES_DIRS leak into these tests."""
    monkeypatch.delenv(NOTES_DIRS_ENV, raising=False)


def test_chunk_markdown_splits_on_headings():
    text = "# A\nalpha\n## B\nbeta\n## C\ngamma"
    chunks = chunk_markdown(text)
    assert len(chunks) == 3
    assert chunks[0] == "[Document: A]\n# A\nalpha"
    assert chunks[1] == "[Document: A > B]\n## B\nbeta"
    assert chunks[2] == "[Document: A > C]\n## C\ngamma"


def test_chunk_markdown_prepends_parent_header_paths():
    text = (
        "# kb-agent\nintro\n## Section 1\nbody 1\n### Subsection 1.1\ndetail\n## Section 2\nbody 2"
    )
    chunks = chunk_markdown(text, doc_title="kb-agent.md")
    assert len(chunks) == 4
    assert chunks[0] == "[Document: kb-agent.md]\n# kb-agent\nintro"
    assert chunks[1] == "[Document: kb-agent.md > Section 1]\n## Section 1\nbody 1"
    assert (
        chunks[2]
        == "[Document: kb-agent.md > Section 1 > Subsection 1.1]\n### Subsection 1.1\ndetail"
    )
    assert chunks[3] == "[Document: kb-agent.md > Section 2]\n## Section 2\nbody 2"


def test_chunk_markdown_caps_long_sections():
    # One heading followed by a body far larger than the char budget.
    body = "\n".join(["a line of text"] * 500)
    chunks = chunk_markdown(f"# Big\n{body}")
    assert len(chunks) > 1
    # No single chunk should be wildly over budget (allow one line of slack).
    assert all(len(c) <= MAX_CHUNK_CHARS + 100 for c in chunks)


def test_chunk_markdown_empty_and_blank():
    assert chunk_markdown("") == []
    assert chunk_markdown("\n\n   \n") == []  # whitespace-only -> nothing


def test_collect_documents_builds_parallel_arrays(tmp_path, monkeypatch):
    projects = tmp_path / "projects"
    projects.mkdir()
    (projects / "foo.md").write_text("# Foo\nhello", encoding="utf-8")
    libraries = tmp_path / "libraries"
    libraries.mkdir()
    (libraries / "bar.md").write_text("# Bar\nworld", encoding="utf-8")

    monkeypatch.setattr(index, "KB_DIR", tmp_path)
    monkeypatch.setattr(index, "REPO_ROOT", tmp_path)

    # Stub out the API call so collect_documents doesn't hit the network.
    monkeypatch.setattr(index, "collect_notes_from_api", lambda: ([], [], []))

    documents, metadatas, ids = index.collect_documents()

    assert len(documents) == len(metadatas) == len(ids) == 2
    assert {m["kind"] for m in metadatas} == {"projects", "libraries"}
    assert {m["name"] for m in metadatas} == {"foo", "bar"}
    assert len(set(ids)) == 2  # ids are unique
    assert all("#" in i for i in ids)  # ids follow "kind/name#i"


def test_is_note_scaffolding():
    notes_dir = Path("/notes")
    # Scaffolding by filename, case-insensitively (Windows filesystems vary).
    assert index.is_note_scaffolding(notes_dir / "README.md", notes_dir)
    assert index.is_note_scaffolding(notes_dir / "CLAUDE.md", notes_dir)
    assert index.is_note_scaffolding(notes_dir / "readme.md", notes_dir)
    # Scaffolding by directory, at any depth.
    assert index.is_note_scaffolding(
        notes_dir / "graphify-out" / "2026-07-19" / "GRAPH_REPORT.md", notes_dir
    )
    # Real notes pass through, including in subdirectories.
    assert not index.is_note_scaffolding(notes_dir / "07-embeddings.md", notes_dir)
    assert not index.is_note_scaffolding(notes_dir / "glossary.md", notes_dir)
    assert not index.is_note_scaffolding(notes_dir / "deep" / "note.md", notes_dir)


def test_collect_documents_filters_notes_scaffolding(tmp_path, monkeypatch):
    """The notes_dirs sweep indexes notes but not repo scaffolding."""
    kb = tmp_path / "kb"
    kb.mkdir()
    notes = tmp_path / "my-notes"
    (notes / "graphify-out" / "2026-07-19").mkdir(parents=True)
    (notes / "01-real-note.md").write_text("# Real\ncontent", encoding="utf-8")
    (notes / "README.md").write_text("# Front door", encoding="utf-8")
    (notes / "CLAUDE.md").write_text("# Steering", encoding="utf-8")
    (notes / "graphify-out" / "2026-07-19" / "GRAPH_REPORT.md").write_text(
        "# Generated", encoding="utf-8"
    )
    (tmp_path / "projects.yaml").write_text(
        f"projects: []\nnotes_dirs:\n  - {notes}\n", encoding="utf-8"
    )

    monkeypatch.setattr(index, "KB_DIR", kb)
    monkeypatch.setattr(index, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(index, "collect_notes_from_api", lambda: ([], [], []))

    documents, metadatas, ids = index.collect_documents()

    assert [m["source"] for m in metadatas] == ["my-notes/01-real-note.md"]
    assert all(m["kind"] == "notes" for m in metadatas)


# --- notes_dirs: the corpus-provenance seam (ADR-012) -----------------------


def test_notes_dirs_reads_projects_yaml(tmp_path, monkeypatch):
    (tmp_path / "projects.yaml").write_text(
        f"projects: []\nnotes_dirs:\n  - {tmp_path / 'a'}\n", encoding="utf-8"
    )
    monkeypatch.setattr(index, "REPO_ROOT", tmp_path)

    dirs, origin = index.notes_dirs()
    assert dirs == [tmp_path / "a"]
    assert "projects.yaml" in origin


def test_notes_dirs_missing_projects_yaml(tmp_path, monkeypatch):
    monkeypatch.setattr(index, "REPO_ROOT", tmp_path)
    dirs, _ = index.notes_dirs()
    assert dirs == []


def test_notes_dirs_env_overrides_projects_yaml(tmp_path, monkeypatch):
    """The override wins outright — it does not merge with projects.yaml."""
    (tmp_path / "projects.yaml").write_text(
        "projects: []\nnotes_dirs:\n  - C:\\Users\\someone\\code\\learning-notes\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(index, "REPO_ROOT", tmp_path)
    monkeypatch.setenv(NOTES_DIRS_ENV, str(tmp_path / "clone"))

    dirs, origin = index.notes_dirs()
    assert dirs == [tmp_path / "clone"]
    assert NOTES_DIRS_ENV in origin


def test_notes_dirs_env_accepts_several_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(index, "REPO_ROOT", tmp_path)
    monkeypatch.setenv(
        NOTES_DIRS_ENV, os.pathsep.join([str(tmp_path / "one"), str(tmp_path / "two")])
    )

    dirs, _ = index.notes_dirs()
    assert dirs == [tmp_path / "one", tmp_path / "two"]


def test_notes_dirs_empty_env_means_no_notes(tmp_path, monkeypatch):
    """An empty override is a real answer ('index no notes'), not a fallback."""
    (tmp_path / "projects.yaml").write_text(
        f"projects: []\nnotes_dirs:\n  - {tmp_path / 'a'}\n", encoding="utf-8"
    )
    monkeypatch.setattr(index, "REPO_ROOT", tmp_path)
    monkeypatch.setenv(NOTES_DIRS_ENV, "")

    dirs, origin = index.notes_dirs()
    assert dirs == []
    assert NOTES_DIRS_ENV in origin


def test_collect_documents_raises_on_missing_notes_dir(tmp_path, monkeypatch):
    """A configured-but-absent corpus fails the run; it is never skipped.

    This is the liveness clause: skipping produced an index quietly missing the
    notes corpus, which an eval would then score as bad retrieval while CI
    stayed green (system/SYS-017 §3, kb-agent/ADR-012).
    """
    kb = tmp_path / "kb"
    kb.mkdir()
    (kb / "projects").mkdir()
    (kb / "projects" / "foo.md").write_text("# Foo\nhello", encoding="utf-8")
    monkeypatch.setattr(index, "KB_DIR", kb)
    monkeypatch.setattr(index, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(index, "collect_notes_from_api", lambda: ([], [], []))
    monkeypatch.setenv(NOTES_DIRS_ENV, str(tmp_path / "not-there"))

    with pytest.raises(FileNotFoundError) as excinfo:
        index.collect_documents()

    message = str(excinfo.value)
    assert "not-there" in message
    # The error must name the knob that set the path, or the reader has to guess
    # between projects.yaml and the environment.
    assert NOTES_DIRS_ENV in message


def test_collect_documents_missing_notes_dir_from_yaml_names_yaml(tmp_path, monkeypatch):
    kb = tmp_path / "kb"
    kb.mkdir()
    (tmp_path / "projects.yaml").write_text(
        f"projects: []\nnotes_dirs:\n  - {tmp_path / 'gone'}\n", encoding="utf-8"
    )
    monkeypatch.setattr(index, "KB_DIR", kb)
    monkeypatch.setattr(index, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(index, "collect_notes_from_api", lambda: ([], [], []))

    with pytest.raises(FileNotFoundError, match="projects.yaml"):
        index.collect_documents()


# --- plan_index_update (incremental diff) ---


def test_plan_index_update_new_ids_are_upserted():
    upsert, delete = plan_index_update(["a#0", "b#0"], ["alpha", "beta"], {})
    assert set(upsert) == {"a#0", "b#0"}
    assert delete == []


def test_plan_index_update_unchanged_ids_are_skipped():
    existing = {"a#0": "alpha", "b#0": "beta"}
    upsert, delete = plan_index_update(["a#0", "b#0"], ["alpha", "beta"], existing)
    assert upsert == []
    assert delete == []


def test_plan_index_update_changed_text_is_upserted():
    existing = {"a#0": "alpha", "b#0": "beta"}
    upsert, delete = plan_index_update(["a#0", "b#0"], ["ALPHA v2", "beta"], existing)
    assert upsert == ["a#0"]  # only the changed one
    assert delete == []


def test_plan_index_update_missing_desired_ids_are_deleted():
    # "b#0" was indexed before but is no longer desired (file deleted/renamed).
    existing = {"a#0": "alpha", "b#0": "beta"}
    upsert, delete = plan_index_update(["a#0"], ["alpha"], existing)
    assert upsert == []
    assert delete == ["b#0"]


def test_plan_index_update_combined():
    existing = {"keep#0": "same", "change#0": "old", "gone#0": "stale"}
    desired_ids = ["keep#0", "change#0", "new#0"]
    desired_docs = ["same", "new text", "fresh"]
    upsert, delete = plan_index_update(desired_ids, desired_docs, existing)
    assert set(upsert) == {"change#0", "new#0"}  # changed + brand-new
    assert delete == ["gone#0"]  # removed source


# --- collect_notes_from_api ---


def _notes_yaml(tmp_path, endpoint: str) -> None:
    """Write a minimal projects.yaml with the given notes-api endpoint."""
    (tmp_path / "projects.yaml").write_text(
        f"projects:\n  - name: notes-api\n    endpoint: {endpoint}\n",
        encoding="utf-8",
    )


def test_collect_notes_from_api_no_projects_yaml(tmp_path, monkeypatch):
    """Returns empty gracefully when projects.yaml doesn't exist."""
    monkeypatch.setattr(index, "REPO_ROOT", tmp_path)
    docs, metas, ids = index.collect_notes_from_api()
    assert docs == [] and metas == [] and ids == []


def test_collect_notes_from_api_no_notes_api_entry(tmp_path, monkeypatch):
    """Returns empty when notes-api is not listed in projects.yaml."""
    (tmp_path / "projects.yaml").write_text("projects: []\n", encoding="utf-8")
    monkeypatch.setattr(index, "REPO_ROOT", tmp_path)
    docs, metas, ids = index.collect_notes_from_api()
    assert docs == [] and metas == [] and ids == []


def test_collect_notes_from_api_connection_error(tmp_path, monkeypatch):
    """Returns empty gracefully when the notes-api is unreachable."""
    _notes_yaml(tmp_path, "http://localhost:8081")
    monkeypatch.setattr(index, "REPO_ROOT", tmp_path)

    def _fail(*a, **k):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(index.httpx, "get", _fail)
    docs, metas, ids = index.collect_notes_from_api()
    assert docs == [] and metas == [] and ids == []


def test_collect_notes_from_api_indexes_notes(tmp_path, monkeypatch):
    """Notes from the API are chunked and indexed with kind='notes'."""
    _notes_yaml(tmp_path, "http://localhost:8081")
    monkeypatch.setattr(index, "REPO_ROOT", tmp_path)

    class _FakeResp:
        def raise_for_status(self) -> None:
            pass

        def json(self):
            return [
                {
                    "id": 1,
                    "title": "F-35 Contract",
                    "content": "DoD awarded a $4.2B contract for 24 F-35 fighters.",
                    "tags": ["category:procurement", "domain:air"],
                },
            ]

    monkeypatch.setattr(index.httpx, "get", lambda *a, **k: _FakeResp())
    docs, metas, ids = index.collect_notes_from_api()

    assert len(docs) >= 1
    assert all(m["kind"] == "notes" for m in metas)
    assert any("F-35" in d for d in docs)
    assert ids[0].startswith("notes/api/1#")
    # Tags should be embedded in the chunk text so they're searchable.
    full_text = " ".join(docs)
    assert "category:procurement" in full_text


def test_collect_notes_from_api_tags_in_chunk(tmp_path, monkeypatch):
    """Classifier-written tags appear in the indexed text so they're searchable."""
    _notes_yaml(tmp_path, "http://localhost:8081")
    monkeypatch.setattr(index, "REPO_ROOT", tmp_path)

    class _FakeResp:
        def raise_for_status(self) -> None:
            pass

        def json(self):
            return [
                {
                    "id": 7,
                    "title": "Cyber Budget",
                    "content": "Senate approves cyber budget increase.",
                    "tags": ["category:policy", "domain:cyber", "urgent"],
                },
            ]

    monkeypatch.setattr(index.httpx, "get", lambda *a, **k: _FakeResp())
    docs, metas, ids = index.collect_notes_from_api()

    full_text = " ".join(docs)
    assert "category:policy" in full_text
    assert "domain:cyber" in full_text
    assert "urgent" in full_text
