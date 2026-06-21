"""Tests for the chunking/collection logic in scripts/index.py.

These cover the pure functions; main() (which writes to ChromaDB) is left to the
manual smoke run.
"""

from __future__ import annotations

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

    documents, metadatas, ids = index.collect_documents()

    assert len(documents) == len(metadatas) == len(ids) == 2
    assert {m["kind"] for m in metadatas} == {"projects", "libraries"}
    assert {m["name"] for m in metadatas} == {"foo", "bar"}
    assert len(set(ids)) == 2  # ids are unique
    assert all("#" in i for i in ids)  # ids follow "kind/name#i"
