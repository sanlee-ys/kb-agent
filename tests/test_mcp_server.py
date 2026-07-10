"""Tests for the MCP server — a real protocol round-trip, no subprocess, no network.

``create_connected_server_and_client_session`` wires an MCP client to our server
over an in-memory transport, so these tests exercise the actual initialize /
``tools/list`` / ``tools/call`` message flow rather than calling the Python
functions directly. That's the part a wrapper can plausibly get wrong.

The tests are ``async`` but the suite has no async plugin installed. Rather than
add ``pytest-asyncio`` just for this file, each test is a sync function that hands
its coroutine to ``anyio.run`` — ``anyio`` is already a hard dependency of ``mcp``.
The tradeoff is one line of boilerplate per test in exchange for zero new
dependencies; revisit if the async surface here grows.
"""

from __future__ import annotations

import json

import anyio
from mcp.shared.memory import create_connected_server_and_client_session
from mcp.types import TextContent

import agent.tools as tools
from mcp_server.server import mcp

# The `_obs` grader lives in test_tools.py; re-assert the shape here so an MCP
# response that loses the contract (double-encoded, wrapped, truncated) fails.
from tests.test_tools import _obs


def _connect():
    """Open an in-memory client session against the kb-agent MCP server."""
    return create_connected_server_and_client_session(mcp._mcp_server)


def _text(result) -> str:
    """Extract the single text block from a CallToolResult."""
    assert len(result.content) == 1, f"expected one content block, got {result.content!r}"
    block = result.content[0]
    assert isinstance(block, TextContent)
    return block.text


def test_lists_the_two_local_tools():
    """The server advertises search_kb and list_projects, and nothing else."""

    async def scenario():
        async with _connect() as session:
            listed = await session.list_tools()
            names = sorted(tool.name for tool in listed.tools)
            assert names == ["list_projects", "search_kb"]

            by_name = {tool.name: tool for tool in listed.tools}
            # Descriptions are sourced from agent/tools.py TOOLS, not retyped.
            expected = {t["name"]: t["description"] for t in tools.TOOLS}
            for name, tool in by_name.items():
                assert tool.description == expected[name]

            # search_kb's generated schema keeps `query` required and the kind enum.
            schema = by_name["search_kb"].inputSchema
            assert schema["required"] == ["query"]
            assert "projects" in json.dumps(schema)

    anyio.run(scenario)


def test_list_projects_round_trips_the_observation_contract(monkeypatch, tmp_path):
    """A tools/call response carries the tool's SYS-003 observation verbatim."""
    projects_file = tmp_path / "projects.yaml"
    projects_file.write_text(
        "projects:\n  - name: alpha\n    description: First project\n", encoding="utf-8"
    )
    monkeypatch.setattr(tools, "PROJECTS_FILE", projects_file)

    async def scenario():
        async with _connect() as session:
            result = await session.call_tool("list_projects", {})
            assert result.isError is False
            data = _obs(_text(result))
            assert data["status"] == "success"
            assert data["payload"] == [{"name": "alpha", "description": "First project"}]
            assert data["source"] == "projects.yaml"

    anyio.run(scenario)


def test_tool_failure_is_an_observation_not_a_protocol_error(monkeypatch, tmp_path):
    """A failing tool returns a SYS-003 error observation, not an MCP isError result.

    The tools return problems rather than raising, so the model can read
    ``next_actions`` and adapt. That property has to survive the MCP transport.
    """
    monkeypatch.setattr(tools, "PROJECTS_FILE", tmp_path / "missing.yaml")

    async def scenario():
        async with _connect() as session:
            result = await session.call_tool("list_projects", {})
            assert result.isError is False
            data = _obs(_text(result))
            assert data["status"] == "warning"
            assert data["next_actions"]

    anyio.run(scenario)


def test_search_kb_forwards_arguments():
    """Arguments survive the protocol hop and reach the underlying tool."""
    seen = {}

    async def scenario():
        async with _connect() as session:
            result = await session.call_tool(
                "search_kb", {"query": "vector store", "kind": "libraries", "n_results": 3}
            )
            assert result.isError is False
            return result

    import mcp_server.server as server

    def fake_search_kb(query, kind=None, n_results=5):
        seen.update(query=query, kind=kind, n_results=n_results)
        return json.dumps(
            {"status": "success", "summary": "1 matching chunk(s).", "payload": [], "source": []}
        )

    original = server._search_kb
    server._search_kb = fake_search_kb
    try:
        anyio.run(scenario)
    finally:
        server._search_kb = original

    assert seen == {"query": "vector store", "kind": "libraries", "n_results": 3}


def test_search_kb_rejects_an_out_of_range_n_results():
    """The Field(ge=1, le=25) bound is enforced by the server, before the tool runs."""

    async def scenario():
        async with _connect() as session:
            result = await session.call_tool("search_kb", {"query": "x", "n_results": 999})
            assert result.isError is True

    anyio.run(scenario)
