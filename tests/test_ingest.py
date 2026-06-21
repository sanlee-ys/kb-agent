"""Tests for the pure/local helpers in scripts/ingest.py.

The model-calling functions (generate_*_stub) and main() aren't covered here —
they need the Anthropic API. Everything tested below is filesystem-only.
"""

from __future__ import annotations

import pytest

import scripts.ingest as ingest
from scripts.ingest import parse_dependencies, read_readme, write_stub


def test_parse_dependencies_from_pyproject(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        "[project]\n"
        'dependencies = ['
        '"anthropic[mcp]>=0.40", "PyYAML>=6.0", '
        "\"httpx; python_version>='3.11'\"]\n",
        encoding="utf-8",
    )
    # Extras, version specifiers, and markers stripped; names lower-cased + sorted.
    assert parse_dependencies(tmp_path) == ["anthropic", "httpx", "pyyaml"]


def test_parse_dependencies_requirements_fallback(tmp_path):
    (tmp_path / "requirements.txt").write_text(
        "# a comment\nrequests==2.0\n-e .\nNumPy>=1.0\n",
        encoding="utf-8",
    )
    # Comments and -e/-r lines skipped; pyproject absent so requirements used.
    assert parse_dependencies(tmp_path) == ["numpy", "requests"]


def test_parse_dependencies_none_found(tmp_path):
    assert parse_dependencies(tmp_path) == []


def test_read_readme_prefers_md_over_txt(tmp_path):
    (tmp_path / "README.md").write_text("hello md", encoding="utf-8")
    (tmp_path / "README.txt").write_text("hello txt", encoding="utf-8")
    assert read_readme(tmp_path) == "hello md"


def test_read_readme_missing_returns_empty(tmp_path):
    assert read_readme(tmp_path) == ""


def test_write_stub_writes_when_absent(tmp_path):
    path = tmp_path / "a.md"
    assert write_stub(path, "content", force=False) is True
    assert path.read_text(encoding="utf-8") == "content\n"


def test_write_stub_skips_when_present(tmp_path):
    path = tmp_path / "a.md"
    path.write_text("original\n", encoding="utf-8")
    assert write_stub(path, "new", force=False) is False
    assert path.read_text(encoding="utf-8") == "original\n"


def test_write_stub_force_overwrites(tmp_path):
    path = tmp_path / "a.md"
    path.write_text("original\n", encoding="utf-8")
    assert write_stub(path, "new", force=True) is True
    assert path.read_text(encoding="utf-8") == "new\n"


def test_load_projects_all_and_filtered(tmp_path, monkeypatch):
    pf = tmp_path / "projects.yaml"
    pf.write_text(
        "projects:\n  - name: a\n    path: /tmp/a\n  - name: b\n    path: /tmp/b\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(ingest, "PROJECTS_FILE", pf)
    assert [p["name"] for p in ingest.load_projects(None)] == ["a", "b"]
    assert [p["name"] for p in ingest.load_projects("b")] == ["b"]


def test_load_projects_unknown_name_exits(tmp_path, monkeypatch):
    pf = tmp_path / "projects.yaml"
    pf.write_text("projects:\n  - name: a\n    path: /tmp/a\n", encoding="utf-8")
    monkeypatch.setattr(ingest, "PROJECTS_FILE", pf)
    with pytest.raises(SystemExit):
        ingest.load_projects("nonexistent")
