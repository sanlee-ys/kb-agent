"""Tests for the agent's tools — all local, no Anthropic API calls.

We monkeypatch ``tools.PROJECTS_FILE`` to a temp file so the YAML-backed tools
read fixture data instead of the repo's real projects.yaml, and stub httpx so
``classify_snippet`` never touches the network.
"""

from __future__ import annotations

import httpx
import pytest

import agent.tools as tools
from agent.tools import (
    _project_endpoint,
    classify_snippet,
    execute_tool,
    list_projects,
)


def _write_projects(tmp_path, body: str, monkeypatch):
    """Point tools.PROJECTS_FILE at a temp projects.yaml with the given body."""
    pf = tmp_path / "projects.yaml"
    pf.write_text(body, encoding="utf-8")
    monkeypatch.setattr(tools, "PROJECTS_FILE", pf)
    return pf


def test_list_projects_includes_name_and_description(tmp_path, monkeypatch):
    _write_projects(
        tmp_path,
        "projects:\n  - name: foo\n    description: a foo project\n",
        monkeypatch,
    )
    out = list_projects()
    assert "foo" in out
    assert "a foo project" in out


def test_list_projects_empty(tmp_path, monkeypatch):
    _write_projects(tmp_path, "projects: []\n", monkeypatch)
    assert "No projects" in list_projects()


def test_project_endpoint_found(tmp_path, monkeypatch):
    _write_projects(
        tmp_path,
        "projects:\n  - name: svc\n    endpoint: http://localhost:8000\n",
        monkeypatch,
    )
    assert _project_endpoint("svc") == "http://localhost:8000"


def test_project_endpoint_missing(tmp_path, monkeypatch):
    _write_projects(tmp_path, "projects:\n  - name: svc\n", monkeypatch)
    assert _project_endpoint("svc") is None  # project exists but no endpoint
    assert _project_endpoint("nope") is None  # project not found at all


def test_execute_tool_unknown_name():
    assert "unknown tool" in execute_tool("does_not_exist", {})


def test_execute_tool_returns_errors_instead_of_raising(monkeypatch):
    def boom(**_kwargs):
        raise ValueError("kaboom")

    monkeypatch.setitem(tools._DISPATCH, "boom", boom)
    out = execute_tool("boom", {})
    assert "Error running boom" in out
    assert "kaboom" in out


def test_classify_snippet_no_endpoint_configured(tmp_path, monkeypatch):
    _write_projects(
        tmp_path,
        "projects:\n  - name: defense-news-classifier\n",
        monkeypatch,
    )
    assert "No endpoint is configured" in classify_snippet("some snippet")


def test_classify_snippet_service_unreachable(tmp_path, monkeypatch):
    _write_projects(
        tmp_path,
        "projects:\n"
        "  - name: defense-news-classifier\n"
        "    endpoint: http://127.0.0.1:9\n",
        monkeypatch,
    )

    def fake_post(*_args, **_kwargs):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(tools.httpx, "post", fake_post)
    out = classify_snippet("text")
    assert "isn't reachable" in out


def test_classify_snippet_happy_path(tmp_path, monkeypatch):
    _write_projects(
        tmp_path,
        "projects:\n"
        "  - name: defense-news-classifier\n"
        "    endpoint: http://127.0.0.1:8000\n",
        monkeypatch,
    )

    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {"category": "procurement", "operational_domain": "air"}

    monkeypatch.setattr(tools.httpx, "post", lambda *a, **k: FakeResponse())
    out = classify_snippet("The Pentagon awarded a contract for 24 F-35s.")
    assert "category: procurement" in out
    assert "operational_domain: air" in out
    assert "[source: defense-news-classifier service" in out
