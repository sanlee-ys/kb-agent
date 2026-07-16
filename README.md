# kb-agent

![CI](https://github.com/sanlee-ys/kb-agent/actions/workflows/ci.yml/badge.svg)

A personal, living knowledge base over my projects and the libraries they use —
with an AI agent that answers questions about them using RAG + tool use.

Point it at my project directories (listed in `projects.yaml`); it auto-generates
Markdown stubs for each project and dependency, embeds them into a local vector
store, and serves an agent that searches that KB to answer questions.

## How it works

```
projects.yaml ──▶ ingest.py ──▶ kb/*.md ──▶ index.py ──▶ ChromaDB (local)
                  (Anthropic)   (you edit)   (local embeds)        │
                                                                   ▼
                          agent.py ──▶ search_kb · list_projects · classify_snippet · search_notes
                          (Claude tool-use loop)                          │ HTTP
                                                          ┌───────────────┴───────────────┐
                                                          ▼                               ▼
                                          defense-news-classifier /classify        notes-api /notes
```

- **`scripts/ingest.py`** — reads each project's `pyproject.toml`/`requirements.txt`
  + README and uses the Anthropic API to write KB stubs. Never overwrites existing
  files (so your hand-annotations survive), unless you pass `--force`. It fingerprints
  the source each project stub was built from (in `kb/.ingest-manifest.json`), so
  `ingest.py --check` reports stubs that have drifted from their source and
  `ingest.py --accept` records the current source as the baseline without regenerating.
- **`scripts/index.py`** — chunks `kb/*.md` and embeds them into a local ChromaDB
  collection using the built-in `all-MiniLM-L6-v2` model (no API key, runs locally).
- **`agent/tools.py`** — four tools. `search_kb` (RAG over the local KB) and
  `list_projects` are local; `classify_snippet` and `search_notes` are the
  *ecosystem* seams — they call a *tracked project's own HTTP service* so the agent can
  **drive and read** a project, not just describe it: `classify_snippet` POSTs to the
  defense-news-classifier's `/classify` endpoint, and `search_notes` GETs the
  notes-api's `/notes` endpoint to read your live notes. Which services are callable is
  config: add an `endpoint:` to a project's `projects.yaml` entry. The seams fail
  gracefully — if a service is down, the tool tells you how to start it instead of
  crashing.
- **`agent/agent.py`** — a manual Claude tool-use loop: the model decides when to
  search the KB and answers from what it finds.
- **`mcp_server/server.py`** — the same local tools, served over the Model Context
  Protocol so any MCP host can query the KB. See [MCP server](#mcp-server).

## Setup

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
```

Set your Anthropic API key (copy the example and fill it in):

```bash
cp -n .env.example .env   # -n: won't clobber an existing .env; then edit it and set ANTHROPIC_API_KEY
```

## Usage

1. List the projects to track in `projects.yaml`, then generate KB stubs:

   ```bash
   uv run python scripts/ingest.py
   ```

2. Build the local vector index (downloads the embedding model on first run):

   ```bash
   uv run python scripts/index.py
   ```

3. Ask the agent questions — either in the browser:

   ```bash
   uv run python app.py        # opens a Gradio chat UI at http://127.0.0.1:7860
   ```

   or in the terminal:

   ```bash
   uv run python agent/agent.py
   ```

### Calling a project's service (optional)

The `classify_snippet` tool routes to the defense-news-classifier's HTTP service,
so that service has to be running first. From the **classifier's** directory:

```bash
uv run --with fastapi --with "uvicorn[standard]" --env-file .env \
  uvicorn api:app --app-dir src --host 127.0.0.1 --port 8000
```

Then ask the agent to classify a snippet (e.g. *"classify: the Pentagon awarded a
$4.2B contract for 24 F-35s"*) and it routes through `classify_snippet` to that
service. If the service isn't up, the tool returns this start command rather than
crashing.

## MCP server

The KB is also exposed as a [Model Context Protocol](https://modelcontextprotocol.io)
server, so any MCP host (Claude Code, Claude Desktop, ...) can search this knowledge
base directly — without going through `agent.py`.

It's a thin **transport adapter**, not a second implementation: `mcp_server/server.py`
calls the same functions in `agent/tools.py` and returns their SYS-003 observation
JSON unchanged. The tool *descriptions* are read out of `TOOLS` too, so the wording
that steers tool selection can't drift between the two transports.

**Tools exposed** (stdio transport):

| Tool | Arguments | What it does |
| --- | --- | --- |
| `search_kb` | `query`, `kind?` (`projects`\|`libraries`\|`notes`), `n_results?` (1–25, default 5) | Semantic search over the local ChromaDB index; returns matching chunks with their `source` files. |
| `list_projects` | — | Lists the projects tracked in `projects.yaml`. |

Only the two **local** tools are exposed. `classify_snippet` and `search_notes` are
cross-repo HTTP seams that need another service running; an MCP server that quietly
depends on two background processes is a bad install, so they're out of scope for now.

Run it standalone (it speaks JSON-RPC on stdin/stdout, so there's nothing to see —
this is mostly to check it starts):

```bash
uv run python mcp_server/server.py
```

Register it with Claude Code (run `scripts/index.py` first, or `search_kb` will
correctly tell you the KB isn't indexed yet):

```bash
claude mcp add kb-agent --scope user -- \
  uv run --directory /absolute/path/to/kb-agent python mcp_server/server.py
```

Then confirm it's up and start using it:

```bash
claude mcp list        # -> kb-agent: ... - ✔ Connected
```

For Claude Desktop, add the equivalent to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "kb-agent": {
      "command": "uv",
      "args": ["run", "--directory", "/absolute/path/to/kb-agent",
               "python", "mcp_server/server.py"]
    }
  }
}
```

`--directory` matters: an MCP host launches the server from an arbitrary working
directory, and it tells `uv` which project's environment to use. The server itself
resolves the repo root from `__file__`, so the KB and index are found either way.

## Observability

A tool-use loop is a distributed system: one `ask()` fans out into several model
calls and tool calls, and the questions that decide whether it's fast and cheap —
*which tool is slow, where the tokens go, how many passes a turn took* — are
invisible without a span per step. The loop is instrumented with
[OpenTelemetry](https://opentelemetry.io/) tracing to make that legible.

It's **off by default and zero-overhead when off.** The loop is instrumented
against the OTel *API*, whose default tracer is a no-op; the *SDK* that records
and exports spans is configured only when `KB_AGENT_TRACING` is set
(`agent/telemetry.py`). Turn it on:

```bash
KB_AGENT_TRACING=1 uv run python agent/agent.py          # spans to stderr (console)
KB_AGENT_TRACING=1 OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318 \
  uv run --extra otlp python agent/agent.py              # also to an OTLP collector
```

Each turn emits a span tree — `kb_agent.ask` → one `chat <model>` per model call →
one `execute_tool <name>` per tool call — carrying, per
[OpenTelemetry's GenAI semantic conventions](https://opentelemetry.io/docs/specs/semconv/gen-ai/):

| Span | Key attributes |
| --- | --- |
| `kb_agent.ask` | `gen_ai.request.model`, `kb_agent.loop.iterations` (how many passes the turn took) |
| `chat <model>` | `gen_ai.usage.{input,output,cache_read,cache_creation}_tokens`, `gen_ai.response.finish_reasons` |
| `execute_tool <name>` | `gen_ai.tool.name`, `kb_agent.tool.status` (the SYS-003 status) — span duration is the tool latency |

The console exporter needs no infrastructure; the OTLP exporter (`--extra otlp`)
sends the same spans to any collector (Jaeger, Tempo, Honeycomb, …).

## Status

v1 — local KB with a RAG/tool-use agent (Gradio chat UI + CLI), an MCP server over the
same tools, now with cross-project
**ecosystem seams**: the agent can call a tracked project's HTTP service — the
defense-news-classifier (`classify_snippet`) and the notes-api (`search_notes`). Tools
follow a shared observation contract (`system/SYS-003`), OpenTelemetry tracing over the
tool-use loop (opt-in via `KB_AGENT_TRACING`), and an offline test suite
(`uv run pytest`).
