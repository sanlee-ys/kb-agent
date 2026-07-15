# kb-agent

A personal, local knowledge base over a developer's projects and their
dependencies, fronted by a RAG + tool-use agent. `projects.yaml` lists the
tracked project directories; `scripts/ingest.py` auto-generates Markdown stubs
for each project and its dependencies via the Anthropic API; `scripts/index.py`
embeds those stubs into a local ChromaDB vector store; and an agent (or any
MCP host) answers questions grounded in that KB. The tool layer is exposed
over two transports that share one implementation: a manual Anthropic
tool-use loop (`agent/agent.py`, `app.py`'s Gradio UI) and a Model Context
Protocol server (`mcp_server/server.py`).

## Tech stack

- **anthropic** — powers `scripts/ingest.py`'s stub generation and the
  `agent/agent.py` tool-use loop.
- **chromadb** — local, embedded (`PersistentClient`, never HTTP/server mode)
  vector store for `search_kb`, using the built-in `all-MiniLM-L6-v2` model —
  no API key, no network.
- **mcp** — the official Python MCP SDK. `mcp_server/server.py` is a `FastMCP`
  stdio server exposing `search_kb` and `list_projects` to any MCP host
  (Claude Code, Claude Desktop). It's a transport adapter, not a second
  implementation: it calls the same functions in `agent/tools.py` and returns
  their SYS-003 observation JSON unchanged, and reads the tool descriptions
  out of the same `TOOLS` list so tool-selection wording can't drift between
  the two transports. Only the two *local* tools are exposed — `classify_snippet`
  and `search_notes` are cross-repo HTTP seams that need another service
  running, which would make the server a bad install.
- **httpx** — the cross-repo HTTP seams: `classify_snippet` POSTs to the
  defense-news-classifier's `/classify`, `search_notes` GETs the notes-api's
  `/notes`.
- **opentelemetry-api / -sdk** — optional tracing over the tool-use loop
  (`agent/telemetry.py`). Instrumented against the OTel API always; the SDK only
  records/exports when `KB_AGENT_TRACING` is set, so it's a no-op by default.
  Each `ask()` emits a span tree (`kb_agent.ask` → `chat <model>` →
  `execute_tool <name>`) with GenAI-semconv token and tool-status attributes.
- **gradio** — the chat UI in `app.py`.
- **pyyaml** — reads `projects.yaml`.
- **python-dotenv** — loads `ANTHROPIC_API_KEY` from `.env`.

## Notes

Every tool returns a SYS-003 observation (`status`/`payload`/`source` on
success, `status`/`summary`/`next_actions` on failure) so results are read,
not raised, and both transports can act on the same shape. The MCP server
(`mcp_server/server.py`) shipped 2026-07-10 — see `kb-agent` PR #31 and the
"MCP server" section of `README.md` for how to run and register it.
