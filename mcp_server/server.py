"""An MCP server exposing the KB agent's local tools over stdio.

This is a *transport adapter*, not a second implementation. The tool functions in
``agent/tools.py`` already return a SYS-003 observation — a JSON string with a
``status`` field, ``payload``/``source`` on success and ``next_actions`` on failure.
Those exact bytes are what the Anthropic tool-use loop in ``agent/agent.py`` feeds
back to the model, and they are what this server hands to an MCP client. One
contract, two transports; the tool logic has exactly one home.

The same holds for the tool *descriptions*: they are read out of ``TOOLS`` in
``agent/tools.py`` rather than retyped here, so the wording that steers tool
selection stays identical whichever transport a model reaches the tool through.

Only the two local tools are exposed. ``classify_snippet`` and ``search_notes``
are cross-repo HTTP seams that require another service to be running; an MCP
server that silently depends on two background processes is a bad install
experience, so they are deliberately out of scope for now.

Run it directly (stdio transport, the default)::

    uv run python mcp_server/server.py

Or register it with an MCP host — see the "MCP server" section of the README.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated, Literal

from mcp.server.fastmcp import FastMCP
from pydantic import Field

# This module lives one level down, and an MCP host launches it by absolute path
# from an arbitrary working directory. Put the repo root on sys.path so `agent`
# resolves the same way it does under pytest (see pyproject's `pythonpath`).
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent.tools import TOOLS  # noqa: E402
from agent.tools import list_projects as _list_projects  # noqa: E402
from agent.tools import search_kb as _search_kb  # noqa: E402

# Reuse the hand-written descriptions from the Anthropic tool schemas. They are
# prescriptive about *when* to call each tool, which is what drives selection
# accuracy — that tuning shouldn't fork between transports.
_DESCRIPTIONS = {tool["name"]: tool["description"] for tool in TOOLS}

# `instructions` reaches the host during initialize. Spend it on the one thing a
# client can't infer from the tool schemas: results are SYS-003 observations, so
# branch on `status` instead of parsing prose.
mcp = FastMCP(
    "kb-agent",
    instructions=(
        "Searches a developer's personal knowledge base of projects, their "
        "libraries, and plain-language concept notes.\n\n"
        "Every tool returns a JSON string with a `status` field. On "
        '`"success"`, read `payload` for the result and cite `source`. On '
        '`"warning"` or `"error"`, read `summary` for the root cause and follow '
        "`next_actions`: they include a stop condition, so do not retry a call "
        "unchanged."
    ),
)


@mcp.tool(description=_DESCRIPTIONS["search_kb"])
def search_kb(
    query: Annotated[str, Field(description="What to search for, in natural language.")],
    kind: Annotated[
        Literal["projects", "libraries", "notes"] | None,
        Field(description="Optional filter: restrict to projects, libraries, or concept notes."),
    ] = None,
    n_results: Annotated[
        int, Field(ge=1, le=25, description="How many chunks to return (default 5).")
    ] = 5,
) -> str:
    """Semantically search the knowledge base.

    Args:
        query: What to search for, in natural language.
        kind: Optional filter — ``"projects"``, ``"libraries"``, or ``"notes"``.
        n_results: Maximum number of chunks to return.

    Returns:
        The SYS-003 observation (JSON string) returned by ``agent.tools.search_kb``.
    """
    return _search_kb(query=query, kind=kind, n_results=n_results)


@mcp.tool(description=_DESCRIPTIONS["list_projects"])
def list_projects() -> str:
    """List the projects tracked in projects.yaml.

    Returns:
        The SYS-003 observation (JSON string) returned by ``agent.tools.list_projects``.
    """
    return _list_projects()


if __name__ == "__main__":
    # stdio transport: protocol messages travel on stdin/stdout, so nothing else
    # may be written to stdout. Diagnostics belong on stderr.
    mcp.run()
