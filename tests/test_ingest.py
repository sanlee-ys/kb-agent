"""Tests for the pure/local helpers in scripts/ingest.py.

The model-calling functions (generate_*_stub) and main() aren't covered here —
they need the Anthropic API. Everything tested below is filesystem-only.
"""

from __future__ import annotations

import pytest

import scripts.ingest as ingest
from scripts.ingest import (
    README_PROMPT_CHARS,
    check_project_freshness,
    load_manifest,
    orphan_stub_names,
    parse_dependencies,
    read_readme,
    record_fingerprint,
    run_accept,
    run_check,
    save_manifest,
    source_fingerprint,
    write_stub,
)


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


# --- Freshness tracking: fingerprint, manifest, --check, --accept -----------


def test_source_fingerprint_stable_and_prefixed():
    fp = source_fingerprint("desc", ["a", "b"], "readme")
    # Deterministic and namespaced by algorithm.
    assert fp == source_fingerprint("desc", ["a", "b"], "readme")
    assert fp.startswith("sha256:")


@pytest.mark.parametrize(
    "a, b",
    [
        (("desc", ["x"], "r"), ("DESC", ["x"], "r")),  # description change
        (("desc", ["x"], "r"), ("desc", ["x", "y"], "r")),  # deps change
        (("desc", ["x"], "r"), ("desc", ["x"], "different")),  # readme change
    ],
)
def test_source_fingerprint_sensitive_to_each_input(a, b):
    assert source_fingerprint(*a) != source_fingerprint(*b)


def test_source_fingerprint_ignores_readme_past_prompt_window():
    base = "x" * README_PROMPT_CHARS
    # A change only beyond the prompt-visible prefix can't have affected the
    # stub, so it must not read as drift.
    assert source_fingerprint("d", [], base + "AAA") == source_fingerprint("d", [], base + "ZZZ")


def test_source_fingerprint_normalizes_newlines():
    assert source_fingerprint("d", [], "a\r\nb") == source_fingerprint("d", [], "a\nb")


def test_manifest_roundtrip_and_missing_is_empty(tmp_path, monkeypatch):
    mf = tmp_path / ".ingest-manifest.json"
    monkeypatch.setattr(ingest, "MANIFEST_FILE", mf)
    # Absent file → empty skeleton, not an error.
    assert load_manifest()["projects"] == {}
    manifest = load_manifest()
    record_fingerprint(manifest, "proj", "sha256:abc")
    save_manifest(manifest)
    assert load_manifest()["projects"]["proj"]["fingerprint"] == "sha256:abc"


def _project(tmp_path, name, *, description="d", readme="hello", deps_toml=None):
    """Create a fake project dir and return its projects.yaml-style entry."""
    proj_dir = tmp_path / name
    proj_dir.mkdir()
    (proj_dir / "README.md").write_text(readme, encoding="utf-8")
    if deps_toml:
        (proj_dir / "pyproject.toml").write_text(deps_toml, encoding="utf-8")
    return {"name": name, "path": str(proj_dir), "description": description}


def test_check_project_freshness_states(tmp_path, monkeypatch):
    kb_projects = tmp_path / "kb" / "projects"
    kb_projects.mkdir(parents=True)
    monkeypatch.setattr(ingest, "KB_PROJECTS", kb_projects)

    entry = _project(tmp_path, "proj")
    manifest = {"version": 1, "projects": {}}

    # No stub yet → missing.
    assert check_project_freshness(entry, manifest)[0] == "missing"

    # Stub exists, no baseline → untracked.
    (kb_projects / "proj.md").write_text("stub\n", encoding="utf-8")
    assert check_project_freshness(entry, manifest)[0] == "untracked"

    # Record the current baseline → fresh.
    record_fingerprint(
        manifest, "proj", source_fingerprint("d", parse_dependencies(tmp_path / "proj"), "hello")
    )
    assert check_project_freshness(entry, manifest)[0] == "fresh"

    # Change the source README → stale.
    (tmp_path / "proj" / "README.md").write_text("changed", encoding="utf-8")
    assert check_project_freshness(entry, manifest)[0] == "stale"

    # Source path gone → skipped (can't verify on this machine).
    assert check_project_freshness({"name": "gone", "path": "/no/such"}, manifest)[0] == "skipped"


def test_orphan_stub_names(tmp_path, monkeypatch):
    kb_projects = tmp_path / "projects"
    kb_projects.mkdir()
    monkeypatch.setattr(ingest, "KB_PROJECTS", kb_projects)
    (kb_projects / "known.md").write_text("x", encoding="utf-8")
    (kb_projects / "orphan.md").write_text("x", encoding="utf-8")
    assert orphan_stub_names([{"name": "known"}]) == ["orphan"]


def test_run_check_exit_code_and_accept_roundtrip(tmp_path, monkeypatch):
    kb_projects = tmp_path / "kb" / "projects"
    kb_projects.mkdir(parents=True)
    monkeypatch.setattr(ingest, "KB_PROJECTS", kb_projects)
    monkeypatch.setattr(ingest, "MANIFEST_FILE", tmp_path / ".ingest-manifest.json")

    entry = _project(tmp_path, "proj")
    (kb_projects / "proj.md").write_text("stub\n", encoding="utf-8")
    projects = [entry]

    # Untracked is not a failure — only stale is.
    assert run_check(projects) == 0

    # Accept blesses the current source; a re-check stays clean.
    run_accept(projects)
    assert run_check(projects) == 0

    # Mutating the source makes the next check fail (exit 1).
    (tmp_path / "proj" / "README.md").write_text("changed", encoding="utf-8")
    assert run_check(projects) == 1


def test_run_accept_writes_nothing_when_no_stubs_match(tmp_path, monkeypatch):
    kb_projects = tmp_path / "kb" / "projects"
    kb_projects.mkdir(parents=True)
    monkeypatch.setattr(ingest, "KB_PROJECTS", kb_projects)
    mf = tmp_path / ".ingest-manifest.json"
    monkeypatch.setattr(ingest, "MANIFEST_FILE", mf)

    # Source path absent → nothing to record → no manifest file created.
    run_accept([{"name": "proj", "path": "/no/such", "description": "d"}])
    assert not mf.exists()
