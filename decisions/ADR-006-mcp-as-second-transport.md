# ADR-006: Serve MCP as a second transport over the same tools, not a second implementation

**Status:** Accepted
**Date:** 2026-07-10 (recorded as an ADR 2026-07-18)
**Deciders:** San Lee

---

## Context

`agent/tools.py` owns the tool layer: the tool functions themselves, their hand-written JSON
schemas in `TOOLS`, and the descriptions inside those schemas that steer *when* a model calls
each tool. Every tool returns a SYS-003 observation — a JSON string with a `status` field,
`payload`/`source` on success and `next_actions` on failure — built through `_success`/`_problem`.

Four tools exist, and they are not alike (`CLAUDE.md:93-100`). `search_kb` and `list_projects`
are **local**: they read ChromaDB and `projects.yaml` on disk, and need nothing else running.
`classify_snippet` and `search_notes` are **cross-repo HTTP seams**: they POST to the
`defense-news-classifier` service's `/classify` endpoint and GET the `notes-api` service's
`/notes` endpoint, so each one only works while a *separate service* is up.

Exposing the KB over the Model Context Protocol meant a second way in — an MCP host
(Claude Code, Claude Desktop) querying the KB directly, without going through `agent.py`.
That raised two questions at once: how much of the tool layer to rebuild for the new protocol,
and how much of it to expose.

**This ADR records a decision that was already made and already written down.** It landed with
the MCP server itself on 2026-07-10 (commit `2184f28`, *"feat: expose the KB's local tools as an
MCP server (stdio)"*, PR #31) and is stated in three places: the `mcp_server/server.py` module
docstring (lines 1-17), `CLAUDE.md` Architecture §6 (lines 115-120) and the Conventions bullet
*"The tool layer has one home"* (lines 144-147), and the README's "MCP server" section
(lines 110-124). Nothing here is new reasoning. The `decisions/README.md` "Still to migrate"
list named this as an ADR-class decision living in prose; this is that migration.

## Decision

**`mcp_server/server.py` is a transport adapter, not a second implementation.** The mechanism:

1. It imports `search_kb` and `list_projects` from `agent/tools.py` and returns their SYS-003
   observation JSON **unchanged** — the same bytes the Anthropic tool-use loop feeds back to
   the model are the bytes handed to an MCP client. One contract, two transports.
2. It reads tool descriptions out of `TOOLS` rather than retyping them
   (`_DESCRIPTIONS = {tool["name"]: tool["description"] for tool in TOOLS}`, `server.py:49`),
   so the wording that drives tool-selection accuracy cannot fork between transports.
3. It spends the FastMCP `instructions` field on the one thing a client cannot infer from the
   schemas: results are SYS-003 observations, so branch on `status` rather than parsing prose.

**Only the two local tools are exposed. `classify_snippet` and `search_notes` are deliberately
excluded** — they need another service running, and an MCP server that quietly depends on two
background processes is a bad install.

The prose marks this exclusion **"for now"** (`server.py:17`, `README.md:124`). No explicit
revisit trigger was written down; the stated condition — the seams requiring separate running
services — is what would have to change.

The general rule this decision installs is in `CLAUDE.md` Conventions and stays there: never
reimplement a tool or retype a description in `mcp_server/`, because a fork there would
silently drift.

## Downstream surfaces

- **`CLAUDE.md` Architecture §6 and Conventions ("The tool layer has one home")** — the
  operative instruction stays there; this ADR carries the reasoning. Not modified by this ADR.
- **`mcp_server/server.py` module docstring** (lines 1-17) — the original statement of the
  decision, kept as an inline summary at the point of use.
- **`README.md`** — the "MCP server" section (transport-adapter framing, the exposed-tools
  table, the exclusion paragraph), the component list entry for `mcp_server/server.py`, and
  the Status section.
- **`kb/projects/kb-agent.md`** — kb-agent's self-description *in its own KB*, which states the
  two-transports/one-implementation framing and the exclusion (lines 8-9, 20-29). It is
  hand-written and sits outside `ingest.py`'s pipeline, so nothing regenerates it; `ingest.py
  --check` lists it as `unmanaged`. If this decision is ever reversed, this stub must be edited
  by hand and `scripts/index.py` rerun, or `search_kb` will answer from a stale description.
- **`tests/test_mcp_server.py`** — drives the server through a real in-memory client session
  (`initialize` → `tools/list` → `tools/call`), so the protocol hop is under test rather than
  just the underlying functions.
- **`decisions/README.md`** — the index table gains this row, and the "Still to migrate" entry
  *"MCP server as a second transport, not a second implementation — `CLAUDE.md`"* is now
  satisfied.
- [`system/SYS-003`](https://github.com/sanlee-ys/architecture/blob/main/decisions/SYS-003-agent-tool-layer-contract.md)
  — unaffected, and load-bearing here: the observation envelope is precisely what makes one
  implementation serviceable over two transports.

## Consequences

- **One place to change a tool.** Editing a tool function or its description updates both
  transports at once. A new local tool needs a thin wrapper in `mcp_server/`, not a
  reimplementation.
- **Selection behaviour stays identical across transports.** Descriptions are prescriptive
  about *when* to call each tool; reading them from `TOOLS` means that tuning cannot diverge.
- **The MCP surface is narrower than the agent's.** A host reaching kb-agent over MCP can search
  the KB and list projects, but cannot drive the classifier or read live notes. Those
  capabilities exist only through `agent.py`/`app.py`. This asymmetry is the accepted cost.
- **The install stays honest.** Registering the server yields something that works immediately
  from a clone plus an index — no hidden prerequisite of two other repos' services running, and
  no tools that fail at call time for environmental reasons a host cannot fix.
- **A thin coupling to `agent/tools.py`'s shape is accepted.** The server depends on `TOOLS`
  entries having `name` and `description` keys, and on `sys.path` manipulation to import
  `agent.tools` at all (an MCP host launches the server by absolute path from an arbitrary CWD,
  `server.py:35-40`).

## Alternatives Considered

| Option | Reason Not Chosen |
|--------|-------------------|
| Reimplement `search_kb`/`list_projects` natively inside `mcp_server/` | Two implementations of one tool drift silently — the failure mode is a query that behaves differently depending on which transport a model reached it through. `CLAUDE.md` Conventions states the rule directly: "Never reimplement a tool or retype a description in `mcp_server/`" |
| Keep the shared functions but retype the tool descriptions for MCP | The descriptions are prescriptive about *when* to call each tool and are what drive selection accuracy; retyping forks that tuning between transports with nothing to catch the divergence (`server.py:46-48`) |
| Expose all four tools, including `classify_snippet` and `search_notes` | The two seams require the `defense-news-classifier` and `notes-api` services to be running. An MCP server that silently depends on two background processes is a bad install experience — a host would see four tools advertised and get failures from two of them for reasons outside its control |
| Translate observations into an MCP-native result shape instead of passing SYS-003 JSON through | Would create a second wire format to keep in sync with SYS-003. The observation envelope already carries `status`/`payload`/`source`/`next_actions`, which is what a client needs; the server instead documents that contract once in FastMCP `instructions` |
| No MCP server at all — reach the KB only through `agent.py`/`app.py` | The record does not preserve a rejected form of this. The stated motivation is that any MCP host can query the KB directly, without going through `agent.py` (`README.md:106-108`); it is not written up as a choice against a considered alternative |
