# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

kb-agent is a personal, local knowledge base over a developer's projects and their
dependencies, fronted by a RAG + tool-use agent. You point it at project directories
(`projects.yaml`), it auto-generates Markdown stubs, embeds them into a local vector
store, and an agent answers questions grounded in that KB.

## Commands

Environment uses [uv](https://docs.astral.sh/uv/); Python 3.11+ required.

```bash
uv sync                                   # install deps incl. the dev group (ruff, pytest)
cp -n .env.example .env                   # then set ANTHROPIC_API_KEY

# Pipeline (run in order):
uv run python scripts/ingest.py           # generate kb/*.md stubs from projects.yaml
uv run python scripts/ingest.py NAME      # ingest a single project by name
uv run python scripts/ingest.py --force   # regenerate existing stubs (see note below)
uv run python scripts/ingest.py --check   # report project stubs that drifted from source (no writes)
uv run python scripts/ingest.py --accept  # bless current source as each stub's baseline
uv run python scripts/index.py            # (re)build the ChromaDB vector index

# Run the agent:
uv run python app.py                      # Gradio chat UI at http://127.0.0.1:7860
uv run python agent/agent.py              # CLI chat loop

uv run python agent/tools.py              # manual smoke test of the tools
uv run pytest                             # run the test suite (no API key needed)
uv run ruff check .                       # lint

# MCP server (stdio; exposes search_kb + list_projects to any MCP host):
uv run python mcp_server/server.py        # speaks JSON-RPC on stdin/stdout — nothing to see
claude mcp list                           # check it's registered and Connected
```

Tests live in `tests/` (`test_tools.py`, `test_index.py`, `test_ingest.py`,
`test_kb_roundtrip.py`, `test_mcp_server.py`). Most run offline — no API key, no network — and
`tests/test_tools.py` includes `_obs()`, a grader that asserts every tool result conforms
to the SYS-003 observation shape. The exception is `test_kb_roundtrip.py`, marked
`@pytest.mark.integration`: it builds a **real ChromaDB** in a temp dir and runs the
index→`search_kb` round-trip, so it loads the local `all-MiniLM-L6-v2` embedding model
(downloaded once, ~80MB, the first time on any machine). It runs by default; skip it for
the fast loop with `uv run pytest -m "not integration"`. CI (`.github/workflows/ci.yml`)
runs ruff + the test suite (including the integration test) on push and PR. The `__main__`
blocks (`agent/tools.py`, `agent/agent.py`) also double as smoke tests.

`tests/test_mcp_server.py` drives the MCP server through a real in-memory client session
(`initialize` → `tools/list` → `tools/call`), so the protocol hop itself is under test, not
just the underlying functions. Its tests are sync functions that call `anyio.run(...)` —
`anyio` is already a hard dependency of `mcp`, so the suite needs no async pytest plugin.

## Architecture

The data flow is a one-directional pipeline; understanding it is the key to the repo:

```
projects.yaml → ingest.py → kb/*.md → index.py → chroma_db/ → tools.search_kb → agent.py / app.py
```

1. **`scripts/ingest.py`** reads each project's dependency manifest
   (`pyproject.toml` `[project].dependencies`, falling back to `requirements.txt`)
   and README, then calls the Anthropic API to write `kb/projects/<name>.md` and
   one `kb/libraries/<pkg>.md` per dependency. **Stubs are never overwritten** unless
   `--force` is passed — hand-edits to KB files are meant to survive re-ingestion.
   Each generated project stub records a **source fingerprint** (description + deps +
   README prefix) in a sidecar manifest `kb/.ingest-manifest.json`; `--check` recomputes
   it to flag stubs that have drifted from their source, and `--accept` blesses the
   current source as the baseline without regenerating (the non-destructive alternative
   to `--force` for hand-curated stubs). The manifest is JSON (not `*.md`), so `index.py`
   never embeds it; it's committed so baselines travel across machines/sessions. Freshness
   tracks *project* stubs only — library stubs are generated from a package name, not a
   source file, so they can't drift against one.
2. **`scripts/index.py`** chunks every `kb/**/*.md` (splits on Markdown headings,
   caps chunks at ~1200 chars) and embeds them into a persistent ChromaDB collection
   `knowledge_base` using the **built-in local `all-MiniLM-L6-v2` model — no API key,
   no network**. The collection is dropped and rebuilt from scratch each run, so
   deleted/renamed KB files leave no stale chunks. Chunk metadata carries
   `source` (repo-relative path), `kind` (`projects`/`libraries`/`notes`), and `name`.
3. **`agent/tools.py`** exposes four tools to the model: `search_kb(query, kind?,
   n_results?)` (semantic query over ChromaDB, optional `kind` filter) and
   `list_projects()` (reads `projects.yaml`) are both local; the other two are
   cross-repo HTTP seams (base URLs from `projects.yaml`, not hardcoded) so the agent
   *drives* / *reads* tracked services rather than just describing them:
   `classify_snippet(text)` POSTs to the `defense-news-classifier` service's
   `/classify` endpoint, and `search_notes(query?, tag?)` GETs the `notes-api`
   service's `/notes` endpoint to read the user's live notes.
   Tool JSON schemas are hand-written in `TOOLS` and dispatched via
   `_DISPATCH`/`execute_tool`. Every tool returns a **SYS-003 observation** — a JSON
   string built via `_success`/`_problem`, with a `status` field
   (`success`/`warning`/`error`), `payload`+`source` on success, and `next_actions`
   (recovery guidance) on failure. Results are returned, not raised, so the model reads
   them and adapts.
4. **`agent/agent.py`** (`KBAgent`) is a **manual Anthropic tool-use loop** — not the
   SDK's tool runner. `ask()` appends the user message, calls the model with `TOOLS`,
   and while `stop_reason == "tool_use"` it executes the requested tools, feeds
   `tool_result` blocks back, and loops (capped at `MAX_TOOL_ITERATIONS = 10`). The
   full assistant turn (including `tool_use` blocks) is preserved in `self.messages`.
5. **`app.py`** wraps `KBAgent` in a `gr.ChatInterface`. Gradio owns history; each turn
   rebuilds a fresh `KBAgent` from the `{"role","content"}` history (text answers only —
   per-turn tool calls are not replayed).
6. **`mcp_server/server.py`** is a **second transport over the same tools**, not a second
   implementation: a `FastMCP` server (stdio) that exposes the two *local* tools,
   `search_kb` and `list_projects`, to any MCP host. It calls `agent/tools.py` and returns
   its SYS-003 observation JSON **unchanged**, and it reads the tool descriptions out of
   `TOOLS` rather than retyping them. The HTTP-seam tools are deliberately excluded — they
   need another service running, which would make the server a bad install.

**Known staleness risk:** `kb/projects/kb-agent.md` (kb-agent's self-description in its own
KB, added so `search_kb` can answer questions about the MCP server) is hand-written and sits
outside `ingest.py`'s pipeline — there's no self-referential-project path, so nothing
regenerates it. If `mcp_server/server.py` or the tool layer changes materially, update that
stub by hand and rerun `scripts/index.py`, or it'll quietly drift from the code it describes.
(`ingest.py --check` lists it as `unmanaged` — it has no projects.yaml entry to fingerprint
against — so the drift risk is visible in the report, but the fix is still by hand.)

## Conventions

- **Model**: defaults to the `claude-sonnet-5` workhorse per the SYS-002 model-tier
  standard (default to Sonnet; escalate only where a task needs it). It's a constant in
  each module that calls the API — `DEFAULT_MODEL` in `agent/agent.py`, `MODEL` in
  `scripts/ingest.py` (update both together). Escalate the agent without code changes via
  the `KB_AGENT_MODEL` env var (e.g. `KB_AGENT_MODEL=claude-opus-4-8`) or per instance
  with `KBAgent(model=...)`.
- **Paths**: every module resolves `REPO_ROOT = Path(__file__).resolve().parent.parent`
  and builds paths from it, so scripts work regardless of CWD. `agent/agent.py` uses a
  `try/except ImportError` shim so it runs both as `python agent/agent.py` and as an
  `import agent.agent`. `mcp_server/server.py` instead prepends `REPO_ROOT` to `sys.path`
  before importing `agent.tools`, because an MCP host launches it by absolute path from an
  arbitrary CWD.
- **The tool layer has one home.** `agent/tools.py` owns the tool functions *and* their
  descriptions; both the Anthropic tool-use loop and the MCP server consume them. Never
  reimplement a tool or retype a description in `mcp_server/` — the SYS-003 observation is
  the wire format for both transports, and a fork there would silently drift.
- `.env` is loaded via `python-dotenv` from `REPO_ROOT / ".env"`; the Anthropic client
  is constructed with no args and reads `ANTHROPIC_API_KEY` from the environment.
- `chroma_db/` is generated and git-ignored — never commit it; rebuild with `index.py`.
- The agent's system prompt forbids answering from prior knowledge about the user's
  projects: answers must come from the tools and cite the `source` file. Preserve this
  grounding behavior when editing the prompt or tools.
- **Tool results follow the SYS-003 observation contract** (`system/SYS-003` in the
  architecture repo). Build every result via `_success`/`_problem` so the shape stays
  consistent, and keep the system prompt's instruction to branch on `status` and follow
  `next_actions`. New tools must conform — the `_obs()` grader in `tests/test_tools.py`
  enforces it.
- `projects.yaml` `path` values are absolute on the author's machine (Windows paths);
  `ingest.py` skips entries whose path doesn't exist.

<!-- shared:links-verify v1 -->
## Links — verify before sending (hard rule)

Links given in chat must resolve: **full `github.com/<owner>/<repo>/blob/<ref>/<path>` URLs only**, **verify the path exists on the ref before sending** (unverified → say so), and **branch links are perishable** (prefer `main` once merged). Full rule + rationale: [claude-ops `conventions/links-verify.md`](https://github.com/sanlee-ys/claude-ops/blob/main/conventions/links-verify.md).
<!-- /shared:links-verify -->
