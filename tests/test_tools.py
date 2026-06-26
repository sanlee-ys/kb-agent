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
    search_notes,
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


# --- SSRF guard: endpoints must be http(s) on an allowed (loopback) host ------
# projects.yaml is the trust boundary; a poisoned endpoint must be rejected
# BEFORE any request is made, so these assert no network call happens.


def _explode_if_called(*_args, **_kwargs):
    raise AssertionError("an HTTP request was made despite an invalid endpoint")


def test_classify_snippet_rejects_non_loopback_endpoint(tmp_path, monkeypatch):
    _write_projects(
        tmp_path,
        "projects:\n"
        "  - name: defense-news-classifier\n"
        "    endpoint: http://attacker.example.com:8000\n",
        monkeypatch,
    )
    monkeypatch.setattr(tools.httpx, "post", _explode_if_called)
    data = _obs(classify_snippet("text"))
    assert data["status"] == "error"
    assert "not allowed" in data["summary"]


def test_classify_snippet_rejects_non_http_scheme(tmp_path, monkeypatch):
    _write_projects(
        tmp_path,
        "projects:\n"
        "  - name: defense-news-classifier\n"
        "    endpoint: file:///etc/passwd\n",
        monkeypatch,
    )
    monkeypatch.setattr(tools.httpx, "post", _explode_if_called)
    data = _obs(classify_snippet("text"))
    assert data["status"] == "error"
    assert "not allowed" in data["summary"]


def test_search_notes_rejects_non_loopback_endpoint(tmp_path, monkeypatch):
    _write_projects(
        tmp_path,
        "projects:\n  - name: notes-api\n    endpoint: http://169.254.169.254\n",
        monkeypatch,
    )
    monkeypatch.setattr(tools.httpx, "get", _explode_if_called)
    data = _obs(search_notes("anything"))
    assert data["status"] == "error"
    assert "not allowed" in data["summary"]


def test_allowed_hosts_env_widens_the_allowlist(tmp_path, monkeypatch):
    monkeypatch.setenv("KB_ALLOWED_HOSTS", "notes.internal")
    _write_projects(
        tmp_path,
        "projects:\n  - name: notes-api\n    endpoint: http://notes.internal:8081\n",
        monkeypatch,
    )

    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return []

    monkeypatch.setattr(tools.httpx, "get", lambda *a, **k: FakeResponse())
    # Empty list -> warning (not error): proves the endpoint passed validation and
    # the request was actually made.
    data = _obs(search_notes("x"))
    assert data["status"] == "warning"


# --- Frozen /classify contract (SYS-004) -------------------------------------
# The /classify contract is frozen: a 200 must carry a JSON object with both
# `category` and `operational_domain`. These tests pin both ends of that — a
# well-formed 200 yields a SUCCESS observation, and a 200 that breaks the
# contract yields an ERROR observation (never a raised exception). Both still
# pass the SYS-003 _obs() grader.


def _classifier_projects_yaml(tmp_path, monkeypatch):
    """Configure a classifier endpoint so classify_snippet reaches the HTTP call."""
    _write_projects(
        tmp_path,
        "projects:\n"
        "  - name: defense-news-classifier\n"
        "    endpoint: http://127.0.0.1:8000\n",
        monkeypatch,
    )


class _FakeResponse:
    """Minimal stand-in for httpx.Response: a status, a JSON body, and .text."""

    def __init__(self, payload, status_code=200, text="<body>"):
        self._payload = payload
        self.status_code = status_code
        self.text = text

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def test_classify_snippet_contract_well_formed_200_is_success(tmp_path, monkeypatch):
    _classifier_projects_yaml(tmp_path, monkeypatch)
    body = {"category": "technology", "operational_domain": "air"}
    monkeypatch.setattr(tools.httpx, "post", lambda *a, **k: _FakeResponse(body))

    data = _obs(classify_snippet("A new autonomous drone swarm was demonstrated."))
    assert data["status"] == "success"
    assert data["payload"]["category"] == "technology"
    assert data["payload"]["operational_domain"] == "air"


def test_classify_snippet_contract_empty_200_is_error(tmp_path, monkeypatch):
    _classifier_projects_yaml(tmp_path, monkeypatch)
    monkeypatch.setattr(tools.httpx, "post", lambda *a, **k: _FakeResponse({}))

    # Must be a returned error observation, not a raised KeyError.
    data = _obs(classify_snippet("some snippet"))
    assert data["status"] == "error"
    assert "SYS-004" in data["summary"]


def test_classify_snippet_contract_partial_200_is_error(tmp_path, monkeypatch):
    _classifier_projects_yaml(tmp_path, monkeypatch)
    # category present, operational_domain missing -> contract violation.
    body = {"category": "technology"}
    monkeypatch.setattr(tools.httpx, "post", lambda *a, **k: _FakeResponse(body))

    data = _obs(classify_snippet("some snippet"))
    assert data["status"] == "error"
    assert "operational_domain" in data["summary"]


def test_classify_snippet_contract_non_json_200_is_error(tmp_path, monkeypatch):
    _classifier_projects_yaml(tmp_path, monkeypatch)
    bad = _FakeResponse(ValueError("no json"), text="not json at all")
    monkeypatch.setattr(tools.httpx, "post", lambda *a, **k: bad)

    data = _obs(classify_snippet("some snippet"))
    assert data["status"] == "error"
    assert "SYS-004" in data["summary"]


# --- search_notes: the notes-api read seam -----------------------------------
# Mirrors the classify_snippet suite: a well-formed 200 yields a SUCCESS
# observation; every failure path (no endpoint, unreachable, non-200, non-JSON,
# non-array) yields an ERROR; an empty result is a WARNING. All pass _obs().


def _notes_projects_yaml(tmp_path, monkeypatch):
    """Configure a notes-api endpoint so search_notes reaches the HTTP call."""
    _write_projects(
        tmp_path,
        "projects:\n  - name: notes-api\n    endpoint: http://127.0.0.1:8081\n",
        monkeypatch,
    )


def test_search_notes_no_endpoint_configured(tmp_path, monkeypatch):
    _write_projects(tmp_path, "projects:\n  - name: notes-api\n", monkeypatch)
    data = _obs(search_notes("anything"))
    assert data["status"] == "error"
    assert "No endpoint is configured" in data["summary"]


def test_search_notes_service_unreachable(tmp_path, monkeypatch):
    _notes_projects_yaml(tmp_path, monkeypatch)

    def fake_get(*_args, **_kwargs):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(tools.httpx, "get", fake_get)
    data = _obs(search_notes("notes about drones"))
    assert data["status"] == "error"
    assert "isn't reachable" in data["summary"]
    # Recovery contract: a remediation step AND an explicit stop condition.
    actions = " ".join(data["next_actions"])
    assert "mvnw" in actions
    assert "stop" in actions.lower()


def test_search_notes_happy_path(tmp_path, monkeypatch):
    _notes_projects_yaml(tmp_path, monkeypatch)
    body = [
        {"id": 1, "title": "Drone doctrine", "content": "UAV ROE", "tags": ["domain:air"]},
        {"id": 2, "title": "Budget memo", "content": "FY26", "tags": []},
    ]
    monkeypatch.setattr(tools.httpx, "get", lambda *a, **k: _FakeResponse(body))
    data = _obs(search_notes(tag="domain:air"))
    assert data["status"] == "success"
    assert [n["id"] for n in data["payload"]] == [1, 2]
    assert data["payload"][0]["title"] == "Drone doctrine"
    assert "notes-api service" in data["source"]


def test_search_notes_empty_is_warning(tmp_path, monkeypatch):
    _notes_projects_yaml(tmp_path, monkeypatch)
    monkeypatch.setattr(tools.httpx, "get", lambda *a, **k: _FakeResponse([]))
    data = _obs(search_notes("nothing matches", tag="domain:space"))
    assert data["status"] == "warning"
    assert any("tag=" in a for a in data["next_actions"])


def test_search_notes_non_200_is_error(tmp_path, monkeypatch):
    _notes_projects_yaml(tmp_path, monkeypatch)
    monkeypatch.setattr(
        tools.httpx,
        "get",
        lambda *a, **k: _FakeResponse([], status_code=500, text="boom"),
    )
    data = _obs(search_notes("x"))
    assert data["status"] == "error"
    assert "HTTP 500" in data["summary"]


def test_search_notes_non_json_200_is_error(tmp_path, monkeypatch):
    _notes_projects_yaml(tmp_path, monkeypatch)
    bad = _FakeResponse(ValueError("no json"), text="not json")
    monkeypatch.setattr(tools.httpx, "get", lambda *a, **k: bad)
    data = _obs(search_notes("x"))
    assert data["status"] == "error"
    assert "isn't valid JSON" in data["summary"]


def test_search_notes_non_array_200_is_error(tmp_path, monkeypatch):
    _notes_projects_yaml(tmp_path, monkeypatch)
    # notes-api's GET /notes returns a JSON array; an object is a contract violation.
    monkeypatch.setattr(tools.httpx, "get", lambda *a, **k: _FakeResponse({"oops": 1}))
    data = _obs(search_notes("x"))
    assert data["status"] == "error"
    assert "array" in data["summary"]


def test_search_notes_non_note_elements_is_error(tmp_path, monkeypatch):
    _notes_projects_yaml(tmp_path, monkeypatch)
    # A non-empty array whose elements aren't note objects is a malformed body —
    # it must NOT collapse to a success with an empty payload (it's not "no matches").
    monkeypatch.setattr(tools.httpx, "get", lambda *a, **k: _FakeResponse(["a", "b"]))
    data = _obs(search_notes("x"))
    assert data["status"] == "error"
    assert "aren't note objects" in data["summary"]
