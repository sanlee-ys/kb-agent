# kb-agent

A personal, living knowledge base over your projects and the libraries you use —
with an AI agent you can ask questions, answered with RAG + tool use.

Point it at your project directories; it auto-generates Markdown stubs for each
project and dependency, embeds them into a local vector store, and serves an
agent that searches that KB to answer your questions.

## How it works

```
projects.yaml ──▶ ingest.py ──▶ kb/*.md ──▶ index.py ──▶ ChromaDB (local)
                  (Anthropic)   (you edit)   (local embeds)        │
                                                                   ▼
                                              agent.py ──▶ search_kb · list_projects
                                              (Claude tool-use loop)      · classify_snippet
                                                                                  │ HTTP
                                                                                  ▼
                                                          defense-news-classifier /classify
```

- **`scripts/ingest.py`** — reads each project's `pyproject.toml`/`requirements.txt`
  + README and uses the Anthropic API to write KB stubs. Never overwrites existing
  files (so your hand-annotations survive), unless you pass `--force`.
- **`scripts/index.py`** — chunks `kb/*.md` and embeds them into a local ChromaDB
  collection using the built-in `all-MiniLM-L6-v2` model (no API key, runs locally).
- **`agent/tools.py`** — the `search_kb` (RAG), `list_projects`, and
  `classify_snippet` tools. The first two are local; `classify_snippet` is the
  "ecosystem" seam — it calls a *tracked project's own HTTP service* (the
  defense-news-classifier's `/classify` endpoint) so the agent can **drive** a
  project, not just describe it. Which projects are callable is config: add an
  `endpoint:` to a project's `projects.yaml` entry. The tool fails gracefully —
  if the service is down, it tells you how to start it instead of crashing.
- **`agent/agent.py`** — a manual Claude tool-use loop: the model decides when to
  search the KB and answers from what it finds.

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

## Status

v1 — local KB with a RAG/tool-use agent (Gradio chat UI + CLI), now with a first
cross-project **ecosystem seam**: the agent can call a tracked project's HTTP
service (defense-news-classifier) through the `classify_snippet` tool.
