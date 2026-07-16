# v2 Kickoff: measure and improve kb-agent's own retrieval

*A warm-start breadcrumb for whoever (human or a fresh Claude session) begins v2, so the
work doesn't re-derive what v1 settled. Read this together with the `README.md`.*

> **Rescoped 2026-07-16.** The original v2 plan — a *shared retrieval backbone*, where
> kb-agent grew a `/search` endpoint and the classifier consumed it over HTTP — is
> **retired.** Its whole justification was "a concrete second consumer with a real need":
> the classifier's RAG iteration. That consumer went its own way — the classifier shipped
> its **own BM25 retriever** in its `v2.0.0` and never took the kb-agent endpoint. With no
> second consumer, the backbone had no one to serve, so factoring one out would be exactly
> the premature abstraction v1 was careful to avoid. The section below preserves that
> history; the *new* v2 is scoped around kb-agent's own quality instead (see
> **What v2 is now**). A separate near-term chore — **KB freshness** — is tracked lower
> down; it's a bugfix, not the milestone.

---

## History: what v1 left off, and the backbone that didn't happen

- **v1 is the front-door ecosystem seam.** kb-agent stopped only *describing* tracked
  projects and started *driving* one: the `classify_snippet` tool routes over HTTP to the
  defense-news-classifier's `/classify` endpoint (and `search_notes` reads the notes-api).
  Which projects are callable is config (an `endpoint:` in `projects.yaml`), the seam is
  HTTP (not a direct import), and the tool fails gracefully when the service is down.
  Verified end to end through the agent.
  - *Since v1:* the tool layer gained a documented contract — `system/SYS-003` (a consistent
    observation shape + recovery contract + eval gate) — governing kb-agent's agent-facing
    tool results.
- **The deliberate non-goal of v1:** no shared library, no merged vector store. With only
  two projects, abstracting a shared backbone risked premature abstraction — so we proved
  the cheap, decoupled seam first.
- **The backbone that was proposed, and why it's dead.** The v2 thesis was that kb-agent
  already *is* a retrieval system (ChromaDB + chunking + a tool-use agent), so rather than
  the classifier standing up its own vector store, kb-agent's retrieval layer would become
  the substrate it consumed — one `/search` endpoint, the two roadmaps converging on one
  backbone. That cleared the premature-abstraction bar *only* because there was a concrete
  second consumer. There no longer is: the classifier built and measured its own BM25
  retriever and shipped it. The convergence never happened, so the backbone is retired
  rather than parked — it was justified entirely by a consumer that now exists elsewhere.

## What v2 is now: earn the quality claim

The pivot exposes an irony worth naming plainly: the retired backbone plan *assumed*
kb-agent's retrieval was good enough to be a shared substrate for another project — but
kb-agent has **never measured its own retrieval quality.** There is no gold set, no
recall@k, no confusion between "the agent answered well" and "the right chunk was
retrieved." Every quality claim about `search_kb` today is a vibe.

So v2 turns inward and applies the same *measure-first* discipline that kept the classifier
honest: **make kb-agent's retrieval measurably good, then decide whether it needs to change
at all.** Concretely, in small steps:

1. **A retrieval gold set.** A small, hand-labeled set of `query → expected source file(s)`
   over the current KB — the questions a real user asks (`"what does project X depend on"`,
   `"which projects use httpx"`, `"how does the MCP server resolve its repo root"`). Kept in
   the repo, public/synthetic-safe like everything else here.
2. **A retrieval metric.** Report **recall@k** and **MRR** for `search_kb` against that gold
   set — does the right chunk come back in the top *k*? This is a retrieval measurement,
   deliberately *separate* from end-answer quality; conflating the two is how RAG systems
   hide bad retrieval behind a capable model.
3. **Only then, a change worth measuring.** The obvious candidate is **hybrid retrieval**
   (lexical BM25 + the current `all-MiniLM-L6-v2` embeddings) — pointedly, the classifier's
   own eval found plain BM25 competitive on its corpus, so the question "does dense
   retrieval actually beat lexical on kb-agent's short, jargon-heavy stubs?" is live, not
   settled. Build the alternative, run it against the gold set, and **ship the negative
   result if the lift is marginal** — same bar as the rest of the portfolio.

## The one scoping question that survives the pivot

The two old scoping questions died with the backbone — the HTTP `/search` boundary and the
shared-collections scheme both existed only to serve the classifier. One survives, because
it was always about measurement rather than the consumer:

- **How do we measure quality with no model-made answer key?** For retrieval this is more
  tractable than the classifier's version: the gold set is `query → expected source`, which
  a human can label directly against the KB (no LLM judge needed for recall@k / MRR). Decide
  the gold-set size and how queries are chosen (cover each `kind`: projects, libraries,
  notes) **before** building the harness. If end-answer quality gets measured too, *that*
  layer may want an LLM judge — keep it distinct from the retrieval metric.

## Near-term chore (a fix, not the v2 milestone): keep the KB fresh

This is a **separate track** from the v2 milestone above — a correctness chore, not a
measured capability — so it's called out on its own rather than folded into v2. It fixes a
staleness bug that already exists; do it whenever, independent of v2.

The problem is baked into the current pipeline. `ingest.py` **never overwrites** an existing
stub unless `--force` is passed, so once written, a stub is frozen while the project it
describes moves on (a new dependency, a rewritten README). `index.py` then **drops and
rebuilds the entire collection from scratch** every run. Two consequences:

- **No staleness signal.** Nothing tells you a stub is now out of date relative to its
  source `pyproject.toml`/README — it just quietly drifts. (`kb/projects/kb-agent.md` is the
  sharp case: hand-written, outside `ingest.py` entirely, so *nothing* regenerates it —
  CLAUDE.md already flags it as a known staleness risk.)
- **Wasteful rebuilds.** Re-embedding every chunk on every index run is fine at today's
  handful of stubs, but it's rebuild-the-world by design.

Two small, independent pieces:

1. **Freshness check — SHIPPED.** `ingest.py --check` fingerprints the source each project
   stub was generated from (description + deps + README prefix) in a sidecar manifest
   `kb/.ingest-manifest.json` and reports which stubs have drifted — `fresh` / `stale` /
   `untracked` / `missing` / `skipped`, plus `unmanaged` for orphan stubs like the
   hand-written `kb-agent.md`. It exits non-zero on `stale` (ready to gate CI/pre-commit
   later), and `ingest.py --accept` records the current source as a baseline without
   regenerating — the non-destructive way to bless the existing hand-curated stubs, unlike
   `--force`. The fingerprint hashes exactly the prompt inputs, so hand-edits to a stub never
   false-trip it. *First run on a machine reports every stub `untracked` until `--accept`
   (or a `--force` regenerate) establishes baselines.*
2. **Incremental re-index — still parked.** Make `index.py` re-embed only the chunks whose
   files changed rather than dropping the whole collection. Deliberately **not** done with
   piece 1: it's a perf refinement "worth it only once the KB is big enough that a full
   rebuild is felt" (see below), and at today's handful of stubs the drop-and-rebuild is
   instant *and* guarantees zero stale chunks. Adding change-tracking now trades that
   simplicity for correctness risk with no measurable win — pick it up when a rebuild is
   actually slow.

Unlike the v2 milestone, this track has **nothing to measure** — it's plumbing. That's
exactly why it's a chore and not the milestone: it fixes a real bug but produces no eval, so
it doesn't carry a "here are the numbers" story. Keep the two straight — don't let the chore
masquerade as v2.

## Constraints carried forward (don't re-litigate)

- Public and/or synthetic text only. Nothing proprietary, anywhere.
- Secrets from the environment (`ANTHROPIC_API_KEY`); never commit `.env`.
- Minimal dependencies; `uv` for the env. A lexical retriever should be a light addition
  (e.g. `rank-bm25`), not a heavy framework — and only if an eval says it earns its place.
- Small steps, one concern per session/branch, checkpoint and surface design choices.
- Retrieval quality is a *per-KB* measurement, not a universal claim — report it against
  this KB and say so.

## How to start the v2 session

1. Open a **new session** (fresh context budget).
2. Read this file and the `README.md` to load the pivot cold — v2 is now inward-facing
   (kb-agent's own retrieval), not a cross-repo backbone.
3. Answer the surviving scoping question *with San* — gold-set size and query selection —
   before writing the harness. Surface the choices and confirm, don't silently pick.
4. Build in small steps. Measure first; ship the negative result if that's what the numbers
   say.
