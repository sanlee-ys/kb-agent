# kb-agent

![CI](https://github.com/sanlee-ys/kb-agent/actions/workflows/ci.yml/badge.svg)

A personal, living knowledge base over my projects and the libraries they use —
with an AI agent that answers questions about them using RAG + tool use.

Point it at my project directories (listed in `projects.yaml`); it auto-generates
Markdown stubs for each project and dependency, embeds them into a local vector
store, and serves an agent that searches that KB to answer questions.

## How it works

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="images/kb-agent-pipeline-dark.svg">
    <source media="(prefers-color-scheme: light)" srcset="images/kb-agent-pipeline-light.svg">
    <img alt="kb-agent pipeline: projects.yaml → ingest.py → kb/*.md → index.py → ChromaDB; agent.py tools search_kb, list_projects, classify_snippet, search_notes; HTTP seams to defense-news-classifier /classify and notes-api /notes" src="images/kb-agent-pipeline-dark.svg" width="100%">
  </picture>
</p>

- **`scripts/ingest.py`** — reads each project's `pyproject.toml`/`requirements.txt`
  + README and uses the Anthropic API to write KB stubs. Never overwrites existing
  files (so your hand-annotations survive), unless you pass `--force`. It fingerprints
  the source each project stub was built from (in `kb/.ingest-manifest.json`), so
  `ingest.py --check` reports stubs that have drifted from their source and
  `ingest.py --accept` records the current source as the baseline without regenerating.
- **`scripts/index.py`** — chunks `kb/*.md` and embeds them into a local ChromaDB
  collection using the built-in `all-MiniLM-L6-v2` model (no API key, runs locally).
  Updates incrementally by default (re-embeds only changed chunks, drops stale ones);
  `--rebuild` re-embeds from scratch.
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

## Retrieval eval

`search_kb`'s quality is measured, not assumed: [`eval/gold_set.yaml`](eval/gold_set.yaml)
is a hand-labeled set of 27 queries → expected source files (scoped in
[`docs/notes/v2-kickoff.md`](docs/notes/v2-kickoff.md)), and the harness reports
recall@1/@3/@5 and MRR — overall, per kind, and for the adversarial slice
(jargon-vs-paraphrase pairs where lexical and dense retrieval should disagree).
Retrieval-only and fully local: no API key, just an indexed KB.

```bash
uv run python scripts/eval_retrieval.py                # the hard setting: no kind filter
uv run python scripts/eval_retrieval.py --kind-filter  # give the retriever each query's kind
```

First measured numbers (2026-07-17, 325-chunk index): unfiltered recall@5 **0.926** /
MRR **0.781**; with the kind filter recall@3 **1.000** / MRR **0.920**. Both unfiltered
misses were cross-kind crowding — indexed non-content files outranking the right stub —
which turns "should the notes ingest filter out repo scaffolding?" from a hunch into a
measurable next change.

Those are **dated workstation measurements**, taken against a corpus assembled from a local
absolute path that CI cannot rebuild — so they are reports, not thresholds, and none of them
is eligible to become a floor. Since
[ADR-012](decisions/ADR-012-reconstruct-the-notes-corpus-in-ci.md) CI reconstructs the corpus
from version control (a shallow clone of `learning-notes`, pointed at by `KB_AGENT_NOTES_DIRS`)
and runs both arms on every push and pull request as a **reporting** step. It does not gate the
merge on the value: per
[`system/SYS-017`](https://github.com/sanlee-ys/architecture/blob/main/decisions/SYS-017-evals-as-ci.md)
a floor needs several CI-measured runs to sit above their noise, and those runs are only now
starting to accumulate.

### Compositional set — an instrument, not yet a measurement

[`eval/compositional_set.yaml`](eval/compositional_set.yaml) is a **second, separate**
corpus of 61 queries that sits alongside the frozen 27 and does not touch them. It exists
because of a property of the gold set: **26 of its 27 queries have exactly one expected
source.** A single dense `search_kb` call answers a single-hop lookup by construction, so
that corpus cannot tell a multi-call retrieval design apart from the shipped loop — with
the kind filter supplied it is already at recall@5 1.000, leaving a headroom of one to two
queries. Every query in the compositional set instead needs evidence from **two or more
files spanning two or more kinds**, which is the only class where extra retrieval work has
anything to recover.

Three things about it are deliberate and load-bearing:

- **Nothing has been run against it.** It was authored before any arm exists, so no query
  can have been chosen because something failed it. The file says so in its own header,
  and the git history is the check.
- **It is scored ALL-of, not ANY-of.** The gold set counts a hit when *any* expected source
  appears in the top *k*; here the sources are conjunctive, so the pre-registered metric is
  complete union recall over every `search_kb` call in a turn, with per-query coverage
  fraction reported beside it.
- **It admits what it cannot decide.** 61 queries reach 80% power at a 75/25 effect only if
  roughly half of them come back discordant; a true 60/40 effect is undetectable at *any*
  discordance rate. The header carries the exact-McNemar tables rather than leaving that to
  be discovered after a run.

There is **no harness for it in this PR and nothing to run today** — a loop-level eval
spends API budget, so under [`system/SYS-017`](https://github.com/sanlee-ys/architecture/blob/main/decisions/SYS-017-evals-as-ci.md)
it belongs on an owner-triggered lane and never on a pull-request leg. When one lands the
shape is the existing paired comparison, unchanged:

```bash
uv run python scripts/index.py                       # the corpus this is scored against
uv run --env-file .env python scripts/eval_graph.py --arm <name> \
    --set eval/compositional_set.yaml --json eval/comp-<name>.json   # once per arm
uv run python scripts/eval_compare.py --baseline eval/comp-a.json --candidate eval/comp-b.json
```

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
