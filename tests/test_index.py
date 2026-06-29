"""Tests for the chunking/collection logic in scripts/index.py.

These cover the pure functions; main() (which writes to ChromaDB) is left to the
manual smoke run.
"""

from __future__ import annotations

import httpx

import scripts.index as index
from scripts.index import MAX_CHUNK_CHARS, chunk_markdown


def test_chunk_markdown_splits_on_headings():
    text = "# A\nalpha\n## B\nbeta\n## C\ngamma"
    chunks = chunk_markdown(text)
    assert len(chunks) == 3
    assert chunks[0].startswith("# A")
    assert chunks[1].startswith("## B")
    assert chunks[2].startswith("## C")


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
