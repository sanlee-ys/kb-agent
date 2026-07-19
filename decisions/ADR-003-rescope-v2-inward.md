# ADR-003: Retire the shared retrieval backbone; rescope v2 inward

**Status:** Accepted
**Date:** 2026-07-16 (recorded as an ADR 2026-07-18)
**Deciders:** San Lee

---

## Context

The original v2 plan was a **shared retrieval backbone**: kb-agent would grow a `/search`
endpoint and the defense-news-classifier would consume it over HTTP, so that rather than the
classifier standing up its own vector store, kb-agent's retrieval layer (ChromaDB + chunking
+ a tool-use agent) became the substrate it retrieved from — "the two roadmaps converging on
one backbone" (`docs/notes/v2-kickoff.md:33-40`).

v1 had deliberately *not* done this. Its stated non-goal was "no shared library, no merged
vector store": with only two projects, abstracting a shared backbone risked premature
abstraction, so v1 proved the cheap, decoupled seam first — `classify_snippet` routing over
HTTP to the classifier's `/classify` endpoint, with which projects are callable kept as
config in `projects.yaml` (`docs/notes/v2-kickoff.md:21-32`).

The backbone cleared that premature-abstraction bar on one condition only: **a concrete
second consumer with a real need** — the classifier's RAG iteration. That consumer went its
own way. The classifier built, measured, and shipped its **own BM25 retriever** in its
`v2.0.0` and never took the kb-agent endpoint. With no second consumer, the backbone had no
one to serve.

A second thing the pivot exposed, named plainly in the source prose: the backbone plan
*assumed* kb-agent's retrieval was good enough to be a shared substrate for another project,
but **kb-agent had never measured its own retrieval quality** — no gold set, no recall@k, no
separation between "the agent answered well" and "the right chunk was retrieved." Every
quality claim about `search_kb` was, in the author's words, a vibe
(`docs/notes/v2-kickoff.md:44-48`).

**This ADR records a decision that was already made and already written down.** It has lived
since 2026-07-16 in the rescope banner and History section of `docs/notes/v2-kickoff.md`.
The `decisions/` tier did not exist until 2026-07-18, so the record lodged in the kickoff
note. Nothing here is new reasoning.

## Decision

**Retire the shared retrieval backbone — retired, not parked — and rescope v2 around
kb-agent's own retrieval quality.**

Retired rather than parked because the plan was justified entirely by a consumer that now
exists elsewhere. Parking it would imply the justification could return on its own; it
cannot, absent a new second consumer.

The `/search` endpoint was never built — no such endpoint or route exists anywhere in this
repo. The retirement removes a *plan*, not shipped code.

**v2 is now inward-facing: earn the quality claim.** Apply the same measure-first discipline
that kept the classifier honest — make kb-agent's retrieval measurably good, *then* decide
whether it needs to change at all (`docs/notes/v2-kickoff.md:50-67`):

1. **A retrieval gold set** — hand-labeled `query → expected source file(s)` over the current
   KB, in-repo and public/synthetic-safe.
2. **A retrieval metric** — recall@k and MRR for `search_kb` against that gold set,
   deliberately kept *separate* from end-answer quality, because conflating the two is how
   RAG systems hide bad retrieval behind a capable model.
3. **Only then, a change worth measuring** — the obvious candidate being hybrid retrieval
   (lexical BM25 + the current `all-MiniLM-L6-v2` embeddings), with the negative result
   shipped if the lift is marginal.

Two of the three original scoping questions **died with the backbone**: the HTTP `/search`
boundary and the shared-collections scheme both existed only to serve the classifier. The
one that survived — how to measure quality with no model-made answer key — survived because
it was always about measurement rather than the consumer (`docs/notes/v2-kickoff.md:69-80`).

## Downstream surfaces

- `docs/notes/v2-kickoff.md` — the source of this record and still the operative warm-start
  brief for the v2 session. It keeps the rescope banner, the preserved history, and the step
  list; this ADR is the canonical decision record of the retirement.
- `README.md` §"Retrieval eval" (lines 165-183) — the shipped output of the inward rescope:
  `eval/gold_set.yaml` (27 queries) and `scripts/eval_retrieval.py`, with first measured
  numbers dated 2026-07-17. Both files exist; the rescope is not a plan, it has landed.
- `decisions/README.md` — "Still to migrate" lists this decision (line 40); it moves to the
  index once migrated. The gold-set scope settled 2026-07-17 is a *separate* listed decision
  and is not recorded here.
- defense-news-classifier — no change is required of it by this ADR. Its existing consumption
  of kb-agent is unaffected: the v1 seam runs the other direction (kb-agent's
  `classify_snippet` calls the classifier's `/classify`), and that stays.
- `CLAUDE.md` — unaffected. It documents the pipeline and the tool layer as built; the
  backbone was never described there, so there is no rule to amend.

## Consequences

- **What was given up: the cross-repo convergence story.** kb-agent stops being pitched as
  shared infrastructure for a second project. The portfolio narrative for v2 becomes "I
  measured my own retrieval" instead of "two projects converged on one backbone" — a smaller
  claim, but one this repo can actually evidence.
- **The premature-abstraction guard from v1 held.** Factoring out a backbone with no consumer
  would have been exactly the mistake v1 avoided. The cost of having been careful is that a
  planned milestone evaporated when its consumer left; the benefit is that no code was
  written for it.
- **The assumption underneath the plan is now the work.** Retrieval quality moved from
  assumed to measured. The retirement is what surfaced that the assumption had never been
  tested.
- **The hybrid-retrieval question is live, not settled.** The classifier's own eval found
  plain BM25 competitive on *its* corpus, so whether dense retrieval beats lexical on
  kb-agent's short, jargon-heavy stubs is an open, measurable question rather than a
  foregone conclusion.
- **Quality claims stay per-KB.** Retrieval quality is measured against *this* KB and must be
  reported as such — carried forward as a standing constraint, and the same modesty that made
  "shared substrate" an unsupported claim in the first place.
- **Timing caveat, flagged not resolved.** This decision is dated 2026-07-16 and rests on the
  classifier having shipped its own BM25 retriever. On 2026-07-17 the classifier
  *retired* that BM25 grounding as a measured negative result
  (`defense-news-classifier/decisions/012-retire-bm25-grounding.md`), leaving it with no
  retrieval layer at all. The source prose predates that and therefore says nothing about
  whether it changes anything here; it does not restore a second consumer, and no revisit was
  recorded. Noted so a future reader does not mistake the silence for oversight.

## Alternatives Considered

| Option | Reason Not Chosen |
|--------|-------------------|
| Build the `/search` backbone anyway | Its whole justification was "a concrete second consumer with a real need." With the classifier having shipped its own BM25 retriever, the backbone had no one to serve — factoring one out would be exactly the premature abstraction v1 was careful to avoid |
| Park the backbone instead of retiring it | It was justified *entirely* by a consumer that now exists elsewhere. Parking implies the justification could revive on its own; retirement states plainly that it would take a new second consumer |
| Keep the two backbone scoping questions open (the HTTP `/search` boundary, the shared-collections scheme) | Both existed only to serve the classifier, so they died with it. Only the measurement question — no model-made answer key — survived, because it was never about the consumer |
| Jump straight to hybrid retrieval (add BM25) as the v2 milestone | Would change retrieval before anything could tell whether it improved. Measure first: build the gold set and the metric, *then* make a change worth measuring — and ship the negative result if the lift is marginal |
| Measure end-answer quality instead of retrieval | Conflating the two is how RAG systems hide bad retrieval behind a capable model. Recall@k / MRR against `query → expected source` needs no LLM judge; if end-answer quality is measured later, that layer stays distinct |
| Fold the KB-freshness work into v2 as the milestone | It is a correctness chore with nothing to measure — it fixes a real staleness bug but produces no eval, so it carries no "here are the numbers" story. Tracked as a separate near-term track so the chore cannot masquerade as v2 |
