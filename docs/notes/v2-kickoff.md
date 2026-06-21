# v2 Kickoff: the shared retrieval backbone

*A warm-start breadcrumb for whoever (human or a fresh Claude session) begins v2, so the
work doesn't re-derive what v1 settled. Read this together with the `README.md` — and with
the **classifier's** own `docs/notes/v2-kickoff.md`, because the two roadmaps meet here.*

---

## Where v1 left off

- **v1 is the front-door ecosystem seam.** kb-agent stopped only *describing* tracked
  projects and started *driving* one: the `classify_snippet` tool routes over HTTP to the
  defense-news-classifier's `/classify` endpoint. Which projects are callable is config
  (an `endpoint:` in `projects.yaml`), the seam is HTTP (not a direct import), and the tool
  fails gracefully when the service is down. Verified end to end through the agent.
  - *Since v1:* the tool layer gained a documented contract — `system/SYS-003` (a consistent
    observation shape + recovery contract + eval gate). Note the scope: that governs kb-agent's
    **agent-facing tool results**; a future service-to-service `/search` API (scoping Q1 below) is
    a normal REST contract, not the observation shape — don't conflate the two.
- **The deliberate non-goal of v1:** no shared library, no merged vector store. With only
  two projects, abstracting a shared backbone risked premature abstraction — so we proved
  the cheap, decoupled seam first.

## What v2 is (and why it's next, not "eventual")

v2 is the **shared retrieval backbone**, and it is now the prioritized next step.

The thesis: kb-agent already *is* a retrieval system (ChromaDB + chunking + a tool-use
agent). The classifier's own v2 ("the RAG iteration") needs exactly that — retrieval, a
vector store, grounded source text. Rather than the classifier standing up its *own* vector
store, kb-agent's retrieval layer becomes the substrate it consumes. The two roadmaps
converge on one backbone.

Why this clears the premature-abstraction bar that v1 deliberately avoided: there is now a
**concrete second consumer with a real need** (the classifier's RAG iteration), not a
speculative one. That is the difference between factoring out a shared thing because two
real callers want it, and inventing one because you might.

## The open scoping questions (decide these first, before code)

1. **The boundary.** Keep the symmetry of the front door: kb-agent grows a `/search`
   endpoint and the classifier consumes it over HTTP — repos stay decoupled, each on its own
   release clock. The alternatives (a vector store shared on disk, or a shared library both
   import) re-introduce the coupling v1 was careful to avoid. Recommend HTTP; confirm.
2. **Collections.** The classifier's corpus (real public defense text) is different in kind
   from kb-agent's hand-edited project/library stubs. Likely a *separate Chroma collection*
   in the *same store*, not one merged collection. Decide the collection/metadata scheme.
3. **The eval rethink (carried from the classifier's v2-kickoff).** With real retrieved
   text there is no model-made answer key, so the classifier's v1 auto-grading breaks.
   Decide *how v2 measures quality* — a small hand-labeled gold set and/or an LLM judge —
   **before** building it. Same "measure first" discipline that kept both projects honest.

## Constraints carried forward (don't re-litigate)

- Public and/or synthetic text only. Nothing proprietary, anywhere.
- Secrets from the environment (`ANTHROPIC_API_KEY`); never commit `.env`.
- Minimal dependencies; `uv` for the env; keep the seam HTTP unless an eval says otherwise.
- Small steps, one concern per session/branch, checkpoint and surface design choices.

## How to start the v2 session

1. Open a **new session** (fresh context budget).
2. Read this file, the `README.md`, and the classifier's `docs/notes/v2-kickoff.md` to load
   both halves of the convergence cold.
3. Answer the three scoping questions *with San* before writing code — surface the choices
   and confirm, don't silently pick.
4. Build in small steps. Measure first.
