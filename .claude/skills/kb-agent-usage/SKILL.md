---
name: kb-agent-usage
description: >-
  Query the developer's personal knowledge base of projects, their libraries,
  and plain-language concept notes through the kb-agent MCP server. Use when a
  question is about how a tool or library was used in one of the user's own
  projects, a design decision they made, a concept from their notes, or which
  projects exist or depend on a given library. Explains when to reach for
  kb-agent:search_kb vs. kb-agent:list_projects and how to phrase queries so
  the semantic search returns useful chunks.
---

# Using the kb-agent knowledge base

The `kb-agent` MCP server fronts a local knowledge base built from the user's
tracked project directories: auto-generated stubs for each project, one stub per
dependency, and hand-written concept notes. It exposes two tools over stdio.
Reach for them instead of answering from general knowledge whenever the question
is about *this user's* projects, dependencies, or notes — the answer must come
from the KB, and results carry a `source` file path to cite.

Both tools return a JSON string with a `status` field. On `"success"`, read
`payload` and cite `source`. On `"warning"` or `"error"`, read `summary` for the
root cause and follow `next_actions` — they include a stop condition, so do not
retry an unchanged call.

## Which tool

- **`kb-agent:search_kb`** — semantic search over the indexed content. Use for
  any *content* question: how a library was used, why a design choice was made,
  what a concept means, what a project does. Takes `query` (required, natural
  language), optional `kind` (`"projects"`, `"libraries"`, or `"notes"` — any
  other value or omission searches all kinds), and optional `n_results`
  (default 5).
- **`kb-agent:list_projects`** — enumerate the tracked projects with their
  descriptions. Use only for the *inventory* question ("what projects exist?")
  or as a first step to discover the exact project name before a targeted
  `search_kb`. Takes no arguments.

Rule of thumb: if the user wants to *know something*, use `search_kb`; if they
want to *see the catalog*, use `list_projects`. When unsure which project a
question refers to, call `list_projects` first to get exact names, then
`search_kb` with that name in the query.

Always write the fully-qualified `kb-agent:search_kb` / `kb-agent:list_projects`
form, not the bare tool name — with multiple MCP servers connected, the bare
name can resolve to the wrong server.

## Phrasing queries

`search_kb` is a semantic vector search, so phrasing matters:

- **Use natural-language noun phrases**, not keyword soup: "how the classifier
  handles retrieval" beats "classifier retrieval BM25 search".
- **Include the specific term** you care about (a library, project, or concept
  name) — the index is keyed on the user's own vocabulary, so the exact library
  or project name pulls the right chunks.
- **Narrow with `kind`** when you already know the target is a concept note vs. a
  project vs. a library — it removes cross-kind noise. Drop the filter and
  retry if a filtered search returns a `"warning"` with no results.
- **Ask one thing per call.** Two unrelated topics in one query dilute the
  embedding; split them into two `search_kb` calls.
- **Raise `n_results`** (e.g. to 8-10) for a broad "what do we know about X"
  sweep; keep the default 5 for a specific lookup.

If `search_kb` returns a no-results warning, broaden or rephrase before giving
up — reword to the user's likely vocabulary, or drop the `kind` filter — but do
not repeat the identical call.

## What this KB does not cover

`search_kb` reads static, indexed stubs and notes. It is not the user's live
notes service and not a general web search. If the KB has no chunk on a topic,
say so and cite that the search came back empty, rather than filling the gap
from general knowledge.
