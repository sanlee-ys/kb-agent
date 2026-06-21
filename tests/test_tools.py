"""Tests for the agent's tools — all local, no Anthropic API calls.

We monkeypatch ``tools.PROJECTS_FILE`` to a temp file so the YAML-backed tools
read fixture data instead of the repo's real projects.yaml, and stub httpx so
``classify_snippet`` never touches the network.

``_obs`` is the deterministic "code grader" from the SYS-003 acceptance rule: it
asserts every tool result conforms to the observation shape. Each test parses
through it, so the shape contract is checked on every path.
"""

from __future__ import annotations

import json

import httpx

import agent.tools as tools
from agent.tools import (
    _project_endpoint,
    classify_snippet,
    execute_tool,
    list_projects,
    search_kb,
)


def _obs(raw: str) -> dict:
    """Parse a tool result and assert it conforms to the SYS-003 observation shape.

    Returns the parsed dict so callers can assert path-specific details.
    """
    data = json.loads(raw)
    assert data["status"] in ("success", "warning", "error")
    assert isinstance(data["summary"], str) and data["summary"]
    if data["status"] == "success":
        assert "payload" in data
        assert "source" in data
    else:
        assert isinstance(data["next_actions"], list) and data["next_actions"]
        assert all(isinstance(a, str) and a for a in data["next_actions"])
    return data


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
    data = _obs(list_projects())
    assert data["status"] == "success"
    assert data["payload"] == [{"name": "foo", "description": "a foo project"}]
    assert data["source"] == "projects.yaml"


def test_list_projects_empty(tmp_path, monkeypatch):
    _write_projects(tmp_path, "projects: []\n", monkeypatch)
    data = _obs(list_projects())
    assert data["status"] == "warning"
    assert "No projects" in data["summary"]


def test_search_kb_not_indexed_is_error_with_recovery(tmp_path, monkeypatch):
    # Point CHROMA_DIR at a path that doesn't exist -> _get_collection() is None.
    monkeypatch.setattr(tools, "CHROMA_DIR", tmp_path / "no_such_index")
    data = _obs(search_kb("anything"))
    assert data["status"] == "error"
    assert any("index.py" in a for a in data["next_actions"])


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
    data = _obs(execute_tool("does_not_exist", {}))
    assert data["status"] == "error"
    assert "does_not_exist" in data["summary"]


def test_execute_tool_returns_errors_instead_of_raising(monkeypatch):
    def boom(**_kwargs):
        raise ValueError("kaboom")

    monkeypatch.setitem(tools._DISPATCH, "boom", boom)
    data = _obs(execute_tool("boom", {}))
    assert data["status"] == "error"
    assert "boom" in data["summary"]
    assert "kaboom" in data["summary"]


def test_classify_snippet_no_endpoint_configured(tmp_path, monkeypatch):
    _write_projects(
        tmp_path,
        "projects:\n  - name: defense-news-classifier\n",
        monkeypatch,
    )
    data = _obs(classify_snippet("some snippet"))
    assert data["status"] == "error"
    assert "No endpoint is configured" in data["summary"]


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
    data = _obs(classify_snippet("text"))
    assert data["status"] == "error"
    assert "isn't reachable" in data["summary"]
    # Recovery contract: a remediation step AND an explicit stop condition.
    actions = " ".join(data["next_actions"])
    assert "uvicorn" in actions
    assert "stop" in actions.lower()


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
    data = _obs(classify_snippet("The Pentagon awarded a contract for 24 F-35s."))
    assert data["status"] == "success"
    assert data["payload"] == {"category": "procurement", "operational_domain": "air"}
    assert "defense-news-classifier service" in data["source"]
